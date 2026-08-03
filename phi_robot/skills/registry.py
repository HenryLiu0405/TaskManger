"""Single source of truth for skill definitions and handlers."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable, Mapping, Optional

from .models import SkillDefinition, SkillHandler, SkillRequest

try:  # Production installs jsonschema; the fallback keeps offline bootstrap safe.
    from jsonschema import Draft202012Validator  # type: ignore
except ImportError:  # pragma: no cover - exercised in dependency-light environments
    Draft202012Validator = None


class SkillValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class SkillRegistry:
    """Versioned registry with strict request validation."""

    def __init__(self) -> None:
        self._definitions: "OrderedDict[tuple[str, str], SkillDefinition]" = OrderedDict()
        self._handlers: dict[tuple[str, str], SkillHandler] = {}

    def register(self, definition: SkillDefinition, handler: SkillHandler) -> None:
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise ValueError(f"skill already registered: {definition.name}@{definition.version}")
        schema = dict(definition.input_schema)
        if schema.get("type", "object") != "object":
            raise ValueError(f"skill input schema must be object: {definition.name}")
        if Draft202012Validator is not None:
            Draft202012Validator.check_schema(schema)
        self._definitions[key] = definition
        self._handlers[key] = handler

    def get(self, name: str, version: str = "1.0") -> Optional[SkillDefinition]:
        return self._definitions.get((name, version))

    def handler(self, name: str, version: str = "1.0") -> Optional[SkillHandler]:
        return self._handlers.get((name, version))

    def definitions(self, *, planner_visible_only: bool = False) -> list[SkillDefinition]:
        values = list(self._definitions.values())
        if planner_visible_only:
            values = [definition for definition in values if definition.planner_visible]
        return values

    def function_schemas(self, *, planner_visible_only: bool = False) -> list[dict[str, Any]]:
        return [
            definition.to_function_schema()
            for definition in self.definitions(planner_visible_only=planner_visible_only)
        ]

    def validate(self, request: SkillRequest) -> list[str]:
        definition = self.get(request.skill_name, request.version)
        if definition is None:
            return [f"unsupported skill: {request.skill_name}@{request.version}"]
        schema = dict(definition.input_schema)
        value = dict(request.args)
        if Draft202012Validator is not None:
            validator = Draft202012Validator(schema)
            return [
                _format_jsonschema_error(error)
                for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path))
            ]
        return _validate_subset(value, schema)

    def require_valid(self, request: SkillRequest) -> None:
        errors = self.validate(request)
        if errors:
            raise SkillValidationError(errors)


def _format_jsonschema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return f"{path}: {error.message}" if path else error.message


def _validate_subset(value: Any, schema: Mapping[str, Any], path: str = "") -> list[str]:
    """Strict fallback for the Draft 2020-12 subset used by this catalog.

    Production environments install ``jsonschema``.  The fallback intentionally
    rejects unsupported schema constructs rather than silently weakening them.
    """

    supported = {
        "$schema", "type", "properties", "required", "additionalProperties",
        "enum", "minimum", "maximum", "minLength", "maxLength", "items",
        "minItems", "maxItems", "description", "default",
    }
    unknown_keywords = set(schema) - supported
    if unknown_keywords:
        return [f"unsupported schema keywords at {path or '$'}: {sorted(unknown_keywords)}"]

    label = path or "$"
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return [f"{label} must be object"]
        errors: list[str] = []
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{label}.{key} is required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{label}.{key} is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(_validate_subset(value[key], child, f"{label}.{key}"))
        return errors
    if expected == "array":
        if not isinstance(value, list):
            return [f"{label} must be array"]
        errors = []
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{label} must contain at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{label} must contain at most {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(_validate_subset(item, schema["items"], f"{label}[{index}]"))
        return errors
    if expected == "string" and not isinstance(value, str):
        return [f"{label} must be string"]
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        return [f"{label} must be number"]
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return [f"{label} must be integer"]
    if expected == "boolean" and not isinstance(value, bool):
        return [f"{label} must be boolean"]

    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{label} must be one of {schema['enum']}")
    if expected in ("number", "integer") and isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{label} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{label} must be <= {schema['maximum']}")
    if expected == "string" and isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{label} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{label} is too long")
    return errors
