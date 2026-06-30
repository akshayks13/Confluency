"""
phone.py — Phone normalization to E.164 format.

Uses Google's libphonenumber (via the `phonenumbers` Python package).
This is the only correct way to handle phone normalization globally.

Rules:
  - Output format: +[country_code][number] (E.164), e.g. "+16505551234"
  - Default region: US (assumption logged in provenance)
  - Drop extensions (ext., x followed by digits)
  - Return None for strings that cannot be parsed as a valid phone number
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

try:
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberFormat
    _PHONENUMBERS_AVAILABLE = True
except ImportError:
    phonenumbers = None  # type: ignore[assignment]
    NumberParseException = Exception  # type: ignore[assignment,misc]
    PhoneNumberFormat = None  # type: ignore[assignment]
    _PHONENUMBERS_AVAILABLE = False

_EXTENSION_RE = re.compile(r"\s*(ext\.?|x)\s*\d+$", re.IGNORECASE)
_PHONE_CHARS_RE = re.compile(r"[^\d\s\+\(\)\-\.]")
_DIGIT_RE = re.compile(r"\d")


def normalize_phone(
    raw: Optional[str],
    default_region: str = "US",
) -> Tuple[Optional[str], float]:
    """
    Returns (e164_phone, confidence).
    confidence is lower when we had to assume a region.
    """
    if not raw or not raw.strip():
        return None, 0.0

    phone = raw.strip()

    # Drop extensions
    phone = _EXTENSION_RE.sub("", phone).strip()

    # Quick sanity: need at least 7 digits
    if len(_DIGIT_RE.findall(phone)) < 7:
        return None, 0.0

    if not _PHONENUMBERS_AVAILABLE:
        # Fallback: preserve explicit international prefixes. Without the
        # library we cannot validate country-specific numbering plans, but we
        # can avoid turning "+44..." or "+91..." into a US number.
        digits = "".join(_DIGIT_RE.findall(phone))
        if not 7 <= len(digits) <= 15:
            return None, 0.0

        if phone.startswith("+"):
            return f"+{digits}", 0.6
        if phone.startswith("00") and len(digits) > 2:
            return f"+{digits[2:]}", 0.6
        if default_region.upper() == "US" and len(digits) == 10:
            return f"+1{digits}", 0.5
        if default_region.upper() == "US" and len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}", 0.5
        return None, 0.0

    # Try to parse with the phonenumbers library
    assumed_region = False
    try:
        parsed = phonenumbers.parse(phone, default_region)
    except NumberParseException:
        return None, 0.0

    if not phonenumbers.is_valid_number(parsed):
        return None, 0.0

    # Detect if we assumed the region (no leading +/country code in raw input)
    if not phone.strip().startswith("+") and not phone.strip().startswith("00"):
        assumed_region = True

    e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    confidence = 0.85 if assumed_region else 1.0
    return e164, confidence


def extract_phones_from_text(
    text: str,
    default_region: str = "US",
) -> List[str]:
    """Extract and normalize all phone numbers from a block of text."""
    if not _PHONENUMBERS_AVAILABLE:
        return []

    results = []
    seen = set()
    for match in phonenumbers.PhoneNumberMatcher(text, default_region):
        e164 = phonenumbers.format_number(match.number, PhoneNumberFormat.E164)
        if e164 not in seen:
            seen.add(e164)
            results.append(e164)
    return results
