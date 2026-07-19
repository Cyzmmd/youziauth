import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from agent_ipc import (
    AgentCommand,
    InvalidAgentCommand,
    NamedPipeServer,
    RuntimeSnapshot,
    UiCommand,
    read_snapshot,
    send_command,
    write_snapshot,
)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_round_trip_is_non_sensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            expected = RuntimeSnapshot(
                boot_id="boot-1",
                state="online_campus",
                notifications_suppressed=False,
                incident_id="",
                detail="已认证",
            )

            write_snapshot(path, expected)

            self.assertEqual(read_snapshot(path), expected)
            serialized = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("password", serialized)
            self.assertNotIn("querystring", serialized)

    def test_invalid_snapshot_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.json"
            path.write_text('{"boot_id":"x","state":"made_up"}', encoding="utf-8")

            with self.assertRaises(ValueError):
                read_snapshot(path)


class CommandTests(unittest.TestCase):
    def test_command_vocabulary_accepts_retry(self):
        self.assertEqual(AgentCommand.parse({"command": "retry"}), AgentCommand("retry"))

    def test_command_vocabulary_rejects_unknown_actions(self):
        with self.assertRaises(InvalidAgentCommand):
            AgentCommand.parse({"command": "run-powershell"})

    def test_command_rejects_unexpected_fields(self):
        with self.assertRaises(InvalidAgentCommand):
            AgentCommand.parse({"command": "retry", "arguments": "whoami"})

    def test_ui_command_has_separate_fixed_vocabulary(self):
        self.assertEqual(UiCommand.parse({"command": "show"}), UiCommand("show"))
        with self.assertRaises(InvalidAgentCommand):
            UiCommand.parse({"command": "run-powershell"})

    @unittest.skipUnless(os.name == "nt", "named pipes are Windows-only")
    def test_real_named_pipe_round_trip(self):
        name = f"youziauth-test-{uuid.uuid4().hex}"
        server = NamedPipeServer(name, lambda command: {"ok": True, "command": command.command})
        thread = threading.Thread(target=server.serve_once, daemon=True)
        thread.start()

        response = send_command(name, AgentCommand("status"), timeout_ms=3000)

        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(response, {"ok": True, "command": "status"})


if __name__ == "__main__":
    unittest.main()
