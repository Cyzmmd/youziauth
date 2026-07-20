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


if __name__ == "__main__":
    unittest.main()
