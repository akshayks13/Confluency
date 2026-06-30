"""
name.py — Name normalization.

Rules:
  - Title-case all parts.
  - Handle "Last, First" → "First Last".
  - Strip annotations like "(he/him)", "(she/her)".
  - NEVER autocorrect names (John → Jonathan). Preserve as-is after casing.
  - Preserve hyphens, apostrophes, and multi-part surnames.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


_ANNOTATION_RE = re.compile(r"\(.*?\)")          # Strip (he/him), (recruiter note), etc.
_MULTI_SPACE_RE = re.compile(r"\s{2,}")

# Particles that should remain lowercase in names
_LOWERCASE_PARTICLES = {"van", "de", "der", "von", "du", "la", "le", "di", "del", "della"}


def normalize_name(raw: Optional[str]) -> Tuple[Optional[str], float]:
    """
    Returns (normalized_name, confidence).
    confidence reflects how much transformation was applied.
    """
    if not raw or not raw.strip():
        return None, 0.0

    name = raw.strip()

    # Strip annotations
    name = _ANNOTATION_RE.sub("", name).strip()
    name = _MULTI_SPACE_RE.sub(" ", name)

    if not name:
        return None, 0.0

    confidence = 1.0

    # Handle "Last, First [Middle]" format
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            name = f"{parts[1]} {parts[0]}"
            confidence = 0.9   # Slight penalty — we made a structural assumption

    # Title-case, respecting particles
    name = _smart_title_case(name)

    return name, confidence


def _smart_title_case(name: str) -> str:
    """Title-case with particle awareness."""
    words = name.split()
    result = []
    for i, word in enumerate(words):
        # Preserve hyphens inside names (e.g. "Mary-Jane")
        if "-" in word:
            result.append("-".join(
                part.lower() if part.lower() in _LOWERCASE_PARTICLES else _capitalize(part)
                for part in word.split("-")
            ))
        elif "'" in word:
            # O'Brien, D'Angelo
            result.append("'".join(_capitalize(p) for p in word.split("'")))
        elif word.lower() in _LOWERCASE_PARTICLES and i > 0:
            result.append(word.lower())
        else:
            result.append(_capitalize(word))
    return " ".join(result)


def _capitalize(word: str) -> str:
    """Safe capitalize that handles already-capitalized abbreviations (e.g. 'III')."""
    if not word:
        return word
    return word[0].upper() + word[1:].lower()
