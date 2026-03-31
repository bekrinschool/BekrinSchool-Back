"""
Secure credential generation for students and parents.
Uses secrets module for cryptographically secure random values.
"""
import re
import secrets
import string
from typing import Tuple
from django.conf import settings


# Default email domain; override via STUDENT_EMAIL_DOMAIN in settings
def _get_email_domain():
    return getattr(
        settings,
        "STUDENT_EMAIL_DOMAIN",
        "bekrinschool.az",
    )


def _slugify(name: str) -> str:
    """Convert full name to a URL-safe slug (Latin + digits)."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:30] if s else "user"


def generate_password(length: int = 12) -> str:
    """
    Generate a cryptographically secure random password.
    Uses uppercase, lowercase, digits; avoids ambiguous chars (0,O,1,l,I).
    """
    alphabet = (
        string.ascii_uppercase.replace("O", "").replace("I", "")
        + string.ascii_lowercase.replace("o", "").replace("l", "").replace("i", "")
        + string.digits.replace("0", "").replace("1", "")
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_simple_password(existing: set = None) -> str:
    """
    Generate an easy-to-type, unique password: 2 letters + 4 digits + 1 letter.
    Format: KA4821M (A-Z excl I,O; digits 2-9).
    Collision-safe for 10k+ users. existing=set of already-used passwords.
    """
    letters = "ABCDEFGHJKLMPQRSTUVWXYZ"
    digits = "23456789"
    existing = existing or set()
    for _ in range(100):
        p = (
            "".join(secrets.choice(letters) for _ in range(2))
            + "".join(secrets.choice(digits) for _ in range(4))
            + secrets.choice(letters)
        )
        if p not in existing:
            return p
    return "".join(secrets.choice(letters + digits) for _ in range(7))


def generate_parent_credentials(full_name: str) -> Tuple[str, str]:
    """Generate parent email and password only."""
    domain = _get_email_domain()
    base = _slugify(full_name)
    suffix = secrets.token_hex(4)
    email = f"parent.{base}.{suffix}@{domain}"
    password = generate_password()
    return email, password


def generate_credentials(full_name: str) -> dict:
    """
    Generate student and parent credentials.
    Returns:
        {
            "student_email": str,
            "student_password": str,
            "parent_email": str,
            "parent_password": str,
        }
    """
    domain = _get_email_domain()
    base = _slugify(full_name)
    suffix = secrets.token_hex(4)

    student_email = f"student.{base}.{suffix}@{domain}"
    parent_email = f"parent.{base}.{suffix}@{domain}"

    return {
        "student_email": student_email,
        "student_password": generate_password(),
        "parent_email": parent_email,
        "parent_password": generate_password(),
    }
