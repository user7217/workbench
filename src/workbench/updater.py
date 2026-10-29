"""Headless per-message updater: a background agent records each message on the dashboard.

Flow (update_mode = "headless" in ~/.workbench/config.toml):

    Stop / AfterAgent hook (hooks.py)
        files or HEAD changed since the last message -> write a job file, start
        `workbench auto-update --job <file>` in the background, never block the session
    workbench auto-update (cli.py)
        read the last exchange from the transcript, build a prompt with the project's full
        entry and the repo changes, run `updater_command` (prompt on stdin, JSON on stdout),
        check the reply, apply it in one worktree and push one `update(...)` commit

The agent gets no tools. It only answers with JSON: `set` and `add` operations on existing
items plus suggestions for the inbox. Workbench checks and applies them itself, so the agent
can never touch code, rename or remove anything, and every change is one undoable commit.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workbench import edits
from workbench.config import get_workbench_dir

# Set in the agent's environment so its own Stop hook never starts another update.
UPDATER_ENV_VAR = "WORKBENCH_UPDATER"

MAX_OPS = 6
MAX_SUGGESTIONS = 5
USER_CHARS = 4000
REPLY_CHARS = 8000
PREVIOUS_REPLY_CHARS = 3000
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
SET_FIELDS = ("status", "notes", "summary")
ADD_FIELDS = ("under", "name", "status", "summary", "notes", "depends_on")

PROMPT_TEMPLATE = """\
You keep a project's entry on Workbench (a dashboard of projects) up to date after one message
of a coding session. Reply with ONLY a JSON object, no prose and no code fence:

{{"summary": "<=70 chars: what changed", "ops": [...], "suggestions": [...]}}

ops (at most {max_ops}), each one of:
  {{"op": "set", "item": "<existing id>", "status": "...", "notes": "...", "summary": "..."}}
      status, notes and summary are each optional; give only what changed
  {{"op": "add", "under": "<existing id, or an id added earlier in this list>", "name": "...",
   "status": "...", "summary": "...", "notes": "...", "depends_on": ["<component ids>"]}}
suggestions (at most {max_suggestions}): {{"name": "...", "summary": "...", "under": "<existing id or null>"}}

Rules:
- Record only what THIS message did or decided. Use the entry's summaries to find where it belongs.
- Statuses: done only when built and working (tests passed or the user saw it work);
  in_progress when partly built; not_started for planned work; parked when the user put it on hold.
- If an item already covers the work, "set" it; "add" only for new work the user asked for or
  agreed to, or a problem actually found. Put a new item under the deepest item it belongs to,
  not at the top; link work it needs first with depends_on (component ids).
- notes replace the item's notes: current state and the next concrete step (<= 1000 chars).
  summary: what it is and why it is needed (<= 300 chars).
- Next steps the assistant proposed that the user has not agreed to go in "suggestions", never in
  "ops". If the user's message picks earlier suggestions ("yes, do 1 and 3"), those are agreed.
- Never rename or remove anything. Use ids exactly as they appear in the entry.
- Never include secrets, API keys, file paths, emails, IP addresses, pasted logs or error output,
  or personal information.
- If nothing worth recording happened (refactor, formatting, discussion that settled nothing),
  reply {{"summary": "", "ops": [], "suggestions": []}}.

Project: {project_id}
Focus (where work was happening): {focus}

Entry (project.yaml):
{entry}
Repo changes:
{repo_changes}

Previous assistant reply (context only, may be cut):
{previous_reply}

User message:
{user}

