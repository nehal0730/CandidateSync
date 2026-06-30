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