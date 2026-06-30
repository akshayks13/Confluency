"""
email.py — Email normalization.

Rules:
  - Lowercase domain always.
  - Lowercase local part.
  - Strip angle brackets (<user@example.com> → user@example.com).
  - Strip "mailto:" prefix.
  - Validate basic format (must contain exactly one @).
  - Do NOT strip Gmail + aliases — they are real routing addresses.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_MAILTO_RE = re.compile(r"^mailto:", re.IGNORECASE)
_ANGLE_RE = re.compile(r"^<(.+)>$")


def normalize_email(raw: Optional[str]) -> Tuple[Optional[str], float]:
    """Returns (normalized_email, confidence)."""
    if not raw or not raw.strip():
        return None, 0.0

    email = raw.strip()

    # Strip mailto: prefix
    email = _MAILTO_RE.sub("", email)

    # Strip angle brackets
    m = _ANGLE_RE.match(email)
    if m:
        email = m.group(1)

    email = email.strip()

    # Validate structure
    if "@" not in email:
        return None, 0.0

    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return None, 0.0

    # Check for double dots in local part (invalid)
    if ".." in local:
        return None, 0.0

    normalized = f"{local.lower()}@{domain.lower()}"
    return normalized, 1.0


def extract_emails_from_text(text: str) -> list[str]:
    """Extract all email addresses from a block of text."""
    found = _EMAIL_RE.findall(text)
    normalized = []
    for raw in found:
        result, conf = normalize_email(raw)
        if result and conf > 0:
            normalized.append(result)
    # Deduplicate preserving order
    seen = set()
    unique = []
    for e in normalized:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def is_gmail_alias(email: str) -> bool:
    """
    Detect Gmail + aliases (user+tag@gmail.com).
    The base address (without +tag) is the same inbox.
    """
    if not email:
        return False
    local, _, domain = email.partition("@")
    return domain.lower() in ("gmail.com", "googlemail.com") and "+" in local


def gmail_base_address(email: str) -> str:
    """Strip Gmail + alias to get the canonical inbox address."""
    local, sep, domain = email.partition("@")
    base_local = local.split("+")[0]
    return f"{base_local}{sep}{domain}"
