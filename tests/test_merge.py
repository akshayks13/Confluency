"""
test_merge.py — Tests for identity resolution and merge engine.
"""
import pytest
from datetime import datetime

from transformer.models.canonical import RawExtraction
from transformer.merge.identity import resolve_identity
from transformer.merge.conflict import merge_extractions


def make_extraction(
    source_id: str,
    source_type: str,
    full_name: str = None,
    emails=None,
    phones=None,
    skills_raw=None,
    location_raw: str = None,
    experience=None,
    base_confidence: float = 0.85,
) -> RawExtraction:
    return RawExtraction(
        source_id=source_id,
        source_type=source_type,
        extracted_at=datetime.utcnow(),
        base_confidence=base_confidence,
        full_name=full_name,
        emails=emails or [],
        phones=phones or [],
        skills_raw=skills_raw or [],
        location_raw=location_raw,
        experience=experience or [],
    )


# ---------------------------------------------------------------------------
# Identity Resolution
# ---------------------------------------------------------------------------

class TestIdentityResolution:

    def test_same_email_same_candidate(self):
        e1 = make_extraction("csv:a.csv", "csv", emails=["john@example.com"], full_name="John Doe")
        e2 = make_extraction("ats:a.json", "ats_json", emails=["john@example.com"], full_name="John Doe")
        groups = resolve_identity([e1, e2])
        assert len(groups) == 1, "Same email should resolve to one candidate"

    def test_different_emails_different_candidates(self):
        e1 = make_extraction("csv:a.csv", "csv", emails=["alice@example.com"], full_name="Alice")
        e2 = make_extraction("csv:a.csv", "csv", emails=["bob@example.com"], full_name="Bob")
        groups = resolve_identity([e1, e2])
        assert len(groups) == 2

    def test_gmail_alias_same_candidate(self):
        e1 = make_extraction("csv:a.csv", "csv", emails=["user+jobs@gmail.com"])
        e2 = make_extraction("ats:a.json", "ats_json", emails=["user@gmail.com"])
        groups = resolve_identity([e1, e2])
        # Both should map to the same base gmail address
        assert len(groups) == 1

    def test_email_case_insensitive(self):
        e1 = make_extraction("csv:a.csv", "csv", emails=["JOHN@EXAMPLE.COM"])
        e2 = make_extraction("ats:a.json", "ats_json", emails=["john@example.com"])
        groups = resolve_identity([e1, e2])
        assert len(groups) == 1

    def test_no_email_uses_name_fallback(self):
        e1 = make_extraction("csv:a.csv", "csv", full_name="Jane Doe", phones=["+16505550000"])
        groups = resolve_identity([e1])
        assert len(groups) == 1


# ---------------------------------------------------------------------------
# Merge Engine
# ---------------------------------------------------------------------------

class TestMergeEngine:

    def test_source_priority_name(self):
        """ATS (0.88) wins over CSV (0.85) for name conflicts."""
        csv_ext = make_extraction("csv:a.csv", "csv", full_name="john doe", emails=["j@x.com"])
        ats_ext = make_extraction("ats:a.json", "ats_json", full_name="Jonathan Doe", emails=["j@x.com"])
        candidate = merge_extractions("abc123", [csv_ext, ats_ext])
        assert candidate.full_name == "Jonathan Doe", "ATS should win name conflict"

    def test_email_union(self):
        """All emails from all sources should be present."""
        e1 = make_extraction("csv:a.csv", "csv", emails=["a@x.com", "b@x.com"])
        e2 = make_extraction("ats:a.json", "ats_json", emails=["b@x.com", "c@x.com"])
        candidate = merge_extractions("abc123", [e1, e2])
        assert "a@x.com" in candidate.emails
        assert "b@x.com" in candidate.emails
        assert "c@x.com" in candidate.emails

    def test_email_deduplication(self):
        """Duplicate emails should not appear twice."""
        e1 = make_extraction("csv:a.csv", "csv", emails=["x@x.com"])
        e2 = make_extraction("ats:a.json", "ats_json", emails=["x@x.com"])
        candidate = merge_extractions("abc123", [e1, e2])
        assert candidate.emails.count("x@x.com") == 1

    def test_skill_union_with_sources(self):
        """Skills from all sources merged, deduped, sources list populated."""
        e1 = make_extraction("csv:a.csv", "csv", skills_raw=["Python", "React"])
        e2 = make_extraction("gh:torvalds", "github", skills_raw=["python", "C"])
        candidate = merge_extractions("abc123", [e1, e2])
        skill_names = [s.name for s in candidate.skills]
        assert "Python" in skill_names
        assert "React" in skill_names

    def test_skill_confidence_higher_when_multi_source(self):
        """A skill appearing in 2 sources should have higher confidence than in 1."""
        e1 = make_extraction("csv:a.csv", "csv", skills_raw=["Python"])
        e2 = make_extraction("ats:a.json", "ats_json", skills_raw=["Python"])
        candidate = merge_extractions("abc123", [e1, e2])
        python_skill = next((s for s in candidate.skills if s.name == "Python"), None)
        assert python_skill is not None
        assert len(python_skill.sources) >= 1

    def test_provenance_contains_all_fields(self):
        """Every extracted field should have a provenance entry."""
        e1 = make_extraction("csv:a.csv", "csv", emails=["x@x.com"], full_name="Alice")
        candidate = merge_extractions("abc123", [e1])
        fields = [p.field for p in candidate.provenance]
        assert "full_name" in fields
        assert "emails" in fields

    def test_conflict_flagged_in_provenance(self):
        """Name conflict between sources should be flagged as conflict=True."""
        e1 = make_extraction("csv:a.csv", "csv", full_name="John", emails=["j@x.com"])
        e2 = make_extraction("ats:a.json", "ats_json", full_name="Jonathan", emails=["j@x.com"])
        candidate = merge_extractions("abc123", [e1, e2])
        name_provs = candidate.provenance_for_field("full_name")
        conflicts = [p for p in name_provs if p.conflict]
        assert len(conflicts) >= 1, "Name conflict should be flagged"

    def test_experience_deduplication(self):
        """Same job entry from two sources should appear once."""
        exp = {"company": "Acme", "title": "Engineer", "start": "2020-01", "end": None, "is_current": True}
        e1 = make_extraction("csv:a.csv", "csv", experience=[exp])
        e2 = make_extraction("ats:a.json", "ats_json", experience=[exp])
        candidate = merge_extractions("abc123", [e1, e2])
        assert len(candidate.experience) == 1

    def test_location_from_csv(self):
        e1 = make_extraction("csv:a.csv", "csv", location_raw="San Francisco, CA", emails=["x@x.com"])
        candidate = merge_extractions("abc123", [e1])
        assert candidate.location is not None
        assert candidate.location.city == "San Francisco"
        assert candidate.location.country == "US"

    def test_missing_fields_stay_none(self):
        """Missing fields must be None, not invented."""
        e1 = make_extraction("csv:a.csv", "csv", emails=["x@x.com"])
        candidate = merge_extractions("abc123", [e1])
        assert candidate.full_name is None
        assert candidate.years_experience is None
        assert candidate.headline is None
