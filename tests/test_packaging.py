from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import make_icons  # noqa: E402


class PackagingWorkflowTests(unittest.TestCase):
    def test_pyinstaller_bundle_includes_third_party_license_materials(self):
        spec = ROOT.joinpath("packaging", "youziauth.spec").read_text(
            encoding="utf-8"
        )

        self.assertTrue(ROOT.joinpath("THIRD_PARTY_NOTICES.md").exists())
        self.assertTrue(
            ROOT.joinpath("third_party_licenses", "CPYTHON-3.14-LICENSE.txt").exists()
        )
        self.assertTrue(
            ROOT.joinpath(
                "third_party_licenses", "PYINSTALLER-6.16-COPYING.txt"
            ).exists()
        )
        self.assertIn("THIRD_PARTY_NOTICES.md", spec)
        self.assertIn("third_party_licenses", spec)

    def test_pyinstaller_spec_builds_gui_and_system_agent_without_user_secrets(self):
        spec = ROOT.joinpath("packaging", "youziauth.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn("campus_auth_gui.py", spec)
        self.assertIn("campus_auth_agent.py", spec)
        self.assertIn('name="youziauth"', spec)
        self.assertIn('name="youziauth-agent"', spec)
        self.assertIn("config.example.ini", spec)
        self.assertIn("yuzu_app.ico", spec)
        self.assertNotIn("config.ini", spec)
        self.assertNotIn("campus_auth_password.txt", spec)
        self.assertNotIn("campus_auth.log", spec)

    def test_icon_generation_uses_generated_yuzu_source(self):
        script = ROOT.joinpath("packaging", "make_icons.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("yuzu_app_source.png", script)
        self.assertIn("make_yuzu_icon_images", script)
        self.assertTrue(ROOT.joinpath("assets", "yuzu_app_source.png").exists())

    def test_generated_yuzu_icon_has_transparent_corners(self):
        from PIL import Image

        icon = Image.open(ROOT / "assets" / "yuzu_app.png").convert("RGBA")

        self.assertEqual(icon.size, (256, 256))
        self.assertEqual(icon.getpixel((0, 0))[3], 0)
        self.assertGreater(icon.getpixel((128, 128))[3], 200)

    def test_checkerboard_source_is_matted_to_alpha(self):
        from PIL import Image

        image = Image.new("RGB", (4, 4), (255, 255, 255))
        image.putpixel((0, 0), (250, 250, 250))
        image.putpixel((1, 0), (236, 236, 236))
        image.putpixel((2, 2), (245, 210, 40))

        matted = make_icons.remove_light_checkerboard_background(image)

        self.assertEqual(matted.getpixel((0, 0))[3], 0)
        self.assertEqual(matted.getpixel((1, 0))[3], 0)
        self.assertEqual(matted.getpixel((2, 2))[3], 255)

    def test_msi_build_script_runs_pyinstaller_then_wix(self):
        script = ROOT.joinpath("build_msi.ps1").read_text(encoding="utf-8")

        self.assertIn("pyinstaller", script)
        self.assertIn("wix.exe", script)
        self.assertIn("youziauth.msi", script)
        self.assertIn("youziauth.spec", script)
        self.assertIn("youziauth.wxs", script)
        self.assertIn("youziauth.exe", script)
        self.assertIn("youziauth-agent.exe", script)
        self.assertIn("InstallDependencies", script)
        self.assertIn("--acceptEula wix7", script)
        self.assertIn("$LASTEXITCODE", script)

    def test_wix_source_has_start_menu_shortcut_and_uninstall_metadata(self):
        source = ROOT.joinpath("packaging", "youziauth.wxs").read_text(
            encoding="utf-8"
        )

        self.assertIn("youziauth", source)
        self.assertIn('Manufacturer="yoouzic"', source)
        self.assertIn("ProgramMenuFolder", source)
        self.assertIn("Shortcut", source)
        self.assertIn("youziauth.exe", source)
        self.assertIn('Version="$(var.ProductVersion)"', source)
        self.assertIn("System.AppUserModel.ID", source)
        self.assertIn('Value="youziauth"', source)
        self.assertNotIn("Campus Network Auth", source)
        self.assertNotIn("CampusNetworkAuth", source)

    def test_wix_source_installs_desktop_shortcut(self):
        source = ROOT.joinpath("packaging", "youziauth.wxs").read_text(
            encoding="utf-8"
        )

        self.assertIn("DesktopFolder", source)
        self.assertIn("ApplicationDesktopShortcut", source)

    def test_wix_source_registers_notification_protocol(self):
        source = ROOT.joinpath("packaging", "youziauth.wxs").read_text(
            encoding="utf-8"
        )

        self.assertIn(r"Software\Classes\youziauth", source)
        self.assertIn("URL Protocol", source)
        self.assertIn("--notification-action", source)
        self.assertIn("ProtocolRegistrationComponent", source)


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_file_is_strict_semver(self):
        version = ROOT.joinpath("VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_wix_version_comes_from_build_variable(self):
        source = ROOT.joinpath("packaging", "youziauth.wxs").read_text(
            encoding="utf-8"
        )
        self.assertIn('Version="$(var.ProductVersion)"', source)
        self.assertNotIn('Version="1.1.3"', source)

    def test_pyinstaller_uses_generated_version_resources_without_upx(self):
        source = ROOT.joinpath("packaging", "youziauth.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn('version=str(VERSION_DIR / "youziauth.version")', source)
        self.assertIn(
            'version=str(VERSION_DIR / "youziauth-agent.version")', source
        )
        self.assertNotIn("upx=True", source)

    def test_version_generator_writes_distinct_descriptions(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "packaging" / "generate_version_info.py"),
                    "--output",
                    str(output),
                ],
                check=True,
            )
            gui = (output / "youziauth.version").read_text(encoding="utf-8")
            agent = (output / "youziauth-agent.version").read_text(
                encoding="utf-8"
            )
            self.assertIn("youziauth campus network tray and settings", gui)
            self.assertIn(
                "youziauth SYSTEM campus network authentication agent", agent
            )
            self.assertIn("1, 1, 4, 0", gui)
            self.assertIn("1.1.4", agent)


if __name__ == "__main__":
    unittest.main()
