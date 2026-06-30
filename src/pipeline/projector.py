"""
pipeline/projector.py
======================
Projects a CanonicalRecord into a custom output dict driven by a runtime config.

Design:
  - The canonical record is NEVER modified — projection is a pure read-only transform.
  - Supports dot-path traversal and array indexing (e.g. "emails[0]", "experience[].title").
  - Applies per-field normalization overrides at projection time.
  - Enforces missing-value policy: null | omit | error.
  - Strips provenance / confidence blocks if config disables them.

Config schema:
  {
    "fields": [
      {
        "path": "output_field_name",
        "from": "canonical.path[0]",   // optional remap
        "type": "string",               // string | string[] | number | object
        "required": true,               // default false
        "normalize": "E164" | "canonical"
      }
    ],
    "include_confidence":  true,   // default true
    "include_provenance":  true,   // default true
    "include_metadata":    true,   // default true
    "on_missing":          "null"  // "null" | "omit" | "error"
  }
"""
from __future__ import annotations

import re
import sys
import os
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import CanonicalRecord
from normalizers import normalize_phone, normalize_skill


# ── Path resolver ─────────────────────────────────────────────────────────────

_ARRAY_INDEX_RE  = re.compile(r"^(.+?)\[(\d+)\]$")
_ARRAY_SPREAD_RE = re.compile(r"^(.+?)\[\]\.(.+)$")
_BARE_SPREAD_RE  = re.compile(r"^(.+?)\[\]$")


def _resolve_path(data: Any, path: str) -> Any:
    """
    Traverse a nested dict/list using a dot-path with optional array indexing.

    Supported syntax:
      "full_name"           → data["full_name"]
      "emails[0]"           → data["emails"][0]
      "experience[0].title" → data["experience"][0]["title"]
      "skills[]"            → data["skills"]  (bare spread — list as-is)
      "experience[].title"  → [e["title"] for e in data["experience"]]
    """
    if not path:
        return None

    # Array spread with sub-path: "experience[].title"
    m_spread = _ARRAY_SPREAD_RE.match(path)
    if m_spread:
        arr = _resolve_path(data, m_spread.group(1))
        sub = m_spread.group(2)
        if isinstance(arr, list):
            results = []
            for item in arr:
                if item is None:
                    continue
                if isinstance(item, (str, int, float, bool)):
                    # Item is a plain scalar (e.g. our canonical `skills`
                    # field is List[str], not List[{name, ...}]).
                    # A sub-path like ".name" on a scalar list has nothing
                    # to traverse into — return the scalar itself rather
                    # than silently producing None. This keeps configs
                    # written against an object-shaped array (e.g. the
                    # assignment brief's own example "skills[].name")
                    # working correctly against our simplified schema,
                    # instead of quietly resolving to a list of nulls.
                    results.append(item)
                else:
                    results.append(_resolve_path(item, sub))
            return results
        return None

    # Bare spread, no sub-path: "skills[]" → return the list itself
    m_bare = _BARE_SPREAD_RE.match(path)
    if m_bare:
        return _resolve_path(data, m_bare.group(1))

    parts = path.split(".", 1)
    head  = parts[0]
    rest  = parts[1] if len(parts) > 1 else None

    # Array index in head: "emails[0]"
    m_idx = _ARRAY_INDEX_RE.match(head)
    if m_idx:
        key = m_idx.group(1)
        idx = int(m_idx.group(2))
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list) and idx < len(val):
            val = val[idx]
        else:
            return None
        return _resolve_path(val, rest) if rest else val

    # Plain key
    if isinstance(data, dict):
        val = data.get(head)
    elif isinstance(data, object) and hasattr(data, head):
        val = getattr(data, head, None)
    else:
        return None

    return _resolve_path(val, rest) if rest else val


# ── Normalization overrides ───────────────────────────────────────────────────

def _apply_normalize(value: Any, normalize: Optional[str]) -> Any:
    """
    Re-apply a normalization at projection time.

    Note: by the time a value reaches the projector, it has already been
    normalized once during extraction (where country-hinted E.164 parsing,
    etc. has full context — location, source, etc.). This function exists
    to (a) re-validate that re-mapped/renamed values still satisfy the
    target normalization, and (b) cover configs that request a different
    normalization than what extraction applied. It does NOT have access to
    a country hint, so E164 calls here default to the same logic as a
    bare phone string with no location context. In practice this is a
    no-op pass-through for canonical phones, since they already carry a
    '+' prefix and normalize_phone() short-circuits on that.
    """
    if not normalize or value is None:
        return value

    norm_key = normalize.lower()

    if norm_key == "e164":
        if isinstance(value, str):
            return normalize_phone(value) or value
        if isinstance(value, list):
            return [normalize_phone(v) or v for v in value]

    if norm_key == "canonical":
        if isinstance(value, str):
            return normalize_skill(value)
        if isinstance(value, list):
            return [normalize_skill(v) or v for v in value]

    if norm_key == "lowercase":
        if isinstance(value, str):
            return value.lower()

    return value


# ── Public entry point ────────────────────────────────────────────────────────

class ProjectionError(Exception):
    """Raised when on_missing='error' and a required field is absent."""
    pass


def project(
    canon: CanonicalRecord,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Project a CanonicalRecord into a custom output dict using the runtime config.

    Parameters
    ----------
    canon  : The merged canonical record (never modified).
    config : Runtime projection config dict.

    Returns
    -------
    Projected output dict.

    Raises
    ------
    ProjectionError : if on_missing='error' and a required field is missing.
    """
    on_missing        = config.get("on_missing", "null")   # null | omit | error
    include_confidence = config.get("include_confidence", True)
    include_provenance = config.get("include_provenance", True)
    include_metadata   = config.get("include_metadata",   True)
    field_specs        = config.get("fields", [])

    # Convert canonical record to a flat dict for path traversal
    canon_dict = canon.to_dict()

    output: Dict[str, Any] = {}

    # ── Always include candidate_id ───────────────────────────────────────────
    output["candidate_id"] = canon_dict.get("candidate_id")

    # ── Project requested fields ──────────────────────────────────────────────
    if field_specs:
        for spec in field_specs:
            out_key   = spec.get("path")
            from_path = spec.get("from", out_key)   # if no 'from', use path as key
            required  = spec.get("required", False)
            normalize = spec.get("normalize")

            if not out_key:
                continue

            value = _resolve_path(canon_dict, from_path)
            value = _apply_normalize(value, normalize)

            if value is None or value == [] or value == "":
                if required and on_missing == "error":
                    raise ProjectionError(
                        f"Required field '{out_key}' (from '{from_path}') is missing."
                    )
                elif on_missing == "omit":
                    continue    # skip this field entirely
                else:           # "null" (default)
                    output[out_key] = None
            else:
                output[out_key] = value

    else:
        # No field spec → output the full canonical record
        output = canon_dict.copy()

    # ── Confidence block ──────────────────────────────────────────────────────
    if include_confidence:
        output["confidence"]        = canon_dict.get("confidence", {})
        output["overall_confidence"]= canon_dict.get("overall_confidence", 0.0)

    # ── Provenance block ──────────────────────────────────────────────────────
    if include_provenance:
        output["provenance"] = canon_dict.get("provenance", {})

    # ── Metadata block ────────────────────────────────────────────────────────
    if include_metadata:
        output["metadata"] = canon_dict.get("metadata", {})

    return output