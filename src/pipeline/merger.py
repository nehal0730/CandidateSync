"""
pipeline/merger.py
==================
Groups IntermediateRecords by candidate identity and merges them into
a single CanonicalRecord per candidate.

Candidate Matching (ordered, first match wins):
  ① normalized email
  ② normalized phone
  ③ name_norm + company_norm
  ④ name_norm + phone_norm

Source Priority (descending):
  ATS JSON (0.90) > Recruiter CSV (0.75) > Resume PDF (0.70) > Recruiter Notes (0.50)

Conflict Resolution:
  - Scalars  : highest-priority non-null wins.
                Equal priority → first deterministic occurrence (insertion order).
  - Arrays   : full union with deduplication (order: high-priority first).
  - Structs  : key-merge — missing sub-fields filled from lower-priority source.

Core principle: if a value cannot be determined, return null — never guess.
"""
from __future__ import annotations

import re
import sys
import os
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    CanonicalRecord, IntermediateRecord,
    ExperienceEntry, EducationEntry, LocationData, LinksData,
)
from normalizers import make_candidate_id


# ── Source priority weight → rank (lower = higher priority) ──────────────────
_SOURCE_RANK: Dict[str, int] = {
    "ats_json":        1,
    "recruiter_csv":   2,
    "resume_pdf":      3,
    "recruiter_notes": 4,
}

def _rank(source_name: str) -> int:
    return _SOURCE_RANK.get(source_name, 99)


# ── Identity key helpers ──────────────────────────────────────────────────────

def _norm_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse spaces — for fuzzy key matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def _identity_keys(rec: IntermediateRecord) -> List[str]:
    """
    Returns a list of candidate identity keys for this record (in priority order).
    The first key that matches an existing bucket is used.
    """
    keys: List[str] = []

    # ① Primary: normalized email
    for email in rec.emails:
        keys.append(f"email:{email.lower()}")

    # ② Normalized phone
    for phone in rec.phones:
        keys.append(f"phone:{phone}")

    # ③ name + company (from first experience entry)
    if rec.full_name:
        name_norm = _norm_text(rec.full_name)
        company_norm = ""
        if rec.experience:
            company_norm = _norm_text(rec.experience[0].company or "")
        if company_norm:
            keys.append(f"name_company:{name_norm}|{company_norm}")

        # ④ name + phone (last resort)
        for phone in rec.phones:
            keys.append(f"name_phone:{name_norm}|{phone}")

    return keys


# ── Array deduplication helpers ───────────────────────────────────────────────

def _dedup_list(items: List[str]) -> List[str]:
    """Order-preserving deduplication."""
    seen: Dict[str, bool] = {}
    return [x for x in items if not (x in seen or seen.update({x: True}))]


# ── Struct merge helpers ──────────────────────────────────────────────────────

def _exp_key(e: ExperienceEntry) -> str:
    """
    Experience identity = (company, title) only.

    Rationale: the same job, reported by different sources, will not always
    agree on exact dates (CSV exports often omit dates entirely; resumes use
    free-text date ranges). Keying on company+title only lets a dateless
    CSV record correctly merge into — and gap-fill — the same job's
    ATS/resume record instead of creating a spurious duplicate entry.
    """
    return (
        _norm_text(e.company or "") + "|" +
        _norm_text(e.title   or "")
    )

def _edu_key(e: EducationEntry) -> str:
    return (
        _norm_text(e.institution or "") + "|" +
        _norm_text(e.degree      or "")
    )


def _merge_experience(all_recs: List[IntermediateRecord]) -> List[ExperienceEntry]:
    """
    Union of experience entries across sources.
    Deduplication by (company_norm + title_norm + start).
    Missing sub-fields filled from lower-priority source.
    Sorted newest-first.
    """
    merged: Dict[str, ExperienceEntry] = {}

    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for entry in rec.experience:
            key = _exp_key(entry)
            if not key.strip("|"):
                continue
            if key not in merged:
                merged[key] = ExperienceEntry(
                    company=entry.company, title=entry.title,
                    start=entry.start, end=entry.end,
                    summary=entry.summary, location=entry.location,
                    employment_type=entry.employment_type,
                )
            else:
                # Gap-fill from lower-priority source
                existing = merged[key]
                if not existing.start    and entry.start:    existing.start    = entry.start
                if not existing.end      and entry.end:       existing.end      = entry.end
                if not existing.summary  and entry.summary:  existing.summary  = entry.summary
                if not existing.location and entry.location:  existing.location = entry.location
                if not existing.employment_type and entry.employment_type:
                    existing.employment_type = entry.employment_type

    # Sort by start date descending (newest first); entries without dates go last
    def sort_key(e: ExperienceEntry) -> str:
        return e.start or "0000-00"

    return sorted(merged.values(), key=sort_key, reverse=True)


