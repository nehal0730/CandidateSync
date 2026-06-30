"""
extractors/csv_extractor.py
============================
Handles Recruiter CSV exports.

Design:
  - Tolerates flexible column names via a header alias table.
  - One IntermediateRecord per data row.
  - Never crashes on missing columns or malformed cells.
  - All normalization delegated to the normalizers package.
"""
import csv
import io
import os
import re
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    IntermediateRecord, ProvenanceEntry,
    ExperienceEntry, EducationEntry, LocationData, LinksData,
)
from normalizers import (
    normalize_email, normalize_phone, normalize_skill,
    normalize_url, normalize_name, normalize_country, normalize_date,
)

# ── Header alias table ────────────────────────────────────────────────────────
# Maps every known CSV column name variant → our canonical field name
_HEADER_ALIASES: Dict[str, str] = {
    # Name
    "name": "full_name", "full_name": "full_name",
    "candidate_name": "full_name", "applicant_name": "full_name",
    # Email
    "email": "email", "email_address": "email",
    "emails": "email", "e-mail": "email",
    # Phone
    "phone": "phone", "phone_number": "phone",
    "mobile": "phone", "mobile_number": "phone", "telephone": "phone",
    # Company / Title
    "current_company": "company", "company": "company",
    "employer": "company", "organization": "company",
    "title": "title", "job_title": "title",
    "position": "title", "current_title": "title", "role": "title",
    # Location
    "location": "location", "city": "city",
    "state": "region", "region": "region", "province": "region",
    "country": "country",
    # Links
    "linkedin": "linkedin", "linkedin_url": "linkedin",
    "linkedin_profile": "linkedin",
    "github": "github", "github_url": "github",
    "portfolio": "portfolio", "website": "portfolio",
    # Skills
    "skills": "skills", "skill_set": "skills", "technologies": "skills",
    "tech_stack": "skills",
    # Headline / summary
    "headline": "headline", "summary": "headline",
    "bio": "headline", "about": "headline",
    # Years experience
    "years_exp": "years_exp", "years_experience": "years_exp",
    "experience_years": "years_exp", "yoe": "years_exp",
    # Education
    "education": "education", "degree": "degree",
    "university": "institution", "college": "institution",
    "institution": "institution",
    # Start/End dates for experience
    "start_date": "start_date", "end_date": "end_date",
}

SOURCE = "recruiter_csv"
WEIGHT = 0.75


def _map_headers(raw_headers: List[str]) -> Dict[str, str]:
    """Returns {original_header: canonical_name}."""
    result = {}
    for h in (raw_headers or []):
        key = h.strip().lower().replace(" ", "_").replace("-", "_")
        result[h] = _HEADER_ALIASES.get(key, key)
    return result


def _safe_float(s: str) -> Optional[float]:
    if not s:
        return None
    nums = re.findall(r"[\d.]+", s)
    try:
        return float(nums[0]) if nums else None
    except ValueError:
        return None


