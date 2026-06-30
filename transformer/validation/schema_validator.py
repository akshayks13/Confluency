"""
schema_validator.py — Output schema validation.

Validates the projected output dict against the canonical output schema.
Uses jsonschema for validation. Semantic checks are separate (cross-field).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import jsonschema
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

logger = logging.getLogger(__name__)

# JSON Schema for the default canonical output
_DEFAULT_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "CandidateProfile",
    "type": "object",
    "required": ["candidate_id"],
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1},
        "full_name": {"type": ["string", "null"]},
        "emails": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[^@]+@[^@]+\\.[^@]+$"}
        },
        "phones": {
            "type": "array",
            "items": {"type": "string", "pattern": "^\\+[1-9]\\d{6,14}$"}
        },
        "location": {
            "type": ["object", "null"],
            "properties": {
                "city": {"type": ["string", "null"]},
                "region": {"type": ["string", "null"]},
                "country": {"type": ["string", "null"], "maxLength": 2},
            },
        },
        "headline": {"type": ["string", "null"]},
        "years_experience": {"type": ["number", "null"]},
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "sources": {"type": "array", "items": {"type": "string"}},
                }
            }
        },
        "experience": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["company", "title"],
                "properties": {
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "is_current": {"type": "boolean"},
                    "summary": {"type": ["string", "null"]},
                }
            }
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["institution"],
                "properties": {
                    "institution": {"type": "string"},
                    "degree": {"type": ["string", "null"]},
                    "field": {"type": ["string", "null"]},
                    "end_year": {"type": ["integer", "null"]},
                }
            }
        },
        "overall_confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "provenance": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "source", "method"],
                "properties": {
                    "field": {"type": "string"},
                    "source": {"type": "string"},
                    "method": {"type": "string"},
                    "confidence": {"type": "number"},
                    "conflict": {"type": "boolean"},
                }
            }
        },
        "pipeline_version": {"type": "string"},
        "sources_ingested": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}


def validate_output(
    output: Dict[str, Any],
    schema: Dict[str, Any] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate an output dict against the JSON schema.
    Returns (is_valid, [error_messages]).
    Never raises — returns errors as a list.
    """
    effective_schema = schema or _DEFAULT_OUTPUT_SCHEMA
    if not _JSONSCHEMA_AVAILABLE:
        if schema is not None:
            logger.warning("jsonschema not installed — custom schema validation unavailable")
        messages = _fallback_validate_output(output)
        return len(messages) == 0, messages

    validator = jsonschema.Draft7Validator(effective_schema)
    errors = sorted(validator.iter_errors(output), key=lambda e: list(e.path))

    messages = []
    for error in errors:
        path = ".".join(str(p) for p in error.path) or "(root)"
        messages.append(f"[{path}] {error.message}")
        logger.warning("schema_validation_error | path=%s | message=%s", path, error.message)

    if messages:
        logger.error(
            "schema_validation_failed | candidate_id=%s | error_count=%d",
            output.get("candidate_id", "unknown"),
            len(messages),
        )
    else:
        logger.debug("schema_validation_passed | candidate_id=%s", output.get("candidate_id"))

    return len(messages) == 0, messages