def _merge_education(all_recs: List[IntermediateRecord]) -> List[EducationEntry]:
    """
    Union of education entries; dedup by (institution_norm + degree_norm).
    Gap-fill from lower-priority source.
    """
    merged: Dict[str, EducationEntry] = {}

    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for entry in rec.education:
            key = _edu_key(entry)
            if not key.strip("|"):
                continue
            if key not in merged:
                merged[key] = EducationEntry(
                    institution=entry.institution, degree=entry.degree,
                    field_of_study=entry.field_of_study, end_year=entry.end_year,
                )
            else:
                existing = merged[key]
                if not existing.field_of_study and entry.field_of_study:
                    existing.field_of_study = entry.field_of_study
                if not existing.end_year and entry.end_year:
                    existing.end_year = entry.end_year

    return list(merged.values())


def _merge_location(all_recs: List[IntermediateRecord]) -> Optional[LocationData]:
    """
    Pick location from highest-priority source; fill missing sub-fields
    from lower-priority sources.
    """
    result = LocationData()
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        if rec.location:
            if not result.city    and rec.location.city:    result.city    = rec.location.city
            if not result.region  and rec.location.region:  result.region  = rec.location.region
            if not result.country and rec.location.country: result.country = rec.location.country
    return result if (result.city or result.region or result.country) else None


def _merge_links(all_recs: List[IntermediateRecord]) -> Optional[LinksData]:
    """Union of links; highest-priority source wins per field."""
    result = LinksData()
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        if rec.links:
            if not result.linkedin  and rec.links.linkedin:  result.linkedin  = rec.links.linkedin
            if not result.github    and rec.links.github:    result.github    = rec.links.github
            if not result.portfolio and rec.links.portfolio: result.portfolio = rec.links.portfolio
            for u in rec.links.other:
                if u not in result.other:
                    result.other.append(u)
    has_any = result.linkedin or result.github or result.portfolio or result.other
    return result if has_any else None


# ── Scalar merge ──────────────────────────────────────────────────────────────

def _pick_scalar(all_recs: List[IntermediateRecord],
                 field: str) -> Tuple[Optional[Any], Optional[str]]:
    """
    Pick the best non-null value for a scalar field.
    Returns (value, source_name).
    Priority: source rank → first deterministic occurrence.
    """
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        val = getattr(rec, field, None)
        if val is not None and val != "" and val != []:
            return val, rec.source_name
    return None, None


# ── Provenance merge ──────────────────────────────────────────────────────────

def _merge_provenance(all_recs: List[IntermediateRecord]) -> Dict[str, Dict[str, str]]:
    """
    Build field-wise provenance dict from all sources.
    Highest-priority source per field wins.
    """
    result: Dict[str, Dict[str, str]] = {}
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for field, prov_entry in rec.provenance.items():
            if field not in result:
                result[field] = {
                    "source": prov_entry.source,
                    "method": prov_entry.method,
                }
    return result


# ── Main merge function ───────────────────────────────────────────────────────

def _derive_years_experience(experience: List[ExperienceEntry]) -> Optional[float]:
    """
    Derive years_experience from experience entries only if ALL entries have start dates.
    Returns None otherwise (never invented).
    """
    if not experience:
        return None
    starts = [e.start for e in experience if e.start]
    if len(starts) != len(experience):
        return None  # not all entries have dates — don't guess
    earliest = min(starts)
    try:
        year, month = map(int, earliest.split("-"))
        today = date.today()
        years = (today.year - year) + (today.month - month) / 12.0
        return round(max(0.0, years), 1)
    except (ValueError, AttributeError):
        return None


