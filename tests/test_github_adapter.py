"""
test_github_adapter.py — Unit tests for GitHubAdapter.

All HTTP calls are mocked via `unittest.mock.patch("requests.get")` because
the adapter does a local `import requests as req` inside _get(), so the patch
must target the canonical `requests.get` rather than the module-level alias.

Covers:
  - Happy path: profile + language extraction
  - Missing / hidden email on profile → bio fallback
  - Profile email not duplicated when it also appears in bio
  - Invalid / non-GitHub URL → empty extraction
  - 404 user not found → empty
  - 429 rate-limit → exhausted retries → empty
  - Network / RequestException → empty (never crashes)
  - 500 API error → empty
  - Forks excluded from language aggregation
  - Language skills ordered by byte-count (highest first)
  - Blog URL: bare hostname prefixed with https://
  - Blog URL: empty → no portfolio link
  - Company field: leading '@' stripped
  - No languages found → warning emitted
  - /repos failure → still returns profile with empty skills
  - Null name on profile
  - Username extraction from URL variants
  - _extract_emails_from_text static helper
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from transformer.adapters.github_adapter import GitHubAdapter
from transformer.models.canonical import RawExtraction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status: int, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = headers or {}
    return resp


_PROFILE = {
    "name": "Alice Dev",
    "email": "alice@example.com",
    "bio": "Senior engineer at Acme",
    "location": "San Francisco, CA",
    "company": "@Acme",
    "blog": "https://alice.dev",
}

_REPOS = [
    {"language": "Python",     "size": 5000, "fork": False},
    {"language": "JavaScript", "size": 3000, "fork": False},
    {"language": "Python",     "size": 2000, "fork": False},  # second Python repo
    {"language": "Go",         "size": 1000, "fork": True},   # fork — must be excluded
    {"language": None,         "size": 100,  "fork": False},  # no language
]

# requests.get raises this for network errors
_REQUEST_EXCEPTION_PATH = "requests.exceptions.RequestException"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestGitHubAdapterHappyPath:

    @patch("requests.get")
    def test_extract_returns_one_extraction(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert len(result) == 1
        assert isinstance(result[0], RawExtraction)

    @patch("requests.get")
    def test_full_name_extracted(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].full_name == "Alice Dev"

    @patch("requests.get")
    def test_email_from_profile(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert "alice@example.com" in result[0].emails

    @patch("requests.get")
    def test_location_raw_passed_through(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].location_raw == "San Francisco, CA"

    @patch("requests.get")
    def test_company_at_sign_stripped(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        experience = result[0].experience
        assert len(experience) == 1
        assert experience[0]["company"] == "Acme"

    @patch("requests.get")
    def test_github_link_always_present(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].links.get("github") == "https://github.com/alice"

    @patch("requests.get")
    def test_blog_https_link_preserved(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].links.get("portfolio") == "https://alice.dev"

    @patch("requests.get")
    def test_source_type_is_github(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].source_type == "github"

    @patch("requests.get")
    def test_base_confidence_is_075(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].base_confidence == 0.75


# ---------------------------------------------------------------------------
# Language / Skills extraction
# ---------------------------------------------------------------------------

class TestLanguageExtraction:

    @patch("requests.get")
    def test_forks_excluded(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        # Go only appears in a fork — must not surface as a skill
        assert "Go" not in result[0].skills_raw

    @patch("requests.get")
    def test_languages_aggregated_and_ordered_by_bytes(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        skills = result[0].skills_raw
        # Python: 5000+2000=7000 > JavaScript: 3000 → Python first
        assert skills[0] == "Python"
        assert "JavaScript" in skills

    @patch("requests.get")
    def test_none_language_repo_ignored(self, mock_get):
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, _REPOS),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert None not in result[0].skills_raw

    @patch("requests.get")
    def test_no_languages_adds_warning(self, mock_get):
        no_lang_repos = [{"language": None, "size": 0, "fork": False}]
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(200, no_lang_repos),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert "no_languages_found" in result[0].warnings


# ---------------------------------------------------------------------------
# Email edge cases
# ---------------------------------------------------------------------------

class TestEmailExtraction:

    @patch("requests.get")
    def test_email_hidden_on_profile_bio_fallback(self, mock_get):
        profile = {**_PROFILE, "email": None, "bio": "Contact me at hidden@corp.io"}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert "hidden@corp.io" in result[0].emails

    @patch("requests.get")
    def test_profile_email_not_duplicated_from_bio(self, mock_get):
        profile = {**_PROFILE, "email": "alice@example.com",
                   "bio": "Reach me at alice@example.com"}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].emails.count("alice@example.com") == 1

    @patch("requests.get")
    def test_emails_lowercased(self, mock_get):
        profile = {**_PROFILE, "email": "Alice@EXAMPLE.COM"}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert all(e == e.lower() for e in result[0].emails)


# ---------------------------------------------------------------------------
# Blog URL normalisation
# ---------------------------------------------------------------------------

class TestBlogUrl:

    @patch("requests.get")
    def test_bare_blog_prefixed_with_https(self, mock_get):
        profile = {**_PROFILE, "blog": "alice.dev"}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].links.get("portfolio") == "https://alice.dev"

    @patch("requests.get")
    def test_empty_blog_no_portfolio_link(self, mock_get):
        profile = {**_PROFILE, "blog": ""}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert "portfolio" not in result[0].links


# ---------------------------------------------------------------------------
# Error / edge cases — adapter must NEVER crash
# ---------------------------------------------------------------------------

class TestGitHubAdapterErrorCases:

    def test_invalid_url_returns_empty(self):
        result = GitHubAdapter("https://notgithub.com/user").extract()
        assert result == []

    def test_empty_url_returns_empty(self):
        result = GitHubAdapter("").extract()
        assert result == []

    @patch("requests.get")
    def test_user_not_found_404_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(404)
        result = GitHubAdapter("https://github.com/ghost_user_xyz").extract()
        assert result == []

    @patch("requests.get")
    def test_api_error_500_returns_empty(self, mock_get):
        mock_get.return_value = _mock_response(500)
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result == []

    @patch("requests.get")
    def test_network_error_returns_empty(self, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.RequestException("no network")
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result == []

    @patch("time.sleep")
    @patch("requests.get")
    def test_rate_limited_exhausts_retries_returns_empty(self, mock_get, _sleep):
        """429 on every attempt → graceful empty result, no crash."""
        mock_get.return_value = _mock_response(429, headers={"Retry-After": "0"})
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result == []

    @patch("requests.get")
    def test_repos_api_failure_returns_profile_with_empty_skills(self, mock_get):
        """If /repos fails we still get the profile extraction (skills=[])."""
        mock_get.side_effect = [
            _mock_response(200, _PROFILE),
            _mock_response(503),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert len(result) == 1
        assert result[0].skills_raw == []

    @patch("requests.get")
    def test_null_name_on_profile(self, mock_get):
        profile = {**_PROFILE, "name": None}
        mock_get.side_effect = [
            _mock_response(200, profile),
            _mock_response(200, []),
        ]
        result = GitHubAdapter("https://github.com/alice").extract()
        assert result[0].full_name is None


# ---------------------------------------------------------------------------
# Username extraction from URL variants
# ---------------------------------------------------------------------------

class TestUsernameExtraction:

    def test_standard_url(self):
        assert GitHubAdapter("https://github.com/alice").username == "alice"

    def test_trailing_slash(self):
        assert GitHubAdapter("https://github.com/alice/").username == "alice"

    def test_url_with_repo_path(self):
        assert GitHubAdapter("https://github.com/alice/my-repo").username == "alice"

    def test_http_scheme(self):
        assert GitHubAdapter("http://github.com/alice").username == "alice"


# ---------------------------------------------------------------------------
# Static helper: _extract_emails_from_text
# ---------------------------------------------------------------------------

class TestExtractEmailsFromText:

    def test_single_email(self):
        emails = GitHubAdapter._extract_emails_from_text("Contact me at foo@bar.com")
        assert emails == ["foo@bar.com"]

    def test_multiple_emails(self):
        emails = GitHubAdapter._extract_emails_from_text("a@x.com and b@y.io")
        assert set(emails) == {"a@x.com", "b@y.io"}

    def test_no_emails(self):
        assert GitHubAdapter._extract_emails_from_text("no email here") == []

    def test_emails_lowercased(self):
        emails = GitHubAdapter._extract_emails_from_text("Alice@EXAMPLE.COM")
        assert emails == ["alice@example.com"]


# ---------------------------------------------------------------------------
# Live integration tests — skipped unless GITHUB_TOKEN is set
# These make REAL HTTP calls to api.github.com
# Run with: GITHUB_TOKEN=ghp_xxx pytest tests/test_github_adapter.py -v -k live
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestGitHubAdapterLive:
    """
    Real HTTP tests against the GitHub API.
    Skipped automatically unless GITHUB_TOKEN env var is set.
    Uses torvalds/linux as a stable, public profile.
    """

    @pytest.fixture(autouse=True)
    def require_token(self):
        import os
        if not os.environ.get("GITHUB_TOKEN"):
            pytest.skip("GITHUB_TOKEN not set — skipping live tests")

    def test_live_torvalds_returns_extraction(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert len(result) == 1

    def test_live_torvalds_has_name(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert result[0].full_name is not None
        assert "Torvalds" in result[0].full_name

    def test_live_torvalds_has_location(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert result[0].location_raw is not None

    def test_live_torvalds_has_languages(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert len(result[0].skills_raw) > 0
        # Linux kernel is C — should appear
        assert "C" in result[0].skills_raw

    def test_live_torvalds_github_link(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert result[0].links.get("github") == "https://github.com/torvalds"

    def test_live_confidence_correct(self):
        result = GitHubAdapter("https://github.com/torvalds").extract()
        assert result[0].base_confidence == 0.75

    def test_live_nonexistent_user_returns_empty(self):
        result = GitHubAdapter("https://github.com/this_user_definitely_does_not_exist_xyz_abc_999").extract()
        assert result == []
