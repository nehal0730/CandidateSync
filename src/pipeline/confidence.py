"""
pipeline/confidence.py
=======================
Compute per-field confidence scores for a CanonicalRecord.

Formula:
  per_field_confidence = source_weight × agreement_bonus

  agreement_bonus:
    1.0  — ≥2 sources provide the same (or compatible) value
    0.8  — only 1 source provides a non-null value
    0.0  — all sources returned null

  source_weight = weight of the highest-priority source that contributed.

Overall confidence = weighted mean of all non-null field confidence scores,
weighted by field importance.

Core principle: never assign high confidence to a value with only one source
and no corroboration.
"""
from __future__ import annotations

import sys
import os
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import CanonicalRecord, IntermediateRecord


# ── Source reliability weights ────────────────────────────────────────────────
_SOURCE_WEIGHT: Dict[str, float] = {
    "ats_json":        0.90,
    "recruiter_csv":   0.75,
    "resume_pdf":      0.70,
    "recruiter_notes": 0.50,
}

# ── Field importance weights (for overall_confidence calculation) ─────────────
_FIELD_IMPORTANCE: Dict[str, float] = {
    "full_name":        1.0,
    "emails":           1.0,
    "phones":           0.8,
    "location":         0.6,
    "links":            0.5,
    "headline":         0.5,
    "years_experience": 0.7,
    "skills":           0.8,
    "experience":       0.9,
    "education":        0.8,
}


def _sources_with_value(
    all_recs: List[IntermediateRecord],
    field: str,
) -> List[str]:
    """Return list of source names that provided a non-null, non-empty value for field."""
    sources = []
    for rec in all_recs:
        val = getattr(rec, field, None)
        if val is not None and val != "" and val != []:
            sources.append(rec.source_name)
    return sources


def _values_agree(all_recs: List[IntermediateRecord], field: str) -> bool:
    """
    Check if ≥2 sources agree on a scalar field value.
    For arrays, agreement = at least one common element.
    """
    values = []
    for rec in all_recs:
        val = getattr(rec, field, None)
        if val is not None and val != "" and val != []:
            values.append(val)

    if len(values) < 2:
        return False

    # Scalar: check equality (case-insensitive for strings)
    if isinstance(values[0], str):
        normalized = [v.lower().strip() for v in values]
        return len(set(normalized)) < len(normalized)   # at least one duplicate

    # List of hashable scalars (emails, phones, skills): check common element
    if isinstance(values[0], list):
        if not values[0] or isinstance(values[0][0], (str, int, float)):
            try:
                first_set = set(values[0])
                return any(first_set & set(v) for v in values[1:] if v)
            except TypeError:
                pass
        # List of structured objects (experience, education) — dataclasses
        # aren't hashable/comparable cheaply; treat "all sources non-empty"
        # as the agreement signal instead.
        return all(len(v) > 0 for v in values)

    # Numeric: check within 10% tolerance
    if isinstance(values[0], (int, float)):
        baseline = float(values[0])
        return any(abs(float(v) - baseline) / max(baseline, 1) < 0.10 for v in values[1:])

    return False


def compute_confidence(
    canon: CanonicalRecord,
    all_recs: List[IntermediateRecord],
) -> CanonicalRecord:
    """
    Populate canon.confidence (field → score) and canon.overall_confidence.
    Mutates canon in-place and returns it.

    Example:
      full_name in ATS (w=0.90) and CSV (w=0.75), both agree:
        conf = 0.90 × 1.0 = 0.90

      full_name only in CSV, no other source:
        conf = 0.75 × 0.8 = 0.60

      full_name in no source:
        conf = 0.0
    """
    scored: Dict[str, float] = {}

    for field in _FIELD_IMPORTANCE:
        contributing_sources = _sources_with_value(all_recs, field)

        if not contributing_sources:
            scored[field] = 0.0
            continue

        # Highest source weight among contributing sources
        top_weight = max(
            _SOURCE_WEIGHT.get(src, 0.5) for src in contributing_sources
        )

        # Agreement bonus
        if len(contributing_sources) >= 2 and _values_agree(all_recs, field):
            agreement_bonus = 1.0
        elif contributing_sources:
            agreement_bonus = 0.8
        else:
            agreement_bonus = 0.0

        scored[field] = round(top_weight * agreement_bonus, 3)

    canon.confidence = scored

    # ── Overall confidence (importance-weighted mean of non-zero fields) ──────
    total_weight = 0.0
    weighted_sum = 0.0
    for field, importance in _FIELD_IMPORTANCE.items():
        score = scored.get(field, 0.0)
        if score > 0:
            weighted_sum  += score * importance
            total_weight  += importance

    canon.overall_confidence = round(
        weighted_sum / total_weight if total_weight > 0 else 0.0, 3
    )

    return canon