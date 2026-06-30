"""
engine.py — Projection Engine.

Takes a CanonicalCandidate and a ProjectionConfig and produces
a plain dict ready for JSON serialization.

The canonical record is NEVER mutated — projection produces a new view.

Path resolution supports:
  - "field"             → candidate.field
  - "field[0]"          → candidate.field[0]
  - "field[0].subfield" → candidate.field[0].subfield
  - "field[*].subfield" → [item.subfield for item in candidate.field]
"""
from __future__ import annotations

import dataclasses
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from transformer.models.canonical import (
    CanonicalCandidate,
    ProjectionValidationError,
)
from transformer.projection.config import ProjectionConfig, FieldSpec
from transformer.projection.transformers import apply_transform

logger = logging.getLogger(__name__)

_INDEX_RE = re.compile(r"^(.+?)\[(\d+|\*)\](.*)$")


class ProjectionEngine:
    """
    Config-driven view layer over the canonical record.
    Zero knowledge of source-specific logic.
    """

    def __init__(self, config: ProjectionConfig):
        self.config = config

    def project(self, candidate: CanonicalCandidate) -> Dict[str, Any]:
        """
        Project a canonical candidate into an output dict.
        Raises ProjectionValidationError if required fields are missing.
        """
        if self.config.output_all_fields:
            return self._project_full(candidate)

        # candidate_id is always emitted — it is the identity key for all downstream systems
        output: Dict[str, Any] = {"candidate_id": candidate.candidate_id}
        errors: List[str] = []

        for spec in self.config.fields:
            try:
                value = self._resolve_path(candidate, spec.source)
                value = self._apply_filter(value, spec.filter)
                value = self._apply_transform(value, spec.transform)
                value = self._apply_missing_policy(value, spec, candidate)
            except ProjectionValidationError:
                errors.append(spec.source)
                continue
            except Exception as e:
                logger.warning("projection_field_error | field=%s | error=%s", spec.source, e)
                value = None

            if value is _OMIT_SENTINEL:
                continue   # omit policy

            target = spec.target or spec.source
            _set_nested(output, target, value)

        if errors:
            raise ProjectionValidationError(
                f"Required fields missing in projection: {errors}"
            )

        # Optionally append provenance
        if self.config.provenance and candidate.provenance:
            output["provenance"] = [
                _serialize_provenance(p) for p in candidate.provenance
            ]

        # Optionally append confidence
        if self.config.confidence:
            output["overall_confidence"] = candidate.overall_confidence

        return output

    # ------------------------------------------------------------------
    # Full canonical output (no field specs)
    # ------------------------------------------------------------------

    def _project_full(self, candidate: CanonicalCandidate) -> Dict[str, Any]:
        """Serialize the full canonical record to a dict."""
        out: Dict[str, Any] = {
            "candidate_id": candidate.candidate_id,
            "full_name": candidate.full_name,
            "emails": candidate.emails,
            "phones": candidate.phones,
            "location": _serialize_location(candidate.location),
            "links": _serialize_links(candidate.links),
            "headline": candidate.headline,
            "years_experience": candidate.years_experience,
            "skills": [_serialize_skill(s) for s in candidate.skills],
            "experience": [_serialize_experience(e) for e in candidate.experience],
            "education": [_serialize_education(e) for e in candidate.education],
        }

        if self.config.confidence:
            out["overall_confidence"] = candidate.overall_confidence

        if self.config.provenance:
            out["provenance"] = [_serialize_provenance(p) for p in candidate.provenance]

        out["sources_ingested"] = candidate.sources_ingested
        out["pipeline_version"] = candidate.pipeline_version
        out["created_at"] = candidate.created_at.isoformat() + "Z"

        return out

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_path(self, candidate: CanonicalCandidate, path: str) -> Any:
        """
        Resolve a dot-path (with optional array indexing) against the canonical record.
        Returns None if the path resolves to nothing — never raises.
        """
        parts = path.split(".")
        current: Any = candidate

        for part in parts:
            if current is None:
                return None

            # Check for array index: field[0] or field[*]
            m = _INDEX_RE.match(part)
            if m:
                field_name = m.group(1)
                index_str = m.group(2)
                remainder = m.group(3)  # e.g. ".subfield"

                # Get the list
                current = _get_attr(current, field_name)
                if not isinstance(current, list):
                    return None

                if index_str == "*":
                    # Wildcard: apply rest of path to each item
                    result = [
                        self._resolve_path_from(item, remainder.lstrip("."))
                        for item in current
                    ]
                    return result
                else:
                    idx = int(index_str)
                    if idx >= len(current):
                        logger.debug("path_resolves_null | path=%s | list_len=%d", path, len(current))
                        return None
                    current = current[idx]

                    if remainder:
                        current = self._resolve_path_from(current, remainder.lstrip("."))
            else:
                current = _get_attr(current, part)

        return _serialize_value(current)

    def _resolve_path_from(self, obj: Any, path: str) -> Any:
        """Resolve a remaining path from an object."""
        if not path:
            return _serialize_value(obj)
        parts = path.split(".", 1)
        val = _get_attr(obj, parts[0])
        if len(parts) > 1:
            return self._resolve_path_from(val, parts[1])
        return _serialize_value(val)

    # ------------------------------------------------------------------
    # Filter / Transform / Missing policy
    # ------------------------------------------------------------------

    def _apply_filter(self, value: Any, filter_spec: Optional[str]) -> Any:
        """
        Apply a filter expression to a list.
        Supports: "confidence > 0.7", "is_current = true"
        """
        if not filter_spec or not isinstance(value, list):
            return value

        m = re.match(r"(\w+)\s*(>|<|>=|<=|=|!=)\s*(.+)", filter_spec.strip())
        if not m:
            return value

        attr, op, threshold_str = m.group(1), m.group(2), m.group(3).strip()

        try:
            threshold: Any
            if threshold_str.lower() in ("true", "false"):
                threshold = threshold_str.lower() == "true"
            else:
                threshold = float(threshold_str)
        except ValueError:
            threshold = threshold_str

        filtered = []
        for item in value:
            item_val = _get_attr(item, attr) if not isinstance(item, dict) else item.get(attr)
            if item_val is None:
                continue
            try:
                if op == ">" and item_val > threshold:
                    filtered.append(item)
                elif op == "<" and item_val < threshold:
                    filtered.append(item)
                elif op == ">=" and item_val >= threshold:
                    filtered.append(item)
                elif op == "<=" and item_val <= threshold:
                    filtered.append(item)
                elif op in ("=", "==") and item_val == threshold:
                    filtered.append(item)
                elif op == "!=" and item_val != threshold:
                    filtered.append(item)
            except TypeError:
                continue

        return filtered

    def _apply_transform(self, value: Any, transform_spec: Optional[str]) -> Any:
        if not transform_spec:
            return value
        # Serialize to dict/primitive first so transforms work on plain types
        return apply_transform(transform_spec, value)

    def _apply_missing_policy(
        self, value: Any, spec: FieldSpec, candidate: CanonicalCandidate
    ) -> Any:
        """Apply missing value policy when value is None or empty list."""
        is_missing = value is None or value == [] or value == {}

        if not is_missing:
            return value

        effective_policy = spec.missing_policy or self.config.missing_value_policy

        if spec.required or effective_policy == "error":
            logger.error(
                "required_field_missing | field=%s | candidate_id=%s",
                spec.source,
                candidate.candidate_id,
            )
            raise ProjectionValidationError(
                f"Required field '{spec.source}' is missing for candidate {candidate.candidate_id}"
            )
        elif effective_policy == "omit":
            return _OMIT_SENTINEL
        else:  # "null"
            return None


