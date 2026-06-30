"""
date_norm.py — Date normalization to YYYY-MM.

Rules:
  - Output format: "YYYY-MM" (ISO 8601 year-month)
  - Year-only inputs ("2020") are returned as "2020" — do NOT invent a month.
  - "Present", "Current", "Now", "—", "-" → None (caller sets is_current=True)
  - Parse common formats: "Jan 2020", "January 2020", "2020-01", "01/2020", etc.
  - Return None for unparseable dates (never guess).
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "oct": "10", "nov": "11", "dec": "12",
}

_CURRENT_TERMS = frozenset([
    "present", "current", "now", "today", "ongoing", "—", "–", "-", "till date",
    "till now", "to date",
])

# Patterns ordered from most specific to least specific
_PATTERNS = [
    # "2020-01" or "2020/01"
    (re.compile(r"^(\d{4})[-/](\d{1,2})$"), "year_month_sep"),
    # "01/2020" or "01-2020"
    (re.compile(r"^(\d{1,2})[-/](\d{4})$"), "month_year_sep"),
    # "January 2020" or "Jan 2020" or "jan. 2020"
    (re.compile(r"^([a-zA-Z]+)\.?\s+(\d{4})$"), "month_name_year"),
    # "2020 January"
    (re.compile(r"^(\d{4})\s+([a-zA-Z]+)$"), "year_month_name"),
    # "Jan '20" or "Jan '20"
    (re.compile(r"^([a-zA-Z]+)\.?\s+[''`](\d{2})$"), "month_name_short_year"),
    # "2020" — year only
    (re.compile(r"^(\d{4})$"), "year_only"),
]


def normalize_date(raw: Optional[str]) -> Tuple[Optional[str], bool, float]:
    """
    Returns (normalized_date, is_current, confidence).

    - normalized_date: "YYYY-MM", "YYYY", or None
    - is_current: True if raw indicated "Present"/"Current"/etc.
    - confidence: [0.0, 1.0]
    """
    if not raw:
        return None, False, 0.0

    text = raw.strip().lower()

    # Check for "current" terms
    if text in _CURRENT_TERMS:
        return None, True, 1.0

    # Try each pattern
    for pattern, kind in _PATTERNS:
        m = pattern.match(text.strip())
        if m:
            return _apply_pattern(kind, m)

    return None, False, 0.0


def _apply_pattern(kind: str, m: re.Match) -> Tuple[Optional[str], bool, float]:
    if kind == "year_month_sep":
        year, month = m.group(1), m.group(2).zfill(2)
        if _valid_month(month):
            return f"{year}-{month}", False, 1.0

    elif kind == "month_year_sep":
        month, year = m.group(1).zfill(2), m.group(2)
        if _valid_month(month):
            return f"{year}-{month}", False, 1.0

    elif kind == "month_name_year":
        month_name = m.group(1).lower().rstrip(".")
        year = m.group(2)
        month = _MONTH_MAP.get(month_name)
        if month:
            return f"{year}-{month}", False, 1.0

    elif kind == "year_month_name":
        year = m.group(1)
        month_name = m.group(2).lower()
        month = _MONTH_MAP.get(month_name)
        if month:
            return f"{year}-{month}", False, 0.95

    elif kind == "month_name_short_year":
        month_name = m.group(1).lower().rstrip(".")
        short_year = m.group(2)
        year = _expand_year(short_year)
        month = _MONTH_MAP.get(month_name)
        if month and year:
            return f"{year}-{month}", False, 0.85   # Slight penalty for 2-digit year

    elif kind == "year_only":
        year = m.group(1)
        if 1950 <= int(year) <= 2050:
            return year, False, 0.8   # Lower confidence — month unknown

    return None, False, 0.0


def normalize_date_range(raw: Optional[str]) -> Tuple[
    Optional[str], Optional[str], bool, float
]:
    """
    Parse a date range like "March 2019 - Present" or "2018-01 – 2020-06".
    Returns (start, end, is_current, confidence).
    """
    if not raw:
        return None, None, False, 0.0

    # Split on common range separators
    for sep in [" – ", " — ", " - ", "–", "—", " to "]:
        if sep in raw:
            parts = raw.split(sep, 1)
            start_raw, end_raw = parts[0].strip(), parts[1].strip()
            start, _, start_conf = normalize_date(start_raw)
            end, is_current, end_conf = normalize_date(end_raw)
            confidence = min(start_conf, end_conf) if not is_current else start_conf
            return start, end, is_current, confidence

    # Single date — treat as start
    date, is_current, conf = normalize_date(raw)
    return date, None, is_current, conf


def _valid_month(month_str: str) -> bool:
    try:
        return 1 <= int(month_str) <= 12
    except ValueError:
        return False


def _expand_year(short: str) -> Optional[str]:
    """'20' → '2020', '95' → '1995'. Cutoff at 30."""
    try:
        y = int(short)
        return f"20{short.zfill(2)}" if y <= 30 else f"19{short.zfill(2)}"
    except ValueError:
        return None
