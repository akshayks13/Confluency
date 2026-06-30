"""
transformers.py — Pluggable transform registry.

Each transform is a pure function registered by name.
Adding a new transform = adding a function + @register() decorator.
Zero changes to the projection engine.

Transform spec format: "name:arg1:arg2" (colon-separated)
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from transformer.models.canonical import ConfigValidationError

_REGISTRY: Dict[str, Callable] = {}


def register(name: str):
    """Decorator to register a transform function by name."""
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return decorator


def apply_transform(transform_spec: str, value: Any) -> Any:
    """
    Apply a named transform to a value.
    Raises ConfigValidationError for unknown transforms.
    """
    if not transform_spec:
        return value
    parts = transform_spec.split(":", 1)
    name = parts[0].strip()
    args_str = parts[1] if len(parts) > 1 else ""

    if name not in _REGISTRY:
        raise ConfigValidationError(f"Unknown transform: '{name}'")

    return _REGISTRY[name](value, args_str)


# ---------------------------------------------------------------------------
# Built-in transforms
# ---------------------------------------------------------------------------

@register("pluck")
def pluck(value: Any, args: str) -> Any:
    """
    Extract a field from each item in a list.
    transform: "pluck:name"  →  [item["name"] for item in value]
    """
    field_name = args.strip()
    if not field_name:
        return value
    if isinstance(value, list):
        return [
            item.get(field_name) if isinstance(item, dict) else getattr(item, field_name, None)
            for item in value
        ]
    return value


@register("round")
def round_value(value: Any, args: str) -> Any:
    """
    Round a float to N decimal places.
    transform: "round:2"
    """
    try:
        decimals = int(args.strip())
        return round(float(value), decimals)
    except (ValueError, TypeError):
        return value


@register("upper")
def upper(value: Any, _args: str) -> Any:
    return str(value).upper() if value is not None else None


@register("lower")
def lower(value: Any, _args: str) -> Any:
    return str(value).lower() if value is not None else None


@register("truncate")
def truncate(value: Any, args: str) -> Any:
    """
    Truncate a string to N characters.
    transform: "truncate:100"
    """
    try:
        n = int(args.strip())
        s = str(value) if value is not None else ""
        return s[:n]
    except (ValueError, TypeError):
        return value


@register("join")
def join(value: Any, args: str) -> Any:
    """
    Join a list into a string with a separator.
    transform: "join:, "
    """
    sep = args if args else ", "
    if isinstance(value, list):
        return sep.join(str(v) for v in value if v is not None)
    return value


@register("first")
def first(value: Any, _args: str) -> Any:
    """Return the first element of a list, or None."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


@register("count")
def count(value: Any, _args: str) -> Any:
    """Return the length of a list."""
    if isinstance(value, list):
        return len(value)
    return 0 if value is None else 1


@register("bool_from_path")
def bool_from_path(value: Any, _args: str) -> bool:
    """Convert a value to boolean (truthy check)."""
    return bool(value)


@register("map_range")
def map_range(value: Any, args: str) -> Any:
    """
    Map a numeric value to a label based on ranges.
    transform: "map_range:[0,2]=Junior,[2,5]=Mid,[5,10]=Senior,[10,]=Staff"
    """
    try:
        num = float(value)
    except (ValueError, TypeError):
        return value

    # Parse specs: "[0,2]=Junior"
    for spec in args.split(","):
        spec = spec.strip()
        m = re.match(r"\[(\d+),(\d*)\]=(.+)", spec)
        if m:
            lo = float(m.group(1))
            hi = float(m.group(2)) if m.group(2) else float("inf")
            label = m.group(3).strip()
            if lo <= num < hi:
                return label

    return str(value)
