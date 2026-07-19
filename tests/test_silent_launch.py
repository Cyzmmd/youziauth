from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SilentLaunchScriptTests(unittest.TestCase):
    def test_silent_web_ui_script_uses_pythonw_and_hidden_window(self):
        script = ROOT.joinpath("start_web_ui_silent.vbs").read_text(encoding="utf-8")

        self.assertIn("pythonw.exe", script)
        self.assertIn("campus_auth_web.py", script)
        self.assertIn(".Run command, 0, False", script)
        self.assertNotIn("cmd.exe", script)

    def test_silent_background_auth_script_uses_hidden_powershell_window(self):
        script = ROOT.joinpath("start_auth_silent.vbs").read_text(encoding="utf-8")

        self.assertIn("run_with_saved_password.ps1", script)
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn(".Run command, 0, False", script)
        self.assertNotIn("cmd.exe", script)

    def test_installed_task_uses_hidden_powershell_window(self):
        script = ROOT.joinpath("install_task.ps1").read_text(encoding="utf-8")

        self.assertIn("-NoProfile", script)
        self.assertIn("-WindowStyle Hidden", script)


if __name__ == "__main__":
    unittest.main()
