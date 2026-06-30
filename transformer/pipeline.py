"""
pipeline.py — End-to-end orchestrator.

Stages (in order):
  1. Adapt   — Run all source adapters, collect RawExtractions
  2. Identity — Group extractions by candidate identity
  3. Merge   — Merge each group into a CanonicalCandidate
  4. Score   — Compute overall_confidence for each candidate
  5. Project — Apply ProjectionConfig to produce output dicts
  6. Validate — Schema + semantic validation of each output dict

Any exception in stages 1–4 for a single candidate is caught and logged.
Stage 6 warnings are logged but do not suppress output.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from transformer.adapters.base import SourceAdapter
from transformer.confidence import score_candidate
from transformer.merge.conflict import merge_extractions
from transformer.merge.identity import resolve_identity
from transformer.models.canonical import CanonicalCandidate, RawExtraction
from transformer.projection.config import ProjectionConfig, default_projection_config
from transformer.projection.engine import ProjectionEngine
from transformer.validation.schema_validator import validate_output, semantic_validate

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    candidates: List[Dict[str, Any]]           # Projected, validated outputs
    run_id: str
    run_at: str
    sources_attempted: int
    candidates_total: int
    candidates_failed: int
    validation_warnings: Dict[str, List[str]]   # candidate_id → [warning strings]
    validation_errors: Dict[str, List[str]]     # candidate_id → [error strings]
    pipeline_version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_at": self.run_at,
            "pipeline_version": self.pipeline_version,
            "sources_attempted": self.sources_attempted,
            "candidates_total": self.candidates_total,
            "candidates_failed": self.candidates_failed,
            "candidates": self.candidates,
            "validation_summary": {
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class Pipeline:
    """
    Main pipeline orchestrator.

    Usage:
        pipeline = Pipeline(adapters=[...], config=projection_config)
        result = pipeline.run()
    """

    def __init__(
        self,
        adapters: List[SourceAdapter],
        config: Optional[ProjectionConfig] = None,
    ):
        self.adapters = adapters
        self.config = config or default_projection_config()
        self.engine = ProjectionEngine(self.config)

    def run(self) -> PipelineResult:
        import uuid
        run_id = str(uuid.uuid4())[:8]
        run_at = datetime.utcnow().isoformat() + "Z"

        logger.info("pipeline_start | run_id=%s | adapters=%d", run_id, len(self.adapters))

        # --- Stage 1: Extract ---
        all_extractions: List[RawExtraction] = []
        sources_attempted = len(self.adapters)
        for adapter in self.adapters:
            try:
                extractions = adapter.extract()
                logger.info(
                    "stage=extract | source=%s | extracted=%d",
                    adapter.source_type,
                    len(extractions),
                )
                all_extractions.extend(extractions)
            except Exception as e:
                logger.error(
                    "stage=extract | source=%s | UNHANDLED_ERROR=%s",
                    adapter.source_type,
                    str(e),
                    exc_info=True,
                )

        if not all_extractions:
            logger.warning("pipeline | no_extractions_produced | run_id=%s", run_id)
            return PipelineResult(
                candidates=[],
                run_id=run_id,
                run_at=run_at,
                sources_attempted=sources_attempted,
                candidates_total=0,
                candidates_failed=0,
                validation_warnings={},
                validation_errors={},
            )

        logger.info("stage=extract | total_extractions=%d", len(all_extractions))

        # --- Stage 2: Identity ---
        groups = resolve_identity(all_extractions)
        logger.info("stage=identity | unique_candidates=%d", len(groups))

        # --- Stages 3–6: Merge, Score, Project, Validate ---
        output_records: List[Dict[str, Any]] = []
        validation_warnings: Dict[str, List[str]] = {}
        validation_errors: Dict[str, List[str]] = {}
        candidates_failed = 0

        for candidate_id, group_extractions in groups.items():
            try:
                # Stage 3: Merge
                candidate = merge_extractions(candidate_id, group_extractions)
                logger.info(
                    "stage=merge | candidate_id=%s | sources=%d",
                    candidate_id,
                    len(group_extractions),
                )

                # Stage 4: Score
                score_candidate(candidate)

                # Stage 5: Project
                projected = self.engine.project(candidate)

                # Stage 6: Validate
                is_valid, schema_errors = validate_output(projected)
                semantic_warnings = semantic_validate(projected)

                if schema_errors:
                    validation_errors[candidate_id] = schema_errors
                    logger.error(
                        "stage=validate | candidate_id=%s | schema_errors=%d",
                        candidate_id,
                        len(schema_errors),
                    )

                if semantic_warnings:
                    validation_warnings[candidate_id] = semantic_warnings
                    logger.warning(
                        "stage=validate | candidate_id=%s | semantic_warnings=%d",
                        candidate_id,
                        len(semantic_warnings),
                    )

                # Always emit the record — errors are logged, not suppressed
                output_records.append(projected)

            except Exception as e:
                candidates_failed += 1
                logger.error(
                    "stage=pipeline | candidate_id=%s | FAILED | error=%s",
                    candidate_id,
                    str(e),
                    exc_info=True,
                )

        logger.info(
            "pipeline_complete | run_id=%s | output=%d | failed=%d",
            run_id,
            len(output_records),
            candidates_failed,
        )

        return PipelineResult(
            candidates=output_records,
            run_id=run_id,
            run_at=run_at,
            sources_attempted=sources_attempted,
            candidates_total=len(output_records) + candidates_failed,
            candidates_failed=candidates_failed,
            validation_warnings=validation_warnings,
            validation_errors=validation_errors,
        )
