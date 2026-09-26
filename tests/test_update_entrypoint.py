# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""The frozen GUI doubles as the update verifier, so its CLI must dispatch first."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import campus_auth_desktop
import windows_update


class VerifierEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.request = self.root / "request.json"
        self.request.write_text(json.dumps({"msi": "x"}), encoding="utf-8")

    def test_verify_update_dispatches_before_any_gui_setup(self):
        calls = []

        def fake(request_path, report_path):
            calls.append((Path(request_path), Path(report_path)))
            return 0

        # A GUI path would need webview, the single-instance lock, and a window;
        # dispatching first is what keeps the verifier usable from PowerShell.
        with patch.object(windows_update, "verifier_main", side_effect=fake):
            code = campus_auth_desktop.main(["--verify-update", str(self.request)])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(self.request, self.root / "report.json")])

    def test_verifier_exit_code_becomes_the_process_exit_code(self):
        for stage in (0, 11, 12, 15, 16, 21, 22):
            with self.subTest(stage=stage):
                with patch.object(windows_update, "verifier_main", return_value=stage):
                    self.assertEqual(
                        campus_auth_desktop.main(["--verify-update", str(self.request)]), stage)

    def test_report_lands_next_to_the_request(self):
        # The worker computes this exact path, so the two must agree.
        with patch.object(windows_update, "verifier_main", return_value=0) as verifier:
            campus_auth_desktop.main(["--verify-update", str(self.request)])
        _, report = verifier.call_args.args
        self.assertEqual(Path(report).name, "report.json")
        self.assertEqual(Path(report).parent, self.request.parent)

    def test_wrong_argument_shapes_fail_fast_without_the_gui(self):
        # A malformed invocation must not fall through to window creation.
        for argv in (["--verify-update"], ["--verify-update", "a", "b"],
                     ["--verify-update", "a", "--other"]):
            with self.subTest(argv=argv):
                with patch.object(windows_update, "verifier_main") as verifier:
                    self.assertEqual(campus_auth_desktop.main(argv), 21)
                    verifier.assert_not_called()

    def test_flag_order_is_irrelevant(self):
        with patch.object(windows_update, "verifier_main", return_value=0) as verifier:
            # argv[0] is not the flag, so this is treated as malformed, but it must
            # still not reach the GUI.
            self.assertEqual(campus_auth_desktop.main(["x", "--verify-update"]), 21)
            verifier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
