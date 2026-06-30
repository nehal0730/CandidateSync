"""
models.py
=========
All data-model dataclasses for the transformer pipeline.

Hierarchy:
  IntermediateRecord  → output of each individual extractor
  CanonicalRecord     → single merged record per candidate
  ProjectedRecord     → final output after config projection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Sub-structures shared across record types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LocationData:
    city:    Optional[str] = None
    region:  Optional[str] = None
    country: Optional[str] = None   # ISO-3166 alpha-2

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"city": self.city, "region": self.region, "country": self.country}

    @staticmethod
    def from_dict(d: dict) -> "LocationData":
        return LocationData(
            city=d.get("city"), region=d.get("region"), country=d.get("country")
        )


@dataclass
class LinksData:
    linkedin:  Optional[str] = None
    github:    Optional[str] = None
    portfolio: Optional[str] = None
    other:     List[str]     = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "linkedin":  self.linkedin,
            "github":    self.github,
            "portfolio": self.portfolio,
            "other":     self.other,
        }

    @staticmethod
    def from_dict(d: dict) -> "LinksData":
        return LinksData(
            linkedin=d.get("linkedin"),
            github=d.get("github"),
            portfolio=d.get("portfolio"),
            other=d.get("other", []),
        )


@dataclass
class ExperienceEntry:
    company:         str            = ""
    title:           str            = ""
    start:           Optional[str]  = None   # YYYY-MM
    end:             Optional[str]  = None   # YYYY-MM  (None = present)
    summary:         Optional[str]  = None
    location:        Optional[str]  = None   # free-text
    employment_type: Optional[str]  = None   # "full-time" | "contract" | etc.

    def to_dict(self) -> Dict[str, Any]:
        return {
            "company":         self.company   or None,
            "title":           self.title     or None,
            "start":           self.start,
            "end":             self.end,
            "summary":         self.summary,
            "location":        self.location,
            "employment_type": self.employment_type,
        }


@dataclass
class EducationEntry:
    institution:    str           = ""
    degree:         Optional[str] = None
    field_of_study: Optional[str] = None
    end_year:       Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "institution": self.institution or None,
            "degree":      self.degree,
            "field":       self.field_of_study,
            "end_year":    self.end_year,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Provenance entry — one per field, stored as field-wise dict in CanonicalRecord
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProvenanceEntry:
    source: str          # "ats_json" | "recruiter_csv" | "resume_pdf" | "recruiter_notes"
    method: str          # "direct_field" | "regex_parse" | "rule_extraction" | "derived" | "parse_failed"
    raw_value: Any = None


# ─────────────────────────────────────────────────────────────────────────────
# IntermediateRecord — output of a single extractor
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IntermediateRecord:
    """
    Raw output from one extractor.
    Only fields the extractor could reliably find are populated.
    Arrays (emails, phones, skills) are lists of already-normalized strings.
    """
    source_name:   str   = ""    # e.g. "recruiter_csv"
    source_weight: float = 0.5   # reliability weight 0–1

    full_name:        Optional[str]          = None
    emails:           List[str]              = field(default_factory=list)
    phones:           List[str]              = field(default_factory=list)
    location:         Optional[LocationData] = None
    links:            Optional[LinksData]    = None
    headline:         Optional[str]          = None
    years_experience: Optional[float]        = None

    skills:     List[str]             = field(default_factory=list)   # canonical names
    experience: List[ExperienceEntry] = field(default_factory=list)
    education:  List[EducationEntry]  = field(default_factory=list)

    # field_name → ProvenanceEntry (populated by extractors)
    provenance: Dict[str, ProvenanceEntry] = field(default_factory=dict)

    extraction_error: Optional[str] = None   # non-fatal error description


# ─────────────────────────────────────────────────────────────────────────────
# CanonicalRecord — single merged, confidence-scored profile
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CanonicalRecord:
    """
    The single source of truth after merging all IntermediateRecords
    for one candidate.  Never modified by the projection layer.
    """
    candidate_id:     str                       = ""
    full_name:        Optional[str]             = None
    emails:           List[str]                 = field(default_factory=list)
    phones:           List[str]                 = field(default_factory=list)
    location:         Optional[LocationData]    = None
    links:            Optional[LinksData]       = None
    headline:         Optional[str]             = None
    years_experience: Optional[float]           = None

    skills:     List[str]             = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education:  List[EducationEntry]  = field(default_factory=list)

    # Separate confidence and provenance objects (field-wise dicts)
    confidence:       Dict[str, float]          = field(default_factory=dict)
    provenance:       Dict[str, Dict[str, Any]] = field(default_factory=dict)
    overall_confidence: float                   = 0.0

    # Metadata for auditability
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id":      self.candidate_id,
            "full_name":         self.full_name,
            "emails":            self.emails,
            "phones":            self.phones,
            "location":          self.location.to_dict() if self.location else None,
            "links":             self.links.to_dict() if self.links else None,
            "headline":          self.headline,
            "years_experience":  self.years_experience,
            "skills":            self.skills,
            "experience":        [e.to_dict() for e in self.experience],
            "education":         [e.to_dict() for e in self.education],
            "confidence":        self.confidence,
            "provenance":        self.provenance,
            "overall_confidence":self.overall_confidence,
            "metadata":          self.metadata,
        }