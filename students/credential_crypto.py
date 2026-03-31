"""
Encryption helpers for credential registry.
Uses Fernet (symmetric) with CREDENTIALS_ENCRYPTION_KEY from env.
Never log plaintext passwords.
"""
import base64
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_fernet():
    """Lazy import to avoid hard dependency if cryptography not installed."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        raise ImportError("cryptography package required. Run: pip install cryptography")
    key = getattr(settings, "CREDENTIALS_ENCRYPTION_KEY", None)
    if not key:
        if settings.DEBUG:
            # Dev fallback: DO NOT use in production
            key = base64.urlsafe_b64encode(b"x" * 32).decode()
        else:
            raise ValueError("CREDENTIALS_ENCRYPTION_KEY must be set in production")
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    """Encrypt a plaintext string. Returns base64 ciphertext."""
    if not plain:
        return ""
    f = _get_fernet()
    return f.encrypt(plain.encode("utf-8")).decode()


def decrypt_secret(cipher: str) -> str:
    """Decrypt ciphertext. Returns plaintext. Raises on invalid token."""
    if not cipher:
        return ""
    f = _get_fernet()
    return f.decrypt(cipher.encode()).decode("utf-8")


def encrypt_credentials(student_password: str, parent_password: str) -> str:
    """Encrypt both passwords as JSON. Returns ciphertext."""
    data = {
        "student_password": student_password or "",
        "parent_password": parent_password or "",
    }
    return encrypt_secret(json.dumps(data))


def decrypt_credentials(cipher: str) -> dict:
    """Decrypt and return {student_password, parent_password}."""
    if not cipher:
        return {"student_password": "", "parent_password": ""}
    data = json.loads(decrypt_secret(cipher))
    return {
        "student_password": data.get("student_password", ""),
        "parent_password": data.get("parent_password", ""),
    }
