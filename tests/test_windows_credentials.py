import base64
import configparser
import os
import tempfile
import unittest
from pathlib import Path

from windows_credentials import (
    CredentialMigrationError,
    CredentialStore,
    DpapiProtector,
    machine_config_path,
    migrate_plaintext_password,
)


class ReversibleProtector:
    def protect(self, value: bytes) -> bytes:
        return b"protected:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"protected:"):
            raise ValueError("invalid protected value")
        return value[len(b"protected:") :][::-1]


class FailedVerificationProtector(ReversibleProtector):
    def unprotect(self, value: bytes) -> bytes:
        return b"different-password"


class CredentialStoreTests(unittest.TestCase):
    def test_machine_config_path_uses_program_data(self):
        path = machine_config_path(Path(r"C:\ProgramData-Test"))

        self.assertEqual(path, Path(r"C:\ProgramData-Test") / "youziauth" / "config.ini")

    def test_store_round_trip_never_writes_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CredentialStore(Path(temporary), protector=ReversibleProtector())

            store.save_password("secret-value")

            self.assertEqual(store.load_password(), "secret-value")
            self.assertNotIn(b"secret-value", store.credential_path.read_bytes())
            base64.b64decode(store.credential_path.read_bytes(), validate=True)

    def test_clear_password_removes_blob(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CredentialStore(Path(temporary), protector=ReversibleProtector())
            store.save_password("secret-value")

            store.clear_password()

            self.assertFalse(store.credential_path.exists())

    def test_migration_clears_plaintext_only_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.ini"
            config_path.write_text("[auth]\nusername = student\npassword = old-secret\n", encoding="utf-8")
            store = CredentialStore(root / "machine", protector=ReversibleProtector())

            changed = migrate_plaintext_password(config_path, store)

            self.assertTrue(changed)
            self.assertEqual(store.load_password(), "old-secret")
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(config_path, encoding="utf-8")
            self.assertEqual(parser.get("auth", "password"), "")

    def test_failed_migration_verification_preserves_old_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.ini"
            config_path.write_text("[auth]\npassword = old-secret\n", encoding="utf-8")
            store = CredentialStore(root / "machine", protector=FailedVerificationProtector())

            with self.assertRaises(CredentialMigrationError):
                migrate_plaintext_password(config_path, store)

            self.assertIn("password = old-secret", config_path.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_real_dpapi_machine_scope_round_trip(self):
        protector = DpapiProtector(machine_scope=True)

        encrypted = protector.protect("校园网密码".encode("utf-8"))

        self.assertNotIn("校园网密码".encode("utf-8"), encrypted)
        self.assertEqual(protector.unprotect(encrypted).decode("utf-8"), "校园网密码")


if __name__ == "__main__":
    unittest.main()