def _fallback_validate_output(output: Dict[str, Any]) -> List[str]:
    """Small dependency-free validator for the default output contract."""
    messages: List[str] = []

    if not isinstance(output, dict):
        return ["[(root)] output must be an object"]

    candidate_id = output.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        messages.append("[candidate_id] must be a non-empty string")

    _check_optional_type(output, "full_name", (str, type(None)), messages)
    _check_optional_type(output, "headline", (str, type(None)), messages)
    _check_optional_type(output, "years_experience", (int, float, type(None)), messages)

    email_re = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
    phone_re = re.compile(r"^\+[1-9]\d{6,14}$")
    _check_string_array(output, "emails", email_re, messages)
    _check_string_array(output, "phones", phone_re, messages)
    _check_string_array(output, "sources_ingested", None, messages)

    location = output.get("location")
    if location is not None:
        if not isinstance(location, dict):
            messages.append("[location] must be an object or null")
        else:
            for key in ("city", "region", "country"):
                _check_optional_type(location, key, (str, type(None)), messages, f"location.{key}")
            country = location.get("country")
            if country is not None and len(country) > 2:
                messages.append("[location.country] must be ISO-3166 alpha-2")

    confidence = output.get("overall_confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            messages.append("[overall_confidence] must be a number between 0 and 1")

    _check_skill_array(output, messages)
    _check_experience_array(output, messages)
    _check_education_array(output, messages)
    _check_provenance_array(output, messages)

    for message in messages:
        logger.warning("schema_validation_error | message=%s", message)
    return messages


def _check_optional_type(
    obj: Dict[str, Any],
    key: str,
    expected: tuple[type, ...],
    messages: List[str],
    label: str = "",
) -> None:
    if key in obj and not isinstance(obj[key], expected):
        messages.append(f"[{label or key}] has invalid type")


def _check_string_array(
    obj: Dict[str, Any],
    key: str,
    pattern: Optional[re.Pattern],
    messages: List[str],
) -> None:
    if key not in obj:
        return
    value = obj[key]
    if not isinstance(value, list):
        messages.append(f"[{key}] must be an array")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            messages.append(f"[{key}.{idx}] must be a string")
        elif pattern and not pattern.match(item):
            messages.append(f"[{key}.{idx}] has invalid format")


def _check_skill_array(output: Dict[str, Any], messages: List[str]) -> None:
    skills = output.get("skills")
    if skills is None:
        return
    if not isinstance(skills, list):
        messages.append("[skills] must be an array")
        return
    for idx, skill in enumerate(skills):
        if not isinstance(skill, dict):
            messages.append(f"[skills.{idx}] must be an object")
            continue
        if not isinstance(skill.get("name"), str) or not skill.get("name"):
            messages.append(f"[skills.{idx}.name] must be a non-empty string")
        confidence = skill.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            messages.append(f"[skills.{idx}.confidence] must be between 0 and 1")
        sources = skill.get("sources")
        if sources is not None and not all(isinstance(s, str) for s in sources):
            messages.append(f"[skills.{idx}.sources] must contain strings")


def _check_experience_array(output: Dict[str, Any], messages: List[str]) -> None:
    experience = output.get("experience")
    if experience is None:
        return
    if not isinstance(experience, list):
        messages.append("[experience] must be an array")
        return
    for idx, exp in enumerate(experience):
        if not isinstance(exp, dict):
            messages.append(f"[experience.{idx}] must be an object")
            continue
        if not isinstance(exp.get("company"), str):
            messages.append(f"[experience.{idx}.company] must be a string")
        if not isinstance(exp.get("title"), str):
            messages.append(f"[experience.{idx}.title] must be a string")


def _check_education_array(output: Dict[str, Any], messages: List[str]) -> None:
    education = output.get("education")
    if education is None:
        return
    if not isinstance(education, list):
        messages.append("[education] must be an array")
        return
    for idx, edu in enumerate(education):
        if not isinstance(edu, dict):
            messages.append(f"[education.{idx}] must be an object")
            continue
        if not isinstance(edu.get("institution"), str):
            messages.append(f"[education.{idx}.institution] must be a string")
        end_year = edu.get("end_year")
        if end_year is not None and not isinstance(end_year, int):
            messages.append(f"[education.{idx}.end_year] must be an integer or null")


def _check_provenance_array(output: Dict[str, Any], messages: List[str]) -> None:
    provenance = output.get("provenance")
    if provenance is None:
        return
    if not isinstance(provenance, list):
        messages.append("[provenance] must be an array")
        return
    for idx, entry in enumerate(provenance):
        if not isinstance(entry, dict):
            messages.append(f"[provenance.{idx}] must be an object")
            continue
        for key in ("field", "source", "method"):
            if not isinstance(entry.get(key), str):
                messages.append(f"[provenance.{idx}.{key}] must be a string")


def semantic_validate(output: Dict[str, Any]) -> List[str]:
    """
    Cross-field semantic checks that JSON schema cannot express.
    Returns list of warning strings (not errors — we never discard valid data).
    """
    warnings = []

    # Experience date order check
    for exp in output.get("experience", []):
        start = exp.get("start")
        end = exp.get("end")
        if start and end and start > end:
            warnings.append(
                f"Invalid date range for {exp.get('company')}: start={start} > end={end}"
            )

    # Education future year check
    import datetime
    current_year = datetime.datetime.utcnow().year
    for edu in output.get("education", []):
        end_year = edu.get("end_year")
        if end_year and end_year > current_year + 10:
            warnings.append(
                f"Suspicious future education year {end_year} at {edu.get('institution')}"
            )

    # Phone format check (belt-and-suspenders over schema)
    import re
    e164_re = re.compile(r"^\+[1-9]\d{6,14}$")
    for phone in output.get("phones", []):
        if not e164_re.match(phone):
            warnings.append(f"Phone not in E.164 format: {phone}")

    return warnings
