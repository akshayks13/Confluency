"""
csv_adapter.py — Recruiter CSV source adapter.

Expected columns (flexible — mapped via header normalization):
  name / full_name, email, phone, current_company, title / job_title,
  location, linkedin, github, skills, headline

Missing columns → field is None, never an error.
Malformed rows → skipped with a warning.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    import chardet
    _CHARDET_AVAILABLE = True
except ImportError:
    _CHARDET_AVAILABLE = False

from transformer.adapters.base import SourceAdapter
from transformer.models.canonical import RawExtraction, SourceUnavailableWarning
from transformer.normalizers.email import normalize_email

logger = logging.getLogger(__name__)

# Canonical column name → list of accepted aliases (all lowercase)
_HEADER_ALIASES = {
    "full_name": ["name", "full_name", "fullname", "candidate_name", "candidate name"],
    "email": ["email", "email_address", "e-mail", "e_mail"],
    "phone": ["phone", "phone_number", "mobile", "cell", "telephone"],
    "current_company": ["current_company", "company", "employer", "organization"],
    "title": ["title", "job_title", "current_title", "position", "role"],
    "location": ["location", "city", "address", "loc"],
    "linkedin": ["linkedin", "linkedin_url", "linkedin url"],
    "github": ["github", "github_url", "github url"],
    "skills": ["skills", "skill_set", "technologies", "tech_stack"],
    "headline": ["headline", "summary", "bio", "about"],
}


class CSVAdapter(SourceAdapter):
    source_type = "csv"
    BASE_CONFIDENCE = 0.85

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.source_id = f"csv:{self.file_path.name}"

    def extract(self) -> List[RawExtraction]:
        if not self.file_path.exists():
            self._warn("File not found", file=str(self.file_path))
            return []

        raw_bytes = self.file_path.read_bytes()
        if not raw_bytes.strip():
            self._warn("Empty file", file=str(self.file_path))
            return []

        encoding = self._detect_encoding(raw_bytes)
        try:
            text = raw_bytes.decode(encoding, errors="replace")
        except Exception as e:
            self._warn("Decode failed", file=str(self.file_path), error=str(e))
            return []

        return self._parse_csv(text)

    def _parse_csv(self, text: str) -> List[RawExtraction]:
        results = []
        try:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames is None:
                self._warn("No headers found")
                return []

            col_map = self._build_column_map(list(reader.fieldnames))
            self._info(
                "Parsed headers",
                mapped_columns=str(list(col_map.keys())),
            )

            for row_num, row in enumerate(reader, start=2):
                extraction = self._parse_row(row, col_map, row_num)
                if extraction:
                    results.append(extraction)

        except csv.Error as e:
            self._warn("CSV parse error", error=str(e))

        self._info("Extraction complete", candidates_found=len(results))
        return results

    def _parse_row(
        self, row: dict, col_map: dict, row_num: int
    ) -> Optional[RawExtraction]:
        def get(canonical: str) -> Optional[str]:
            csv_col = col_map.get(canonical)
            if csv_col is None:
                return None
            val = row.get(csv_col, "").strip()
            return val if val else None

        name = get("full_name")
        email = get("email")

        normalized_email, _ = normalize_email(email)

        # Need at least one usable identity field to make this row useful.
        # An invalid email string by itself should not become an "unknown"
        # candidate that later fails custom required-field projections.
        if not name and not normalized_email:
            logger.debug("Row %d skipped: no name or email", row_num)
            return None

        skills_raw = []
        skills_str = get("skills")
        if skills_str:
            # Split on comma, semicolon, pipe
            import re
            skills_raw = [s.strip() for s in re.split(r"[,;|]", skills_str) if s.strip()]

        # Build experience entry from current company/title if present
        experience = []
        company = get("current_company")
        title = get("title")
        if company or title:
            experience.append({
                "company": company or "",
                "title": title or "",
                "start": None,
                "end": None,
                "is_current": True,
                "summary": None,
            })

        warnings = []
        links = {}
        linkedin = get("linkedin")
        github = get("github")
        if linkedin:
            links["linkedin"] = linkedin
        if github:
            links["github"] = github

        return RawExtraction(
            source_id=self.source_id,
            source_type=self.source_type,
            extracted_at=datetime.utcnow(),
            base_confidence=self.BASE_CONFIDENCE,
            full_name=name,
            emails=[email] if email else [],
            phones=[get("phone")] if get("phone") else [],
            location_raw=get("location"),
            headline=get("headline"),
            skills_raw=skills_raw,
            experience=experience,
            links=links,
            warnings=warnings,
        )

    def _build_column_map(self, headers: List[str]) -> dict:
        """Map canonical field name → actual CSV column name."""
        normalized = {h.lower().strip(): h for h in headers}
        col_map = {}
        for canonical, aliases in _HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized:
                    col_map[canonical] = normalized[alias]
                    break
        return col_map

    def _detect_encoding(self, raw_bytes: bytes) -> str:
        if _CHARDET_AVAILABLE:
            detected = chardet.detect(raw_bytes)
            enc = detected.get("encoding") or "utf-8"
            logger.debug("Encoding detected: %s (confidence %.2f)", enc, detected.get("confidence", 0))
            return enc
        return "utf-8"
