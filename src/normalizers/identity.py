import hashlib
from typing import Optional

def make_candidate_id(primary_email: Optional[str],
                      fallback_name: Optional[str] = None,
                      fallback_phone: Optional[str] = None,
                      bucket_key: Optional[str] = None) -> str:
    """
    Deterministic candidate_id = 'cand_' + first 16 hex chars of SHA-256.

    Priority:
      1. SHA-256(lowercase(primary_email))
      2. SHA-256(normalized_name + '|' + normalized_phone)
      3. SHA-256(normalized_name)
      4. SHA-256(bucket_key)   — internal merge-bucket key, used only when
         no email/name/phone identity signal exists at all. This still
         keeps IDs deterministic (same bucket_key -> same id) while
         avoiding false collisions between unrelated "no identity" records
         that would otherwise all collapse onto a single shared constant.
      5. SHA-256('unknown')    — absolute last resort.
    """
    if primary_email:
        seed = primary_email.strip().lower()
    elif fallback_name and fallback_phone:
        seed = f"{fallback_name.strip().lower()}|{fallback_phone.strip()}"
    elif fallback_name:
        seed = fallback_name.strip().lower()
    elif bucket_key:
        seed = f"bucket:{bucket_key}"
    else:
        seed = "unknown"

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"cand_{digest[:16]}"