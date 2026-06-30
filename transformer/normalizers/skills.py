"""
skills.py — Skills normalization via alias taxonomy.

Rules:
  - Lookup alias → canonical name from taxonomy JSON.
  - Case-insensitive alias matching.
  - Unknown skills: preserved as-is with lower confidence.
  - Never hallucinate or infer skills not present in input.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_TAXONOMY_PATH = Path(__file__).parent.parent.parent / "resources" / "skills_taxonomy.json"
_SPLIT_RE = re.compile(r"[,;|•·/]|\band\b", re.IGNORECASE)
_CLEAN_RE = re.compile(r"[^\w\s\.\+\#\-]")


def _load_taxonomy() -> Dict[str, str]:
    try:
        with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k.lower(): v for k, v in data.get("aliases", {}).items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_TAXONOMY: Dict[str, str] = _load_taxonomy()


def normalize_skill(raw: Optional[str]) -> Tuple[str, float, bool]:
    """
    Returns (canonical_name, confidence, is_known).
    - confidence: 1.0 if in taxonomy, 0.7 if not (preserved as-is)
    - is_known: True if found in taxonomy
    """
    if not raw or not raw.strip():
        return "", 0.0, False

    cleaned = _CLEAN_RE.sub("", raw.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        return "", 0.0, False

    lookup = cleaned.lower()
    if lookup in _TAXONOMY:
        return _TAXONOMY[lookup], 1.0, True

    # Try partial match (e.g. "Python 3.x" → lookup "python")
    for alias, canonical in _TAXONOMY.items():
        if lookup.startswith(alias + " ") or lookup.startswith(alias + "."):
            return canonical, 0.9, True

    # Unknown skill — preserve as-is with title casing
    return cleaned.title(), 0.7, False


def normalize_skills_list(raw_skills: List[str]) -> List[Tuple[str, float, bool]]:
    """
    Normalize a list of raw skill strings.
    Returns list of (canonical_name, confidence, is_known).
    Deduplicates by canonical name (keep highest confidence).
    """
    result: Dict[str, Tuple[str, float, bool]] = {}
    for raw in raw_skills:
        name, conf, known = normalize_skill(raw)
        if not name:
            continue
        if name not in result or conf > result[name][1]:
            result[name] = (name, conf, known)
    return list(result.values())


def extract_skills_from_text(text: str) -> List[str]:
    """
    Extract skill tokens from free text by matching against the taxonomy aliases.
    Conservative — only emits skills that have a taxonomy match.
    """
    found = []
    text_lower = text.lower()
    for alias in sorted(_TAXONOMY.keys(), key=len, reverse=True):
        # Word-boundary match
        pattern = re.compile(
            r"(?<![a-zA-Z0-9\-])" + re.escape(alias) + r"(?![a-zA-Z0-9\-])"
        )
        if pattern.search(text_lower):
            found.append(_TAXONOMY[alias])
    # Deduplicate preserving order
    seen = set()
    unique = []
    for s in found:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique
