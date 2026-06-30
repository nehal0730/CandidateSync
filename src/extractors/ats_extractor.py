"""
extractors/ats_extractor.py
============================
Handles ATS (Applicant Tracking System) JSON blobs.

Design:
  - ATS field names do NOT match our canonical schema → explicit key mapping.
  - Supports Greenhouse-style, Lever-style, and generic flat JSON structures.
  - One IntermediateRecord per candidate object.
  - All arrays (emails, phones) handled as both list-of-objects and plain strings.
  - Normalization delegated entirely to the normalizers package.
"""
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    IntermediateRecord, ProvenanceEntry,
    ExperienceEntry, EducationEntry, LocationData, LinksData,
)
from normalizers import (
    normalize_email, normalize_phone, normalize_skill,
    normalize_url, normalize_name, normalize_country, normalize_date,
)

SOURCE = "ats_json"
WEIGHT = 0.90


# ── Safe deep-getter ──────────────────────────────────────────────────────────

def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """
    Try each key in order. Keys support dot-notation for nested paths.
    Returns first non-None match, or default.
    """
    for key in keys:
        parts = key.split(".")
        val = d
        try:
            for p in parts:
                if isinstance(val, list):
                    val = val[int(p)]
                elif isinstance(val, dict):
                    val = val[p]
                else:
                    val = None
                    break
            if val is not None:
                return val
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return default


# ── Sub-extractors ────────────────────────────────────────────────────────────

def _extract_name(data: dict, rec: IntermediateRecord):
    raw = _get(data, "name", "full_name", "candidate.name", "applicant.name",
               "candidate_name", "applicant_name")
    if raw:
        rec.full_name = normalize_name(str(raw))
        rec.provenance["full_name"] = ProvenanceEntry(SOURCE, "direct_field", raw)


def _extract_emails(data: dict, rec: IntermediateRecord):
    raw_list = _get(data, "email_addresses", "emails", "email",
                    "candidate.email_addresses", default=[])
    if isinstance(raw_list, str):
        raw_list = [raw_list]
    elif isinstance(raw_list, dict):
        raw_list = [raw_list.get("value", "")]

    found = []
    for item in (raw_list if isinstance(raw_list, list) else []):
        raw = item.get("value", "") if isinstance(item, dict) else str(item)
        norm = normalize_email(raw)
        if norm and norm not in found:
            found.append(norm)

    if found:
        rec.emails = found
        rec.provenance["emails"] = ProvenanceEntry(SOURCE, "direct_field", str(raw_list))


def _extract_phones(data: dict, rec: IntermediateRecord):
    raw_list = _get(data, "phone_numbers", "phones", "phone",
                    "candidate.phone_numbers", default=[])
    if isinstance(raw_list, str):
        raw_list = [raw_list]
    elif isinstance(raw_list, dict):
        raw_list = [raw_list.get("value", "")]

    country_hint = "US"
    if rec.location and rec.location.country:
        country_hint = rec.location.country

    found = []
    for item in (raw_list if isinstance(raw_list, list) else []):
        raw = item.get("value", "") if isinstance(item, dict) else str(item)
        norm = normalize_phone(raw, country_hint)
        if norm and norm not in found:
            found.append(norm)
        elif raw:
            rec.provenance["phones"] = ProvenanceEntry(SOURCE, "parse_failed", raw)

    if found:
        rec.phones = found
        rec.provenance["phones"] = ProvenanceEntry(SOURCE, "regex_parse", str(raw_list))


def _extract_location(data: dict, rec: IntermediateRecord):
    raw = _get(data, "addresses", "location", "candidate.location",
               "current_location", "address")

    # Normalize list → first item
    if isinstance(raw, list) and raw:
        raw = raw[0]

    if isinstance(raw, dict):
        city    = _get(raw, "city", "locality")
        region  = _get(raw, "state", "region", "province")
        country = normalize_country(str(_get(raw, "country", "country_code", default="") or ""))
        if city or region or country:
            rec.location = LocationData(city=city, region=region, country=country)
            rec.provenance["location"] = ProvenanceEntry(SOURCE, "direct_field", str(raw))

    elif isinstance(raw, str) and raw:
        parts = [p.strip() for p in raw.split(",")]
        city    = parts[0] if parts else None
        country = normalize_country(parts[-1]) if len(parts) > 1 else None
        rec.location = LocationData(city=city, country=country)
        rec.provenance["location"] = ProvenanceEntry(SOURCE, "direct_field", raw)


def _extract_links(data: dict, rec: IntermediateRecord):
    linkedin  = _get(data, "linkedin_profile_url", "linkedin", "social.linkedin")
    github    = _get(data, "github_url", "github",    "social.github")
    portfolio = _get(data, "website",   "portfolio",  "social.website", "personal_website")

    links: Dict[str, Optional[str]] = {}
    if linkedin:  links["linkedin"]  = normalize_url(str(linkedin))
    if github:    links["github"]    = normalize_url(str(github))
    if portfolio: links["portfolio"] = normalize_url(str(portfolio))

    if links:
        rec.links = LinksData(**links)
        rec.provenance["links"] = ProvenanceEntry(SOURCE, "direct_field", str(links))


