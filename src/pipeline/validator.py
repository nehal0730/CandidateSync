"""
pipeline/validator.py
======================
Validates a projected output dict against the canonical schema.

Design:
  - No external jsonschema library required — custom lightweight validator.
  - Validates types, required fields, and array element types.
  - Degrade gracefully: returns (is_valid, list_of_errors).
  - Caller decides whether to raise or log based on on_missing policy.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ── Default schema definition ─────────────────────────────────────────────────
# Mirrors the canonical output schema from the assignment brief.

_CANONICAL_SCHEMA: Dict[str, Any] = {
    "candidate_id":      {"type": "string",  "required": True},
    "full_name":         {"type": "string",  "required": False, "nullable": True},
    "emails":            {"type": "array",   "items": "string",  "required": False},
    "phones":            {"type": "array",   "items": "string",  "required": False},
    "location":          {"type": "object",  "required": False, "nullable": True},
    "links":             {"type": "object",  "required": False, "nullable": True},
    "headline":          {"type": "string",  "required": False, "nullable": True},
    "years_experience":  {"type": "number",  "required": False, "nullable": True},
    "skills":            {"type": "array",   "items": "string",  "required": False},
    "experience":        {"type": "array",   "items": "object",  "required": False},
    "education":         {"type": "array",   "items": "object",  "required": False},
    "confidence":        {"type": "object",  "required": False, "nullable": True},
    "provenance":        {"type": "object",  "required": False, "nullable": True},
    "overall_confidence":{"type": "number",  "required": False, "nullable": True},
    "metadata":          {"type": "object",  "required": False, "nullable": True},
}

_TYPE_MAP = {
    "string":  str,
    "number":  (int, float),
    "object":  dict,
    "array":   list,
    "boolean": bool,
}


def _check_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True


def _validate_against_schema(
    data: Dict[str, Any],
    schema: Dict[str, Any],
) -> List[str]:
    """Returns list of validation error messages (empty = valid)."""
    errors: List[str] = []

    for field, spec in schema.items():
        value    = data.get(field)
        required = spec.get("required", False)
        nullable = spec.get("nullable", False)
        expected = spec.get("type")
        items    = spec.get("items")   # for arrays

        # Required check
        if required and (value is None or field not in data):
            errors.append(f"Required field '{field}' is missing.")
            continue

        # Skip optional null/missing fields
        if value is None:
            if not nullable and required:
                errors.append(f"Field '{field}' must not be null.")
            continue

        # Type check
        if expected and not _check_type(value, expected):
            actual = type(value).__name__
            errors.append(
                f"Field '{field}' expected type '{expected}', got '{actual}'."
            )
            continue

        # Array item type check
        if expected == "array" and items and isinstance(value, list):
            for i, item in enumerate(value):
                if not _check_type(item, items) and item is not None:
                    errors.append(
                        f"Field '{field}[{i}]' expected item type '{items}', "
                        f"got '{type(item).__name__}'."
                    )

    return errors


def _build_schema_from_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a validation schema from a projection config's fields[] spec.
    Falls back to the canonical schema if no fields are specified.
    """
    field_specs = config.get("fields", [])
    if not field_specs:
        return _CANONICAL_SCHEMA

    schema: Dict[str, Any] = {
        "candidate_id": {"type": "string", "required": True}
    }
    for spec in field_specs:
        path     = spec.get("path")
        typ      = spec.get("type", "string")
        required = spec.get("required", False)
        if not path:
            continue
        schema[path] = {
            "type":     typ.rstrip("[]"),   # "string[]" → "array" with items=string
            "required": required,
            "nullable": not required,
        }
        if typ.endswith("[]"):
            schema[path]["type"]  = "array"
            schema[path]["items"] = typ.rstrip("[]")

    return schema


# ── Public entry point ────────────────────────────────────────────────────────

class ValidationError(Exception):
    """Raised when output fails schema validation and strict mode is on."""
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(f"Schema validation failed: {'; '.join(errors)}")


def validate_output(
    output: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Validate a projected output dict against the schema.

    Parameters
    ----------
    output  : The projected output dict to validate.
    config  : The runtime projection config (used to build per-config schema).
              If None, validates against the default canonical schema.
    strict  : If True, raise ValidationError on any error.

    Returns
    -------
    (is_valid: bool, errors: List[str])

    Raises
    ------
    ValidationError : if strict=True and validation fails.
    """
    schema = _build_schema_from_config(config) if config else _CANONICAL_SCHEMA
    errors = _validate_against_schema(output, schema)
    is_valid = len(errors) == 0

    if not is_valid and strict:
        raise ValidationError(errors)

    return is_valid, errors