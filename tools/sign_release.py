#!/usr/bin/env python3
# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""Sign a built MSI for the youziauth updater.

Produces the two detached authenticators the client requires:

* ``SHA256SUMS.txt``        - ``<sha256>  youziauth.msi`` for manual verification
* ``youziauth.msi.ed25519`` - hex Ed25519 signature over the canonical payload

The canonical payload is built by :func:`windows_update.canonical_payload`, which
is the same function the installer runs, so the signed bytes can never drift from
the verified bytes.

The private key stays offline. Pass it through ``YOUZIAUTH_RELEASE_KEY`` (base64
of the 32-byte seed) or ``--key-file``; never commit it.

Usage
-----
    python tools/sign_release.py --msi dist/youziauth.msi --version 1.6.7 \
        --output-dir release --key-file /secure/path/ed25519-release.key
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ed25519  # noqa: E402
import windows_update  # noqa: E402


KEY_ENVIRONMENT = "YOUZIAUTH_RELEASE_KEY"


class SigningError(RuntimeError):
    """The release could not be signed safely."""


def load_secret_key(key_file: Path | None) -> bytes:
    """Read the 32-byte private seed from the environment or a file."""
    if key_file is not None:
        try:
            raw = key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SigningError(f"cannot read key file: {exc.strerror or exc}") from None
    else:
        raw = (os.environ.get(KEY_ENVIRONMENT) or "").strip()
        if not raw:
            raise SigningError(
                f"no release key: set {KEY_ENVIRONMENT} or pass --key-file"
            )
    try:
        secret = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise SigningError("release key is not valid base64") from None
    if len(secret) != ed25519.SECRET_KEY_BYTES:
        raise SigningError(f"release key must decode to {ed25519.SECRET_KEY_BYTES} bytes")
    return secret


def read_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SigningError(f"cannot read the installer: {exc.strerror or exc}") from None
    return digest.hexdigest()


def check_version(version: str) -> str:
    if re.fullmatch(r"(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,4})", version) is None:
        raise SigningError("version must be strict MAJOR.MINOR.PATCH")
    if any(part > limit for part, limit in zip(map(int, version.split(".")), (255, 255, 65535))):
        raise SigningError("version exceeds the range Windows Installer supports")
    return version


def sign_release(msi: Path, version: str, output_directory: Path, secret_key: bytes) -> dict:
    version = check_version(version)
    try:
        msi = msi.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SigningError("the installer path does not exist") from None
    if msi.suffix.lower() != ".msi":
        raise SigningError("the installer must be an .msi file")
    size = msi.stat().st_size
    if not 0 < size <= 0xFFFFFFFF:
        raise SigningError("the installer size is not usable for an MSI package")

    digest = read_sha256(msi)
    payload = windows_update.canonical_payload(version, digest, size)
    signature = ed25519.sign(payload, secret_key)
    # Prove the signature before writing anything, so a bad key cannot publish.
    if not ed25519.verify(signature, payload, ed25519.derive_public_key(secret_key)):
        raise SigningError("the produced signature failed self-verification")
    expected_key = windows_update.public_key()
    if ed25519.derive_public_key(secret_key) != expected_key:
        raise SigningError(
            "this key does not match the public key compiled into the client; "
            "the release would be rejected by every installed copy"
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    checksum_file = output_directory / "SHA256SUMS.txt"
    signature_file = output_directory / "youziauth.msi.ed25519"
    checksum_file.write_text(f"{digest}  youziauth.msi\n", encoding="ascii")
    signature_file.write_text(signature.hex() + "\n", encoding="ascii")
    provenance_file = output_directory / "release-provenance.json"
    provenance_file.write_text(json.dumps({
        "version": version,
        "git_commit": os.environ.get("GITHUB_SHA", ""),
        "git_tag": os.environ.get("GITHUB_REF_NAME", ""),
        "msi_sha256": digest,
        "msi_bytes": size,
        "signature_algorithm": "Ed25519",
        "release_public_key": base64.b64encode(expected_key).decode("ascii"),
    }, indent=2) + "\n", encoding="utf-8")
    return {
        "version": version, "sha256": digest, "bytes": size,
        "signature": signature.hex(), "checksum_file": str(checksum_file),
        "signature_file": str(signature_file), "provenance_file": str(provenance_file),
    }


def verify_signature(msi: Path, version: str, signature_text: str) -> str:
    """Check a published signature against the pinned key; return the digest.

    Needs no private key, so release verification can confirm that a signature
    really covers these installer bytes before the release is published.
    """
    version = check_version(version)
    try:
        msi = msi.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SigningError("the installer path does not exist") from None
    text = (signature_text or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{128}", text) is None:
        raise SigningError("the signature file must hold 128 hex characters")
    size = msi.stat().st_size
    if not 0 < size <= 0xFFFFFFFF:
        raise SigningError("the installer size is not usable for an MSI package")
    digest = read_sha256(msi)
    payload = windows_update.canonical_payload(version, digest, size)
    if not ed25519.verify(bytes.fromhex(text), payload, windows_update.public_key()):
        raise SigningError("the signature does not cover this installer")
    return digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sign a youziauth MSI for the in-app updater")
    parser.add_argument("--msi", required=True, type=Path, help="path to the built youziauth.msi")
    parser.add_argument("--version", required=True, help="release version, e.g. 1.6.7")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="where to write the authenticators (signing mode)")
    parser.add_argument("--key-file", type=Path, default=None,
                        help=f"file holding the base64 private key (default: ${KEY_ENVIRONMENT})")
    parser.add_argument("--verify-signature-file", type=Path, default=None,
                        help="check an existing .ed25519 file against the pinned key, then exit; "
                             "needs no private key")
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify_signature_file is not None:
            try:
                text = arguments.verify_signature_file.read_text(encoding="ascii")
            except OSError as exc:
                raise SigningError(f"cannot read the signature file: {exc.strerror or exc}") from None
            digest = verify_signature(arguments.msi, arguments.version, text)
            print(f"signature verified for youziauth.msi {arguments.version} ({digest})")
            return 0
        if arguments.output_dir is None:
            raise SigningError("--output-dir is required when signing")
        secret = load_secret_key(arguments.key_file)
        result = sign_release(arguments.msi, arguments.version, arguments.output_dir, secret)
    except SigningError as exc:
        print(f"signing failed: {exc}", file=sys.stderr)
        return 1
    print(f"signed youziauth.msi {result['version']} ({result['bytes']} bytes)")
    print(f"  sha256    {result['sha256']}")
    print(f"  signature {result['signature']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
