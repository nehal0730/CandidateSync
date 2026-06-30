"""
extractors/notes_extractor.py
===============================
Handles free-text Recruiter Notes (.txt files).

Design (fully deterministic — no LLM):
  - Rule-based extraction using labeled patterns ("Name: ...", "Email: ...")
  - Falls back to generic regex for emails, phones, URLs.
  - Skills extracted by matching against a known keyword vocabulary.
  - Lowest source weight (0.50) — informal, paraphrased content.
"""
import os
import re
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    IntermediateRecord, ProvenanceEntry,
    LocationData, LinksData,
)
from normalizers import (
    normalize_email, normalize_phone, normalize_skill, normalize_url,
    normalize_name, normalize_country, normalize_date, SKILL_ALIAS_MAP,
    infer_country_from_city_mention,
)

SOURCE = "recruiter_notes"
WEIGHT = 0.50

# ── Labeled field patterns ────────────────────────────────────────────────────
# Matches "Label: value" or "Label - value" style lines (case-insensitive)
_LABELED = re.compile(
    r"^(?P<label>"
    r"name|full\s*name|candidate|"
    r"email|e-?mail|"
    r"phone|mobile|tel(?:ephone)?|"
    r"location|city|country|"
    r"linkedin|github|portfolio|website|"
    r"skills?|tech(?:nologies)?|stack|"
    r"headline|title|role|position|"
    r"company|employer|organization|"
    r"summary|note|notes|about|"
    r"years?\s+(?:of\s+)?exp(?:erience)?"
    r")\s*[:\-–]\s*(?P<value>.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# Generic regex patterns
_EMAIL_RE    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# Phone regex: requires at least 3 separator-grouped digit clusters or a leading
# '+' to avoid matching plain dates like "2024-05-12" or "30 mins".
_PHONE_RE    = re.compile(
    r"(?:\+\d[\d\s\-.()]{6,}\d)"          # international, starts with '+'
    r"|(?:\b\d{10}\b)"                     # bare 10-digit local number
    r"|(?:\b\d{3,5}[\s\-]\d{3,5}[\s\-]\d{3,5}\b)"  # grouped e.g. 987-654-3210
)
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)
_GITHUB_RE   = re.compile(r"github\.com/[\w\-]+", re.IGNORECASE)
_YEARS_RE    = re.compile(
    r"(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?\s+exp(?:erience)?", re.IGNORECASE
)

# Block delimiter: a line of 3+ dashes/underscores/equals, or two+ blank lines,
# or a new "Name:" label appearing after content has already started — any of
# these signal a new candidate block within the same notes file.
_BLOCK_DELIM_RE = re.compile(r"^[\-_=]{3,}\s*$")

# Skill vocabulary: check the alias map keys + a few extra common terms
_SKILL_VOCAB = set(SKILL_ALIAS_MAP.keys()) | {
    "python", "java", "javascript", "typescript", "react", "angular", "vue",
    "node.js", "django", "flask", "fastapi", "spring", "rails",
    "sql", "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "docker", "kubernetes", "aws", "gcp", "azure", "terraform",
    "machine learning", "deep learning", "nlp", "data science",
    "tensorflow", "pytorch", "scikit-learn", "pandas", "numpy",
    "git", "ci/cd", "rest", "graphql", "microservices",
    "html", "css", "sass", "webpack", "vite",
    "c", "c++", "c#", "go", "rust", "kotlin", "swift", "php",
    "hadoop", "spark", "kafka", "airflow",
    "linux", "bash", "powershell",
}


def _prescan_country_hint(text: str) -> str:
    """
    Scan the whole block for a location/country mention to use as the
    phone country_hint. Falls back to 'US' if nothing is found.

    Order of precedence:
      1. Explicit "Location:"/"City:"/"Country:" labeled line.
      2. A known major city name mentioned anywhere in the block
         (handles informal phrasing like "Based in Hyderabad").
      3. Default: "US".

    Looking at the whole block (not just an already-seen Location: line)
    matters because labeled fields can appear in any order.
    """
    loc_match = re.search(
        r"(?:location|city|country)\s*[:\-–]\s*(.+)", text, re.IGNORECASE
    )
    if loc_match:
        parts = [p.strip() for p in loc_match.group(1).split(",")]
        candidate = normalize_country(parts[-1]) if len(parts) > 1 else normalize_country(parts[0])
        if candidate:
            return candidate

    city_inferred = infer_country_from_city_mention(text)
    if city_inferred:
        return city_inferred

    return "US"


