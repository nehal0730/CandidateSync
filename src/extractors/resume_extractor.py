"""
extractors/resume_extractor.py
================================
Handles Resume files: PDF (via pdfplumber) and DOCX (via python-docx).

Design (fully deterministic — no LLM):
  1. Extract raw text from the file.
  2. Run a section splitter that identifies common resume section headings
     (Experience, Education, Skills, Contact, Summary, etc.) using regex.
  3. Apply field-specific regex patterns to each section.
  4. Normalize all extracted values via the normalizers package.

Limitations (by design — explicitly descoped):
  - Multi-column PDF layouts may produce merged text → tolerant parsing handles it.
  - Non-English resumes → English patterns only.
"""
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from models import (
    IntermediateRecord, ProvenanceEntry,
    ExperienceEntry, EducationEntry, LocationData, LinksData,
)
from normalizers import (
    normalize_email, normalize_phone, normalize_skill,
    normalize_url, normalize_name, normalize_country, normalize_date,
)

SOURCE = "resume_pdf"
WEIGHT = 0.70

# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text_pdf(path: str) -> str:
    """Use pdfplumber for reliable text extraction with layout."""
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                pages.append(text)
    return "\n".join(pages)


def _extract_text_docx(path: str) -> str:
    """Use python-docx for DOCX text extraction."""
    from docx import Document
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _extract_text_pdf(path)
    elif ext in (".docx", ".doc"):
        return _extract_text_docx(path)
    elif ext == ".txt":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported resume format: {ext}")


# ── Section splitter ──────────────────────────────────────────────────────────

# Section heading patterns — captures the section name and its content
_SECTION_HEADINGS = re.compile(
    r"^(?P<heading>"
    r"(?:EXPERIENCE|WORK\s+EXPERIENCE|EMPLOYMENT|PROFESSIONAL\s+EXPERIENCE"
    r"|EDUCATION|ACADEMIC\s+BACKGROUND|QUALIFICATIONS"
    r"|SKILLS|TECHNICAL\s+SKILLS|CORE\s+COMPETENCIES|TECHNOLOGIES"
    r"|CONTACT|PERSONAL\s+INFORMATION|PROFILE|SUMMARY|OBJECTIVE|ABOUT"
    r"|CERTIFICATIONS?|AWARDS?|PROJECTS?|PUBLICATIONS?|LANGUAGES?"
    r"))\s*[:\-]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _split_sections(text: str) -> Dict[str, str]:
    """
    Split resume text into {section_name: content} dict.
    Section names are normalized to uppercase canonical keys.
    """
    sections: Dict[str, str] = {"__header__": ""}
    current = "__header__"
    lines = text.split("\n")

    for line in lines:
        stripped = line.strip()
        m = _SECTION_HEADINGS.match(stripped)
        if m:
            heading = m.group("heading").upper().strip()
            # Normalize heading aliases
            if re.search(r"EXPERIENCE|EMPLOYMENT|WORK", heading):
                heading = "EXPERIENCE"
            elif re.search(r"EDUCATION|ACADEMIC|QUALIFICATIONS", heading):
                heading = "EDUCATION"
            elif re.search(r"SKILL|COMPETEN|TECHNOLOG", heading):
                heading = "SKILLS"
            elif re.search(r"CONTACT|PERSONAL", heading):
                heading = "CONTACT"
            elif re.search(r"SUMMARY|OBJECTIVE|PROFILE|ABOUT", heading):
                heading = "SUMMARY"
            current = heading
            sections.setdefault(current, "")
        else:
            sections[current] = sections.get(current, "") + line + "\n"

    return sections


# ── Field-specific regex extractors ──────────────────────────────────────────

_EMAIL_RE    = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_PHONE_RE    = re.compile(
    r"(?:\+?\d[\s\-.()\d]{7,}\d)"  # general international
)
_URL_RE      = re.compile(
    r"(?:https?://|www\.|linkedin\.com|github\.com)"
    r"[^\s,;\"'<>()]+",
    re.IGNORECASE,
)
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)
_GITHUB_RE   = re.compile(r"github\.com/[\w\-]+", re.IGNORECASE)
_YEARS_RE    = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", re.IGNORECASE)

