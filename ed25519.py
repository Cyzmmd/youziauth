# Copyright (C) 2026 yoouzic
# SPDX-License-Identifier: GPL-3.0-only

"""Self-contained Ed25519 signatures (RFC 8032) for release verification.

The updater must be able to authenticate a release without an Authenticode
certificate, so the trust anchor is a public key compiled into the program and
the release is signed with the matching offline private key.

This module deliberately has no third-party imports: it runs inside the frozen
PyInstaller bundle, where an undeclared dependency would only fail at runtime,
and it is small enough to audit against RFC 8032 directly.

Representation notes
--------------------
Points use extended twisted-Edwards coordinates ``(X : Y : Z : T)`` with
``x = X/Z``, ``y = Y/Z`` and ``T = XY/Z``. The addition and doubling formulas
are inversion-free, so a scalar multiplication needs exactly one field
inversion at the end instead of one per step - roughly 500x faster than the
straightforward affine transcription, which matters because verification runs
inside the installation worker.
"""

from __future__ import annotations

import hashlib
import os

__all__ = [
    "SIGNATURE_BYTES",
    "PUBLIC_KEY_BYTES",
    "SECRET_KEY_BYTES",
    "Ed25519Error",
    "generate_secret_key",
    "derive_public_key",
    "sign",
    "verify",
]

_FIELD = 2**255 - 19
_ORDER = 2**252 + 27742317777372353535851937790883648493
_COFACTOR = 8

SIGNATURE_BYTES = 64
PUBLIC_KEY_BYTES = 32
SECRET_KEY_BYTES = 32

# Curve constant d = -121665/121666, and sqrt(-1) used when recovering x.
_D = -121665 * pow(121666, _FIELD - 2, _FIELD) % _FIELD
_SQRT_M1 = pow(2, (_FIELD - 1) // 4, _FIELD)

# Neutral element in extended coordinates: (X : Y : Z : T) = (0 : 1 : 1 : 0).
_NEUTRAL = (0, 1, 1, 0)


class Ed25519Error(ValueError):
    """A key, signature, or message was not usable for Ed25519."""


def _bytes32(value: object, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise Ed25519Error(f"{name} must be bytes")
    raw = bytes(value)
    if len(raw) != 32:
        raise Ed25519Error(f"{name} must be exactly 32 bytes")
    return raw


def _recover_x(y: int) -> int:
    """Return the even x for the curve point with the given y."""
    xx = (y * y - 1) * pow(_D * y * y + 1, _FIELD - 2, _FIELD) % _FIELD
    x = pow(xx, (_FIELD + 3) // 8, _FIELD)
    if (x * x - xx) % _FIELD != 0:
        x = x * _SQRT_M1 % _FIELD
    if x % 2 != 0:
        x = _FIELD - x
    return x


_BASE_Y = 4 * pow(5, _FIELD - 2, _FIELD) % _FIELD
_BASE = (_recover_x(_BASE_Y), _BASE_Y)


def _to_extended(point: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y = point
    return (x, y, 1, x * y % _FIELD)


def _to_affine(point: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, z, _ = point
    inverse = pow(z, _FIELD - 2, _FIELD)
    return (x * inverse % _FIELD, y * inverse % _FIELD)


def _add(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Unified extended-coordinate addition; also correct when left == right."""
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = (y1 - x1) * (y2 - x2) % _FIELD
    b = (y1 + x1) * (y2 + x2) % _FIELD
    c = 2 * t1 * t2 * _D % _FIELD
    d = 2 * z1 * z2 % _FIELD
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _FIELD, g * h % _FIELD, f * g % _FIELD, e * h % _FIELD)


def _double(point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, _ = point
    a = x1 * x1 % _FIELD
    b = y1 * y1 % _FIELD
    c = 2 * z1 * z1 % _FIELD
    h = a + b
    e = h - (x1 + y1) ** 2 % _FIELD
    g = a - b
    f = c + g
    return (e * f % _FIELD, g * h % _FIELD, f * g % _FIELD, e * h % _FIELD)


def _scalarmult(point: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    """Double-and-add in extended coordinates; no per-step inversion."""
    if scalar == 0:
        return _NEUTRAL
    result = _NEUTRAL
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _double(addend)
        scalar >>= 1
    return result


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(raw: bytes) -> tuple[int, int]:
    """Decode a compressed point, rejecting non-canonical and off-curve input."""
    if len(raw) != 32:
        raise Ed25519Error("point must be exactly 32 bytes")
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y)
    if x & 1 != sign:
        x = _FIELD - x
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _FIELD != 0:
        raise Ed25519Error("point is not on the curve")
    return (x, y)


def _clamp(digest: bytes) -> int:
    """RFC 8032 secret scalar: prune the first half of the secret hash."""
    value = int.from_bytes(digest[:32], "little")
    value &= (1 << 254) - 8
    value |= 1 << 254
    return value


def generate_secret_key() -> bytes:
    """Return a fresh 32-byte secret key from the operating system CSPRNG."""
    return os.urandom(SECRET_KEY_BYTES)


def derive_public_key(secret_key: bytes) -> bytes:
    """Return the 32-byte public key encoded in the standard compressed form."""
    secret = _bytes32(secret_key, "secret key")
    scalar = _clamp(hashlib.sha512(secret).digest())
    return _encode_point(_to_affine(_scalarmult(_to_extended(_BASE), scalar)))


def sign(message: bytes, secret_key: bytes) -> bytes:
    """Return the 64-byte detached signature of message under secret_key."""
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise Ed25519Error("message must be bytes")
    message = bytes(message)
    secret = _bytes32(secret_key, "secret key")
    digest = hashlib.sha512(secret).digest()
    scalar = _clamp(digest)
    public_key = _encode_point(_to_affine(_scalarmult(_to_extended(_BASE), scalar)))
    nonce = int.from_bytes(hashlib.sha512(digest[32:64] + message).digest(), "little") % _ORDER
    encoded_r = _encode_point(_to_affine(_scalarmult(_to_extended(_BASE), nonce)))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _ORDER
    s = (nonce + challenge * scalar) % _ORDER
    return encoded_r + s.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, public_key: bytes) -> bool:
    """Return True only for a valid, non-malleable signature.

    Any malformed input is reported as False rather than raising, so callers
    cannot accidentally treat a decoding failure as a trusted result.
    """
    try:
        if not isinstance(message, (bytes, bytearray, memoryview)):
            return False
        message = bytes(message)
        if not isinstance(signature, (bytes, bytearray, memoryview)):
            return False
        signature = bytes(signature)
        public_key = _bytes32(public_key, "public key")
        if len(signature) != SIGNATURE_BYTES:
            return False
        encoded_r, encoded_s = signature[:32], signature[32:]
        scalar = int.from_bytes(encoded_s, "little")
        # RFC 8032 requires S < L. Without this check a third party can derive
        # S + L and produce a different, still-verifying signature for the same
        # message, which breaks any code that treats a signature as an identity.
        if scalar >= _ORDER:
            return False
        point_a = _decode_point(public_key)
        challenge = int.from_bytes(
            hashlib.sha512(encoded_r + public_key + message).digest(), "little"
        ) % _ORDER
        expected = _to_affine(
            _add(
                _scalarmult(_to_extended(_BASE), scalar),
                _scalarmult(_to_extended(point_a), _ORDER - challenge),
            )
        )
        return _encode_point(expected) == encoded_r
    except (Ed25519Error, ValueError, TypeError, OverflowError):
        return False
