import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowPolicyTests(unittest.TestCase):
    def test_actions_are_pinned_to_full_commit_shas(self):
        for path in ROOT.joinpath(".github", "workflows").glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            for reference in re.findall(r"uses:\s*([^\s]+)", text):
                self.assertRegex(
                    reference,
                    r"^[^@]+@[0-9a-f]{40}$",
                    msg=f"{path}: {reference}",
                )

    def test_ci_uses_github_hosted_windows_and_runs_full_suite(self):
        text = ROOT.joinpath(".github", "workflows", "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("python -m unittest discover -s tests -v", text)
        self.assertIn("build_msi.ps1", text)
        self.assertIn("permissions:\n  contents: read", text)

    def test_signpath_configuration_deep_signs_both_exes_and_msi(self):
        text = ROOT.joinpath(
            ".signpath", "artifact-configuration.xml"
        ).read_text(encoding="utf-8")
        self.assertIn('<msi-file path="youziauth.msi"', text)
        self.assertIn('<pe-file path="youziauth.exe"', text)
        self.assertIn('<pe-file path="youziauth-agent.exe"', text)
        self.assertEqual(text.count("<authenticode-sign"), 3)

    def test_release_requires_signpath_and_never_publishes_unsigned_msi(self):
        text = ROOT.joinpath(".github", "workflows", "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "signpath/github-action-submit-signing-request@"
            "b9d91eadd323de506c0c81cf0c7fe7438f3360fd",
            text,
        )
        self.assertIn("packaging/verify_release.ps1", text)
        self.assertIn("gh release create", text)
        self.assertNotIn("dist/youziauth.msi ${{", text)


if __name__ == "__main__":
    unittest.main()
