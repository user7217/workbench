"""End-to-end Playwright UI tests and full scripted demo walkthrough (§14 + Additional Workbench Refinements §1–§5)."""

from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import Page, Route, expect, sync_playwright
from typer.testing import CliRunner

from workbench.cli import app as cli_app
from workbench.demo_env import init_demo_environment
from workbench.gitops import commit_all, create_worktree, push_head_to_branch, remove_worktree, run_git
from workbench.hooks import evaluate_hook_input
from workbench.server.app import create_app
from workbench.server.sync import ServerState
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip

runner = CliRunner()


def _get_free_port() -> int | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])
    except PermissionError:
        return None


class LiveServerHandle:
    def __init__(
        self,
        url: str,
        state: ServerState,
        demo_info: dict,
        server: uvicorn.Server | None = None,
        thread: threading.Thread | None = None,
        test_client: TestClient | None = None,
    ) -> None:
        self.url = url
        self.state = state
        self.demo_info = demo_info
        self._server = server
        self._thread = thread
        self.test_client = test_client

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.test_client is not None:
            self.test_client.close()


@pytest.fixture
def demo_live_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    demo_dir = tmp_path / "e2e_demo"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    monkeypatch.setenv("HOME", str(laptop_home))

    state = ServerState(
        remote_url=info["remote_bare"],
        state_dir=demo_dir / "server_state",
        branch="main",
    )
    fastapi_app = create_app(state=state, enable_background_loop=False)
    port = _get_free_port()

    if port is not None:
        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        url = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)

        handle = LiveServerHandle(
            url=url, state=state, demo_info=info, server=server, thread=thread
        )
    else:
        tc = TestClient(fastapi_app)
        tc.__enter__()
        handle = LiveServerHandle(
            url="http://127.0.0.1:8420",
            state=state,
            demo_info=info,
            test_client=tc,
        )

    try:
        yield handle
    finally:
        handle.stop()


