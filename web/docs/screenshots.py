"""Regenerate the README screenshots in web/docs/images/ (1600x1000 PNGs).

Run from the repository root:

    uv run python web/docs/screenshots.py

What it does:
1. Builds the default look (npm run build -> web/dist) and starts the demo server on a free port
   with a throw-away WORKBENCH_DEMO_DIR; the server serves web/dist.
2. Captures: Home, a project page with its visual map, and inline editing in action.
3. Builds examples/minimal-look into a temporary folder and serves it with `vite preview`, whose
   /api proxy points at the same demo server (WORKBENCH_API), then captures the minimal look.
4. Stops every process it started and leaves web/dist holding the default look.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

WEB = Path(__file__).resolve().parents[1]
REPO = WEB.parent
OUT = WEB / "docs" / "images"
VIEWPORT = {"width": 1600, "height": 1000}
PROJECT = "demo-robot-arm"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for_http(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"{url} did not come up within {timeout}s")


def npm_build(env_extra: dict[str, str] | None = None, *args: str) -> None:
    env = {**os.environ, **(env_extra or {})}
    subprocess.run(["npm", "run", "build", "--", *args], cwd=WEB, env=env, check=True)


@contextmanager
def background(cmd: list[str], cwd: Path, env_extra: dict[str, str], ready_url: str):
    """Run a server in its own process group; always stop the whole group on exit."""
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        cmd, cwd=cwd, env={**os.environ, **env_extra}, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        wait_for_http(ready_url)
        yield proc
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            os.killpg(proc.pid, signal.SIGKILL)
        log.close()


def shot(page: Page, name: str) -> None:
    path = OUT / f"{name}.png"
    page.wait_for_timeout(600)  # let transitions and the map layout settle
    page.screenshot(path=str(path))
    print(f"  {path.relative_to(REPO)}  {path.stat().st_size // 1024} KB")


def capture_default(page: Page, base: str) -> None:
    page.goto(base)
    expect(page.get_by_test_id("unified-projects-grid")).to_be_visible()
    shot(page, "default-home")

    page.get_by_test_id(f"project-card-{PROJECT}").click()
    expect(page.get_by_test_id(f"project-page-{PROJECT}")).to_be_visible()
    if "visual" in (page.get_by_test_id("view-mode-toggle").text_content() or ""):
        page.get_by_test_id("view-mode-toggle").click()
    page.get_by_test_id("component-row-elbow-joint").locator(".tier-2-subtopic-header").click()
    shot(page, "default-project-map")

    # Inline editing: the summary editor open on the selected component
    page.get_by_test_id("edit-summary-elbow-joint").click()
    editor = page.get_by_test_id("edit-input-summary-elbow-joint")
    editor.fill("Belt-driven harmonic reduction stage with zero-backlash tensioner.\nTarget: < 2 arcmin.")
    page.get_by_test_id("save-edit").hover()
    shot(page, "default-inline-editing")


def capture_minimal(page: Page, base: str) -> None:
    page.goto(base)
    page.get_by_role("button", name="6-DOF Robot Arm").click()
    expect(page.locator(".mini main h2")).to_have_text("6-DOF Robot Arm")
    shot(page, "minimal-look")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    api_port, preview_port = free_port(), free_port()
    api = f"http://127.0.0.1:{api_port}"
    demo_dir = tempfile.mkdtemp(prefix="workbench-shots-")
    minimal_dist = tempfile.mkdtemp(prefix="workbench-minimal-dist-")

    try:
        print("Building the default look...")
        npm_build()
        demo_env = {"WORKBENCH_PORT": str(api_port), "WORKBENCH_DEMO_DIR": demo_dir}
        print("Building the minimal example look...")
        npm_build({"WORKBENCH_UI": "examples/minimal-look"}, "--outDir", minimal_dist, "--emptyOutDir")

        with background([sys.executable, "demo/run_demo.py"], REPO, demo_env, f"{api}/api/status"):
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport=VIEWPORT, color_scheme="dark")
                print("Capturing the default look...")
                capture_default(page, api)

                preview_cmd = [
                    "npx", "vite", "preview", "--outDir", minimal_dist,
                    "--port", str(preview_port), "--strictPort", "--host", "127.0.0.1",
                ]
                preview_url = f"http://127.0.0.1:{preview_port}"
                with background(preview_cmd, WEB, {"WORKBENCH_API": api}, preview_url):
                    print("Capturing the minimal look...")
                    page = browser.new_page(viewport=VIEWPORT, color_scheme="light")
                    capture_minimal(page, preview_url)
                browser.close()
    finally:
        # The minimal build went to a temp folder, so web/dist still holds the default look
        subprocess.run(["rm", "-rf", minimal_dist, demo_dir], check=False)


if __name__ == "__main__":
    main()
