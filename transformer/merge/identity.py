"""
identity.py — Candidate identity resolution.

Determines which RawExtraction objects belong to the same person and
assigns a stable, deterministic candidate_id.

Strategy:
  1. Primary key: SHA-256 of the normalized primary email (lowest email alphabetically).
  2. Fallback: UUID v5 from (normalized_name + normalized_phone) if no email.
  3. Gmail + aliases are resolved to their base address before hashing.
  4. Returns groups: Dict[candidate_id → List[RawExtraction]]
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from transformer.models.canonical import RawExtraction
from transformer.normalizers.email import normalize_email, gmail_base_address, is_gmail_alias
from transformer.normalizers.name import normalize_name
from transformer.normalizers.phone import normalize_phone

logger = logging.getLogger(__name__)

_UUID5_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def resolve_identity(
    extractions: List[RawExtraction],
) -> Dict[str, List[RawExtraction]]:
    """
    Groups extractions by resolved candidate identity.
    Returns {candidate_id: [extraction, ...]}
    """
    groups: Dict[str, List[RawExtraction]] = defaultdict(list)

    for extraction in extractions:
        cid = _compute_candidate_id(extraction)
        groups[cid].append(extraction)
        logger.debug(
            "identity | source=%s | candidate_id=%s",
            extraction.source_id,
            cid,
        )

    logger.info("identity | total_extractions=%d | unique_candidates=%d", len(extractions), len(groups))
    return dict(groups)


def _compute_candidate_id(extraction: RawExtraction) -> str:
    """Compute a deterministic candidate ID from an extraction."""
    primary_email = _primary_email(extraction.emails)
    if primary_email:
        # Resolve Gmail aliases to base address
        if is_gmail_alias(primary_email):
            base = gmail_base_address(primary_email)
            logger.debug("gmail_alias_normalized | %s → %s", primary_email, base)
            primary_email = base
        return _hash_id(primary_email)

    # Fallback: name + phone
    name, _ = normalize_name(extraction.full_name)
    phone = None
    for raw_phone in extraction.phones:
        p, _ = normalize_phone(raw_phone)
        if p:
            phone = p
            break

    if name and phone:
        logger.warning(
            "identity_fallback | no_email | source=%s | using=name+phone | confidence_penalty=0.2",
            extraction.source_id,
        )
        return _uuid5_id(f"{name.lower()}:{phone}")

    if name:
        logger.warning(
            "identity_fallback | no_email_no_phone | source=%s | using=name_only | UNRELIABLE",
            extraction.source_id,
        )
        return _uuid5_id(f"{name.lower()}")

    # Last resort — random (cannot merge with anything)
    logger.error("identity_unknown | source=%s | no_identity_fields", extraction.source_id)
    return _uuid5_id(f"unknown:{extraction.source_id}:{extraction.extracted_at.isoformat()}")


def _primary_email(emails: List[str]) -> Optional[str]:
    """Return the normalized, lexicographically smallest email, or None."""
    normalized = []
    for raw in emails:
        norm, conf = normalize_email(raw)
        if norm and conf > 0:
            normalized.append(norm)
    return min(normalized) if normalized else None


def _hash_id(key: str) -> str:
    """Deterministic 16-char hex ID from a string."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _uuid5_id(key: str) -> str:
    return str(uuid.uuid5(_UUID5_NAMESPACE, key)).replace("-", "")[:16]
