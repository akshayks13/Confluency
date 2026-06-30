"""
location.py — Location normalization to {city, region, country: ISO-3166-1 alpha-2}.

Strategy:
  - Regex-based city/state/country extraction.
  - pycountry for country code normalization.
  - Special handling for "Remote" → is_remote flag.
  - Never invent a city/country from a partial string.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

try:
    import pycountry
    _PYCOUNTRY_AVAILABLE = True
except ImportError:
    pycountry = None  # type: ignore[assignment]
    _PYCOUNTRY_AVAILABLE = False

from transformer.models.canonical import Location


# Hand-built common US state abbreviations
_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

# Common country name overrides (pycountry sometimes misses abbreviations)
_COUNTRY_OVERRIDES = {
    "usa": "US", "u.s.a": "US", "u.s.": "US", "us": "US",
    "uk": "GB", "u.k.": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "uae": "AE", "u.a.e.": "AE",
    "south korea": "KR", "north korea": "KP",
    "taiwan": "TW",
    "russia": "RU",
    "vietnam": "VN",
    "czech republic": "CZ",
    "iran": "IR",
    "syria": "SY",
}

_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)


def normalize_location(raw: Optional[str]) -> Tuple[Location, float]:
    """
    Returns (Location, confidence).
    Location.raw always contains the original string.
    """
    if not raw or not raw.strip():
        return Location(raw=""), 0.0

    raw_stripped = raw.strip()
    loc = Location(raw=raw_stripped)

    # Remote check
    if _REMOTE_RE.search(raw_stripped):
        # Don't fill city/region/country for "Remote"
        return loc, 0.7   # We know it's remote but can't ISO-code it

    # Split on comma — handles "City, ST", "City, State", "City, Country"
    parts = [p.strip() for p in raw_stripped.split(",")]

    if len(parts) == 1:
        # Could be just a country or just a city — ambiguous
        resolved_country = _resolve_country(parts[0])
        if resolved_country:
            loc.country = resolved_country
            return loc, 0.8
        else:
            loc.city = _title(parts[0])
            return loc, 0.5   # Assumed it's a city — low confidence

    elif len(parts) == 2:
        loc.city = _title(parts[0])
        # Second part: state abbrev, state name, or country
        second = parts[1].strip()
        state = _resolve_us_state(second)
        if state:
            loc.region = state
            loc.country = "US"
            return loc, 0.95
        country = _resolve_country(second)
        if country:
            loc.country = country
            return loc, 0.90
        # Treat as region (state/province) with unknown country
        loc.region = _title(second)
        return loc, 0.75

    elif len(parts) >= 3:
        # "City, State/Region, Country"
        loc.city = _title(parts[0])
        state = _resolve_us_state(parts[1])
        if state:
            loc.region = state
        else:
            loc.region = _title(parts[1])
        country = _resolve_country(parts[-1])
        if country:
            loc.country = country
        return loc, 0.90

    return loc, 0.0


def _resolve_us_state(text: str) -> Optional[str]:
    """Return US state abbreviation or None."""
    t = text.strip().lower()
    if t in _US_STATES:
        return _US_STATES[t]
    upper = text.strip().upper()
    if upper in _US_STATES.values():
        return upper
    return None


def _resolve_country(text: str) -> Optional[str]:
    """Return ISO-3166-1 alpha-2 country code or None."""
    t = text.strip().lower()
    if t in _COUNTRY_OVERRIDES:
        return _COUNTRY_OVERRIDES[t]

    if not _PYCOUNTRY_AVAILABLE:
        return None

    # Try exact alpha-2 match
    try:
        c = pycountry.countries.get(alpha_2=text.strip().upper())
        if c:
            return c.alpha_2
    except (KeyError, AttributeError):
        pass

    # Try name search
    try:
        results = pycountry.countries.search_fuzzy(text.strip())
        if results:
            return results[0].alpha_2
    except LookupError:
        pass

    return None


def _title(s: str) -> str:
    return s.strip().title()
