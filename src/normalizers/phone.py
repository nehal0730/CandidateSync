"""
normalizers/phone.py
====================
Convert raw phone strings to E.164 format (+{CC}{subscriber}).

Strategy (deterministic, no external library):
  1. Strip all non-digit/non-'+' characters.
  2. If already starts with '+' → validate digit count (7–15 total after '+').
  3. Otherwise prepend country code from the CC_MAP using country_hint.
  4. Strip leading '0' from local number (common in EU/IN formats).
  5. If digit count outside [7, 15] → return None (never stored).
"""
import re
from typing import Optional

# Calling code map for common countries (ISO-3166 alpha-2 → calling code)
_CC_MAP: dict[str, str] = {
    "US": "1",  "CA": "1",  "GB": "44", "IN": "91", "AU": "61",
    "DE": "49", "FR": "33", "SG": "65", "AE": "971","NL": "31",
    "JP": "81", "CN": "86", "BR": "55", "MX": "52", "ZA": "27",
    "SE": "46", "NO": "47", "DK": "45", "FI": "358","CH": "41",
    "AT": "43", "ES": "34", "IT": "39", "PT": "351","IE": "353",
    "IL": "972","PL": "48", "NZ": "64", "HK": "852","MY": "60",
}

_STRIP_RE = re.compile(r"[^\d+]")


def normalize_phone(raw: str, country_hint: str = "US") -> Optional[str]:
    """
    Returns E.164 string (e.g. '+14155552671') or None if unparseable.

    Parameters
    ----------
    raw          : raw phone string from any source
    country_hint : ISO-3166 alpha-2 country code used to prepend calling code
                   when the number has no '+' prefix.
    """
    if not raw or not isinstance(raw, str):
        return None

    stripped = _STRIP_RE.sub("", raw.strip())

    if stripped.startswith("+"):
        candidate = stripped
    else:
        cc = _CC_MAP.get((country_hint or "US").upper(), "1")
        # Remove leading trunk prefix '0' (common in UK, IN, EU)
        local = stripped.lstrip("0") or stripped
        candidate = f"+{cc}{local}"

    # Validate: '+' followed by 7–15 digits only
    body = candidate[1:]
    if not body.isdigit() or not (7 <= len(body) <= 15):
        return None

    return candidate