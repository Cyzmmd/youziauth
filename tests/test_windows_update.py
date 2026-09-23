# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import Mock, patch

import windows_update as update


VERSION = "1.2.3"
UPGRADE_CODE = "{D029E636-7E7E-42EE-8B38-C2D455AD2AA1}"
SUBJECT = "CN=Test Publisher, O=Test Organisation, C=CN"


def valid_result():
    # Certificate names are fixtures, never production trust anchors.
    return {
        "exe": {"status": "Valid", "subject": SUBJECT},
        "msi": {"status": "Valid", "subject": SUBJECT, "timestamp": True},
        "properties": {
            "ProductName": "youziauth",
            "Manufacturer": "yoouzic",
            "ProductVersion": VERSION,
            "UpgradeCode": UPGRADE_CODE,
        },
    }


class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.msi = self.root / "更新 ' ; $([x]) & package.msi"
        self.msi.write_bytes(b"not an installer\x00test fixture")
        self.exe = self.root / "youziauth.exe"
        self.exe.write_bytes(b"not executable; signature API is mocked")
        self.sha256 = hashlib.sha256(self.msi.read_bytes()).hexdigest()
        self.events = []
        self.result = valid_result()
        self.api = Mock()
        self.states = [("launch", {"pid": 456}), ("final", {"code": 3010})]
        self.directory = None

        def system_directory(buffer, size):
            buffer.value = str(self.root / "System32")
            return len(buffer.value)

        self.api.GetSystemDirectoryW.side_effect = system_directory
        self.api.OpenProcess.return_value = 123
        self.api.WaitForSingleObject.side_effect = self.advance_worker
        self.api.CloseHandle.side_effect = lambda handle: self.events.append("close") or 1
        make_directory = tempfile.mkdtemp

        def private_directory(**options):
            directory = make_directory(**options)
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            return directory

        self.patches = [
            patch.object(update.tempfile, "mkdtemp", side_effect=private_directory),
            patch.object(update.sys, "platform", "win32"),
            patch.object(update.sys, "frozen", True, create=True),
            patch.object(update.sys, "executable", str(self.exe)),
            patch.object(update.ctypes, "WinDLL", return_value=self.api, create=True),
            patch.object(update.subprocess, "run", side_effect=self.run_powershell),
            patch.object(update.subprocess, "Popen", side_effect=AssertionError("direct child launch")),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.run = update.subprocess.run
        self.popen = update.subprocess.Popen
        self.callback = Mock(side_effect=lambda: self.events.append("callback"))

    def run_powershell(self, command, **kwargs):
        if command[-1] == update._ENCODED_COMMAND:
            self.events.append("verify")
            return subprocess.CompletedProcess(command, 0, json.dumps(self.result), "")
        self.assertEqual(command[-1], update._ENCODED_LAUNCHER)
        self.events.append("launcher")
        self.request = json.loads(base64.b64decode(kwargs["env"]["YOUZIAUTH_UPDATE_REQUEST"]))
        self.directory = Path(self.request["directory"])
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        return subprocess.CompletedProcess(command, 0, '{"pid":321}', "")

    def advance_worker(self, handle, milliseconds):
        self.assertEqual(handle, 123)
        self.assertTrue(0 < milliseconds <= 1000)
        if not self.states:
            return 0
        name, status = self.states.pop(0)
        (self.directory / (name + ".json")).write_text(json.dumps(status), encoding="utf-8")
        self.events.append(name)
        return 258 if self.states else 0

    def verify(self, **overrides):
        arguments = dict(path=self.msi, executable=self.exe, version=VERSION, sha256=self.sha256)
        arguments.update(overrides)
        return update.verify_msi(**arguments)

    def install(self, **overrides):
        arguments = dict(path=self.msi, executable=self.exe, version=VERSION,
                         sha256=self.sha256, on_launch=self.callback)
        arguments.update(overrides)
        return update.install_msi(**arguments)

    def test_public_verifier_accepts_valid_package_without_launching(self):
        self.assertIsNone(self.verify())
        self.popen.assert_not_called()

    def test_sha256_mismatch_rejects_before_system_process(self):
        with self.assertRaisesRegex(update.UpdateVerificationError, "哈希"):
            self.verify(sha256="0" * 64)
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_uppercase_sha256_is_accepted(self):
        self.assertIsNone(self.verify(sha256=self.sha256.upper()))

    def test_each_untrusted_signature_is_rejected(self):
        for target in ("exe", "msi"):
            for status in ("NotSigned", "HashMismatch", "NotTrusted", "UnknownError", "valid", None):
                with self.subTest(target=target, status=status):
                    self.result = valid_result()
                    self.result[target]["status"] = status
                    with self.assertRaises(update.UpdateVerificationError):
                        self.verify()
        self.popen.assert_not_called()

    def test_exe_rejection_explains_official_signed_install_requirement(self):
        self.result["exe"]["status"] = "NotSigned"
        with self.assertRaisesRegex(update.UpdateVerificationError, "官方签名安装版"):
            self.verify()

    def test_full_subject_must_match_exactly(self):
        for subject in ("CN=Test Publisher", SUBJECT.lower(), SUBJECT + " ", "CN=Other", "", None):
            with self.subTest(subject=subject):
                self.result = valid_result()
                self.result["msi"]["subject"] = subject
                with self.assertRaises(update.UpdateVerificationError):
                    self.verify()

    def test_no_publisher_name_is_pinned_in_code(self):
        self.result["exe"]["subject"] = "CN=Another locally trusted publisher, O=Renewable"
        self.result["msi"]["subject"] = self.result["exe"]["subject"]
        self.assertIsNone(self.verify())

    def test_empty_local_signer_cannot_anchor_trust(self):
        for subject in ("", " ", None, 123):
            with self.subTest(subject=subject):
                self.result["exe"]["subject"] = subject
                self.result["msi"]["subject"] = subject
                with self.assertRaisesRegex(update.UpdateVerificationError, "官方签名安装版"):
                    self.verify()

    def test_timestamp_must_be_present_not_truthy(self):
        for timestamp in (False, None, "true", 1):
            with self.subTest(timestamp=timestamp):
                self.result["msi"]["timestamp"] = timestamp
                with self.assertRaisesRegex(update.UpdateVerificationError, "时间戳"):
                    self.verify()

    def test_each_product_property_is_required_and_exact(self):
        wrong_values = {
            "ProductName": "another-app",
            "Manufacturer": "another-company",
            "ProductVersion": "1.2.4",
            "UpgradeCode": "{00000000-0000-0000-0000-000000000000}",
        }
        for name, wrong in wrong_values.items():
            for value in (wrong, None, ""):
                with self.subTest(property=name, value=value):
                    self.result = valid_result()
                    self.result["properties"][name] = value
                    with self.assertRaises(update.UpdateVerificationError):
                        self.verify()
            self.result = valid_result()
            del self.result["properties"][name]
            with self.assertRaises(update.UpdateVerificationError):
                self.verify()

    def test_signature_failure_takes_precedence_over_product_failure(self):
        result = valid_result()
        result["exe"]["status"] = "NotSigned"
        result["properties"] = None
        with self.assertRaisesRegex(update.UpdateVerificationError, "官方签名安装版"):
            update._verify_result(result, VERSION)

    def test_malformed_system_json_fails_closed_without_raw_details(self):
        for output in ("not json; SECRET", "[]", "null", '{}', '{"exe": []}',
                       json.dumps({**valid_result(), "properties": []})):
            with self.subTest(output=output):
                self.run.side_effect = None
                self.run.return_value = subprocess.CompletedProcess([], 0, output, "SECRET OS")
                with self.assertRaises(update.UpdateVerificationError) as caught:
                    self.verify()
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertRegex(str(caught.exception), "[\u4e00-\u9fff]")

    def test_fixed_system_failures_are_sanitized(self):
        for code, message in ((10, "官方签名安装版"), (11, "签名"), (12, "时间戳"),
                              (13, "发布者"), (14, "产品"), (99, "验证")):
            with self.subTest(code=code):
                self.run.side_effect = None
                self.run.return_value = subprocess.CompletedProcess([], code, "SECRET script", "SECRET OS")
                with self.assertRaisesRegex(update.UpdateVerificationError, message) as caught:
                    self.verify()
                self.assertNotIn("SECRET", str(caught.exception))

    def test_invalid_inputs_do_not_reach_system_boundary(self):
        invalid = [
            {"version": item} for item in (None, 123, "1.2", "v1.2.3", "1.2.3.4", "01.2.3",
                                          "1.2.3-beta", "1.2.3\n", "１.2.3", "1.2.3'; exit 0", "256.0.0")
        ] + [{"sha256": item} for item in (None, 123, "", "f" * 63, "g" * 64, "f" * 64 + "\n")]
        invalid += [{"path": item} for item in (None, str(self.msi), self.root,
                                                self.root / "missing.msi", Path("bad\x00.msi"))]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(update.UpdateVerificationError):
                    self.verify(**overrides)
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_source_build_cannot_supply_an_alternate_signed_exe(self):
        with patch.object(update.sys, "frozen", False):
            with self.assertRaisesRegex(update.UpdateVerificationError, "官方签名安装版"):
                self.verify()
        self.run.assert_not_called()

    def test_executable_must_be_the_running_packaged_executable(self):
        other = self.root / "other.exe"
        other.write_bytes(b"not the running application")
        for executable in (other, self.root, str(self.exe)):
            with self.subTest(executable=executable):
                with self.assertRaises(update.UpdateVerificationError):
                    self.verify(executable=executable)
        with patch.object(update.sys, "executable", str(other)):
            with self.assertRaisesRegex(update.UpdateVerificationError, "官方签名安装版"):
                self.verify(executable=other)
        self.run.assert_not_called()

    def test_none_executable_defaults_to_current_packaged_executable(self):
        self.assertIsNone(self.verify(executable=None))
        self.assertEqual(self.run.call_args.kwargs["env"]["YOUZIAUTH_UPDATE_EXE"], str(self.exe))

    def test_non_windows_is_explicitly_rejected(self):
        with patch.object(update.sys, "platform", "linux"):
            with self.assertRaisesRegex(update.UpdateVerificationError, "Windows"):
                self.verify()
        self.run.assert_not_called()

    def test_paths_are_absolute_and_data_never_becomes_powershell_source(self):
        self.verify()
        first_command = self.run.call_args.args[0]
        options = self.run.call_args.kwargs
        self.assertEqual(Path(first_command[0]), self.root / "System32/WindowsPowerShell/v1.0/powershell.exe")
        self.assertEqual(first_command[1:4], ["-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertEqual(len(first_command), 5)
        source = base64.b64decode(first_command[4]).decode("utf-16le")
        self.assertNotIn(str(self.msi), source)
        self.assertIs(options["shell"], False)
        self.assertEqual(options["encoding"].lower().replace("-", ""), "utf8")
        self.assertTrue(0 < options["timeout"] <= 60)
        self.assertEqual(options["creationflags"], 0x08000000)
        self.assertEqual(options["env"]["YOUZIAUTH_UPDATE_MSI"], str(self.msi))
        # TEMP and the checkout may be on different Windows drives.
        with chdir(self.root), patch.dict(os.environ, {"SystemRoot": "C:/attacker", "PATH": "C:/attacker"}):
            self.verify(path=Path(self.msi.name))
        self.assertEqual(self.run.call_args.args[0], first_command)
        self.assertEqual(self.run.call_args.kwargs["env"]["YOUZIAUTH_UPDATE_MSI"], str(self.msi))

    def test_powershell_timeout_and_launch_errors_are_sanitized(self):
        for error in (subprocess.TimeoutExpired("SECRET SCRIPT", 60), OSError("SECRET OS"),
                      UnicodeError("SECRET ENCODING")):
            with self.subTest(error=type(error).__name__):
                self.run.side_effect = error
                with self.assertRaises(update.UpdateVerificationError) as caught:
                    self.verify()
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
        self.popen.assert_not_called()

    def test_system_directory_failure_is_not_replaced_with_path_lookup(self):
        self.api.GetSystemDirectoryW.side_effect = None
        self.api.GetSystemDirectoryW.return_value = 0
        with self.assertRaises(update.UpdateVerificationError):
            self.verify()
        self.run.assert_not_called()

    def test_install_does_not_create_msiexec_in_desktop_process_tree(self):
        self.install()
        self.popen.assert_not_called()
        self.api.CreateFileW.assert_not_called()

    def test_install_waits_for_final_and_returns_actual_exit_code(self):
        for code in (0, 1602, 1603, 3010):
            with self.subTest(code=code):
                self.states = [("launch", {"pid": 456}), ("final", {"code": code})]
                self.events.clear()
                self.assertEqual(self.install(), code)
                self.assertEqual(self.events, ["launcher", "launch", "callback", "final", "close"])
                self.assertFalse(self.directory.exists())
        self.api.OpenProcess.assert_called_with(0x00100000, False, 321)

    def test_install_handoff_uses_private_local_state_and_fixed_commands(self):
        with patch.dict(os.environ, {"SystemRoot": "C:/attacker", "PATH": "C:/attacker",
                                     "YOUZIAUTH_UPDATE_REQUEST": "untrusted"}):
            self.install()
        command = self.run.call_args.args[0]
        options = self.run.call_args.kwargs
        self.assertEqual(Path(command[0]), self.root / "System32/WindowsPowerShell/v1.0/powershell.exe")
        self.assertEqual(command[1:4], ["-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertIs(options["shell"], False)
        self.assertLessEqual(options["timeout"], 20)
        self.assertEqual(options["env"]["YOUZIAUTH_UPDATE_MSI"], str(self.msi))
        self.assertEqual(options["env"]["YOUZIAUTH_UPDATE_EXE"], str(self.exe))
        self.assertEqual(self.request["sha256"], self.sha256)
        self.assertEqual(self.request["properties"], valid_result()["properties"])
        self.assertTrue(self.directory.is_absolute())
        self.assertNotEqual(self.directory, self.root)
        self.assertNotIn(str(self.msi), base64.b64decode(command[-1]).decode("utf-16le"))
        self.assertEqual(options["env"]["YOUZIAUTH_UPDATE_VERIFY"], update._ENCODED_COMMAND)
        self.assertEqual(options["env"]["YOUZIAUTH_UPDATE_WORKER"], update._ENCODED_WORKER)

    def test_worker_early_errors_never_notify_launch(self):
        for error in (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 999):
            with self.subTest(error=error):
                self.states = [("final", {"error": error})]
                with self.assertRaises(update.UpdateVerificationError):
                    self.install()
                self.callback.assert_not_called()

    def test_launcher_does_not_decode_localized_stderr(self):
        self.install()
        self.assertEqual(self.run.call_args.kwargs.get("stderr"), subprocess.DEVNULL)
        self.assertEqual(self.run.call_args.kwargs.get("stdout"), subprocess.PIPE)

    def test_launcher_failures_are_sanitized(self):
        for error in (OSError("SECRET"), subprocess.TimeoutExpired("SECRET", 20), UnicodeError("SECRET")):
            self.run.side_effect = error
            with self.assertRaises(update.UpdateVerificationError) as caught:
                self.install()
            self.assertNotIn("SECRET", str(caught.exception))
        self.callback.assert_not_called()

    def test_invalid_callback_prevents_any_process_or_lock(self):
        with self.assertRaises(update.UpdateVerificationError):
            self.install(on_launch=None)
        self.run.assert_not_called()
        self.popen.assert_not_called()
        self.api.OpenProcess.assert_not_called()

    def test_callback_failure_still_waits_for_final(self):
        def broken_callback():
            self.events.append("callback")
            raise RuntimeError("SECRET callback")

        with self.assertRaisesRegex(update.UpdateVerificationError, "通知") as caught:
            self.install(on_launch=broken_callback)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(self.events, ["launcher", "launch", "callback", "final", "close"])

    def test_system_exit_callback_still_waits_for_final(self):
        self.callback.side_effect = SystemExit(7)
        with self.assertRaises(SystemExit) as caught:
            self.install()
        self.assertEqual(caught.exception.code, 7)
        self.assertIn("final", self.events)

    def test_dead_worker_without_final_is_not_waited_forever(self):
        for states in ([], [("launch", {"pid": 456})]):
            with self.subTest(states=states):
                self.states = list(states)
                with self.assertRaisesRegex(update.UpdateVerificationError, "意外退出"):
                    self.install()

    def test_process_watch_failure_does_not_kill_or_clean_live_worker(self):
        self.api.WaitForSingleObject.side_effect = OSError("SECRET wait")
        with self.assertRaises(update.UpdateVerificationError) as caught:
            self.install()
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertTrue(self.directory.exists())
        self.api.TerminateProcess.assert_not_called()
        self.popen.assert_not_called()

    def test_unobservable_worker_is_not_waited_forever(self):
        self.api.OpenProcess.return_value = 0
        with self.assertRaisesRegex(update.UpdateVerificationError, "无法观察"):
            self.install()
        self.api.WaitForSingleObject.assert_not_called()
        self.callback.assert_not_called()
        self.assertTrue(self.directory.exists())

    def test_live_worker_startup_is_bounded(self):
        with patch.object(update.time, "monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(update.UpdateVerificationError, "超时"):
                self.install()
        self.callback.assert_not_called()
        self.assertTrue(self.directory.exists())

    def test_final_code_without_launch_is_not_success(self):
        self.states = [("final", {"code": 0})]
        with self.assertRaises(update.UpdateVerificationError):
            self.install()
        self.callback.assert_not_called()

    def test_fast_worker_final_still_delivers_callback_once(self):
        original = self.run_powershell

        def finished_worker(command, **options):
            result = original(command, **options)
            self.advance_worker(123, 100)
            self.advance_worker(123, 100)
            return result

        self.run.side_effect = finished_worker
        self.api.OpenProcess.return_value = 0
        self.assertEqual(self.install(), 3010)
        self.callback.assert_called_once_with()

    def test_malformed_status_never_reports_completion(self):
        cases = [("launch", {"pid": True}), ("launch", {"pid": -1}),
                 ("final", {"code": True}), ("final", {"code": 0, "error": 10}),
                 ("final", {"error": "SECRET"}), ("final", {}),
                 ("final", {"padding": "SECRET" * 1000})]
        for name, status in cases:
            with self.subTest(name=name, status=str(status)[:60]):
                self.states = [(name, status)]
                with self.assertRaises(update.UpdateVerificationError) as caught:
                    self.install()
                self.assertNotIn("SECRET", str(caught.exception))
        self.callback.assert_not_called()


def encoded(script):
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


_SAFE_VERIFIER = r"""
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::Out.Write([Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($env:YOUZIAUTH_TEST_RESULT)))
exit 0
"""
_SAFE_INSTALLER = r"""
$request = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($env:YOUZIAUTH_UPDATE_REQUEST)) | ConvertFrom-Json
$release = [IO.Path]::Combine($request.directory, 'release')
$timer = [Diagnostics.Stopwatch]::StartNew()
while (-not [IO.File]::Exists($release) -and $timer.Elapsed.TotalSeconds -lt 15) {
    Start-Sleep -Milliseconds 50
}
exit ([int]$env:YOUZIAUTH_TEST_CODE)
"""
_SAFE_START = r"""
$processes = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name)
$boundary = @{ file = $start.FileName; arguments = $start.Arguments; shell = $start.UseShellExecute;
               worker = $PID; launcher = [int]$env:YOUZIAUTH_UPDATE_LAUNCHER; processes = $processes }
[IO.File]::WriteAllText([IO.Path]::Combine($directory, 'boundary.json'),
                       ($boundary | ConvertTo-Json -Depth 4 -Compress), (New-Object Text.UTF8Encoding($false)))
$start.FileName = $powershell
$start.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $env:YOUZIAUTH_TEST_INSTALLER
$start.CreateNoWindow = $true
$installer = [Diagnostics.Process]::Start($start)
"""


@unittest.skipUnless(os.name == "nt", "safe native boundary checks require Windows")
class NativeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.msi = self.root / "无签名 '$([x]); & file.msi"
        self.msi.write_bytes(b"This is deliberately not a Windows Installer database.")
        self.sha256 = hashlib.sha256(self.msi.read_bytes()).hexdigest()
        self.anchor = update._system_tool("WindowsPowerShell/v1.0/powershell.exe")
        self.callback = Mock()
        self.sequence = 0

    def start(self, digest=None):
        self.sequence += 1
        self.directory = self.root / str(self.sequence)
        self.directory.mkdir(mode=0o700)
        pid = update._start_worker(self.msi, self.anchor, VERSION, digest or self.sha256, self.directory)
        api = update._kernel32()
        handle = api.OpenProcess(0x00100000, False, pid)
        if handle:
            directory = self.directory

            def finish():
                if directory.exists():
                    (directory / "release").touch(exist_ok=True)
                try:
                    self.assertEqual(api.WaitForSingleObject(handle, 25000), 0)
                finally:
                    api.CloseHandle(handle)
            self.addCleanup(finish)
        return pid

    def safe_boundaries(self, result=None, code=3010):
        original = "$installer = [Diagnostics.Process]::Start($start)"
        self.assertEqual(update._WORKER.count(original), 1)
        replacements = [
            patch.object(update, "_ENCODED_WORKER", encoded(update._WORKER.replace(original, _SAFE_START))),
            patch.object(update, "_ENCODED_COMMAND", encoded(_SAFE_VERIFIER)),
            patch.dict(os.environ, {
                "YOUZIAUTH_TEST_RESULT": base64.b64encode(json.dumps(
                    valid_result() if result is None else result).encode("utf-8")).decode("ascii"),
                "YOUZIAUTH_TEST_INSTALLER": encoded(_SAFE_INSTALLER), "YOUZIAUTH_TEST_CODE": str(code),
            }),
        ]
        for replacement in replacements:
            replacement.start()
            self.addCleanup(replacement.stop)

    def observe_error(self, message):
        self.callback.reset_mock()
        pid = self.start()
        with self.assertRaisesRegex(update.UpdateVerificationError, message):
            update._observe_worker(pid, self.directory, self.callback)
        self.callback.assert_not_called()
        self.msi.write_bytes(self.msi.read_bytes())

    def test_real_powershell_rejects_fake_msi_before_database_open(self):
        with self.assertRaisesRegex(update.UpdateVerificationError, "MSI.*签名"):
            update._powershell_result(self.msi, self.anchor)

    def test_complete_unmodified_worker_rejects_unsigned_msi(self):
        self.observe_error("MSI.*签名")

    def test_detached_worker_owns_lock_until_harmless_installer_exits(self):
        self.safe_boundaries()
        pid = self.start()
        evidence = {}

        def launched():
            self.assertFalse((self.directory / "final.json").exists())
            evidence.update(json.loads((self.directory / "boundary.json").read_text(encoding="utf-8")))
            self.assertEqual(Path(evidence["file"]), update._system_tool("msiexec.exe"))
            self.assertEqual(evidence["arguments"], '/i "' + str(self.msi) + '" /norestart')
            self.assertIs(evidence["shell"], False)
            active = {item["ProcessId"]: item for item in evidence["processes"]}
            self.assertNotIn(evidence["launcher"], active)
            self.assertEqual(active[pid]["ParentProcessId"], evidence["launcher"])
            desktop_tree = {os.getpid()}
            desktop_tree.update(p for p, item in active.items() if item["Name"].lower() == "youziauth.exe")
            while True:
                children = {p for p, item in active.items() if item["ParentProcessId"] in desktop_tree}
                if children <= desktop_tree:
                    break
                desktop_tree.update(children)
            self.assertNotIn(pid, desktop_tree)
            for operation in (lambda: self.msi.write_bytes(b"replace"), self.msi.unlink,
                              lambda: self.msi.rename(self.root / "renamed.msi")):
                with self.assertRaises(PermissionError):
                    operation()
            self.assertTrue(self.msi.read_bytes())
            self.assertEqual(update._kernel32().WaitForSingleObject(self.worker_handle, 0), 258)
            (self.directory / "release").touch()

        api = update._kernel32()
        self.worker_handle = api.OpenProcess(0x00100000, False, pid)
        self.addCleanup(api.CloseHandle, self.worker_handle)
        self.assertEqual(update._observe_worker(pid, self.directory, launched), 3010)
        self.assertEqual(evidence["worker"], pid)
        self.msi.write_bytes(b"released")
        self.msi.unlink()

    def test_worker_waits_for_late_launcher_exit_before_starting_installer(self):
        self.safe_boundaries()
        delayed = update._LAUNCHER.replace("$worker.Dispose()", "Start-Sleep -Seconds 3\n    $worker.Dispose()")
        with patch.object(update, "_ENCODED_LAUNCHER", encoded(delayed)):
            pid = self.start()

        def launched():
            boundary = json.loads((self.directory / "boundary.json").read_text(encoding="utf-8"))
            self.assertNotIn(boundary["launcher"], {p["ProcessId"] for p in boundary["processes"]})
            (self.directory / "release").touch()

        self.assertEqual(update._observe_worker(pid, self.directory, launched), 3010)

    def test_expired_startup_cannot_launch_late(self):
        self.safe_boundaries()
        with patch.object(update.time, "time", return_value=0):
            self.observe_error("超时")

    def test_safe_installer_cancel_and_success_codes_are_preserved(self):
        for code in (0, 1602):
            with self.subTest(code=code):
                self.safe_boundaries(code=code)
                pid = self.start()
                self.assertEqual(update._observe_worker(
                    pid, self.directory, lambda: (self.directory / "release").touch()), code)

    def test_changed_file_is_rehashed_before_verification_or_start(self):
        self.safe_boundaries()
        self.msi.write_bytes(b"changed after prior preview verification")
        self.observe_error("哈希")

    def test_existing_writer_prevents_worker_read_lock_and_launch(self):
        self.safe_boundaries()
        with self.msi.open("r+b"):
            self.observe_error("锁定")

    def test_worker_checks_all_four_product_properties(self):
        for name in ("ProductName", "Manufacturer", "ProductVersion", "UpgradeCode"):
            with self.subTest(name=name):
                result = valid_result()
                result["properties"][name] = "mismatch"
                self.safe_boundaries(result)
                self.observe_error("产品")

    def test_worker_rejects_invalid_signature_result_and_timestamp(self):
        cases = [("exe", "status", "NotSigned", "官方签名"),
                 ("exe", "status", ["Valid"], "官方签名"),
                 ("exe", "subject", " ", "官方签名"),
                 ("msi", "status", "NotTrusted", "签名"),
                 ("msi", "status", ["Valid"], "签名"),
                 ("msi", "subject", SUBJECT.lower(), "发布者"),
                 ("msi", "timestamp", "true", "时间戳"),
                 ("msi", "timestamp", 1, "时间戳")]
        for target, key, value, message in cases:
            with self.subTest(target=target, key=key, value=value):
                result = valid_result()
                result[target][key] = value
                self.safe_boundaries(result)
                self.observe_error(message)

    def test_worker_crash_without_status_is_observable(self):
        with patch.object(update, "_ENCODED_WORKER", encoded("exit 42")):
            started = time.monotonic()
            pid = self.start()
            with self.assertRaisesRegex(update.UpdateVerificationError, "意外退出"):
                update._observe_worker(pid, self.directory, self.callback)
        self.assertLess(time.monotonic() - started, 20)
        self.callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
