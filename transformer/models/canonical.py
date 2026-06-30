"""
canonical.py — Internal canonical data model.

This is the single source of truth for what a candidate record looks like
inside the pipeline. The projection layer produces *views* over this — it
never mutates these objects.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceEntry:
    """Full audit trail entry for a single field value."""
    field: str                          # Dot-path, e.g. "emails[0]", "skills[2].name"
    source: str                         # Source ID, e.g. "csv:recruiter_export.csv"
    method: str                         # "structured_field" | "api_field" | "regex" | "nlp_ner" | "inferred"
    raw_value: str                      # Exactly what was extracted before normalization
    normalized_value: Any               # What ended up in the canonical record
    confidence: float                   # [0.0, 1.0]
    extracted_at: datetime
    conflict: bool = False              # True if another source had a different value
    conflict_resolution: Optional[str] = None  # e.g. "source_priority:csv_wins"


# ---------------------------------------------------------------------------
# Sub-structures
# ---------------------------------------------------------------------------

@dataclass
class Location:
    city: Optional[str] = None
    region: Optional[str] = None        # State / Province
    country: Optional[str] = None       # ISO-3166-1 alpha-2, e.g. "US"
    raw: str = ""                       # Original string, never discarded

    def is_empty(self) -> bool:
        return not any([self.city, self.region, self.country])


@dataclass
class Skill:
    name: str                           # Canonical name from taxonomy
    aliases_seen: List[str] = field(default_factory=list)  # ["JS", "javascript"]
    confidence: float = 0.0
    sources: List[str] = field(default_factory=list)


@dataclass
class ExperienceEntry:
    company: str
    title: str
    start: Optional[str] = None         # YYYY-MM or None
    end: Optional[str] = None           # YYYY-MM or None (None = current if is_current)
    is_current: bool = False
    summary: Optional[str] = None
    source: str = ""                    # Which source provided this entry


@dataclass
class EducationEntry:
    institution: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    end_year: Optional[int] = None
    source: str = ""


@dataclass
class Links:
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Canonical Candidate
# ---------------------------------------------------------------------------

@dataclass
class CanonicalCandidate:
    """
    The immutable-after-merge internal representation of a candidate.

    Rules:
    - Never invent values. Unknown = None, never a guess.
    - All multi-value fields are lists, even if currently single-valued.
    - Provenance is a flat, queryable list — not nested inside each field.
    """

    # Identity
    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Core
    full_name: Optional[str] = None
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)       # E.164 format
    location: Optional[Location] = None
    headline: Optional[str] = None

    # Career
    years_experience: Optional[float] = None
    skills: List[Skill] = field(default_factory=list)
    experience: List[ExperienceEntry] = field(default_factory=list)
    education: List[EducationEntry] = field(default_factory=list)

    # Links
    links: Links = field(default_factory=Links)

    # Meta
    provenance: List[ProvenanceEntry] = field(default_factory=list)
    overall_confidence: float = 0.0
    sources_ingested: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    pipeline_version: str = "1.0.0"

    # Internal: raw extractions before merge (not emitted in output)
    _raw_extractions: List[dict] = field(default_factory=list, repr=False)

    def add_provenance(self, entry: ProvenanceEntry) -> None:
        self.provenance.append(entry)

    def provenance_for_field(self, field_path: str) -> List[ProvenanceEntry]:
        return [p for p in self.provenance if p.field == field_path]

    def provenance_for_source(self, source_id: str) -> List[ProvenanceEntry]:
        return [p for p in self.provenance if p.source == source_id]

    def conflicts(self) -> List[ProvenanceEntry]:
        return [p for p in self.provenance if p.conflict]

    def low_confidence_fields(self, threshold: float = 0.6) -> List[ProvenanceEntry]:
        return [p for p in self.provenance if p.confidence < threshold]


# ---------------------------------------------------------------------------
# Raw extraction (pre-normalization, one per source)
# ---------------------------------------------------------------------------

@dataclass
class RawExtraction:
    """
    What a source adapter produces. Immutable after creation.
    Normalization reads from this and produces NormalizedExtraction.
    """
    source_id: str                      # e.g. "csv:recruiter_export.csv"
    source_type: str                    # "csv" | "ats_json" | "github"
    extracted_at: datetime
    base_confidence: float              # Source-level confidence before field scoring

    # Raw fields — exactly as parsed, before any normalization
    full_name: Optional[str] = None
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    location_raw: Optional[str] = None
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills_raw: List[str] = field(default_factory=list)
    experience: List[dict] = field(default_factory=list)
    education: List[dict] = field(default_factory=list)
    links: dict = field(default_factory=dict)

    # Warnings accumulated during extraction
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Exceptions / warnings
# ---------------------------------------------------------------------------

class SourceUnavailableWarning(Exception):
    """Source file/API could not be reached. Pipeline continues."""

class SourceParseError(Exception):
    """Source data was malformed. Pipeline continues."""

class ExtractionPartialWarning(Exception):
    """Partial extraction — some fields were recovered."""

class ProjectionValidationError(Exception):
    """Projected output failed required-field validation."""

class ConfigValidationError(Exception):
    """Projection config is invalid."""
