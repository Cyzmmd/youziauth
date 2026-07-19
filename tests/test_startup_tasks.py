import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from startup_tasks import (
    SYSTEM_TASK_NAME,
    TRAY_TASK_NAME,
    build_system_task_xml,
    build_tray_task_xml,
    configure_system_startup,
    is_system_startup_enabled,
)


NS = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class StartupTaskXmlTests(unittest.TestCase):
    def test_system_task_is_zero_delay_system_priority_four(self):
        install_dir = Path(r"C:\Program Files\youziauth")
        user_sid = "S-1-5-21-123-456-789-1001"
        root = ET.fromstring(build_system_task_xml(install_dir, user_sid))

        self.assertIsNotNone(root.find(".//task:BootTrigger", NS))
        self.assertIsNone(root.find(".//task:BootTrigger/task:Delay", NS))
        self.assertEqual(root.findtext(".//task:Principal/task:UserId", namespaces=NS), "S-1-5-18")
        self.assertIsNone(root.find(".//task:Principal/task:LogonType", NS))
        self.assertEqual(root.findtext(".//task:Settings/task:Priority", namespaces=NS), "4")
        self.assertEqual(
            root.findtext(".//task:Exec/task:Command", namespaces=NS),
            str(install_dir / "youziauth-agent.exe"),
        )
        self.assertEqual(root.findtext(".//task:Settings/task:ExecutionTimeLimit", namespaces=NS), "PT0S")
        self.assertIsNone(root.find(".//task:Settings/task:RunOnlyIfNetworkAvailable", NS))
        self.assertEqual(
            root.findtext(".//task:Exec/task:Arguments", namespaces=NS),
            f"--allowed-user-sid {user_sid}",
        )

    def test_tray_task_uses_interactive_token_and_hidden_mode(self):
        install_dir = Path(r"C:\Program Files\youziauth")
        user_sid = "S-1-5-21-123-456-789-1001"
        root = ET.fromstring(build_tray_task_xml(install_dir, user_sid))

        self.assertIsNotNone(root.find(".//task:LogonTrigger", NS))
        self.assertIsNone(root.find(".//task:LogonTrigger/task:Delay", NS))
        self.assertEqual(root.findtext(".//task:Principal/task:UserId", namespaces=NS), user_sid)
        self.assertEqual(
            root.findtext(".//task:Principal/task:LogonType", namespaces=NS),
            "InteractiveToken",
        )
        self.assertEqual(root.findtext(".//task:Exec/task:Arguments", namespaces=NS), "--tray-startup")


class ConfigureStartupTests(unittest.TestCase):
    def test_enable_creates_both_tasks_before_removing_legacy_shortcut(self):
        with tempfile.TemporaryDirectory() as temporary:
            shortcut = Path(temporary) / "youziauth.lnk"
            shortcut.write_text("legacy", encoding="utf-8")
            calls = []

            def runner(arguments, **kwargs):
                calls.append(arguments)
                return subprocess.CompletedProcess(arguments, 0, "", "")

            configure_system_startup(
                True,
                Path(r"C:\Program Files\youziauth"),
                "S-1-5-21-1-2-3-1001",
                runner=runner,
                legacy_shortcut=shortcut,
            )

            self.assertEqual([call[1] for call in calls], ["/Create", "/Create"])
            self.assertIn(SYSTEM_TASK_NAME, calls[0])
            self.assertIn(TRAY_TASK_NAME, calls[1])
            self.assertFalse(shortcut.exists())

    def test_partial_enable_failure_rolls_back_and_keeps_legacy_shortcut(self):
        with tempfile.TemporaryDirectory() as temporary:
            shortcut = Path(temporary) / "youziauth.lnk"
            shortcut.write_text("legacy", encoding="utf-8")
            calls = []

            def runner(arguments, **kwargs):
                calls.append(arguments)
                if arguments[1] == "/Create" and TRAY_TASK_NAME in arguments:
                    raise subprocess.CalledProcessError(1, arguments)
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with self.assertRaises(subprocess.CalledProcessError):
                configure_system_startup(
                    True,
                    Path(r"C:\Program Files\youziauth"),
                    "S-1-5-21-1-2-3-1001",
                    runner=runner,
                    legacy_shortcut=shortcut,
                )

            self.assertTrue(shortcut.exists())
            self.assertTrue(any(call[1] == "/Delete" and SYSTEM_TASK_NAME in call for call in calls))


class EnabledStateTests(unittest.TestCase):
    def test_visible_tray_task_is_the_non_admin_enabled_marker(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        self.assertTrue(is_system_startup_enabled(runner=runner))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3], TRAY_TASK_NAME)

    def test_missing_tray_task_means_disabled(self):
        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 1, "", "not found")

        self.assertFalse(is_system_startup_enabled(runner=runner))


if __name__ == "__main__":
    unittest.main()
