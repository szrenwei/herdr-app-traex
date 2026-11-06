#!/usr/bin/env python3
"""Best-effort TRAE CLI lifecycle reporting for Herdr App.

The hook writes an OSC 7501 record to the controlling terminal. Reporting is
deliberately silent and fail-open: a missing TTY or malformed hook payload must
never interrupt a TRAE CLI session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Iterator, Mapping, Optional


OSC_PREFIX = b"\x1b]7501;"
OSC_SUFFIX = b"\x1b\\"
APP_NAME = "traex"

EVENT_REPORTS = {
    "SessionStart": ("idle", None),
    "UserPromptSubmit": ("working", None),
    "PreToolUse": ("working", None),
    "PreCompact": ("working", None),
    "PostCompact": ("working", None),
    "PermissionRequest": ("blocked", "permission"),
    "Stop": ("done", None),
    "Interrupt": ("idle", None),
    "SessionEnd": ("clear", None),
}

NOTIFICATION_REPORTS = {
    "permission_prompt": ("blocked", "permission"),
    "auth_prompt": ("blocked", "auth"),
}

SAFE_TTY_NAME = re.compile(r"^(?:tty[A-Za-z0-9._-]+|pts/[0-9]+)$")


def build_osc(state: str, kind: Optional[str] = None) -> bytes:
    """Build one Herdr Core OSC 7501 root-record update."""
    fields = [f"state={state}"]
    if state != "clear":
        if kind:
            fields.append(f"kind={kind}")
        fields.append(f"app={APP_NAME}")
    return OSC_PREFIX + ":".join(fields).encode("ascii") + OSC_SUFFIX


def report_for_event(
    event_name: str, payload: Mapping[str, object]
) -> Optional[tuple[str, Optional[str]]]:
    """Translate a TRAE CLI hook event into a Herdr state and optional kind."""
    if event_name == "Notification":
        notification_type = payload.get("notification_type")
        if not isinstance(notification_type, str):
            notification_type = payload.get("matcher")
        if isinstance(notification_type, str):
            return NOTIFICATION_REPORTS.get(notification_type)
        return None
    return EVENT_REPORTS.get(event_name)


def load_payload(stream: object) -> Mapping[str, object]:
    """Read a hook payload, returning an empty object for absent/invalid JSON."""
    try:
        raw = stream.read()  # type: ignore[attr-defined]
        if not raw or not str(raw).strip():
            return {}
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def reporting_enabled(environ: Mapping[str, str]) -> bool:
    """Allow users to disable reporting without affecting the hook result."""
    return environ.get("HERDR_TRAEX_DISABLE") != "1"


def parse_ps_record(record: str) -> Optional[tuple[int, str]]:
    """Parse the `ppid tty` columns emitted by ps, rejecting unsafe paths."""
    parts = record.split()
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    tty_name = parts[1]
    if not SAFE_TTY_NAME.fullmatch(tty_name):
        return None
    return int(parts[0]), f"/dev/{tty_name}"


def find_ancestor_tty(start_pid: int, max_depth: int = 12) -> Optional[str]:
    """Find the first safe TTY attached to a hook ancestor process."""
    pid = start_pid
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid <= 1 or pid in seen:
            return None
        seen.add(pid)
        try:
            result = subprocess.run(
                ["/bin/ps", "-o", "ppid=", "-o", "tty=", "-p", str(pid)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=0.25,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None

        fields = result.stdout.split()
        if not fields or not fields[0].isdigit():
            return None
        parent_pid = int(fields[0])
        if len(fields) >= 2:
            parsed = parse_ps_record(f"{parent_pid} {fields[1]}")
            if parsed is not None:
                return parsed[1]
        pid = parent_pid
    return None


def tty_candidates(environ: Mapping[str, str]) -> Iterator[str]:
    """Return explicit or safely discovered terminal device candidates."""
    override = environ.get("HERDR_TRAEX_TTY")
    if override:
        yield override
        return
    yield "/dev/tty"
    # This lookup is intentionally lazy: avoid spawning ps when /dev/tty works.
    ancestor_tty = find_ancestor_tty(os.getppid())
    if ancestor_tty and ancestor_tty != "/dev/tty":
        yield ancestor_tty


def write_report(
    report: tuple[str, Optional[str]], environ: Mapping[str, str]
) -> None:
    """Write one complete record to the hook process's controlling terminal."""
    flags = os.O_WRONLY
    if hasattr(os, "O_NOCTTY"):
        flags |= os.O_NOCTTY
    last_error: Optional[OSError] = None
    for tty_path in tty_candidates(environ):
        try:
            fd = os.open(tty_path, flags)
            try:
                state, kind = report
                os.write(fd, build_osc(state, kind))
                return
            finally:
                os.close(fd)
        except OSError as error:
            last_error = error
    if last_error is not None:
        raise last_error


def main(argv: list[str], environ: Mapping[str, str]) -> int:
    payload = load_payload(sys.stdin)
    event_name = argv[1] if len(argv) > 1 else payload.get("hook_event_name", "")

    try:
        if reporting_enabled(environ) and isinstance(event_name, str):
            report = report_for_event(event_name, payload)
            if report is not None:
                write_report(report, environ)
    except (OSError, TypeError, ValueError):
        # Hooks are observability only. Never block or alter the TRAE workflow.
        pass

    # An empty JSON object is valid for every lifecycle event used by the plugin.
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv, os.environ))
