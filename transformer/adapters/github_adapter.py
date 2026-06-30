"""
github_adapter.py — GitHub public API source adapter.

Extracts from: GET /users/{username} and GET /users/{username}/repos
Languages used across repos → proxy for technical skills.

Never scrapes — uses only the public unauthenticated REST API.
Rate limit: 60 req/hr unauthenticated. Uses optional GITHUB_TOKEN env var.

Confidence notes:
  - Profile fields (name, bio, location): 0.75 (self-reported, unstructured bio)
  - Languages: 0.65 (proxy signal — not a skills list)
  - Repos/pinned: not used (too noisy)
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from transformer.adapters.base import SourceAdapter
from transformer.models.canonical import RawExtraction
from transformer.normalizers.url import extract_github_username

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_MAX_REPOS = 30          # Limit to avoid too many API calls
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = [1, 2, 4]   # seconds


class GitHubAdapter(SourceAdapter):
    source_type = "github"
    BASE_CONFIDENCE = 0.75

    def __init__(self, github_url: str):
        self.github_url = github_url
        self.username = extract_github_username(github_url)
        self.source_id = f"github:{self.username or 'unknown'}"
        token = os.environ.get("GITHUB_TOKEN")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def extract(self) -> List[RawExtraction]:
        if not _REQUESTS_AVAILABLE:
            self._warn("requests library not installed — GitHub adapter disabled")
            return []

        if not self.username:
            self._warn("Could not extract GitHub username from URL", url=self.github_url)
            return []

        profile = self._fetch_user_profile()
        if profile is None:
            return []

        languages = self._fetch_languages()

        extraction = self._build_extraction(profile, languages)
        self._info("Extraction complete", username=self.username, languages=len(languages))
        return [extraction]

    # ------------------------------------------------------------------

    def _fetch_user_profile(self) -> Optional[Dict[str, Any]]:
        url = f"{_GITHUB_API_BASE}/users/{self.username}"
        resp = self._get(url)
        if resp is None:
            return None
        if resp.status_code == 404:
            self._warn("GitHub user not found", username=self.username)
            return None
        if resp.status_code != 200:
            self._warn("GitHub API error", status=resp.status_code, username=self.username)
            return None
        return resp.json()

    def _fetch_languages(self) -> Dict[str, int]:
        """Aggregate language byte-counts across the user's repos."""
        repos_url = f"{_GITHUB_API_BASE}/users/{self.username}/repos"
        params = {"per_page": _MAX_REPOS, "sort": "pushed", "type": "owner"}
        resp = self._get(repos_url, params=params)
        if resp is None or resp.status_code != 200:
            return {}

        repos = resp.json()
        if not isinstance(repos, list):
            return {}

        aggregated: Dict[str, int] = {}
        for repo in repos:
            if repo.get("fork"):
                continue   # Skip forks — not the user's own code
            lang = repo.get("language")
            if lang:
                # bytes from the primary language
                size = repo.get("size", 0)
                aggregated[lang] = aggregated.get(lang, 0) + size

        return aggregated

    def _build_extraction(
        self, profile: Dict[str, Any], languages: Dict[str, int]
    ) -> RawExtraction:
        warnings = []

        # Bio may contain multiple emails — extract all
        bio = profile.get("bio") or ""
        emails_from_bio = self._extract_emails_from_text(bio)

        # GitHub profile email (may be None if user hides it)
        profile_email = profile.get("email")
        emails: List[str] = []
        if profile_email:
            emails.append(profile_email.strip().lower())
        emails.extend(e for e in emails_from_bio if e not in emails)

        # Location
        location_raw = profile.get("location")

        # Links
        blog = profile.get("blog") or ""
        links: Dict[str, str] = {
            "github": f"https://github.com/{self.username}",
        }
        if blog and blog.startswith("http"):
            links["portfolio"] = blog.strip().rstrip("/")
        elif blog:
            links["portfolio"] = f"https://{blog.strip().rstrip('/')}"

        # Skills from languages — ordered by byte count descending
        skill_names = [
            lang for lang, _ in sorted(languages.items(), key=lambda x: -x[1])
        ]
        if not skill_names:
            warnings.append("no_languages_found")

        # Headline from bio
        headline = bio.strip()[:200] if bio else None

        # Company
        company = profile.get("company") or ""
        company = re.sub(r"^@", "", company).strip()
        experience = []
        if company:
            experience.append({
                "company": company,
                "title": profile.get("bio") or "",  # bio sometimes has title
                "start": None,
                "end": None,
                "is_current": True,
                "summary": None,
            })

        return RawExtraction(
            source_id=self.source_id,
            source_type=self.source_type,
            extracted_at=datetime.utcnow(),
            base_confidence=self.BASE_CONFIDENCE,
            full_name=profile.get("name") or None,
            emails=emails,
            phones=[],
            location_raw=location_raw,
            headline=headline,
            skills_raw=skill_names,
            experience=experience,
            links=links,
            warnings=warnings,
        )

    def _get(self, url: str, params: Optional[dict] = None):
        """HTTP GET with retry + exponential backoff."""
        import requests as req
        for attempt, wait in enumerate(_RETRY_BACKOFF):
            try:
                resp = req.get(url, headers=self._headers, params=params, timeout=10)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", wait))
                    self._warn(
                        "Rate limited",
                        retry_after=retry_after,
                        attempt=attempt + 1,
                    )
                    time.sleep(retry_after)
                    continue
                return resp
            except req.exceptions.RequestException as e:
                self._warn("Request failed", url=url, error=str(e), attempt=attempt + 1)
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(wait)
        return None

    @staticmethod
    def _extract_emails_from_text(text: str) -> List[str]:
        pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
        return [m.lower() for m in pattern.findall(text)]