def _merge_candidate_records(
    all_recs: List[IntermediateRecord],
    bucket_key: Optional[str] = None,
) -> CanonicalRecord:
    """
    Merge a list of IntermediateRecords (all for the same candidate)
    into a single CanonicalRecord.

    bucket_key is the internal merge-bucket identifier assigned by
    merge_records(); it is only used as a last-resort candidate_id seed
    when no email/name/phone identity signal exists, to avoid distinct
    "no identity" records from colliding onto the same ID.
    """
    canon = CanonicalRecord()

    # ── Emails (union, primary first from highest-priority source) ────────────
    email_sets: List[str] = []
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for e in rec.emails:
            if e not in email_sets:
                email_sets.append(e)
    canon.emails = email_sets

    # ── Phones (union, deduped) ───────────────────────────────────────────────
    phone_sets: List[str] = []
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for p in rec.phones:
            if p not in phone_sets:
                phone_sets.append(p)
    canon.phones = phone_sets

    # ── Candidate ID ──────────────────────────────────────────────────────────
    primary_email = canon.emails[0] if canon.emails else None
    name_scalar, _ = _pick_scalar(all_recs, "full_name")
    phone_scalar   = canon.phones[0] if canon.phones else None
    canon.candidate_id = make_candidate_id(
        primary_email, name_scalar, phone_scalar, bucket_key=bucket_key
    )

    # ── Scalar fields ─────────────────────────────────────────────────────────
    canon.full_name,        _ = _pick_scalar(all_recs, "full_name")
    canon.headline,         _ = _pick_scalar(all_recs, "headline")
    canon.years_experience, _ = _pick_scalar(all_recs, "years_experience")

    # ── Struct fields ─────────────────────────────────────────────────────────
    canon.location   = _merge_location(all_recs)
    canon.links      = _merge_links(all_recs)
    canon.experience = _merge_experience(all_recs)
    canon.education  = _merge_education(all_recs)

    # Derive years_experience if not found directly
    if canon.years_experience is None:
        canon.years_experience = _derive_years_experience(canon.experience)

    # ── Skills (union, deduped, canonical names only) ─────────────────────────
    skill_union: List[str] = []
    for rec in sorted(all_recs, key=lambda r: _rank(r.source_name)):
        for s in rec.skills:
            if s and s not in skill_union:
                skill_union.append(s)
    canon.skills = skill_union

    # ── Provenance ────────────────────────────────────────────────────────────
    canon.provenance = _merge_provenance(all_recs)

    # ── Metadata ──────────────────────────────────────────────────────────────
    canon.metadata = {
        "processed_at": date.today().isoformat(),
        "merged_from":  list({r.source_name for r in all_recs
                               if not r.extraction_error}),
        "version":      "1.0.0",
    }

    return canon


# ── Public entry point ────────────────────────────────────────────────────────

def merge_records(
    all_records: List[IntermediateRecord],
) -> List[Tuple[CanonicalRecord, List[IntermediateRecord]]]:
    """
    Group IntermediateRecords by candidate identity and merge each group
    into a CanonicalRecord.

    Returns a list of (CanonicalRecord, contributing_records) tuples — one
    per unique candidate. The contributing_records list is the exact subset
    of IntermediateRecords that were merged into that CanonicalRecord; this
    is required downstream for correctly-scoped confidence scoring (a
    candidate's confidence must never be influenced by another candidate's
    sources).

    Records with extraction errors are still included (partial data).
    """
    # Bucket: identity_key → list of records that belong to that candidate
    buckets: Dict[str, List[IntermediateRecord]] = {}
    key_to_bucket: Dict[str, str] = {}   # maps each identity key → bucket primary key

    for rec in all_records:
        id_keys = _identity_keys(rec)
        assigned_bucket: Optional[str] = None

        # Find if any of this record's keys already maps to a bucket
        for k in id_keys:
            if k in key_to_bucket:
                assigned_bucket = key_to_bucket[k]
                break

        if assigned_bucket is None:
            # New candidate — create bucket using first available key or fallback
            bucket_key = id_keys[0] if id_keys else f"unknown_{len(buckets)}"
            assigned_bucket = bucket_key
            buckets[assigned_bucket] = []

        # Register all this record's keys → same bucket
        for k in id_keys:
            key_to_bucket[k] = assigned_bucket

        buckets.setdefault(assigned_bucket, []).append(rec)

    # Merge each bucket into a CanonicalRecord, keeping the source records alongside
    results: List[Tuple[CanonicalRecord, List[IntermediateRecord]]] = []
    for bucket_key, recs in buckets.items():
        canonical = _merge_candidate_records(recs, bucket_key=bucket_key)
        results.append((canonical, recs))

    return results