# Date range: "Jan 2020 – Mar 2023"  or  "2019 - present"
_DATE_RANGE_RE = re.compile(
    r"(?P<start>[A-Za-z]+\.?\s*\d{4}|\d{4})"
    r"\s*[–—\-–to]+\s*"
    r"(?P<end>[A-Za-z]+\.?\s*\d{4}|\d{4}|[Pp]resent|[Cc]urrent|[Nn]ow)",
)


def _parse_contact(header_text: str, contact_text: str,
                   rec: IntermediateRecord) -> None:
    """Extract name, emails, phones, links from header + contact sections."""
    full_text = header_text + "\n" + contact_text
    lines = [l.strip() for l in full_text.split("\n") if l.strip()]

    # ── Emails ──────────────────────────────────────────────────────────────
    for raw in _EMAIL_RE.findall(full_text):
        norm = normalize_email(raw)
        if norm and norm not in rec.emails:
            rec.emails.append(norm)
    if rec.emails:
        rec.provenance["emails"] = ProvenanceEntry(SOURCE, "regex_parse", rec.emails[0])

    # ── Phones ──────────────────────────────────────────────────────────────
    for raw in _PHONE_RE.findall(full_text):
        clean = re.sub(r"[^\d+]", "", raw)
        if len(clean) >= 7:
            country_hint = (rec.location.country if rec.location else None) or "US"
            norm = normalize_phone(raw, country_hint)
            if norm and norm not in rec.phones:
                rec.phones.append(norm)
            elif not norm and raw.strip():
                rec.provenance["phones"] = ProvenanceEntry(SOURCE, "parse_failed", raw)
    if rec.phones:
        rec.provenance["phones"] = ProvenanceEntry(SOURCE, "regex_parse", rec.phones[0])

    # ── LinkedIn / GitHub ────────────────────────────────────────────────────
    links: Dict[str, Optional[str]] = {}
    m_li = _LINKEDIN_RE.search(full_text)
    if m_li:
        links["linkedin"] = normalize_url(m_li.group())
    m_gh = _GITHUB_RE.search(full_text)
    if m_gh:
        links["github"] = normalize_url(m_gh.group())
    # Other URLs
    other_urls = [normalize_url(u) for u in _URL_RE.findall(full_text)
                  if "linkedin" not in u.lower() and "github" not in u.lower()]
    if other_urls:
        links["portfolio"] = other_urls[0]
    if links:
        rec.links = LinksData(**links)
        rec.provenance["links"] = ProvenanceEntry(SOURCE, "regex_parse", str(links))

    # ── Name: first non-email, non-URL, non-phone line in header ────────────
    for line in lines[:8]:
        if (not _EMAIL_RE.search(line)
                and not _PHONE_RE.search(line)
                and not _URL_RE.search(line)
                and len(line.split()) >= 2
                and len(line) < 60
                and not line.upper() == line):  # not all-caps heading
            rec.full_name = normalize_name(line)
            rec.provenance["full_name"] = ProvenanceEntry(SOURCE, "rule_extraction", line)
            break

    # ── Years experience from header/summary ─────────────────────────────────
    m_yexp = _YEARS_RE.search(full_text)
    if m_yexp:
        try:
            rec.years_experience = float(m_yexp.group(1))
            rec.provenance["years_experience"] = ProvenanceEntry(
                SOURCE, "regex_parse", m_yexp.group()
            )
        except ValueError:
            pass


