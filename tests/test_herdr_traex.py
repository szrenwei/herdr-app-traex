from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "herdr_traex.py"

SPEC = importlib.util.spec_from_file_location("herdr_traex", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
herdr_traex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(herdr_traex)


class ProtocolTests(unittest.TestCase):
    def test_working_sequence(self) -> None:
        self.assertEqual(
            herdr_traex.build_osc("working"),
            b"\x1b]7501;state=working:app=traex\x1b\\",
        )

    def test_blocked_sequence(self) -> None:
        self.assertEqual(
            herdr_traex.build_osc("blocked", "permission"),
            b"\x1b]7501;state=blocked:kind=permission:app=traex\x1b\\",
        )

    def test_clear_omits_app(self) -> None:
        self.assertEqual(
            herdr_traex.build_osc("clear"),
            b"\x1b]7501;state=clear\x1b\\",
        )

    def test_lifecycle_mapping(self) -> None:
        expected = {
            "SessionStart": ("idle", None),
            "UserPromptSubmit": ("working", None),
            "PreToolUse": ("working", None),
            "PermissionRequest": ("blocked", "permission"),
            "Stop": ("done", None),
            "Interrupt": ("idle", None),
            "SessionEnd": ("clear", None),
        }
        for event, report in expected.items():
            with self.subTest(event=event):
                self.assertEqual(herdr_traex.report_for_event(event, {}), report)

    def test_notification_mapping(self) -> None:
        self.assertEqual(
            herdr_traex.report_for_event(
                "Notification", {"notification_type": "auth_prompt"}
            ),
            ("blocked", "auth"),
        )
        self.assertIsNone(
            herdr_traex.report_for_event(
                "Notification", {"notification_type": "idle_prompt"}
            )
        )

    def test_parse_macos_tty(self) -> None:
        self.assertEqual(
            herdr_traex.parse_ps_record("123 ttys012"),
            (123, "/dev/ttys012"),
        )

    def test_parse_linux_pty(self) -> None:
        self.assertEqual(
            herdr_traex.parse_ps_record("456 pts/7"),
            (456, "/dev/pts/7"),
        )

    def test_parse_ps_record_rejects_unsafe_tty(self) -> None:
        for record in ("123 ??", "123 ../../tmp/x", "pid ttys001", "123"):
            with self.subTest(record=record):
                self.assertIsNone(herdr_traex.parse_ps_record(record))

    def test_tty_lookup_is_lazy(self) -> None:
        with mock.patch.object(herdr_traex, "find_ancestor_tty") as find_tty:
            candidates = herdr_traex.tty_candidates({})
            self.assertEqual(next(candidates), "/dev/tty")
            find_tty.assert_not_called()

    def test_tty_lookup_falls_back_to_ancestor(self) -> None:
        with mock.patch.object(
            herdr_traex, "find_ancestor_tty", return_value="/dev/ttys012"
        ):
            self.assertEqual(
                list(herdr_traex.tty_candidates({})),
                ["/dev/tty", "/dev/ttys012"],
            )


class ScriptTests(unittest.TestCase):
    def run_hook(
        self, event: str, payload: dict[str, object], enable: bool = True
    ) -> tuple[subprocess.CompletedProcess[bytes], bytes]:
        with tempfile.NamedTemporaryFile() as tty:
            env = os.environ.copy()
            env.pop("HERDR_TRAEX_DISABLE", None)
            env["HERDR_TRAEX_TTY"] = tty.name
            if enable:
                env.pop("HERDR_TRAEX_DISABLE", None)
            else:
                env["HERDR_TRAEX_DISABLE"] = "1"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), event],
                input=json.dumps(payload).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            tty.seek(0)
            return result, tty.read()

    def test_hook_writes_exact_sequence_and_valid_json(self) -> None:
        result, output = self.run_hook("UserPromptSubmit", {})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(
            output, b"\x1b]7501;state=working:app=traex\x1b\\"
        )

    def test_explicit_disable_is_noop(self) -> None:
        result, output = self.run_hook("Stop", {}, enable=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(output, b"")

    def test_missing_tty_fails_open(self) -> None:
        env = os.environ.copy()
        env.pop("HERDR_TRAEX_DISABLE", None)
        env["HERDR_TRAEX_TTY"] = "/path/that/does/not/exist"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "Stop"],
            input=b"{}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(result.stderr, b"")

    def test_invalid_payload_fails_open(self) -> None:
        result, output = self.run_hook("Stop", {})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            output, b"\x1b]7501;state=done:app=traex\x1b\\"
        )


if __name__ == "__main__":
    unittest.main()
