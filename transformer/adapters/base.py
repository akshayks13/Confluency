"""
base.py — Abstract base class for all source adapters.

Every adapter must:
  1. Never crash the pipeline — catch all exceptions and emit warnings.
  2. Return a list of RawExtraction objects (one per candidate found in source).
  3. Log every extraction decision with enough context to debug.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

from transformer.models.canonical import RawExtraction

logger = logging.getLogger(__name__)


class SourceAdapter(ABC):
    """
    Base class for source adapters.

    Subclasses implement `extract()` which returns zero or more RawExtraction
    objects. The pipeline never calls anything else on an adapter.
    """

    source_type: str = "unknown"

    # Source-level base confidence — subclasses override
    BASE_CONFIDENCE: float = 0.5

    @abstractmethod
    def extract(self) -> List[RawExtraction]:
        """
        Extract raw candidate data from the source.

        Must NEVER raise — catch all exceptions internally and return
        whatever was successfully extracted (possibly empty list).
        """
        ...

    def _warn(self, message: str, **context) -> None:
        logger.warning(
            "%s | source=%s | %s",
            message,
            self.source_type,
            " | ".join(f"{k}={v}" for k, v in context.items()),
        )

    def _info(self, message: str, **context) -> None:
        logger.info(
            "%s | source=%s | %s",
            message,
            self.source_type,
            " | ".join(f"{k}={v}" for k, v in context.items()),
        )