def _extract_labeled_fields(text: str, rec: IntermediateRecord) -> None:
    """Extract explicitly labeled key-value pairs from notes text."""
    country_hint = _prescan_country_hint(text)

    for m in _LABELED.finditer(text):
        label = m.group("label").strip().lower().replace(" ", "")
        value = m.group("value").strip()
        if not value:
            continue

        if re.match(r"name|fullname|candidate", label):
            rec.full_name = normalize_name(value)
            rec.provenance["full_name"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"email|e-?mail", label):
            e = normalize_email(value)
            if e and e not in rec.emails:
                rec.emails.append(e)
                rec.provenance["emails"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"phone|mobile|tel", label):
            p = normalize_phone(value, country_hint)
            if p and p not in rec.phones:
                rec.phones.append(p)
                rec.provenance["phones"] = ProvenanceEntry(SOURCE, "rule_extraction", value)
            elif value:
                rec.provenance["phones"] = ProvenanceEntry(SOURCE, "parse_failed", value)

        elif re.match(r"location|city|country", label):
            parts = [x.strip() for x in value.split(",")]
            city    = parts[0] if parts else None
            country = normalize_country(parts[-1]) if len(parts) > 1 else None
            rec.location = LocationData(city=city, country=country)
            rec.provenance["location"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"linkedin", label):
            url = normalize_url(value)
            if url:
                rec.links = rec.links or LinksData()
                rec.links.linkedin = url
                rec.provenance["links"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"github", label):
            url = normalize_url(value)
            if url:
                rec.links = rec.links or LinksData()
                rec.links.github = url

        elif re.match(r"portfolio|website", label):
            url = normalize_url(value)
            if url:
                rec.links = rec.links or LinksData()
                rec.links.portfolio = url

        elif re.match(r"skill|tech|stack", label):
            for token in re.split(r"[,;|]", value):
                canonical = normalize_skill(token.strip())
                if canonical and canonical not in rec.skills:
                    rec.skills.append(canonical)
            rec.provenance["skills"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"headline|title|role|position", label):
            rec.headline = value[:160]
            rec.provenance["headline"] = ProvenanceEntry(SOURCE, "rule_extraction", value)

        elif re.match(r"years?", label):
            nums = re.findall(r"[\d.]+", value)
            if nums:
                try:
                    rec.years_experience = float(nums[0])
                    rec.provenance["years_experience"] = ProvenanceEntry(
                        SOURCE, "rule_extraction", value
                    )
                except ValueError:
                    pass


def _extract_generic_patterns(text: str, rec: IntermediateRecord) -> None:
    """Fall-back: generic regex for emails, phones, URLs from free text."""
    country_hint = _prescan_country_hint(text)

    # Emails
    for raw in _EMAIL_RE.findall(text):
        e = normalize_email(raw)
        if e and e not in rec.emails:
            rec.emails.append(e)
            if "emails" not in rec.provenance:
                rec.provenance["emails"] = ProvenanceEntry(SOURCE, "regex_parse", raw)

    # Phones
    for raw in _PHONE_RE.findall(text):
        p = normalize_phone(raw, country_hint)
        if p and p not in rec.phones:
            rec.phones.append(p)
            if "phones" not in rec.provenance:
                rec.provenance["phones"] = ProvenanceEntry(SOURCE, "regex_parse", raw)

    # LinkedIn
    m_li = _LINKEDIN_RE.search(text)
    if m_li:
        url = normalize_url(m_li.group())
        if url:
            rec.links = rec.links or LinksData()
            if not rec.links.linkedin:
                rec.links.linkedin = url
                rec.provenance["links"] = ProvenanceEntry(SOURCE, "regex_parse", m_li.group())

    # GitHub
    m_gh = _GITHUB_RE.search(text)
    if m_gh:
        url = normalize_url(m_gh.group())
        if url:
            rec.links = rec.links or LinksData()
            if not rec.links.github:
                rec.links.github = url

    # Years experience
    if not rec.years_experience:
        m_y = _YEARS_RE.search(text)
        if m_y:
            try:
                rec.years_experience = float(m_y.group(1))
                rec.provenance["years_experience"] = ProvenanceEntry(
                    SOURCE, "regex_parse", m_y.group()
                )
            except ValueError:
                pass


