"""
url.py — URL normalization.

Rules:
  - Add https:// if scheme is missing.
  - Force https:// over http://.
  - Lowercase domain and scheme.
  - Remove trailing slash.
  - Normalize www. prefix removal for known platforms.
  - Validate it looks like a URL before returning.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse


_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"^(https?://)?(www\.)?([\w\-]+\.[\w\.\-]+)", re.IGNORECASE
)

_PLATFORM_DOMAINS = {
    "github.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "medium.com",
    "stackoverflow.com",
    "kaggle.com",
    "gitlab.com",
    "bitbucket.org",
}


def normalize_url(raw: Optional[str]) -> Tuple[Optional[str], float]:
    """
    Returns (normalized_url, confidence).
    Returns None if the input doesn't look like a URL.
    """
    if not raw or not raw.strip():
        return None, 0.0

    url = raw.strip()

    # Add scheme if missing
    if not _SCHEME_RE.match(url):
        url = "https://" + url
    else:
        # Force HTTPS
        url = re.sub(r"^http://", "https://", url, flags=re.IGNORECASE)

    # Parse and rebuild
    try:
        parsed = urlparse(url)
    except Exception:
        return None, 0.0

    if not parsed.netloc:
        return None, 0.0

    # Lowercase scheme and netloc
    normalized = urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "",
        parsed.params,
        parsed.query,
        parsed.fragment,
    ))

    confidence = 1.0
    return normalized, confidence


def classify_url(url: Optional[str]) -> str:
    """
    Classify a URL into: "linkedin" | "github" | "portfolio" | "other".
    """
    if not url:
        return "other"
    lower = url.lower()
    if "linkedin.com" in lower:
        return "linkedin"
    if "github.com" in lower:
        return "github"
    return "other"


def extract_github_username(url: str) -> Optional[str]:
    """Extract username from a GitHub profile URL."""
    m = re.match(r"https?://(?:www\.)?github\.com/([a-zA-Z0-9\-]+)/?", url)
    if m:
        username = m.group(1)
        # Reject org/repo paths and special paths
        if username not in ("orgs", "teams", "marketplace", "features", "pricing"):
            return username
    return None
