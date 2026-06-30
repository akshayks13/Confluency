"""
config.py — Projection configuration DSL parser.

Parses YAML projection config into a ProjectionConfig object.
Validates the config at parse time — fail fast before processing any records.

Example YAML:
  version: "1.0"
  provenance: false
  confidence: true
  missing_value_policy: null   # null | omit | error
  fields:
    - source: "full_name"
      target: "candidate_name"
    - source: "emails[0]"
      target: "primary_email"
      required: true
    - source: "skills"
      target: "tech_skills"
      filter: "confidence > 0.7"
      transform: "pluck:name"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

from transformer.models.canonical import ConfigValidationError

logger = logging.getLogger(__name__)

MissingPolicy = Literal["null", "omit", "error"]

_VALID_MISSING_POLICIES = {"null", "omit", "error"}
_SUPPORTED_TRANSFORMS = {
    "pluck", "round", "map_range", "upper", "lower",
    "bool_from_path", "join", "first", "count", "truncate",
}


@dataclass
class FieldSpec:
    source: str                              # Dot-path or array index into canonical record
    target: Optional[str] = None            # Output key name (defaults to source)
    required: bool = False                   # Override missing policy to "error"
    filter: Optional[str] = None            # Filter expression (e.g. "confidence > 0.7")
    transform: Optional[str] = None         # Transform spec (e.g. "pluck:name")
    missing_policy: Optional[MissingPolicy] = None  # Per-field override


@dataclass
class ProjectionConfig:
    version: str = "1.0"
    output_schema_name: str = "default"
    provenance: bool = True
    confidence: bool = True
    missing_value_policy: MissingPolicy = "null"
    fields: List[FieldSpec] = field(default_factory=list)

    @property
    def output_all_fields(self) -> bool:
        """If no fields specified, output the full canonical record."""
        return len(self.fields) == 0


def load_projection_config(path: str) -> ProjectionConfig:
    """Load and validate a projection config from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise ConfigValidationError(f"Projection config not found: {path}")

    try:
        with open(p, "r", encoding="utf-8") as f:
            if _YAML_AVAILABLE:
                raw = yaml.safe_load(f)
            else:
                raw = _load_simple_yaml(f.read())
    except Exception as e:
        parser_name = "YAML" if _YAML_AVAILABLE else "simple YAML"
        raise ConfigValidationError(f"{parser_name} parse error in config {path}: {e}")

    if not isinstance(raw, dict):
        raise ConfigValidationError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    return _parse_config(raw, source_path=str(p))


def default_projection_config() -> ProjectionConfig:
    """Return the default config — outputs all canonical fields with provenance."""
    return ProjectionConfig(
        version="1.0",
        output_schema_name="default",
        provenance=True,
        confidence=True,
        missing_value_policy="null",
        fields=[],   # Empty = output everything
    )


def _load_simple_yaml(text: str) -> Dict[str, Any]:
    """
    Parse the small YAML subset used by projection configs when PyYAML is not
    installed. Supports top-level scalars and a `fields:` list of flat maps.
    """
    result: Dict[str, Any] = {}
    current_list_key: Optional[str] = None
    current_item: Optional[Dict[str, Any]] = None

    for raw_line in text.splitlines():
        line = _strip_yaml_comment(raw_line).rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            current_item = None
            if stripped.endswith(":"):
                key = stripped[:-1].strip()
                result[key] = []
                current_list_key = key
                continue

            key, value = _split_yaml_pair(stripped)
            result[key] = _parse_yaml_scalar(value)
            current_list_key = None
            continue

        if current_list_key:
            if stripped.startswith("- "):
                item_text = stripped[2:].strip()
                current_item = {}
                result[current_list_key].append(current_item)
                if item_text:
                    key, value = _split_yaml_pair(item_text)
                    current_item[key] = _parse_yaml_scalar(value)
                continue

            if current_item is None:
                raise ValueError(f"nested value outside list item: {stripped}")
            key, value = _split_yaml_pair(stripped)
            current_item[key] = _parse_yaml_scalar(value)
            continue

        raise ValueError(f"unsupported indentation: {raw_line}")

    return result


def _strip_yaml_comment(line: str) -> str:
    in_quote: Optional[str] = None
    for i, char in enumerate(line):
        if char in ("'", '"'):
            in_quote = None if in_quote == char else char
        elif char == "#" and in_quote is None:
            return line[:i]
    return line


def _split_yaml_pair(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise ValueError(f"expected key/value pair: {line}")
    key, value = line.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"empty key in line: {line}")
    return key, value.strip()


def _parse_yaml_scalar(value: str) -> Any:
    if value in ("", "null", "Null", "NULL", "~"):
        return None
    if value in ("true", "True", "TRUE"):
        return True
    if value in ("false", "False", "FALSE"):
        return False
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    return value


def _parse_config(raw: Dict[str, Any], source_path: str) -> ProjectionConfig:
    missing_policy = raw.get("missing_value_policy", "null")
    if missing_policy not in _VALID_MISSING_POLICIES:
        raise ConfigValidationError(
            f"Invalid missing_value_policy '{missing_policy}'. "
            f"Must be one of: {_VALID_MISSING_POLICIES}"
        )

    fields = []
    for i, fspec in enumerate(raw.get("fields", [])):
        if not isinstance(fspec, dict):
            raise ConfigValidationError(f"Field spec at index {i} must be a mapping")
        if "source" not in fspec:
            raise ConfigValidationError(f"Field spec at index {i} is missing 'source'")

        transform = fspec.get("transform")
        if transform:
            transform_name = transform.split(":")[0]
            if transform_name not in _SUPPORTED_TRANSFORMS:
                raise ConfigValidationError(
                    f"Unknown transform '{transform_name}' in field '{fspec['source']}'. "
                    f"Supported: {_SUPPORTED_TRANSFORMS}"
                )

        per_field_missing = fspec.get("missing_policy", None)
        if per_field_missing and per_field_missing not in _VALID_MISSING_POLICIES:
            raise ConfigValidationError(
                f"Invalid per-field missing_policy '{per_field_missing}'"
            )

        fields.append(FieldSpec(
            source=fspec["source"],
            target=fspec.get("target") or fspec["source"],
            required=bool(fspec.get("required", False)),
            filter=fspec.get("filter"),
            transform=fspec.get("transform"),
            missing_policy=per_field_missing,
        ))

    config = ProjectionConfig(
        version=str(raw.get("version", "1.0")),
        output_schema_name=str(raw.get("output_schema_name", "default")),
        provenance=bool(raw.get("provenance", True)),
        confidence=bool(raw.get("confidence", True)),
        missing_value_policy=missing_policy,
        fields=fields,
    )

    logger.info(
        "projection_config_loaded | schema=%s | fields=%d | provenance=%s | confidence=%s",
        config.output_schema_name,
        len(config.fields),
        config.provenance,
        config.confidence,
    )
    return config