def _extract_skills_from_text(text: str, rec: IntermediateRecord) -> None:
    """
    Scan entire text for known skill keywords.
    Uses word-boundary matching to avoid false positives.
    """
    text_lower = text.lower()
    found = []
    for vocab_term in sorted(_SKILL_VOCAB, key=len, reverse=True):
        pattern = r"\b" + re.escape(vocab_term) + r"\b"
        if re.search(pattern, text_lower):
            canonical = normalize_skill(vocab_term)
            if canonical and canonical not in found and canonical not in rec.skills:
                found.append(canonical)

    if found:
        rec.skills = list(dict.fromkeys(rec.skills + found))
        if "skills" not in rec.provenance:
            rec.provenance["skills"] = ProvenanceEntry(
                SOURCE, "rule_extraction", f"{len(found)} skills from vocabulary scan"
            )


# ── Public entry point ────────────────────────────────────────────────────────

def _split_into_blocks(text: str) -> List[str]:
    """
    Split a recruiter notes file into per-candidate blocks.

    A new block starts when:
      - A horizontal rule line is encountered (---, ___, ===), or
      - A "Name:" labeled line appears after the current block already has
        content (signals a new candidate's notes beginning).

    If no delimiters are found, the entire file is treated as one block
    (single-candidate notes — the common case).
    """
    lines = text.split("\n")
    blocks: List[List[str]] = [[]]
    name_label_re = re.compile(r"^\s*(?:name|full\s*name|candidate)\s*[:\-–]", re.IGNORECASE)

    for line in lines:
        if _BLOCK_DELIM_RE.match(line.strip()):
            blocks.append([])
            continue

        current = blocks[-1]
        # New "Name:" line after current block already has non-trivial content
        # (more than just a date/title line) → start a new block.
        if name_label_re.match(line) and any(l.strip() for l in current[1:]):
            blocks.append([])

        blocks[-1].append(line)

    return ["\n".join(b) for b in blocks if any(l.strip() for l in b)]


def _extract_one_block(text: str) -> IntermediateRecord:
    """Run the full deterministic extraction pipeline on a single candidate block."""
    rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
    try:
        _extract_labeled_fields(text, rec)
        _extract_generic_patterns(text, rec)
        _extract_skills_from_text(text, rec)
    except Exception as exc:
        rec.extraction_error = f"Parsing error: {exc}"
    return rec


def extract_from_notes(path: str) -> List[IntermediateRecord]:
    """
    Parse a recruiter notes text file and return one IntermediateRecord per
    candidate block found in the file.

    A single notes file may contain notes about multiple candidates,
    separated by horizontal rules or repeated "Name:" labels — each block
    is parsed independently so that data from different candidates is never
    merged together.

    All extraction is deterministic: labeled patterns + regex + vocabulary
    matching. No LLM is used.
    """
    if not os.path.exists(path):
        rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        rec.extraction_error = f"File not found: {path}"
        return [rec]

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            full_text = f.read()
    except Exception as exc:
        rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        rec.extraction_error = f"Cannot read file: {exc}"
        return [rec]

    if not full_text.strip():
        rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)
        rec.extraction_error = "Empty notes file"
        return [rec]

    blocks = _split_into_blocks(full_text)
    if not blocks:
        blocks = [full_text]

    records = [_extract_one_block(block) for block in blocks]

    # Drop blocks that produced no usable signal at all (e.g. a lone
    # separator line) — these add noise without contributing any data.
    def _has_data(r: IntermediateRecord) -> bool:
        return bool(
            r.full_name or r.emails or r.phones or r.skills
            or r.headline or r.location or r.links
        )

    meaningful = [r for r in records if _has_data(r) or r.extraction_error]
    return meaningful or records[:1]