Assistant reply:
{reply}
"""


class UpdaterError(RuntimeError):
    """The updater could not produce or apply a plan; the message says why."""


# ---------------------------------------------------------------------------
# The last exchange of the session
# ---------------------------------------------------------------------------


@dataclass
class Exchange:
    user: str
    reply: str
    previous_reply: str = ""


# Harness wrappers that are not part of what the user wrote.
_WRAPPER_RE = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|local-command-stderr)>.*?</\1>",
    re.DOTALL,
)


def _clean(text: str) -> str:
    return _WRAPPER_RE.sub("", text).strip()


def _content_text(content: Any) -> tuple[str, bool]:
    """(text of a message's content, whether it carries tool results)."""
    if isinstance(content, str):
        return content, False
    if not isinstance(content, list):
        return "", False
    texts: list[str] = []
    has_tool_result = False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "tool_result":
            has_tool_result = True
    return "\n".join(texts), has_tool_result


@dataclass
class _Turn:
    start: int
    user: str
    reply: list[str] = field(default_factory=list)


def read_claude_turns(transcript: Path, upto: int | None = None) -> list[_Turn]:
    """User turns (with the assistant text that followed each) from a Claude Code transcript.

    Reads only the last few MB. Tool results, meta entries, subagent (sidechain) entries and
    compaction summaries are not user turns. Turn.start is the byte offset of the user entry.
    """
    size = transcript.stat().st_size
    end = size if upto is None else min(upto + TRANSCRIPT_TAIL_BYTES, size)
    begin = max(0, (upto if upto is not None else size) - TRANSCRIPT_TAIL_BYTES)
    with transcript.open("rb") as fh:
        fh.seek(begin)
        data = fh.read(end - begin)
    offset = begin
    if begin > 0:
        # Skip the partial first line
        cut = data.find(b"\n") + 1
        offset += cut
        data = data[cut:]

    turns: list[_Turn] = []
    for raw in data.split(b"\n"):
        line_start = offset
        offset += len(raw) + 1
        try:
            entry = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        text, has_tool_result = _content_text(message.get("content"))
        if entry.get("type") == "user":
            if has_tool_result or entry.get("isMeta") or entry.get("isCompactSummary"):
                continue
            text = _clean(text)
            if text:
                turns.append(_Turn(start=line_start, user=text))
        elif entry.get("type") == "assistant" and turns and text.strip():
            turns[-1].reply.append(text.strip())
    return turns


def exchange_from_hook(payload: dict[str, Any], transcript_size: int | None = None) -> Exchange | None:
    """The message the hook fired for: Gemini sends it in the payload, Claude in a transcript.

    transcript_size is the transcript's size when the hook fired: the turn that started before
    it is the one to record, even if the job runs after later messages were written.
    """
    prompt = payload.get("prompt")
    response = payload.get("prompt_response")
    if isinstance(prompt, str) and isinstance(response, str) and prompt.strip():
        return Exchange(user=_clean(prompt), reply=response.strip())

    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path or not Path(path).is_file():
        return None
    turns = read_claude_turns(Path(path), upto=transcript_size)
    if transcript_size is not None:
        turns = [t for t in turns if t.start < transcript_size] or turns
    if not turns:
        return None
    last = turns[-1]
    reply = "\n\n".join(last.reply)
    last_message = payload.get("last_assistant_message")
    if isinstance(last_message, str) and last_message.strip() and last_message.strip() not in reply:
        reply = f"{reply}\n\n{last_message.strip()}".strip()
    previous = "\n\n".join(turns[-2].reply) if len(turns) > 1 else ""
    return Exchange(user=last.user, reply=reply, previous_reply=previous)


def cut(text: str, limit: int) -> str:
    """Keep the start and the end of a long text (the end of a reply holds its summary)."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.4)
    tail = limit - head
    return f"{text[:head]}\n[... cut ...]\n{text[-tail:]}"


# ---------------------------------------------------------------------------
# Prompt, agent and reply
# ---------------------------------------------------------------------------


def build_prompt(
    project_id: str, entry_yaml: str, exchange: Exchange, repo_changes: str, focus: str | None
) -> str:
    return PROMPT_TEMPLATE.format(
        max_ops=MAX_OPS,
        max_suggestions=MAX_SUGGESTIONS,
        project_id=project_id,
        focus=focus or "none",
        entry=entry_yaml.rstrip() + "\n",
        repo_changes=repo_changes.strip() or "(none)",
        previous_reply=cut(exchange.previous_reply, PREVIOUS_REPLY_CHARS) or "(none)",
        user=cut(exchange.user, USER_CHARS),
        reply=cut(exchange.reply, REPLY_CHARS) or "(none)",
    )


def command_available(command: str, env: dict[str, str] | None = None) -> bool:
    parts = shlex.split(command)
    if not parts:
        return False
    exe = parts[0]
    if os.path.isabs(exe):
        return os.access(exe, os.X_OK)
    return shutil.which(exe, path=(os.environ if env is None else env).get("PATH")) is not None


def run_agent(command: str, prompt: str, timeout_seconds: float = 180.0) -> str:
    """Run the updater command with the prompt on stdin, in an empty directory; return stdout."""
    parts = shlex.split(command)
    if not parts:
        raise UpdaterError("updater_command is empty")
    run_env = dict(os.environ)
    run_env[UPDATER_ENV_VAR] = "1"
    with tempfile.TemporaryDirectory(prefix="workbench-updater-") as tmp_dir:
        try:
            proc = subprocess.run(
                parts,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=tmp_dir,
                env=run_env,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise UpdaterError(f"updater command timed out after {timeout_seconds:.0f}s")
        except OSError as exc:
            raise UpdaterError(f"updater command failed to start: {exc}")
    if proc.returncode != 0:
        output = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = f": {output[-1][:200]}" if output else ""
        raise UpdaterError(f"updater command exited with code {proc.returncode}{detail}")
    return proc.stdout


@dataclass
class Plan:
    summary: str
    ops: list[dict[str, Any]]
    suggestions: list[dict[str, Any]]


def parse_reply(text: str) -> Plan:
    """Parse the agent's JSON reply (a code fence or text around the object is tolerated)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise UpdaterError("agent reply has no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise UpdaterError(f"agent reply is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise UpdaterError("agent reply is not a JSON object")
    ops = data.get("ops") or []
    suggestions = data.get("suggestions") or []
    if not isinstance(ops, list) or not isinstance(suggestions, list):
        raise UpdaterError("'ops' and 'suggestions' must be lists")
    summary = data.get("summary")
    return Plan(
        summary=" ".join(summary.split())[:100] if isinstance(summary, str) else "",
        ops=[op for op in ops if isinstance(op, dict)][:MAX_OPS],
        suggestions=[s for s in suggestions if isinstance(s, dict)][:MAX_SUGGESTIONS],
    )


# ---------------------------------------------------------------------------
# Applying a plan
# ---------------------------------------------------------------------------


@dataclass
class Applied:
    changes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)


