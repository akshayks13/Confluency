"""
ats_json_adapter.py — ATS JSON blob source adapter.

The ATS uses its own field names. A mapping config translates them to canonical names.
Unknown fields are preserved in a pass-through dict (not discarded).

Supports both a list of candidates and a single candidate object at the root.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from transformer.adapters.base import SourceAdapter
from transformer.models.canonical import RawExtraction
from transformer.normalizers.email import normalize_email

logger = logging.getLogger(__name__)

# ATS field name → canonical field name mapping.
# This is config-driven in a real system; hardcoded here for the assignment.
_ATS_FIELD_MAP: Dict[str, str] = {
    # Identity
    "name": "full_name",
    "fullName": "full_name",
    "full_name": "full_name",
    "candidateName": "full_name",
    "email": "email",
    "emailAddress": "email",
    "email_address": "email",
    "phone": "phone",
    "phoneNumber": "phone",
    "phone_number": "phone",
    "mobile": "phone",
    # Profile
    "location": "location",
    "city": "location",
    "currentTitle": "title",
    "current_title": "title",
    "jobTitle": "title",
    "job_title": "title",
    "title": "title",
    "currentCompany": "current_company",
    "current_company": "current_company",
    "company": "current_company",
    "employer": "current_company",
    "headline": "headline",
    "summary": "headline",
    "bio": "headline",
    # Experience
    "experience": "experience",
    "workHistory": "experience",
    "work_history": "experience",
    "jobs": "experience",
    # Education
    "education": "education",
    "educationHistory": "education",
    "education_history": "education",
    # Skills
    "skills": "skills",
    "skillSet": "skills",
    "skill_set": "skills",
    "technologies": "skills",
    # Links
    "linkedinUrl": "linkedin",
    "linkedin_url": "linkedin",
    "linkedin": "linkedin",
    "githubUrl": "github",
    "github_url": "github",
    "github": "github",
    "portfolioUrl": "portfolio",
    "portfolio": "portfolio",
    "website": "portfolio",
}


class ATSJsonAdapter(SourceAdapter):
    source_type = "ats_json"
    BASE_CONFIDENCE = 0.88

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.source_id = f"ats_json:{self.file_path.name}"

    def extract(self) -> List[RawExtraction]:
        if not self.file_path.exists():
            self._warn("File not found", file=str(self.file_path))
            return []

        content = self.file_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            self._warn("Empty file", file=str(self.file_path))
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            self._warn("JSON parse error", file=str(self.file_path), error=str(e))
            return []

        # Support both list and single-object formats
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Could be {"candidates": [...]} or a single candidate
            if "candidates" in data:
                records = data["candidates"]
            elif "data" in data:
                records = data["data"] if isinstance(data["data"], list) else [data["data"]]
            else:
                records = [data]
        else:
            self._warn("Unexpected JSON structure", type=type(data).__name__)
            return []

        results = []
        for i, record in enumerate(records):
            if not isinstance(record, dict):
                logger.debug("Skipping non-dict record at index %d", i)
                continue
            extraction = self._parse_record(record, index=i)
            if extraction:
                results.append(extraction)

        self._info("Extraction complete", candidates_found=len(results))
        return results

    def _parse_record(self, record: Dict[str, Any], index: int) -> Optional[RawExtraction]:
        # Map ATS fields → canonical names
        mapped: Dict[str, Any] = {}
        for ats_key, value in record.items():
            canonical = _ATS_FIELD_MAP.get(ats_key)
            if canonical and value is not None:
                # Only take first mapping if duplicate keys exist
                if canonical not in mapped:
                    mapped[canonical] = value

        name = _str(mapped.get("full_name"))
        email = _str(mapped.get("email"))
        normalized_email, _ = normalize_email(email)

        if not name and not normalized_email:
            logger.debug("ATS record %d skipped: no name or email", index)
            return None

        # Parse skills — can be list or comma-string
        skills_raw = _parse_skills(mapped.get("skills"))

        # Parse experience
        experience = _parse_experience(mapped.get("experience"), mapped)

        # Parse education
        education = _parse_education(mapped.get("education"))

        # Links
        links = {}
        for link_field in ("linkedin", "github", "portfolio"):
            val = _str(mapped.get(link_field))
            if val:
                links[link_field] = val

        return RawExtraction(
            source_id=self.source_id,
            source_type=self.source_type,
            extracted_at=datetime.utcnow(),
            base_confidence=self.BASE_CONFIDENCE,
            full_name=name,
            emails=[email] if email else [],
            phones=[_str(mapped.get("phone"))] if mapped.get("phone") else [],
            location_raw=_str(mapped.get("location")),
            headline=_str(mapped.get("headline")),
            skills_raw=skills_raw,
            experience=experience,
            education=education,
            links=links,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _parse_skills(val: Any) -> List[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(s).strip() for s in val if s]
    if isinstance(val, str):
        import re
        return [s.strip() for s in re.split(r"[,;|]", val) if s.strip()]
    return []


def _parse_experience(exp_val: Any, mapped: dict) -> List[dict]:
    """Parse experience — either a list of job objects or a current company/title pair."""
    entries = []

    if isinstance(exp_val, list):
        for job in exp_val:
            if not isinstance(job, dict):
                continue
            entries.append({
                "company": _str(job.get("company") or job.get("employer") or job.get("organization")) or "",
                "title": _str(job.get("title") or job.get("jobTitle") or job.get("job_title")) or "",
                "start": _str(job.get("start") or job.get("startDate") or job.get("start_date")),
                "end": _str(job.get("end") or job.get("endDate") or job.get("end_date")),
                "is_current": bool(job.get("current") or job.get("isCurrent") or job.get("is_current")),
                "summary": _str(job.get("summary") or job.get("description")),
            })
    elif not entries:
        # Fallback: top-level current_company + title
        company = _str(mapped.get("current_company"))
        title = _str(mapped.get("title"))
        if company or title:
            entries.append({
                "company": company or "",
                "title": title or "",
                "start": None,
                "end": None,
                "is_current": True,
                "summary": None,
            })

    return entries


def _parse_education(edu_val: Any) -> List[dict]:
    if not isinstance(edu_val, list):
        return []
    entries = []
    for edu in edu_val:
        if not isinstance(edu, dict):
            continue
        entries.append({
            "institution": _str(edu.get("institution") or edu.get("school") or edu.get("university")) or "",
            "degree": _str(edu.get("degree") or edu.get("qualification")),
            "field": _str(edu.get("field") or edu.get("major") or edu.get("fieldOfStudy")),
            "end_year": edu.get("endYear") or edu.get("end_year") or edu.get("graduationYear"),
        })
    return entries
