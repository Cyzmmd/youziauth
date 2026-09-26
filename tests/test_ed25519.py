# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""Ed25519 must match RFC 8032 exactly and fail closed on hostile input."""

from __future__ import annotations

import unittest

import ed25519


# RFC 8032 section 7.1: (secret key, public key, message, signature).
VECTORS = (
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
)

# RFC 8032 section 7.1 TEST SHA(abc), a 64-byte message that exercises
# multi-block hashing rather than only the single-block path.
VECTORS += (
    (
        "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
        "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
        "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704",
    ),
)


class Rfc8032Tests(unittest.TestCase):
    def test_public_key_derivation_matches_rfc8032(self):
        for secret, expected, _, _ in VECTORS:
            with self.subTest(secret=secret[:16]):
                self.assertEqual(ed25519.derive_public_key(bytes.fromhex(secret)).hex(), expected)

    def test_signature_matches_rfc8032(self):
        for secret, public, message, expected in VECTORS:
            with self.subTest(message=message[:16]):
                produced = ed25519.sign(bytes.fromhex(message), bytes.fromhex(secret))
                self.assertEqual(produced.hex(), expected)
                self.assertEqual(ed25519.derive_public_key(bytes.fromhex(secret)).hex(), public)

    def test_signature_verifies(self):
        for secret, public, message, signature in VECTORS:
            with self.subTest(message=message[:16]):
                self.assertTrue(
                    ed25519.verify(bytes.fromhex(signature), bytes.fromhex(message), bytes.fromhex(public))
                )


class RejectionTests(unittest.TestCase):
    def setUp(self):
        self.secret = bytes.fromhex(VECTORS[1][0])
        self.public = ed25519.derive_public_key(self.secret)
        self.message = b"youziauth|1|1.6.7|" + b"ab" * 32
        self.signature = ed25519.sign(self.message, self.secret)

    def test_roundtrip_works(self):
        self.assertTrue(ed25519.verify(self.signature, self.message, self.public))

    def test_tampered_message_is_rejected(self):
        for message in (self.message + b"x", self.message[:-1], b"", self.message.replace(b"1.6.7", b"9.9.9")):
            with self.subTest(message=message[:24]):
                self.assertFalse(ed25519.verify(self.signature, message, self.public))

    def test_tampered_signature_is_rejected(self):
        for index in (0, 31, 32, 63):
            with self.subTest(index=index):
                broken = bytearray(self.signature)
                broken[index] ^= 0x01
                self.assertFalse(ed25519.verify(bytes(broken), self.message, self.public))

    def test_other_public_key_is_rejected(self):
        other = ed25519.derive_public_key(ed25519.generate_secret_key())
        self.assertNotEqual(other, self.public)
        self.assertFalse(ed25519.verify(self.signature, self.message, other))

    def test_malleable_signature_variant_is_rejected(self):
        # S + L is the classic second valid encoding of the same signature.
        scalar = int.from_bytes(self.signature[32:], "little")
        forged = self.signature[:32] + (scalar + ed25519._ORDER).to_bytes(32, "little")
        self.assertNotEqual(forged, self.signature)
        self.assertFalse(ed25519.verify(forged, self.message, self.public))

    def test_high_scalar_is_rejected_even_if_it_would_verify(self):
        scalar = int.from_bytes(self.signature[32:], "little") + ed25519._ORDER
        self.assertGreaterEqual(scalar, ed25519._ORDER)
        self.assertFalse(ed25519.verify(self.signature[:32] + scalar.to_bytes(32, "little"),
                                        self.message, self.public))

    def test_wrong_shapes_are_rejected_not_raised(self):
        cases = {
            "short signature": (self.signature[:63], self.message, self.public),
            "long signature": (self.signature + b"\x00", self.message, self.public),
            "short key": (self.signature, self.message, self.public[:31]),
            "long key": (self.signature, self.message, self.public + b"\x00"),
            "signature not bytes": ("x" * 64, self.message, self.public),
            "key not bytes": (self.signature, self.message, "k" * 32),
            "message not bytes": (self.signature, "m", self.public),
            "signature none": (None, self.message, self.public),
            "message none": (self.signature, None, self.public),
            "key none": (self.signature, self.message, None),
        }
        for name, (signature, message, public) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(ed25519.verify(signature, message, public))

    def test_non_canonical_public_key_is_rejected(self):
        # y >= p must not be accepted as a valid encoding of a curve point.
        non_canonical = (ed25519._FIELD + 1).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(self.signature, self.message, non_canonical))

    def test_off_curve_public_key_is_rejected(self):
        for raw in (b"\x02" + b"\x00" * 31, b"\xff" * 32):
            with self.subTest(raw=raw[:4].hex()):
                self.assertFalse(ed25519.verify(self.signature, self.message, raw))

    def test_signing_rejects_bad_keys_and_messages(self):
        for secret in (None, b"", b"short", "x" * 32, b"k" * 33):
            with self.subTest(secret=secret):
                with self.assertRaises(ed25519.Ed25519Error):
                    ed25519.sign(b"message", secret)
        for message in (None, "text", 123):
            with self.subTest(message=message):
                with self.assertRaises(ed25519.Ed25519Error):
                    ed25519.sign(message, self.secret)

    def test_generated_keys_are_unique_and_usable(self):
        keys = [ed25519.generate_secret_key() for _ in range(8)]
        self.assertEqual(len(set(keys)), len(keys))
        for secret in keys:
            self.assertEqual(len(secret), ed25519.SECRET_KEY_BYTES)
            public = ed25519.derive_public_key(secret)
            self.assertEqual(len(public), ed25519.PUBLIC_KEY_BYTES)
            signature = ed25519.sign(b"payload", secret)
            self.assertEqual(len(signature), ed25519.SIGNATURE_BYTES)
            self.assertTrue(ed25519.verify(signature, b"payload", public))

    def test_empty_message_is_supported(self):
        signature = ed25519.sign(b"", self.secret)
        self.assertTrue(ed25519.verify(signature, b"", self.public))

    def test_large_message_is_supported(self):
        message = b"z" * 200_000
        signature = ed25519.sign(message, self.secret)
        self.assertTrue(ed25519.verify(signature, message, self.public))
        self.assertFalse(ed25519.verify(signature, message + b"z", self.public))


if __name__ == "__main__":
    unittest.main()
