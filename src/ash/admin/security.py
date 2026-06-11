"""Password hashing for admin users — PBKDF2-SHA256 (stdlib, no extra deps).

Format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``. Verification is constant-time.
"""

from __future__ import annotations

import hashlib
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = encoded.split("$")
    except ValueError:
        return False
    if algo != _ALGO:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return secrets.compare_digest(dk.hex(), hash_hex)
