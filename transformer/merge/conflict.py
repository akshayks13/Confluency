"""
conflict.py — Merge engine and conflict resolution.

Takes a list of RawExtraction objects for the same candidate and produces
a single CanonicalCandidate with full provenance.

Source priority (descending, highest wins for scalar conflicts):
  1. ats_json   → 0.88
  2. csv        → 0.85
  3. github     → 0.75
  4. resume     → 0.65
  5. notes      → 0.50

For multi-value fields (emails, phones, skills, experience, education):
  → Union all values, deduplicate by normalized form.

For scalar fields (full_name, location, headline):
  → Highest source priority wins. Conflicts logged in provenance.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from transformer.models.canonical import (
    CanonicalCandidate,
    ProvenanceEntry,
    Location,
    Skill,
    ExperienceEntry,
    EducationEntry,
    Links,
    RawExtraction,
)
from transformer.normalizers.email import normalize_email
from transformer.normalizers.phone import normalize_phone
from transformer.normalizers.name import normalize_name
from transformer.normalizers.location import normalize_location
from transformer.normalizers.skills import normalize_skill
from transformer.normalizers.url import normalize_url, classify_url
from transformer.normalizers.date_norm import normalize_date, normalize_date_range

logger = logging.getLogger(__name__)

_SOURCE_PRIORITY: Dict[str, float] = {
    "ats_json": 0.88,
    "csv": 0.85,
    "github": 0.75,
    "resume": 0.65,
    "notes": 0.50,
}


def merge_extractions(
    candidate_id: str,
    extractions: List[RawExtraction],
) -> CanonicalCandidate:
    """
    Merge all extractions for one candidate into a single CanonicalCandidate.
    """
    candidate = CanonicalCandidate(candidate_id=candidate_id)
    candidate.sources_ingested = [e.source_id for e in extractions]

    # Sort by source priority descending — highest priority processed last
    # (later values overwrite scalars, so highest priority should be last)
    extractions_sorted = sorted(
        extractions,
        key=lambda e: _source_priority(e.source_type),
    )

    provenance: List[ProvenanceEntry] = []

    # --- Multi-value fields (union all) ---
    all_emails: Dict[str, Tuple[str, str, str]] = {}   # normalized → (raw, source_id, method)
    all_phones: Dict[str, Tuple[str, str, str]] = {}
    all_skills: Dict[str, List[Any]] = {}              # canonical → [aliases, max_conf, sources]

    # --- Scalar fields (last-writer-wins by priority, conflicts logged) ---
    scalar_candidates: Dict[str, List[Tuple[Any, float, str, str]]] = {}
    # field → [(value, confidence, source_id, method)]

    for extraction in extractions_sorted:
        src_priority = _source_priority(extraction.source_type)
        source_id = extraction.source_id
        now = extraction.extracted_at

        # ---- Emails ----
        for raw_email in extraction.emails:
            norm, conf = normalize_email(raw_email)
            if norm:
                method = "structured_field" if extraction.source_type in ("csv", "ats_json") else "regex"
                if norm not in all_emails:
                    all_emails[norm] = (raw_email, source_id, method)
                    provenance.append(_prov("emails", source_id, method, raw_email, norm, conf * src_priority, now))

        # ---- Phones ----
        for raw_phone in extraction.phones:
            norm, conf = normalize_phone(raw_phone)
            if norm:
                method = "structured_field" if extraction.source_type in ("csv", "ats_json") else "regex"
                if norm not in all_phones:
                    all_phones[norm] = (raw_phone, source_id, method)
                    provenance.append(_prov("phones", source_id, method, raw_phone, norm, conf * src_priority, now))

        # ---- Skills ----
        if extraction.skills_raw:
            method = "api_field" if extraction.source_type == "github" else "structured_field"
            for raw_skill in extraction.skills_raw:
                skill_name, skill_conf, is_known = normalize_skill(raw_skill)
                if not skill_name:
                    continue
                adjusted_conf = skill_conf * src_priority
                if extraction.source_type == "github":
                    adjusted_conf *= 0.85   # Language proxy — not a stated skill
                if skill_name not in all_skills:
                    all_skills[skill_name] = [[], 0.0, []]
                if raw_skill not in all_skills[skill_name][0]:
                    all_skills[skill_name][0].append(raw_skill)
                all_skills[skill_name][1] = max(all_skills[skill_name][1], adjusted_conf)
                if source_id not in all_skills[skill_name][2]:
                    all_skills[skill_name][2].append(source_id)

        # ---- Scalar: full_name ----
        if extraction.full_name:
            norm_name, conf = normalize_name(extraction.full_name)
            if norm_name:
                field_conf = conf * src_priority
                _add_scalar(scalar_candidates, "full_name", norm_name, field_conf, source_id, "structured_field")
                provenance.append(_prov("full_name", source_id, "structured_field", extraction.full_name, norm_name, field_conf, now))

        # ---- Scalar: location ----
        if extraction.location_raw:
            loc, conf = normalize_location(extraction.location_raw)
            if not loc.is_empty():
                field_conf = conf * src_priority
                _add_scalar(scalar_candidates, "location", loc, field_conf, source_id, "structured_field")
                provenance.append(_prov("location", source_id, "structured_field", extraction.location_raw, _loc_str(loc), field_conf, now))

        # ---- Scalar: headline ----
        if extraction.headline:
            hl = extraction.headline.strip()[:200]
            field_conf = 0.7 * src_priority
            _add_scalar(scalar_candidates, "headline", hl, field_conf, source_id, "structured_field")
            provenance.append(_prov("headline", source_id, "structured_field", hl, hl, field_conf, now))

        # ---- Experience ----
        for exp in extraction.experience:
            exp_entry = _parse_experience_entry(exp, source_id)
            if exp_entry:
                candidate.experience = _dedup_add_experience(candidate.experience, exp_entry)

        # ---- Education ----
        for edu in extraction.education:
            edu_entry = _parse_education_entry(edu, source_id)
            if edu_entry:
                candidate.education = _dedup_add_education(candidate.education, edu_entry)

        # ---- Links ----
        for link_type, url_raw in extraction.links.items():
            if not url_raw:
                continue
            norm_url, url_conf = normalize_url(url_raw)
            if norm_url:
                classified = classify_url(norm_url)
                if classified == "linkedin" and not candidate.links.linkedin:
                    candidate.links.linkedin = norm_url
                elif classified == "github" and not candidate.links.github:
                    candidate.links.github = norm_url
                elif link_type == "portfolio" and not candidate.links.portfolio:
                    candidate.links.portfolio = norm_url
                elif norm_url not in candidate.links.other:
                    candidate.links.other.append(norm_url)

    # --- Resolve scalar conflicts and assign winning values ---
    candidate.full_name = _resolve_scalar(scalar_candidates, "full_name", provenance)
    candidate.location = _resolve_scalar(scalar_candidates, "location", provenance)
    candidate.headline = _resolve_scalar(scalar_candidates, "headline", provenance)

    # --- Assign multi-value fields ---
    candidate.emails = sorted(all_emails.keys())
    candidate.phones = sorted(all_phones.keys())
    candidate.skills = [
        Skill(
            name=name,
            aliases_seen=data[0],
            confidence=round(data[1], 3),
            sources=data[2],
        )
        for name, data in all_skills.items()
    ]
    candidate.skills.sort(key=lambda s: -s.confidence)

    # Sort experience/education
    candidate.experience.sort(key=lambda e: e.start or "0000", reverse=True)
    candidate.education.sort(key=lambda e: e.end_year or 0, reverse=True)

    candidate.provenance = provenance
    return candidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_priority(source_type: str) -> float:
    return _SOURCE_PRIORITY.get(source_type, 0.5)


def _prov(
    field: str, source: str, method: str,
    raw: str, norm: Any, confidence: float, extracted_at: datetime,
    conflict: bool = False, conflict_resolution: Optional[str] = None,
) -> ProvenanceEntry:
    return ProvenanceEntry(
        field=field,
        source=source,
        method=method,
        raw_value=str(raw),
        normalized_value=norm,
        confidence=round(min(confidence, 1.0), 3),
        extracted_at=extracted_at,
        conflict=conflict,
        conflict_resolution=conflict_resolution,
    )


def _add_scalar(
    candidates: Dict[str, List], field: str,
    value: Any, confidence: float, source_id: str, method: str,
) -> None:
    if field not in candidates:
        candidates[field] = []
    candidates[field].append((value, confidence, source_id, method))


def _resolve_scalar(
    candidates: Dict[str, List],
    field: str,
    provenance: List[ProvenanceEntry],
) -> Optional[Any]:
    """
    Pick the highest-confidence value for a scalar field.
    Mark all others as conflicts in provenance.
    """
    options = candidates.get(field, [])
    if not options:
        return None

    # Sort by confidence descending
    options_sorted = sorted(options, key=lambda x: -x[1])
    winner_value, winner_conf, winner_source, _ = options_sorted[0]

    if len(options_sorted) > 1:
        # Mark all non-winner provenance entries as conflicts
        for pentry in provenance:
            if pentry.field == field and pentry.source != winner_source:
                pentry.conflict = True
                pentry.conflict_resolution = f"source_priority:{winner_source}_wins"
                logger.info(
                    "conflict_resolved | field=%s | winner=%s | loser=%s",
                    field, winner_source, pentry.source,
                )

    return winner_value


def _parse_experience_entry(exp: dict, source_id: str) -> Optional[ExperienceEntry]:
    company = (exp.get("company") or "").strip()
    title = (exp.get("title") or "").strip()
    if not company and not title:
        return None

    start_raw = exp.get("start")
    end_raw = exp.get("end")
    is_current = bool(exp.get("is_current", False))

    start, _, _, _ = normalize_date_range(start_raw) if start_raw else (None, None, False, 0.0)
    if start_raw and not start:
        start, _, _ = normalize_date(start_raw)

    end = None
    if end_raw:
        end, end_is_current, _ = normalize_date(end_raw)
        if end_is_current:
            is_current = True
            end = None

    return ExperienceEntry(
        company=company,
        title=title,
        start=start,
        end=end,
        is_current=is_current,
        summary=exp.get("summary"),
        source=source_id,
    )


def _parse_education_entry(edu: dict, source_id: str) -> Optional[EducationEntry]:
    institution = (edu.get("institution") or "").strip()
    if not institution:
        return None

    end_year = edu.get("end_year")
    if end_year is not None:
        try:
            end_year = int(end_year)
        except (ValueError, TypeError):
            end_year = None

    return EducationEntry(
        institution=institution,
        degree=edu.get("degree"),
        field_of_study=edu.get("field"),
        end_year=end_year,
        source=source_id,
    )


def _dedup_key_experience(e: ExperienceEntry) -> str:
    company = re.sub(r"\s+", "", (e.company or "").lower())
    title = re.sub(r"\s+", "", (e.title or "").lower())
    return f"{company}|{title}|{e.start or ''}"


def _dedup_add_experience(
    existing: List[ExperienceEntry], new_entry: ExperienceEntry
) -> List[ExperienceEntry]:
    key = _dedup_key_experience(new_entry)
    for ex in existing:
        if _dedup_key_experience(ex) == key:
            # Corroboration — update source list
            if new_entry.source not in ex.source:
                ex.source += f",{new_entry.source}"
            return existing
    existing.append(new_entry)
    return existing


def _dedup_add_education(
    existing: List[EducationEntry], new_entry: EducationEntry
) -> List[EducationEntry]:
    for ex in existing:
        if (
            ex.institution.lower() == new_entry.institution.lower()
            and ex.end_year == new_entry.end_year
        ):
            return existing
    existing.append(new_entry)
    return existing


def _loc_str(loc: Location) -> str:
    parts = [loc.city, loc.region, loc.country]
    return ", ".join(p for p in parts if p)
