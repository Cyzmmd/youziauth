# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""Release signing must produce authenticators the client will actually accept."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import ed25519
import windows_update
from tools import sign_release


VERSION = "1.6.7"


class SignReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.msi = self.root / "youziauth.msi"
        self.msi.write_bytes(b"MSI fixture for signing tests" * 32)
        self.secret = ed25519.generate_secret_key()
        self.public = ed25519.derive_public_key(self.secret)
        # Signing must target the key the client trusts.
        pinned = patch.object(windows_update, "PUBLIC_KEY_B64",
                              base64.b64encode(self.public).decode("ascii"))
        pinned.start()
        self.addCleanup(pinned.stop)
        self.output = self.root / "release"

    def run_main(self, argv):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = sign_release.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def sign(self, **overrides):
        arguments = ["--msi", str(self.msi), "--version", VERSION,
                     "--output-dir", str(self.output)]
        if not overrides.pop("no_key", False):
            arguments += ["--key-file", str(self.write_key())]
        for key, value in overrides.items():
            arguments += [f"--{key.replace('_', '-')}", str(value)]
        return self.run_main(arguments)

    def write_key(self) -> Path:
        path = self.root / "release.key"
        path.write_text(base64.b64encode(self.secret).decode("ascii"), encoding="utf-8")
        return path

    def test_produces_all_three_authenticators(self):
        code, out, _ = self.sign()
        self.assertEqual(code, 0)
        self.assertIn("signed youziauth.msi", out)
        for name in ("SHA256SUMS.txt", "youziauth.msi.ed25519", "release-provenance.json"):
            self.assertTrue((self.output / name).is_file(), msg=name)

    def test_checksum_file_matches_the_client_and_coreutils_format(self):
        self.assertEqual(self.sign()[0], 0)
        digest = hashlib.sha256(self.msi.read_bytes()).hexdigest()
        self.assertEqual((self.output / "SHA256SUMS.txt").read_text(encoding="ascii"),
                         f"{digest}  youziauth.msi\n")
        # app_update.read_checksum parses exactly this shape.
        self.assertEqual((self.output / "SHA256SUMS.txt").read_text(encoding="ascii").split()[0], digest)

    def test_signature_verifies_against_the_pinned_key_and_payload(self):
        self.assertEqual(self.sign()[0], 0)
        text = (self.output / "youziauth.msi.ed25519").read_text(encoding="ascii").strip()
        digest = hashlib.sha256(self.msi.read_bytes()).hexdigest()
        payload = windows_update.canonical_payload(VERSION, digest, self.msi.stat().st_size)
        self.assertTrue(ed25519.verify(bytes.fromhex(text), payload, self.public))
        # And the client's own parser/verifier accept it end to end.
        self.assertEqual(windows_update._signature(text).hex(), text)

    def test_provenance_records_the_public_key_and_digest(self):
        self.assertEqual(self.sign()[0], 0)
        body = json.loads((self.output / "release-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(body["version"], VERSION)
        self.assertEqual(body["msi_sha256"], hashlib.sha256(self.msi.read_bytes()).hexdigest())
        self.assertEqual(body["msi_bytes"], self.msi.stat().st_size)
        self.assertEqual(body["signature_algorithm"], "Ed25519")
        self.assertEqual(body["release_public_key"], base64.b64encode(self.public).decode("ascii"))

    def test_a_key_that_does_not_match_the_pin_is_refused(self):
        # Shipping with the wrong key would strand every installed copy.
        other = ed25519.generate_secret_key()
        path = self.root / "other.key"
        path.write_text(base64.b64encode(other).decode("ascii"), encoding="utf-8")
        code, _, err = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                      "--output-dir", str(self.output), "--key-file", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("does not match the public key", err)
        self.assertFalse(self.output.exists())

    def test_missing_key_is_refused(self):
        code, _, err = self.sign(no_key=True)
        self.assertEqual(code, 1)
        self.assertIn("no release key", err)
        code, _, err = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                      "--output-dir", str(self.output)])
        self.assertEqual(code, 1)

    def test_environment_variable_supplies_the_key(self):
        with patch.dict("os.environ",
                        {sign_release.KEY_ENVIRONMENT: base64.b64encode(self.secret).decode("ascii")}):
            code, _, _ = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                        "--output-dir", str(self.output)])
        self.assertEqual(code, 0)

    def test_malformed_keys_are_refused(self):
        for contents in ("", "not base64!!", base64.b64encode(b"short").decode("ascii")):
            with self.subTest(contents=contents[:16]):
                path = self.root / "bad.key"
                path.write_text(contents, encoding="utf-8")
                code, _, err = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                              "--output-dir", str(self.output), "--key-file", str(path)])
                self.assertEqual(code, 1)
                self.assertIn("release key", err)

    def test_invalid_versions_and_paths_are_refused(self):
        for version in ("1.2", "v1.6.7", "1.6.7.1", "256.0.0", "01.6.7"):
            with self.subTest(version=version):
                self.assertEqual(self.sign(version=version)[0], 1)
        self.assertEqual(self.sign(msi=str(self.root / "missing.msi"))[0], 1)
        non_msi = self.root / "package.zip"
        non_msi.write_bytes(b"x")
        self.assertEqual(self.sign(msi=str(non_msi))[0], 1)

    def test_verify_mode_accepts_a_correct_signature_without_a_key(self):
        self.assertEqual(self.sign()[0], 0)
        signature_file = self.output / "youziauth.msi.ed25519"
        with patch.dict("os.environ", {}, clear=True):
            code, out, _ = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                          "--verify-signature-file", str(signature_file)])
        self.assertEqual(code, 0)
        self.assertIn("signature verified", out)

    def test_verify_mode_rejects_wrong_versions_and_tampered_files(self):
        self.assertEqual(self.sign()[0], 0)
        signature_file = self.output / "youziauth.msi.ed25519"
        self.assertEqual(self.run_main(["--msi", str(self.msi), "--version", "1.6.8",
                                        "--verify-signature-file", str(signature_file)])[0], 1)
        original = self.msi.read_bytes()
        self.msi.write_bytes(original + b"tampered")
        self.assertEqual(self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                        "--verify-signature-file", str(signature_file)])[0], 1)
        self.msi.write_bytes(original)

    def test_verify_mode_rejects_malformed_signature_files(self):
        for contents in ("", "zz" * 64, "ab" * 63, "ab" * 65):
            with self.subTest(contents=contents[:16]):
                path = self.root / "bad.ed25519"
                path.write_text(contents, encoding="ascii")
                self.assertEqual(self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                                "--verify-signature-file", str(path)])[0], 1)

    def test_unsigned_mode_requires_an_output_directory(self):
        code, _, err = self.run_main(["--msi", str(self.msi), "--version", VERSION,
                                      "--key-file", str(self.write_key())])
        self.assertEqual(code, 1)
        self.assertIn("output-dir", err)


if __name__ == "__main__":
    unittest.main()