@pytest.fixture
def page(demo_live_server: LiveServerHandle):
    """Provide an isolated Playwright Chromium page per test, supporting restricted macOS sandboxes."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.chromium.launch(args=["--single-process", "--no-zygote"])

        pg = browser.new_page(viewport={"width": 1440, "height": 900})

        if demo_live_server.test_client is not None:
            tc = demo_live_server.test_client

            def _forward_to_asgi(route: Route) -> None:
                req = route.request
                parsed = urllib.parse.urlsplit(req.url)
                path_and_query = parsed.path or "/"
                if parsed.query:
                    path_and_query = f"{path_and_query}?{parsed.query}"
                headers = {
                    k: v
                    for k, v in req.headers.items()
                    if k.lower() not in {"host", "content-length"}
                }
                resp = tc.request(
                    method=req.method,
                    url=path_and_query,
                    headers=headers,
                    content=req.post_data_buffer,
                )
                resp_headers = {
                    k: v
                    for k, v in resp.headers.items()
                    if k.lower() not in {"content-encoding", "transfer-encoding", "content-length"}
                }
                route.fulfill(
                    status=resp.status_code,
                    headers=resp_headers,
                    body=resp.content,
                )

            pg.route(f"{demo_live_server.url}/**", _forward_to_asgi)

        try:
            yield pg
        finally:
            browser.close()


def test_ui_end_to_end_playwright(page: Page, demo_live_server: LiveServerHandle) -> None:
    """Verify UI E2E requirements, status badge removal, adjustable contextual panel, concise subtopics, and phased vs non-phased projects."""
    base_url = demo_live_server.url
    page.goto(base_url)

    # 1. Home shows unified Projects and Learning grids without separate status sections
    expect(page.get_by_test_id("section-building")).to_be_visible()
    expect(page.get_by_test_id("section-learning")).to_be_visible()

    projects_grid = page.get_by_test_id("unified-projects-grid")
    expect(projects_grid).to_be_visible()
    expect(projects_grid.get_by_test_id("project-card-demo-notes-sync")).to_be_visible()
    expect(projects_grid.get_by_test_id("new-badge-demo-notes-sync")).to_have_text("New")
    expect(projects_grid.get_by_test_id("project-card-demo-robot-arm")).to_be_visible()
    expect(projects_grid.get_by_test_id("project-card-demo-weather-station")).to_be_visible()
    expect(projects_grid.get_by_test_id("project-card-demo-budget-cli")).to_be_visible()
    expect(projects_grid.get_by_test_id("project-card-demo-portfolio-site")).to_be_visible()

    # Confirm separate New / Active / Paused / Done sections are removed
    expect(page.get_by_test_id("building-group-new")).to_have_count(0)
    expect(page.get_by_test_id("building-group-active")).to_have_count(0)
    expect(page.get_by_test_id("building-group-paused")).to_have_count(0)
    expect(page.get_by_test_id("building-group-done")).to_have_count(0)

    # Archived is hidden by default and toggled via Show Archived
    expect(page.get_by_test_id("project-card-demo-old-game")).to_have_count(0)
    page.get_by_test_id("building-archived-toggle").click()
    expect(page.get_by_test_id("project-card-demo-old-game")).to_be_visible()
    page.get_by_test_id("building-archived-toggle").click()
    expect(page.get_by_test_id("project-card-demo-old-game")).to_have_count(0)

    # Check unified Learning grid on Home
    learning_grid = page.get_by_test_id("unified-learning-grid")
    expect(learning_grid.get_by_test_id("learning-card-demo-signal-processing")).to_be_visible()
    expect(learning_grid.get_by_test_id("learning-card-demo-linear-algebra")).to_be_visible()
    expect(learning_grid.get_by_test_id("learning-card-demo-control-theory")).to_be_visible()

    # 2. Check that the page contains NO percentage or count text (§13, §14)
    body_text = page.locator("body").inner_text()
    assert "%" not in body_text, f"Found forbidden percentage symbol in body text: {body_text}"
    assert not re.search(r"\(\d+\)", body_text), f"Found forbidden item count in body text: {body_text}"
    assert not re.search(r"\d+\s*/\s*\d+", body_text), f"Found forbidden progress fraction in body text: {body_text}"

    # 3. Verify collapsible sidebar works
    sidebar = page.get_by_test_id("left-sidebar")
    page.get_by_test_id("sidebar-collapse-toggle").click()
    expect(sidebar).to_have_class(re.compile(r"collapsed"))
    page.get_by_test_id("sidebar-collapse-toggle").click()
    expect(sidebar).not_to_have_class(re.compile(r"collapsed"))

    # 4. Navigate to Phased Project (Weather Station, uses_phases: true)
    page.get_by_test_id("project-card-demo-weather-station").click()
    expect(page.get_by_test_id("building-three-panel-workspace")).to_be_visible()
    expect(page.get_by_test_id("building-panel-nav")).to_be_visible()
    expect(page.get_by_test_id("building-panel-contents")).to_be_visible()
    expect(page.get_by_test_id("building-panel-visual")).to_be_visible()
    expect(page.get_by_test_id("project-page-demo-weather-station")).to_be_visible()
    expect(page.get_by_test_id("project-list-view")).to_be_visible()
    expect(page.get_by_test_id("visual-graph-canvas")).to_be_visible()
    expect(page.get_by_test_id("visual-context-panel")).to_be_visible()

    # Verify status badges ("Active", "Paused", etc.) are removed from project details header (§1)
    detail_banner = page.get_by_test_id("project-detail-banner")
    expect(detail_banner).to_be_visible()
    expect(detail_banner.locator(".quiet-status-indicator")).to_have_count(0)

    # Verify independent scroll containers exist on Panel A, Panel B, and Panel C (§2, §5)
    panel_a_overflow = page.get_by_test_id("panel-a-scroll").evaluate(
        "el => window.getComputedStyle(el).overflowY"
    )
    panel_b_overflow = page.get_by_test_id("project-page-demo-weather-station").evaluate(
        "el => window.getComputedStyle(el).overflowY"
    )
    panel_c_overflow = page.get_by_test_id("panel-c-scroll").evaluate(
        "el => window.getComputedStyle(el).overflowY"
    )
    assert panel_a_overflow == "auto"
    assert panel_b_overflow == "auto"
    assert panel_c_overflow == "auto"

    # 5. Test vertical & horizontal draggable resizing, persistence across reload, and Reset layout (§2, §5)
    nav_panel = page.get_by_test_id("building-panel-nav")
    initial_nav_width = nav_panel.bounding_box()["width"]
    assert abs(initial_nav_width - 276) <= 2

    resizer_left = page.get_by_test_id("panel-resizer-left")
    r_box = resizer_left.bounding_box()
    page.mouse.move(r_box["x"] + r_box["width"] / 2, r_box["y"] + r_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(r_box["x"] + 64, r_box["y"] + r_box["height"] / 2)
    page.mouse.up()

    resized_nav_width = nav_panel.bounding_box()["width"]
    assert resized_nav_width > initial_nav_width + 40

    # Test horizontal resizer between visual overview and contextual section (§2)
    diagram_pane = page.get_by_test_id("panel-c-diagram-pane")
    initial_diag_height = diagram_pane.bounding_box()["height"]
    assert abs(initial_diag_height - 360) <= 2

    resizer_horiz = page.get_by_test_id("panel-resizer-horizontal")
    h_box = resizer_horiz.bounding_box()
    page.mouse.move(h_box["x"] + h_box["width"] / 2, h_box["y"] + h_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(h_box["x"] + h_box["width"] / 2, h_box["y"] - 80)
    page.mouse.up()

    resized_diag_height = diagram_pane.bounding_box()["height"]
    assert resized_diag_height < initial_diag_height - 50
    assert resized_diag_height >= 160

    toggle_btn = page.get_by_test_id("view-mode-toggle")
    expect(toggle_btn).to_have_text("Switch to visual view")
    toggle_btn.click()
    expect(toggle_btn).to_have_text("Switch to list view")

    page.reload()
    page.get_by_test_id("project-card-demo-weather-station").click()
    expect(page.get_by_test_id("visual-graph-canvas")).to_be_visible()
    expect(page.get_by_test_id("view-mode-toggle")).to_have_text("Switch to list view")
    persisted_nav_width = page.get_by_test_id("building-panel-nav").bounding_box()["width"]
    persisted_diag_height = page.get_by_test_id("panel-c-diagram-pane").bounding_box()["height"]
    assert abs(persisted_nav_width - resized_nav_width) <= 2
    assert abs(persisted_diag_height - resized_diag_height) <= 2

    # Reset panel widths and diagram height to defaults
    page.get_by_test_id("reset-panel-widths").click()
    reset_nav_width = page.get_by_test_id("building-panel-nav").bounding_box()["width"]
    reset_diag_height = page.get_by_test_id("panel-c-diagram-pane").bounding_box()["height"]
    assert abs(reset_nav_width - 276) <= 2
    assert abs(reset_diag_height - 360) <= 2

    # 6. Verify simplified subtopics (concise by default, expandable on demand) & Context Panel synchronization (§3)
    page.get_by_test_id("visual-phase-firmware").click()
    expect(page.get_by_test_id("visual-context-title")).to_have_text("Embedded Firmware")
    expect(page.get_by_test_id("visual-context-subtopic-radio-tx")).to_be_visible()

    # Subtopic detail items are hidden by default in the overview and shown when explicitly requested (§3)
    expect(page.get_by_test_id("visual-context-items-radio-tx")).to_have_count(0)
    page.get_by_test_id("visual-context-expand-radio-tx").click()
    expect(page.get_by_test_id("visual-context-items-radio-tx")).to_be_visible()
    page.get_by_test_id("visual-context-expand-radio-tx").click()
    expect(page.get_by_test_id("visual-context-items-radio-tx")).to_have_count(0)

    # Clicking a subtopic in the Context Panel beneath the diagram synchronizes Panel A and Panel B
    page.get_by_test_id("visual-context-subtopic-radio-tx").click()
    inspector = page.get_by_test_id("inspector-panel")
    expect(inspector).to_be_visible()
    expect(page.get_by_test_id("inspector-title")).to_have_text("Radio transmitter")
    expect(page.get_by_test_id("inspector-summary")).to_contain_text("Framed LoRa packet transmission")
    expect(page.get_by_test_id("nav-tree-comp-radio-tx")).to_have_class(re.compile(r"active"))

    # Click dependency link `I2C sensor driver` in Panel B and verify synchronized navigation across all panels
    page.get_by_test_id("inspector-dep-i2c-driver").click()
    expect(page.get_by_test_id("inspector-title")).to_have_text("I2C sensor driver")
    expect(page.get_by_test_id("nav-tree-comp-i2c-driver")).to_have_class(re.compile(r"active"))
    expect(page.get_by_test_id("visual-context-subtopic-i2c-driver")).to_have_class(
        re.compile(r"selected")
    )

    # Press Esc to close component inspector card
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("inspector-panel")).to_have_count(0)

    # 7. Verify Non-Phased Project (`demo-portfolio-site`, uses_phases: false) displays direct topics without phase groupings (§4)
    page.get_by_test_id("project-card-demo-portfolio-site").click()
    expect(page.get_by_test_id("project-page-demo-portfolio-site")).to_be_visible()
    expect(page.get_by_test_id("direct-topics-view")).to_be_visible()
    expect(page.get_by_test_id("visual-context-title")).to_have_text("Typography & Layout")
    expect(page.get_by_test_id("visual-context-subtopic-article-layout")).to_be_visible()

    # 8. Learning mark-done updates the UI and the remote bare repo
    page.get_by_test_id("nav-learning").click()
    page.get_by_test_id("learning-card-demo-signal-processing").click()
    expect(page.get_by_test_id("learning-page-demo-signal-processing")).to_be_visible()

    page.get_by_test_id("learning-subtopic-aliasing").click()
    expect(page.get_by_test_id("inspector-title")).to_have_text("Aliasing & Folding")
    expect(page.get_by_test_id("inspector-markdown")).to_contain_text("Nyquist frequency")

    with page.expect_response(
        lambda r: "/api/learning/demo-signal-processing/items/aliasing/status" in r.url
        and r.request.method == "POST"
    ) as resp_info:
        page.get_by_test_id("status-btn-done").click()
    assert resp_info.value.status == 200
    expect(page.get_by_test_id("status-btn-done")).to_have_class(re.compile(r"active"))

    # Confirm the change was committed and pushed to the remote bare repo
    laptop_data = Path(demo_live_server.demo_info["laptop_data_dir"])
    run_git(["fetch", "origin", "main"], cwd=laptop_data)
    git_log = run_git(["log", "origin/main", "-n1", "--oneline"], cwd=laptop_data).stdout
    assert "Mark aliasing done via dashboard" in git_log

    # 9. Failed-sync banner appears after an invalid push
    wt_bad = Path(demo_live_server.demo_info["base_dir"]) / "wt_bad_e2e"
    create_worktree(laptop_data, wt_bad, ref="origin/main")
    bad_proj = wt_bad / "projects" / "demo-weather-station" / "project.yaml"
    bad_proj.write_text(
        "schema_version: 1\nid: demo-weather-station\nname: Weather Station\n"
        "description: Leaked secret AKIAIOSFODNN7EXAMPLE\nstatus: active\n"
        "origin: agent\nupdated: 2026-10-20\nrepos: []\nlinks: []\nphases: []\n",
        encoding="utf-8",
    )
    commit_all(wt_bad, "Push invalid secret for E2E banner test")
    push_head_to_branch(wt_bad, branch="main")
    remove_worktree(laptop_data, wt_bad)

    # Click "Check for updates" in footer
    page.get_by_test_id("check-updates-button").click()
    banner = page.get_by_test_id("failed-sync-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Latest update failed validation — showing previous version")


def test_full_demo_walkthrough_scripted(page: Page, demo_live_server: LiveServerHandle) -> None:
    """Full scripted walkthrough (§14): demo init -> simulated agent session -> hook prompts -> begin/edit/push -> server picks it up -> visible in UI."""
    info = demo_live_server.demo_info
    laptop_home = Path(info["laptop_home"])
    ws_repo = Path(info["code_dir"]) / "weather-station"

    # 1. Simulate agent working in weather-station repo and making a commit
    (ws_repo / "firmware" / "enclosure_sensor.c").write_text("// new telemetry code\n", encoding="utf-8")
    commit_all(ws_repo, "Implement radiation shield telemetry")

    # 2. Agent session ends -> Stop hook fires and blocks with reminder to run workbench skill
    hook_out = evaluate_hook_input(
        json.dumps(
            {
                "session_id": "walkthrough-session-1",
                "cwd": str(ws_repo),
                "stop_hook_active": False,
            }
        ),
        home_dir=laptop_home,
    )
    assert hook_out is not None
    assert json.loads(hook_out)["decision"] == "block"

    # 3. Agent runs `workbench begin` for weather-station, edits YAML, and runs `workbench push`
    res_begin = runner.invoke(cli_app, ["begin", "--id", "demo-weather-station"])
    assert res_begin.exit_code == 0, res_begin.output
    wt_yaml = Path(res_begin.stdout.strip())

    doc = load_yaml_roundtrip(wt_yaml)
    doc["phases"][2]["status"] = "done"
    doc["phases"][2]["components"][0]["status"] = "done"
    doc["phases"][2]["components"][0]["summary"] = "Completed PETG radiation shield assembly."
    dump_yaml_roundtrip(doc, wt_yaml)

    res_push = runner.invoke(
        cli_app, ["push", str(wt_yaml), "-m", "Mark enclosure radiation shield done"]
    )
    assert res_push.exit_code == 0, res_push.output

    # 4. Server syncs and picks up the pushed change
    assert demo_live_server.state.sync_once() is True

    # 5. Open UI and verify the updated component summary is visible on the Weather Station page
    page.goto(demo_live_server.url)
    page.get_by_test_id("project-card-demo-weather-station").click()
    expect(page.get_by_test_id("project-page-demo-weather-station")).to_be_visible()
    expect(page.get_by_test_id("component-row-radiation-shield")).to_contain_text(
        "Completed PETG radiation shield assembly."
    )
