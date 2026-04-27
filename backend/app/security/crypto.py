"""
Symmetric encryption helper for sensitive connection params at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library —
a sealed, vetted construction that we should never hand-roll.

Key resolution order:
  1. ``APP_SECRET_KEY`` env var — production path. MUST be a 32-byte
     urlsafe base64 string (i.e. Fernet.generate_key().decode()).
  2. Local key file at ``backend/data/.secret_key`` — auto-generated on
     first run for dev so the app boots without manual setup. The file
     is intentionally outside the source tree and should be in .gitignore.

The file-based fallback is convenient but means an attacker with disk
access can decrypt stored Postgres passwords. In production, always set
APP_SECRET_KEY via your secrets manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


_KEY_FILE = Path(settings.data_dir) / ".secret_key"
_cached_fernet: Optional[Fernet] = None


def _load_or_create_key() -> bytes:
    """Resolve the encryption key, generating one for dev if missing."""
    if settings.app_secret_key:
        return settings.app_secret_key.encode()

    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes().strip()

    # Dev fallback — generate, persist, restrict permissions where the OS allows.
    key = Fernet.generate_key()
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _KEY_FILE.write_bytes(key)
    try:
        _KEY_FILE.chmod(0o600)
    except OSError:
        # Windows ignores POSIX perms — that's fine.
        pass
    return key


def _fernet() -> Fernet:
    global _cached_fernet
    if _cached_fernet is None:
        _cached_fernet = Fernet(_load_or_create_key())
    return _cached_fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string. Returns urlsafe-base64 ciphertext."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Reverse :func:`encrypt`. Raises ``ValueError`` if the token is invalid."""
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover — bad key/data
        raise ValueError("Could not decrypt — secret key changed or data corrupted.") from exc
