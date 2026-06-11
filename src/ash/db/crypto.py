"""Transparent column encryption for integration secrets (Fernet, via `cryptography`).

`EncryptedString` encrypts on write and decrypts on read at the SQLAlchemy type layer, so models
and the admin form deal in plaintext while the DB only ever stores ciphertext. The key comes from
`Settings.secret_key` (a Fernet key); generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from ash.config.settings import get_settings


def _coerce_fernet_key(secret_key: str) -> bytes:
    """Accept any SECRET_KEY string.

    If it is already a valid Fernet key (32 url-safe base64 bytes), use it as-is; otherwise derive
    a deterministic Fernet key from it via SHA-256 so arbitrary secrets still work.
    """
    raw = secret_key.encode()
    try:
        if len(base64.urlsafe_b64decode(raw)) == 32:
            return raw
    except (binascii.Error, ValueError):
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def get_fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise RuntimeError(
            "Settings.secret_key is unset — cannot encrypt/decrypt integration secrets. "
            'Generate one: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(_coerce_fernet_key(key))


class EncryptedString(TypeDecorator[str]):
    """A String column whose value is Fernet-encrypted at rest."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None or value == "":
            return value
        return get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None or value == "":
            return value
        return get_fernet().decrypt(value.encode()).decode()
