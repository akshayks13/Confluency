"""
confidence.py — Formula-based confidence scoring engine.

Overall candidate confidence is a weighted average of field-level confidences.
A hard cap applies when critical fields (email, name) are missing.

Formula:
  field_confidence  = base_source_conf × extraction_method_mult × completeness × consistency_bonus
  overall_confidence = weighted_avg(field_confidences, FIELD_WEIGHTS)
  if missing email OR name → overall_confidence = min(overall_confidence, 0.5)
"""
from __future__ import annotations

import logging
from typing import Optional

from transformer.models.canonical import CanonicalCandidate, ProvenanceEntry

logger = logging.getLogger(__name__)

# Field weights for overall confidence computation
_FIELD_WEIGHTS = {
    "emails": 0.25,
    "full_name": 0.20,
    "phones": 0.10,
    "skills": 0.20,
    "experience": 0.15,
    "location": 0.05,
    "headline": 0.05,
}

# Extraction method multipliers
_METHOD_MULT = {
    "structured_field": 1.00,
    "api_field": 0.95,
    "regex": 0.90,
    "nlp_ner": 0.80,
    "inferred": 0.60,
}

# Consistency bonus: field appears in 2+ sources
_CONSISTENCY_BONUS = 1.05


def score_candidate(candidate: CanonicalCandidate) -> float:
    """
    Compute overall_confidence and attach it to the candidate in-place.
    Returns the confidence score.
    """
    field_scores = {}

    # Email
    email_entries = candidate.provenance_for_field("emails")
    if candidate.emails:
        field_scores["emails"] = _avg_conf(email_entries, _consistency_bonus(email_entries))
    else:
        field_scores["emails"] = 0.0

    # Name
    name_entries = candidate.provenance_for_field("full_name")
    if candidate.full_name:
        field_scores["full_name"] = _avg_conf(name_entries, _consistency_bonus(name_entries))
    else:
        field_scores["full_name"] = 0.0

    # Phones
    phone_entries = candidate.provenance_for_field("phones")
    if candidate.phones:
        field_scores["phones"] = _avg_conf(phone_entries, _consistency_bonus(phone_entries))
    else:
        field_scores["phones"] = 0.0

    # Skills
    if candidate.skills:
        avg_skill_conf = sum(s.confidence for s in candidate.skills) / len(candidate.skills)
        field_scores["skills"] = min(avg_skill_conf * _consistency_bonus(
            [s for s in candidate.skills if len(s.sources) > 1]
        ), 1.0)
    else:
        field_scores["skills"] = 0.0

    # Experience
    if candidate.experience:
        field_scores["experience"] = 0.7  # Present = good signal
    else:
        field_scores["experience"] = 0.0

    # Location
    loc_entries = candidate.provenance_for_field("location")
    if candidate.location and not candidate.location.is_empty():
        field_scores["location"] = _avg_conf(loc_entries, 1.0)
    else:
        field_scores["location"] = 0.0

    # Headline
    hl_entries = candidate.provenance_for_field("headline")
    if candidate.headline:
        field_scores["headline"] = _avg_conf(hl_entries, 1.0)
    else:
        field_scores["headline"] = 0.0

    # Weighted average
    total_weight = sum(_FIELD_WEIGHTS.values())
    overall = sum(
        field_scores.get(field, 0.0) * weight
        for field, weight in _FIELD_WEIGHTS.items()
    ) / total_weight

    # Hard cap: missing critical fields
    missing_critical = []
    if not candidate.emails:
        missing_critical.append("email")
    if not candidate.full_name:
        missing_critical.append("name")

    if missing_critical:
        original = overall
        overall = min(overall, 0.50)
        logger.warning(
            "confidence_capped | candidate_id=%s | missing=%s | %.3f → %.3f",
            candidate.candidate_id,
            missing_critical,
            original,
            overall,
        )

    overall = round(overall, 3)
    candidate.overall_confidence = overall

    logger.info(
        "confidence_scored | candidate_id=%s | score=%.3f | fields=%s",
        candidate.candidate_id,
        overall,
        {k: round(v, 3) for k, v in field_scores.items()},
    )
    return overall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg_conf(entries: list, bonus: float = 1.0) -> float:
    if not entries:
        return 0.0
    avg = sum(e.confidence if hasattr(e, 'confidence') else 0.0 for e in entries) / len(entries)
    return min(avg * bonus, 1.0)


def _consistency_bonus(entries: list) -> float:
    """Return CONSISTENCY_BONUS if entries from 2+ distinct sources, else 1.0."""
    sources = set(
        e.source if hasattr(e, 'source') else ""
        for e in entries
    )
    return _CONSISTENCY_BONUS if len(sources) >= 2 else 1.0
