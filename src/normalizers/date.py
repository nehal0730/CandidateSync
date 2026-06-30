"""
normalizers/date.py
===================
Convert any date string to YYYY-MM format.

Supported input formats (deterministic pattern library):
  - ISO:         2023-06-15  → 2023-06
  - Slash ISO:   2023/06/15  → 2023-06
  - MM/YYYY:     06/2023     → 2023-06
  - YYYY/MM:     2023/06     → 2023-06
  - Month YYYY:  June 2023   → 2023-06
  - Mon YYYY:    Jun 2023    → 2023-06
  - YYYY only:   2023        → 2023-01
  - "present" / "current" / "now" / "ongoing" → today's YYYY-MM

Unrecognized formats → None  (never invented).
"""
import re
from datetime import date
from typing import Optional

_PRESENT_WORDS = {"present", "current", "now", "ongoing", "today", "—", "-", "–"}

_MONTH_MAP: dict[str, str] = {
    "january": "01", "february": "02", "march": "03",    "april": "04",
    "may":     "05", "june":     "06", "july":  "07",    "august": "08",
    "september":"09","october":  "10", "november":"11",  "december":"12",
    "jan":"01","feb":"02","mar":"03","apr":"04",
    "jun":"06","jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12",
}

# Ordered patterns: (compiled_regex, year_group, month_group_or_None)
_PATTERNS = [
    # 2023-06-15  or  2023-06
    (re.compile(r"\b(\d{4})[-/](\d{1,2})(?:[-/]\d{1,2})?\b"), 1, 2),
    # 06/2023  or  06-2023
    (re.compile(r"\b(\d{1,2})[-/](\d{4})\b"), 2, 1),
    # June 2023  /  Jun 2023  /  JUNE 2023
    (re.compile(r"\b([A-Za-z]+)\s+(\d{4})\b"), None, None),   # special-cased below
    # 2023 only
    (re.compile(r"\b(\d{4})\b"), 1, None),
]


def normalize_date(raw: str) -> Optional[str]:
    """Returns 'YYYY-MM' or None."""
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()

    if cleaned.lower() in _PRESENT_WORDS or cleaned == "":
        return date.today().strftime("%Y-%m")

    # Month-name pattern (special case)
    m = re.search(r"\b([A-Za-z]+)\s+(\d{4})\b", cleaned)
    if m:
        mon_str = m.group(1).lower()
        # Try full name then 3-letter prefix
        mon_code = _MONTH_MAP.get(mon_str) or _MONTH_MAP.get(mon_str[:3])
        if mon_code:
            return f"{m.group(2)}-{mon_code}"

    # Numeric patterns
    for pattern, yg, mg in _PATTERNS:
        if yg is None:   # already handled above
            continue
        m = re.search(pattern, cleaned)
        if not m:
            continue
        year = m.group(yg)
        if not (1900 <= int(year) <= 2100):
            continue
        if mg is not None:
            month_raw = m.group(mg)
            month = month_raw.zfill(2)
            if 1 <= int(month) <= 12:
                return f"{year}-{month}"
        else:
            return f"{year}-01"

    return None