# ---------------------------------------------------------------------------
# Sentinel for "omit this field"
# ---------------------------------------------------------------------------

class _OmitSentinel:
    pass

_OMIT_SENTINEL = _OmitSentinel()


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _get_attr(obj: Any, name: str) -> Any:
    """Get attribute from dataclass, dict, or object."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _serialize_value(val: Any) -> Any:
    """Convert dataclass instances to dicts for output."""
    if val is None:
        return None
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        return _serialize_dataclass(val)
    if isinstance(val, list):
        return [_serialize_value(v) for v in val]
    if isinstance(val, datetime):
        return val.isoformat() + "Z"
    return val


def _serialize_dataclass(obj: Any) -> Dict[str, Any]:
    """Shallow serialize a dataclass to a dict, skipping private fields."""
    result = {}
    for f in dataclasses.fields(obj):
        if f.name.startswith("_"):
            continue
        result[f.name] = _serialize_value(getattr(obj, f.name))
    return result


def _set_nested(d: dict, path: str, value: Any) -> None:
    """Set a value at a dot-path in a dict, creating intermediate dicts."""
    parts = path.split(".", 1)
    if len(parts) == 1:
        d[path] = value
    else:
        if parts[0] not in d or not isinstance(d[parts[0]], dict):
            d[parts[0]] = {}
        _set_nested(d[parts[0]], parts[1], value)


def _serialize_location(loc) -> Optional[Dict]:
    if not loc:
        return None
    return {"city": loc.city, "region": loc.region, "country": loc.country}


def _serialize_links(links) -> Dict:
    if not links:
        return {}
    return {
        "linkedin": links.linkedin,
        "github": links.github,
        "portfolio": links.portfolio,
        "other": links.other,
    }


def _serialize_skill(skill) -> Dict:
    return {
        "name": skill.name,
        "confidence": skill.confidence,
        "sources": skill.sources,
        "aliases_seen": skill.aliases_seen,
    }


def _serialize_experience(exp) -> Dict:
    return {
        "company": exp.company,
        "title": exp.title,
        "start": exp.start,
        "end": exp.end,
        "is_current": exp.is_current,
        "summary": exp.summary,
    }


def _serialize_education(edu) -> Dict:
    return {
        "institution": edu.institution,
        "degree": edu.degree,
        "field": edu.field_of_study,
        "end_year": edu.end_year,
    }


def _serialize_provenance(prov) -> Dict:
    return {
        "field": prov.field,
        "source": prov.source,
        "method": prov.method,
        "raw_value": prov.raw_value,
        "normalized_value": str(prov.normalized_value) if prov.normalized_value is not None else None,
        "confidence": prov.confidence,
        "extracted_at": prov.extracted_at.isoformat() + "Z",
        "conflict": prov.conflict,
        "conflict_resolution": prov.conflict_resolution,
    }