def _extract_headline(data: dict, rec: IntermediateRecord):
    raw = _get(data, "headline", "title", "current_title", "job_title")
    if raw:
        rec.headline = str(raw)[:160]
        rec.provenance["headline"] = ProvenanceEntry(SOURCE, "direct_field", raw)


def _extract_years_exp(data: dict, rec: IntermediateRecord):
    raw = _get(data, "years_of_experience", "years_experience", "experience_years", "yoe")
    if raw is not None:
        try:
            rec.years_experience = float(str(raw))
            rec.provenance["years_experience"] = ProvenanceEntry(SOURCE, "direct_field", raw)
        except ValueError:
            pass


def _extract_experience(data: dict, rec: IntermediateRecord):
    raw_list = _get(data, "employment_history", "experience", "work_history",
                    "candidate.employment_history", "positions", default=[])
    if not isinstance(raw_list, list):
        return

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        company = str(_get(item, "employer", "company", "company_name",
                           "organization", default="") or "")
        title   = str(_get(item, "title", "job_title", "position", default="") or "")
        start   = normalize_date(str(_get(item, "start_date", "start", default="") or ""))
        end     = normalize_date(str(_get(item, "end_date",   "end",   default="") or ""))
        summary = _get(item, "description", "summary", "notes")
        loc     = _get(item, "location")
        emp_type = _get(item, "employment_type", "type")

        if company or title:
            rec.experience.append(ExperienceEntry(
                company=company, title=title, start=start, end=end,
                summary=str(summary) if summary else None,
                location=str(loc) if loc else None,
                employment_type=str(emp_type) if emp_type else None,
            ))

    if rec.experience:
        rec.provenance["experience"] = ProvenanceEntry(
            SOURCE, "direct_field", f"{len(rec.experience)} entries"
        )


def _extract_education(data: dict, rec: IntermediateRecord):
    raw_list = _get(data, "education", "education_history",
                    "candidate.education", "schools", default=[])
    if not isinstance(raw_list, list):
        return

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        institution = str(_get(item, "school_name", "institution",
                               "university", "college", default="") or "")
        degree      = _get(item, "degree", "degree_type", "degree_name")
        field_s     = _get(item, "discipline", "field_of_study", "major", "concentration")
        end_raw     = _get(item, "end_date", "end_year", "graduation_year")
        end_year: Optional[int] = None
        if end_raw:
            m = re.search(r"\d{4}", str(end_raw))
            if m:
                end_year = int(m.group())

        if institution:
            rec.education.append(EducationEntry(
                institution=institution,
                degree=str(degree) if degree else None,
                field_of_study=str(field_s) if field_s else None,
                end_year=end_year,
            ))

    if rec.education:
        rec.provenance["education"] = ProvenanceEntry(
            SOURCE, "direct_field", f"{len(rec.education)} entries"
        )


def _extract_skills(data: dict, rec: IntermediateRecord):
    raw = _get(data, "skills", "candidate.skills", "skill_set", default=[])
    if isinstance(raw, str):
        raw = re.split(r"[,;|]", raw)
    if not isinstance(raw, list):
        return

    parsed: List[str] = []
    for item in raw:
        name = item.get("name", "") if isinstance(item, dict) else str(item)
        canonical = normalize_skill(name.strip())
        if canonical and canonical not in parsed:
            parsed.append(canonical)

    if parsed:
        rec.skills = parsed
        rec.provenance["skills"] = ProvenanceEntry(
            SOURCE, "direct_field", f"{len(parsed)} skills"
        )


# ── Public entry point ────────────────────────────────────────────────────────

def extract_from_ats_json(path: str) -> List[IntermediateRecord]:
    """
    Parse an ATS JSON file. Returns one IntermediateRecord per candidate.
    Handles both a single candidate object and an array of candidates.
    """
    if not os.path.exists(path):
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"File not found: {path}"
        return [r]

    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"Invalid JSON: {exc}"
        return [r]
    except Exception as exc:
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"Cannot read file: {exc}"
        return [r]

    candidates = data if isinstance(data, list) else [data]
    records: List[IntermediateRecord] = []

    for cand_data in candidates:
        if not isinstance(cand_data, dict):
            continue
        rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        try:
            # Order matters: location before phones (country hint)
            _extract_name(cand_data, rec)
            _extract_location(cand_data, rec)
            _extract_emails(cand_data, rec)
            _extract_phones(cand_data, rec)
            _extract_links(cand_data, rec)
            _extract_headline(cand_data, rec)
            _extract_years_exp(cand_data, rec)
            _extract_experience(cand_data, rec)
            _extract_education(cand_data, rec)
            _extract_skills(cand_data, rec)
        except Exception as exc:
            rec.extraction_error = f"Extraction error: {exc}"

        records.append(rec)

    return records