import re
from typing import Optional

_MULTI_SPACE = re.compile(r"\s{2,}")

def normalize_name(raw: str) -> Optional[str]:
    """Title-case, collapse whitespace, strip. Returns None for empty."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = _MULTI_SPACE.sub(" ", raw.strip())
    return cleaned.title() if cleaned else None