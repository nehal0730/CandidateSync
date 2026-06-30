"""
normalizers/email.py
====================
Normalize and validate email addresses.

Rules:
  - Lowercase the entire address.
  - Strip leading/trailing whitespace.
  - Validate with RFC-5322-compatible regex (common subset).
  - Return None for invalid addresses — never stored.
"""
import re
from typing import Optional

# Practical RFC-5322 subset (covers 99.9% of real emails)
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9]"              # must start with alphanumeric
    r"[a-zA-Z0-9._%+\-]*"        # local-part body
    r"@"
    r"[a-zA-Z0-9]"               # domain starts with alphanumeric
    r"[a-zA-Z0-9.\-]*"
    r"\.[a-zA-Z]{2,}$"           # TLD at least 2 chars
)


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


def normalize_email(raw: str) -> Optional[str]:
    """
    Returns lowercase normalized email or None if invalid.
    """
    if not raw or not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    return normalized if is_valid_email(normalized) else None