# Model limits (models.py), so an over-long text never fails the whole push
LIMITS = {"name": 80, "summary": 300, "notes": 1000}


def _text(value: Any, limit: int | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _known_names(doc: Any) -> set[str]:
    """Normalized names of every item and suggestion, so nothing is filed twice."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        for child in edits.children(node, edits.ITEM_LIST_KEYS):
            if isinstance(child.get("name"), str):
                names.add(_norm(child["name"]))
            walk(child)

    walk(doc)
    for sug in doc.get("suggestions") or []:
        if isinstance(sug, dict) and isinstance(sug.get("name"), str):
            names.add(_norm(sug["name"]))
    return names


def _apply_set(doc: Any, project_id: str, op: dict[str, Any], out: Applied) -> None:
    item = op.get("item")
    status = _text(op.get("status"))
    fields = {k: v for k in ("notes", "summary") if (v := _text(op.get(k), LIMITS[k])) is not None}
    edits.require_project_item(doc, project_id, item)
    if status is not None:
        edits.require_status(status, edits.PHASE_STATUSES)
    if status is None and not fields:
        raise edits.EditError("nothing to set")
    if status is not None:
        edits.set_project_item_status(doc, project_id, item, status)
    if fields:
        edits.set_project_item_fields(doc, project_id, item, fields)
    what = ", ".join(([status] if status else []) + [f"{k} updated" for k in fields])
    out.changes.append(f"{item}: {what}")
    out.touched.append(str(item))


def _apply_add(doc: Any, project_id: str, op: dict[str, Any], out: Applied) -> None:
    under = op.get("under")
    if not isinstance(under, str) or not under:
        raise edits.EditError("'add' needs 'under' (an existing item)")
    name = _text(op.get("name"), LIMITS["name"]) or ""
    if _norm(name) in _known_names(doc):
        raise edits.EditError(f"an item named '{name}' already exists")
    deps = op.get("depends_on")
    new_id, _ = edits.add_project_item(
        doc,
        project_id,
        parent_id=under,
        name=name,
        status=_text(op.get("status")) or "not_started",
        summary=_text(op.get("summary"), LIMITS["summary"]) or "",
        notes=_text(op.get("notes"), LIMITS["notes"]) or "",
        depends_on=[d for d in deps if isinstance(d, str)] if isinstance(deps, list) else None,
    )
    out.changes.append(f"add {new_id} under {under}")
    out.touched.append(under)


def apply_plan(doc: Any, project_id: str, plan: Plan, today: str) -> Applied:
    """Apply the plan to a round-trip project doc; an op that does not fit is skipped, not fatal."""
    out = Applied()
    for op in plan.ops:
        kind = op.get("op")
        unknown = set(op) - {"op", *(SET_FIELDS if kind == "set" else ADD_FIELDS), "item"}
        try:
            if unknown:
                raise edits.EditError(f"unexpected fields {sorted(unknown)}")
            if kind == "set":
                _apply_set(doc, project_id, op, out)
            elif kind == "add":
                _apply_add(doc, project_id, op, out)
            else:
                raise edits.EditError(f"unknown op {kind!r} (only set and add are allowed)")
        except (edits.EditError, TypeError, ValueError) as exc:
            out.skipped.append(f"{kind or '?'} {op.get('item') or op.get('name') or ''}: {exc}".strip())
    for sug in plan.suggestions:
        name = _text(sug.get("name"), LIMITS["name"]) or ""
        under = sug.get("under") if isinstance(sug.get("under"), str) and sug.get("under") else None
        try:
            if _norm(name) in _known_names(doc):
                raise edits.EditError("already on the dashboard")
            new_id = edits.add_suggestion(
                doc, project_id, name=name, summary=_text(sug.get("summary"), LIMITS["summary"]) or "",
                under=under, added=today,
            )
            out.changes.append(f"suggest {new_id}")
        except (edits.EditError, TypeError, ValueError) as exc:
            out.skipped.append(f"suggestion {name}: {exc}")
    return out


def commit_scope_and_summary(project_id: str, plan: Plan, applied: Applied) -> tuple[str, str]:
    """Scope `<project>/<item>` when one item was touched, else `<project>`; the agent's summary."""
    touched = list(dict.fromkeys(applied.touched))
    scope = f"{project_id}/{touched[0]}" if len(touched) == 1 else project_id
    summary = plan.summary or "; ".join(applied.changes)
    return scope, summary[:100]


# ---------------------------------------------------------------------------
# Jobs (written by the hook, run by `workbench auto-update --job`)
# ---------------------------------------------------------------------------


def jobs_dir(home_dir: Path | None = None, env: dict[str, str] | None = None) -> Path:
    return get_workbench_dir(home_dir=home_dir, env=env) / "jobs"


def log_path(home_dir: Path | None = None, env: dict[str, str] | None = None) -> Path:
    return get_workbench_dir(home_dir=home_dir, env=env) / "logs" / "updater.log"


def launch_update_job(
    job: dict[str, Any], home_dir: Path | None = None, env: dict[str, str] | None = None
) -> Path:
    """Write the job file and start `workbench auto-update --job` detached; returns the job path.

    The child's output goes to the updater log, so the hook returns at once.
    """
    path = jobs_dir(home_dir, env) / f"{int(time.time() * 1000)}-{secrets.token_hex(3)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job), encoding="utf-8")
    log = log_path(home_dir, env)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        subprocess.Popen(
            [sys.executable, "-m", "workbench", "auto-update", "--job", str(path)],
            cwd=job.get("repo_top") or None,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=None if env is None else dict(env),
        )
    return path