def _parse_summary(text: str, rec: IntermediateRecord) -> None:
    """Extract headline from summary/objective section."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        headline = " ".join(lines[:2])[:160]
        if headline:
            rec.headline = headline
            rec.provenance["headline"] = ProvenanceEntry(SOURCE, "rule_extraction", headline)

    if not rec.years_experience:
        m = _YEARS_RE.search(text)
        if m:
            try:
                rec.years_experience = float(m.group(1))
                rec.provenance["years_experience"] = ProvenanceEntry(
                    SOURCE, "regex_parse", m.group()
                )
            except ValueError:
                pass


def _parse_skills(text: str, rec: IntermediateRecord) -> None:
    """
    Extract skills from the Skills section.
    Strategy: split on common delimiters (comma, bullet, pipe, newline),
    normalize each token.
    """
    # Remove common decorators
    clean = re.sub(r"[•·▪▸►‣●]", ",", text)
    tokens = re.split(r"[,;|\n]", clean)

    parsed: List[str] = []
    for token in tokens:
        t = token.strip()
        if not t or len(t) > 50:
            continue
        # Skip tokens that look like sentences
        if len(t.split()) > 5:
            continue
        canonical = normalize_skill(t)
        if canonical and canonical not in parsed:
            parsed.append(canonical)

    if parsed:
        rec.skills = list(dict.fromkeys(rec.skills + parsed))
        rec.provenance["skills"] = ProvenanceEntry(
            SOURCE, "rule_extraction", f"{len(parsed)} skills parsed"
        )


def _parse_experience(text: str, rec: IntermediateRecord) -> None:
    """
    Parse experience section into ExperienceEntry objects.

    Strategy:
      - Split into blocks by date-range anchors (most reliable delimiter).
      - Title/Company are taken ONLY from the 1-2 lines immediately
        preceding the date line that have not already been consumed by a
        prior entry (prevents bleeding a previous job's summary text into
        the next job's title/company).
      - Summary text is everything between this date line and the next
        date-range anchor (or section end).
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    entries: List[ExperienceEntry] = []
    consumed_until = 0   # index up to which lines have already been used

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _DATE_RANGE_RE.search(line)
        if m:
            start = normalize_date(m.group("start"))
            end   = normalize_date(m.group("end"))

            # Title / company: only from unconsumed lines immediately BEFORE
            # the date line (never look forward — that belongs to the
            # previous entry's summary or this entry's own summary).
            back_start = max(consumed_until, i - 2)
            back_lines = [
                l for l in lines[back_start:i]
                if not _EMAIL_RE.search(l) and not _DATE_RANGE_RE.search(l)
            ]

            title, company = "", ""
            if len(back_lines) >= 2:
                title, company = back_lines[-2][:100], back_lines[-1][:100]
            elif len(back_lines) == 1:
                title = back_lines[-1][:100]

            # Summary: lines after the date block until next date anchor,
            # reserving the last 1-2 lines immediately before that next date
            # (they are the next entry's title/company, not this entry's
            # summary).
            j = i + 1
            while j < len(lines) and not _DATE_RANGE_RE.search(lines[j]):
                j += 1
            summary_end = j
            if j < len(lines):  # a next date anchor exists — reserve back-window
                summary_end = max(i + 1, j - 2)
            summary_lines = lines[i + 1: summary_end]
            summary = " ".join(summary_lines[:5]).strip()[:500] or None

            if company or title:
                entries.append(ExperienceEntry(
                    company=company, title=title,
                    start=start, end=end,
                    summary=summary,
                ))
            # Only lines actually used as summary are "consumed" — the
            # reserved title/company lines (summary_end .. j-1) remain
            # visible to the back-window search of the next date anchor at j.
            consumed_until = summary_end
            i = j
        else:
            i += 1

    if entries:
        # Merge with existing (from earlier sections), newest first
        rec.experience = entries + rec.experience
        rec.provenance["experience"] = ProvenanceEntry(
            SOURCE, "rule_extraction", f"{len(entries)} entries"
        )


def _parse_education(text: str, rec: IntermediateRecord) -> None:
    """
    Parse education section.
    Looks for degree keywords and year patterns.
    """
    _DEGREE_RE = re.compile(
        r"\b(B\.?(?:Tech|E|S|Sc|A|Eng)|M\.?(?:Tech|E|S|Sc|A|Eng|BA)|"
        r"Ph\.?D|MBA|B\.Com|BCA|MCA|Bachelor|Master|Doctor|Associate)\b",
        re.IGNORECASE,
    )
    # Non-capturing group so findall() returns the FULL 4-digit year, not
    # just the captured "19"/"20" prefix (a real bug found via DOCX testing:
    # capturing groups make re.findall() return only the group contents).
    _YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
    _FIELD_RE = re.compile(
        r"\b(Computer\s+Science|Information\s+Technology|Engineering|"
        r"Mathematics|Physics|Business|Economics|Finance|"
        r"Data\s+Science|AI|Machine\s+Learning|Electronics)\b",
        re.IGNORECASE,
    )
    # Institution keyword list expanded to cover common abbreviations
    # (NIT, IIT, IIM, BITS, etc.) in addition to full words — real resumes
    # frequently abbreviate institution names rather than spelling out
    # "institute"/"university" in full.
    _INSTITUTION_KEYWORDS = (
        "university", "college", "institute", "school", "academy",
        "polytechnic", "iit", "nit", "iiit", "iim", "bits ",
    )

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    entries: List[EducationEntry] = []

    for i, line in enumerate(lines):
        deg_m = _DEGREE_RE.search(line)
        if deg_m:
            degree = deg_m.group()
            institution = ""
            field_s = ""

            # Look for institution in surrounding lines (skip the degree
            # line itself unless it's the only candidate — institution
            # name is usually a separate line).
            ctx_window = lines[max(0, i-2):i+3]
            for ctx in ctx_window:
                if any(kw in ctx.lower() for kw in _INSTITUTION_KEYWORDS):
                    institution = ctx[:100]
                    break

            # Field of study
            field_m = _FIELD_RE.search(line)
            if not field_m:
                for ctx in lines[max(0, i-1):i+2]:
                    field_m = _FIELD_RE.search(ctx)
                    if field_m:
                        break
            if field_m:
                field_s = field_m.group()

            # End year — look near the degree line
            end_year = None
            years_near = _YEAR_RE.findall(
                " ".join(lines[max(0, i-2):i+3])
            )
            if years_near:
                end_year = int(max(years_near))   # take latest year

            entries.append(EducationEntry(
                institution=institution,
                degree=degree,
                field_of_study=field_s or None,
                end_year=end_year,
            ))

    if entries:
        rec.education = entries
        rec.provenance["education"] = ProvenanceEntry(
            SOURCE, "rule_extraction", f"{len(entries)} entries"
        )


# ── Public entry point ────────────────────────────────────────────────────────

def extract_from_resume(path: str) -> List[IntermediateRecord]:
    """
    Parse a resume file (PDF/DOCX/TXT) and return a single IntermediateRecord.
    All extraction is deterministic: pdfplumber + regex + rule-based parsing.
    """
    rec = IntermediateRecord(source_name=SOURCE, source_weight=WEIGHT)

    if not os.path.exists(path):
        rec.extraction_error = f"File not found: {path}"
        return [rec]

    try:
        text = _extract_text(path)
    except Exception as exc:
        rec.extraction_error = f"Cannot extract text: {exc}"
        return [rec]

    if not text or not text.strip():
        rec.extraction_error = "Empty text extracted from resume"
        return [rec]

    try:
        sections = _split_sections(text)

        header_text  = sections.get("__header__", "")
        contact_text = sections.get("CONTACT", "")
        summary_text = sections.get("SUMMARY", "")
        skills_text  = sections.get("SKILLS", "")
        exp_text     = sections.get("EXPERIENCE", "")
        edu_text     = sections.get("EDUCATION", "")

        # Extract in order — contact/header gives us identity fields
        _parse_contact(header_text, contact_text, rec)
        if summary_text:
            _parse_summary(summary_text, rec)
        if skills_text:
            _parse_skills(skills_text, rec)
        if exp_text:
            _parse_experience(exp_text, rec)
        if edu_text:
            _parse_education(edu_text, rec)

    except Exception as exc:
        rec.extraction_error = f"Parsing error: {exc}"

    return [rec]