def extract_from_csv(path: str) -> List[IntermediateRecord]:
    """
    Parse a Recruiter CSV and return one IntermediateRecord per candidate row.
    Returns a single error record if the file cannot be read.
    """
    if not os.path.exists(path):
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"File not found: {path}"
        return [r]

    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"Cannot read file: {exc}"
        return [r]

    records: List[IntermediateRecord] = []
    try:
        reader = csv.DictReader(io.StringIO(content))
        hmap   = _map_headers(reader.fieldnames or [])

        for row in reader:
            # Remap row keys to canonical names; strip whitespace
            norm = {hmap.get(k, k): (v.strip() if v else "") for k, v in row.items()}
            rec  = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
            prov = rec.provenance  # Dict[str, ProvenanceEntry]

            # ── Full name ─────────────────────────────────────────────────
            raw_name = norm.get("full_name", "")
            if raw_name:
                rec.full_name = normalize_name(raw_name)
                prov["full_name"] = ProvenanceEntry(SOURCE, "direct_field", raw_name)

            # ── Email ─────────────────────────────────────────────────────
            raw_email = norm.get("email", "")
            if raw_email:
                e = normalize_email(raw_email)
                if e:
                    rec.emails.append(e)
                    prov["emails"] = ProvenanceEntry(SOURCE, "direct_field", raw_email)

            # ── Location (parsed BEFORE phone, so its country can be used
            #    as the phone's country_hint — handles the common case
            #    where country only appears in a combined "City, Country"
            #    location field rather than a separate country column) ──────
            city    = norm.get("city", "")
            region  = norm.get("region", "")
            country = normalize_country(norm.get("country", ""))

            # Try splitting a combined "location" field "City, Country"
            if not city and norm.get("location"):
                parts = [p.strip() for p in norm["location"].split(",")]
                if len(parts) >= 2:
                    city    = parts[0]
                    country = normalize_country(parts[-1]) or country
                elif parts:
                    city = parts[0]

            if city or region or country:
                rec.location = LocationData(city=city or None,
                                            region=region or None,
                                            country=country)
                prov["location"] = ProvenanceEntry(SOURCE, "direct_field",
                                                   norm.get("location", f"{city},{country}"))

            # ── Phone ─────────────────────────────────────────────────────
            raw_phone    = norm.get("phone", "")
            country_hint = country or "US"
            if raw_phone:
                p = normalize_phone(raw_phone, country_hint)
                if p:
                    rec.phones.append(p)
                    prov["phones"] = ProvenanceEntry(SOURCE, "regex_parse", raw_phone)
                else:
                    prov["phones"] = ProvenanceEntry(SOURCE, "parse_failed", raw_phone)

            # ── Links ─────────────────────────────────────────────────────
            links_dict: Dict[str, Optional[str]] = {}
            for link_field in ("linkedin", "github", "portfolio"):
                raw_link = norm.get(link_field, "")
                if raw_link:
                    links_dict[link_field] = normalize_url(raw_link)
            if links_dict:
                rec.links = LinksData(**links_dict)
                prov["links"] = ProvenanceEntry(SOURCE, "direct_field", str(links_dict))

            # ── Headline ──────────────────────────────────────────────────
            raw_headline = norm.get("headline", "")
            if raw_headline:
                rec.headline = raw_headline[:160]
                prov["headline"] = ProvenanceEntry(SOURCE, "direct_field", raw_headline)

            # ── Years experience ──────────────────────────────────────────
            yexp = _safe_float(norm.get("years_exp", ""))
            if yexp is not None:
                rec.years_experience = yexp
                prov["years_experience"] = ProvenanceEntry(SOURCE, "direct_field",
                                                           norm.get("years_exp", ""))

            # ── Experience (company + title from this row) ─────────────────
            company = norm.get("company", "")
            title   = norm.get("title", "")
            if company or title:
                start = normalize_date(norm.get("start_date", ""))
                end   = normalize_date(norm.get("end_date", ""))
                rec.experience.append(ExperienceEntry(
                    company=company, title=title, start=start, end=end,
                ))
                prov["experience"] = ProvenanceEntry(SOURCE, "direct_field",
                                                     f"{title} @ {company}")

            # ── Skills (comma/semicolon/pipe-separated) ────────────────────
            raw_skills = norm.get("skills", "")
            if raw_skills:
                parsed: List[str] = []
                for s in re.split(r"[,;|]", raw_skills):
                    canonical = normalize_skill(s.strip())
                    if canonical:
                        parsed.append(canonical)
                rec.skills = list(dict.fromkeys(parsed))  # deduplicate, preserve order
                prov["skills"] = ProvenanceEntry(SOURCE, "direct_field", raw_skills)

            records.append(rec)

    except Exception as exc:
        r = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        r.extraction_error = f"Parse error: {exc}"
        records.append(r)

    return records