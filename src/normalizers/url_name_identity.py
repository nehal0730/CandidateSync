"""
normalizers/url.py  — URL normalization to https://
normalizers/name.py — Name normalization (Title-case, strip)
normalizers/identity.py — Deterministic candidate_id generation
"""
# ── url.py ────────────────────────────────────────────────────────────────────
import re
import hashlib
from typing import Optional


def normalize_url(raw: str) -> Optional[str]:
    """Ensure URL has https:// scheme. Returns None for empty input."""
    if not raw or not isinstance(raw, str):
        return None
    u = raw.strip()
    if not u:
        return None
    if u.startswith("https://") or u.startswith("http://"):
        return u
    if u.startswith("//"):
        return "https:" + u
    return "https://" + u


# ── name.py ───────────────────────────────────────────────────────────────────

_MULTI_SPACE = re.compile(r"\s{2,}")

def normalize_name(raw: str) -> Optional[str]:
    """Title-case, collapse whitespace, strip. Returns None for empty."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = _MULTI_SPACE.sub(" ", raw.strip())
    return cleaned.title() if cleaned else None


# ── identity.py ───────────────────────────────────────────────────────────────

def make_candidate_id(primary_email: Optional[str],
                      fallback_name: Optional[str] = None,
                      fallback_phone: Optional[str] = None) -> str:
    """
    Deterministic candidate ID = 'cand_' + first 16 hex chars of SHA-256.

    Primary key : SHA-256(lowercase(primary_email))
    Fallback    : SHA-256(normalize_name + normalize_phone)
    Last resort : SHA-256('unknown')
    """
    if primary_email:
        seed = primary_email.strip().lower()
    elif fallback_name and fallback_phone:
        seed = f"{fallback_name.strip().lower()}|{fallback_phone.strip()}"
    elif fallback_name:
        seed = fallback_name.strip().lower()
    else:
        seed = "unknown"

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"cand_{digest[:16]}"