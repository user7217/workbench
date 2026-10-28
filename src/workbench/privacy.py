"""Deterministic privacy scanning and isolated LLM privacy reviewer (§7)."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

from detect_secrets.core.scan import get_plugins
from detect_secrets.settings import default_settings

REVIEWER_PROMPT_TEMPLATE = """You review text that will be stored in a private personal dashboard.
Flag anything that is: a credential or secret, a personal email or phone number,
a home address, a local filesystem path, private information about other people,
or internal details of an employer or client. Ignore ordinary technical content.
Reply with ONLY JSON: {"verdict": "pass" | "fail", "issues": [{"field": "...", "reason": "..."}]}
TEXT:
"""

# Custom regex rules: (pattern_type, compiled_regex)
CUSTOM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|github_pat|gho)_[A-Za-z0-9_]{16,}\b")),
    ("api_secret_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("slack_token", re.compile(r"\bxox[abp]-[A-Za-z0-9-]{10,}\b")),
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*[:=]\s*\S+")),
    (
        "connection_string_credentials",
        re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:@/]+:[^\s@/]+@[^\s/]+"),
    ),
    ("local_path_users", re.compile(r"(?:^|[\s\"'=(])/Users/[^\s\"')]*")),
    ("local_path_home", re.compile(r"(?:^|[\s\"'=(])/home/[^\s\"')]*")),
    ("local_path_windows", re.compile(r"\b[A-Za-z]:\\[^\s\"')]*")),
    ("local_path_tilde", re.compile(r"(?:^|[\s\"'=(])~/[^\s\"')]*")),
    (
        "ipv4_private",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|"
            r"127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
        ),
    ),
    ("local_hostname", re.compile(r"\b[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.local\b")),
]

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.invalid"}


@dataclass
class PrivacyIssue:
    field_path: str
    pattern_type: str

    def format_error(self) -> str:
        """Format error naming field path and pattern type WITHOUT echoing the matched value."""
        return f"Privacy violation in '{self.field_path}': detected {self.pattern_type}"


@dataclass
class ReviewerResult:
    verdict: str  # "pass" | "fail" | "skipped" | "unavailable"
    issues: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def scan_text_value(text: str, field_path: str) -> list[PrivacyIssue]:
    """Scan a single string value for secrets, PII, paths, IPs, and emails.

    Never includes the matched value in the returned PrivacyIssue objects.
    """
    if not text:
        return []

    issues: list[PrivacyIssue] = []
    seen_types: set[str] = set()

    # 1. Custom regex patterns
    for pattern_type, regex in CUSTOM_PATTERNS:
        if regex.search(text):
            if pattern_type not in seen_types:
                seen_types.add(pattern_type)
                issues.append(PrivacyIssue(field_path=field_path, pattern_type=pattern_type))

    # 2. Email check (allow @example.com and @example.invalid)
    for match in EMAIL_REGEX.finditer(text):
        domain = match.group(1).lower()
        if domain not in ALLOWED_EMAIL_DOMAINS:
            if "email_address" not in seen_types:
                seen_types.add("email_address")
                issues.append(PrivacyIssue(field_path=field_path, pattern_type="email_address"))

    # 3. detect-secrets default plugins via analyze_line
    if not (field_path.endswith("root_commit") or ".refs[" in field_path):
        with default_settings():
            plugins = get_plugins()
            for idx, line in enumerate(text.splitlines(), start=1):
                for plugin in plugins:
                    # Skip HexHighEntropyString/Base64HighEntropyString on plain prose words
                    # unless analyze_line matches a quoted token
                    findings = plugin.analyze_line("metadata.yaml", line, idx)
                    if findings:
                        plugin_type = f"detect_secrets:{plugin.secret_type}"
                        if plugin_type not in seen_types:
                            seen_types.add(plugin_type)
                            issues.append(
                                PrivacyIssue(field_path=field_path, pattern_type=plugin_type)
                            )

    return issues


def scan_data_structure(data: Any, path_prefix: str = "") -> list[PrivacyIssue]:
    """Recursively scan all string fields in a dict/list data structure."""
    issues: list[PrivacyIssue] = []
    if isinstance(data, dict):
        for k, v in data.items():
            sub_path = f"{path_prefix}.{k}" if path_prefix else str(k)
            if k == "root_commit" and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{7,64}", v):
                continue
            if k == "refs" and isinstance(v, list):
                for idx, item in enumerate(v):
                    if isinstance(item, str) and not re.fullmatch(r"[0-9a-f]{7,64}", item):
                        issues.extend(scan_text_value(item, f"{sub_path}[{idx}]"))
                continue
            issues.extend(scan_data_structure(v, sub_path))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            sub_path = f"{path_prefix}[{idx}]"
            issues.extend(scan_data_structure(item, sub_path))
    elif isinstance(data, str):
        issues.extend(scan_text_value(data, path_prefix or "root"))
    return issues


_FREE_TEXT_KEYS = ("summary", "description", "notes")
_CHILD_KEYS = ("components", "topics", "subtopics")


def _collect_free_text(items: Any, path: str, out: dict[str, str]) -> None:
    """Add free-text fields of each item, then recurse into nested components/topics/subtopics."""
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        item_path = f"{path}[{item.get('id', str(idx))}]"
        for key in _FREE_TEXT_KEYS:
            if isinstance(item.get(key), str) and item.get(key):
                out[f"{item_path}.{key}"] = item[key]
        for child_key in _CHILD_KEYS:
            _collect_free_text(item.get(child_key), f"{item_path}.{child_key}", out)


def extract_free_text_fields(
    data: dict[str, Any], topic_markdowns: dict[str, str] | None = None
) -> dict[str, str]:
    """Extract all free-text fields (description, summary, notes, topic .md) keyed by field path.

    Recurses through components nested to any depth so the LLM reviewer sees them too.
    """
    out: dict[str, str] = {}
    if isinstance(data.get("description"), str) and data.get("description"):
        out["description"] = data["description"]

    _collect_free_text(data.get("phases"), "phases", out)
    _collect_free_text(data.get("topics"), "topics", out)

    if topic_markdowns:
        for md_name, md_text in sorted(topic_markdowns.items()):
            if md_text:
                out[f"topics/{md_name}"] = md_text

    return out


def build_free_text_diff(
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any],
    old_markdowns: dict[str, str] | None = None,
    new_markdowns: dict[str, str] | None = None,
) -> str:
    """Build a text diff of changed or added free-text fields only."""
    old_fields = extract_free_text_fields(old_data or {}, old_markdowns)
    new_fields = extract_free_text_fields(new_data, new_markdowns)

    diff_lines: list[str] = []
    for key, new_val in new_fields.items():
        old_val = old_fields.get(key)
        if old_val != new_val:
            diff_lines.append(f"--- {key}")
            if old_val:
                diff_lines.append(f"- {old_val}")
            diff_lines.append(f"+ {new_val}")
    return "\n".join(diff_lines)


def run_llm_privacy_reviewer(
    diff_text: str,
    mode: str = "auto",
    reviewer_command: str = "claude -p --model haiku",
    timeout_seconds: float = 60.0,
    env: dict[str, str] | None = None,
) -> ReviewerResult:
    """Run the external LLM privacy reviewer in an isolated empty temporary directory (§7.2)."""
    if mode == "off" or not diff_text.strip():
        return ReviewerResult(verdict="skipped")

    cmd_parts = shlex.split(reviewer_command)
    if not cmd_parts:
        if mode == "required":
            return ReviewerResult(
                verdict="unavailable",
                errors=["Privacy reviewer command is empty, but privacy_review is 'required'."],
            )
        return ReviewerResult(
            verdict="unavailable",
            warnings=["Privacy reviewer command is empty; skipping LLM review."],
        )

    exe = cmd_parts[0]
    run_env = dict(os.environ if env is None else env)
    resolved_exe = (
        exe
        if os.path.isabs(exe) and os.access(exe, os.X_OK)
        else shutil.which(exe, path=run_env.get("PATH"))
    )
    if not resolved_exe:
        msg = f"Privacy reviewer command '{exe}' not found"
        if mode == "required":
            return ReviewerResult(
                verdict="unavailable", errors=[f"{msg} (privacy_review=required)"]
            )
        return ReviewerResult(verdict="unavailable", warnings=[f"{msg}; skipping LLM review."])

    prompt = REVIEWER_PROMPT_TEMPLATE + diff_text + "\n"

    with tempfile.TemporaryDirectory(prefix="workbench-privacy-review-") as tmp_dir:
        try:
            proc = subprocess.run(
                cmd_parts,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=tmp_dir,
                env=run_env,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            msg = f"Privacy reviewer timed out after {timeout_seconds}s"
            if mode == "required":
                return ReviewerResult(verdict="unavailable", errors=[msg])
            return ReviewerResult(verdict="unavailable", warnings=[msg])
        except OSError as exc:
            msg = f"Privacy reviewer failed to start: {exc}"
            if mode == "required":
                return ReviewerResult(verdict="unavailable", errors=[msg])
            return ReviewerResult(verdict="unavailable", warnings=[msg])

    if proc.returncode != 0:
        msg = f"Privacy reviewer exited with code {proc.returncode}"
        if mode == "required":
            return ReviewerResult(verdict="unavailable", errors=[msg])
        return ReviewerResult(verdict="unavailable", warnings=[msg])

    raw_out = proc.stdout.strip()
    if raw_out.startswith("```"):
        lines = [line for line in raw_out.splitlines() if not line.strip().startswith("```")]
        raw_out = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw_out)
    except json.JSONDecodeError:
        msg = "Privacy reviewer returned invalid JSON"
        if mode == "required":
            return ReviewerResult(verdict="unavailable", errors=[msg])
        return ReviewerResult(verdict="unavailable", warnings=[msg])

    verdict = parsed.get("verdict")
    issues = parsed.get("issues", [])
    if verdict == "fail":
        err_msgs = []
        for iss in issues:
            f_name = iss.get("field", "unknown")
            reason = iss.get("reason", "policy violation")
            err_msgs.append(f"LLM privacy review failed on '{f_name}': {reason}")
        if not err_msgs:
            err_msgs.append("LLM privacy review returned verdict 'fail'")
        return ReviewerResult(verdict="fail", issues=issues, errors=err_msgs)
    if verdict == "pass":
        return ReviewerResult(verdict="pass", issues=[])

    msg = f"Privacy reviewer returned unexpected verdict: {verdict!r}"
    if mode == "required":
        return ReviewerResult(verdict="unavailable", errors=[msg])
    return ReviewerResult(verdict="unavailable", warnings=[msg])
