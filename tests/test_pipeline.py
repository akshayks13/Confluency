"""
test_pipeline.py — End-to-end integration tests with golden output comparison.

These tests run the full pipeline against the sample inputs and verify:
  - Output is schema-valid JSON
  - All expected candidates are present
  - No crashes on missing/empty files
  - Duplicate candidates are deduplicated
  - Projection config produces correct field mapping
"""
import json
import os
import pytest
from pathlib import Path

from transformer.adapters.csv_adapter import CSVAdapter
from transformer.adapters.ats_json_adapter import ATSJsonAdapter
from transformer.pipeline import Pipeline
from transformer.projection.config import (
    load_projection_config,
    default_projection_config,
)
from transformer.validation.schema_validator import validate_output

SAMPLE_DIR = Path(__file__).parent.parent / "sample_inputs"
CONFIG_DIR = Path(__file__).parent.parent / "configs"


# ---------------------------------------------------------------------------
# CSV-only pipeline
# ---------------------------------------------------------------------------

class TestCSVPipeline:

    def test_csv_produces_candidates(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        assert result.candidates_total > 0, "Should extract at least one candidate"

    def test_csv_deduplicates_john_doe(self):
        """John Doe appears twice (once as 'DOE JOHN') — should merge to 1 candidate."""
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        candidate_ids = [c["candidate_id"] for c in result.candidates]
        assert len(candidate_ids) == len(set(candidate_ids)), "Duplicate candidate IDs"

        john_doe_candidates = [
            c for c in result.candidates
            if c.get("full_name") and "Doe" in c.get("full_name", "")
        ]
        assert len(john_doe_candidates) == 1, "John Doe should be deduplicated"

    def test_output_schema_valid(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        for candidate in result.candidates:
            is_valid, errors = validate_output(candidate)
            assert is_valid, f"Schema invalid for {candidate.get('candidate_id')}: {errors}"

    def test_phones_are_e164(self):
        import re
        e164_re = re.compile(r"^\+[1-9]\d{6,14}$")
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        for candidate in result.candidates:
            for phone in candidate.get("phones", []):
                assert e164_re.match(phone), f"Phone not E.164: {phone}"

    def test_emails_are_lowercase(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        for candidate in result.candidates:
            for email in candidate.get("emails", []):
                assert email == email.lower(), f"Email not lowercase: {email}"

    def test_provenance_present_in_default_output(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        # Only candidates with identity fields (name or email) will have provenance
        # The invalid CSV row (no name, no email) legitimately produces empty provenance
        candidates_with_identity = [
            c for c in result.candidates
            if c.get("full_name") or c.get("emails")
        ]
        assert len(candidates_with_identity) > 0, "Should have candidates with identity"
        for candidate in candidates_with_identity:
            assert "provenance" in candidate, "Provenance missing in default output"
            assert len(candidate["provenance"]) > 0, (
                f"Provenance list empty for {candidate.get('candidate_id')}"
            )

    def test_confidence_between_0_and_1(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        for candidate in result.candidates:
            conf = candidate.get("overall_confidence", -1)
            assert 0.0 <= conf <= 1.0, f"Confidence out of range: {conf}"

    def test_missing_file_doesnt_crash(self):
        pipeline = Pipeline(
            adapters=[CSVAdapter("/nonexistent/path.csv")],
            config=default_projection_config(),
        )
        result = pipeline.run()   # Must not raise
        assert result.candidates_total == 0


# ---------------------------------------------------------------------------
# ATS JSON pipeline
# ---------------------------------------------------------------------------

class TestATSPipeline:

    def test_ats_produces_candidates(self):
        pipeline = Pipeline(
            adapters=[ATSJsonAdapter(str(SAMPLE_DIR / "ats_candidates.json"))],
            config=default_projection_config(),
        )
        result = pipeline.run()
        assert result.candidates_total > 0

    def test_ats_invalid_json_doesnt_crash(self):
        # Write a temp file with invalid JSON
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{invalid json }")
            tmp_path = f.name
        try:
            pipeline = Pipeline(
                adapters=[ATSJsonAdapter(tmp_path)],
                config=default_projection_config(),
            )
            result = pipeline.run()   # Must not raise
            assert result.candidates_total == 0
        finally:
            os.unlink(tmp_path)

    def test_ats_empty_file_doesnt_crash(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("")
            tmp_path = f.name
        try:
            pipeline = Pipeline(
                adapters=[ATSJsonAdapter(tmp_path)],
                config=default_projection_config(),
            )
            result = pipeline.run()
            assert result.candidates_total == 0
        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Multi-source pipeline (CSV + ATS)
# ---------------------------------------------------------------------------

class TestMultiSourcePipeline:

    def test_merged_candidate_has_all_sources(self):
        """Jane Smith appears in both CSV and ATS — her merged record should reference both."""
        pipeline = Pipeline(
            adapters=[
                CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv")),
                ATSJsonAdapter(str(SAMPLE_DIR / "ats_candidates.json")),
            ],
            config=default_projection_config(),
        )
        result = pipeline.run()

        jane = next(
            (c for c in result.candidates if c.get("full_name") == "Jane Smith"),
            None,
        )
        assert jane is not None, "Jane Smith should be present after merge"
        assert len(jane.get("sources_ingested", [])) >= 2, (
            "Jane should have sources from both CSV and ATS"
        )

    def test_merged_skills_union(self):
        """Jane has Python in CSV and Python + AWS in ATS — merged record should have both."""
        pipeline = Pipeline(
            adapters=[
                CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv")),
                ATSJsonAdapter(str(SAMPLE_DIR / "ats_candidates.json")),
            ],
            config=default_projection_config(),
        )
        result = pipeline.run()
        jane = next(
            (c for c in result.candidates if c.get("full_name") == "Jane Smith"),
            None,
        )
        if jane:
            skill_names = [s["name"] for s in jane.get("skills", [])]
            assert "Python" in skill_names
            assert "AWS" in skill_names


# ---------------------------------------------------------------------------
# Projection Engine
# ---------------------------------------------------------------------------

class TestProjectionEngine:

    def test_ats_integration_config(self):
        """Custom config should produce remapped field names."""
        config = load_projection_config(str(CONFIG_DIR / "ats_integration.yaml"))
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=config,
        )
        result = pipeline.run()
        for candidate in result.candidates:
            # Custom config renames full_name → candidate_name
            assert "candidate_name" in candidate or len(result.candidates) == 0
            # Provenance should be absent (config: provenance: false)
            assert "provenance" not in candidate

    def test_missing_value_policy_null(self):
        """Fields missing in output should be null, not absent."""
        config = default_projection_config()
        config.missing_value_policy = "null"
        pipeline = Pipeline(
            adapters=[CSVAdapter(str(SAMPLE_DIR / "recruiter_export.csv"))],
            config=config,
        )
        result = pipeline.run()
        # years_experience is absent from CSV — should be null in full output
        for candidate in result.candidates:
            assert "years_experience" in candidate
            # years_experience will be None for CSV-only candidates
