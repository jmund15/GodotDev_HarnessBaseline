"""The JSON Schema subset a sidecar `-S` schema uses: type, enum, required, properties,
additionalProperties false, items, minItems, pattern, oneOf. `validate` raises ValueError naming
the first failing path; it returns None on a match. `check_schema` (and `python3 json_schema_lite.py <file>`,
exit 2) refuses a schema outside that subset."""
import re

_TYPES = {
    "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str), "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate(value, rule, where="$"):
    if "oneOf" in rule:
        matches = 0
        for branch in rule["oneOf"]:
            try:
                validate(value, branch, where)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(f"{where}: oneOf requires exactly one matching schema")
    kinds = rule.get("type")
    if kinds and not any(_TYPES[k](value) for k in ([kinds] if isinstance(kinds, str) else kinds)):
        raise ValueError(f"{where}: wrong type")
    if "enum" in rule and value not in rule["enum"]:
        raise ValueError(f"{where}: value outside enum")
    if isinstance(value, dict):
        for name in rule.get("required", []):
            if name not in value:
                raise ValueError(f"{where}: missing {name}")
        for name, item in value.items():
            if name in (rule.get("properties") or {}):
                validate(item, rule["properties"][name], f"{where}.{name}")
            elif rule.get("additionalProperties") is False:
                raise ValueError(f"{where}: unexpected key {name}")
    if isinstance(value, list):
        if len(value) < rule.get("minItems", 0):
            raise ValueError(f"{where}: fewer than {rule['minItems']} items")
        for index, item in enumerate(value):
            validate(item, rule.get("items") or {}, f"{where}[{index}]")
    if isinstance(value, str) and "pattern" in rule and not re.search(rule["pattern"], value):
        raise ValueError(f"{where}: pattern mismatch")


SUPPORTED = {"type", "description", "additionalProperties", "required", "properties", "items", "enum", "pattern",
             "oneOf", "minItems"}


def check_schema(rule):
    """Raise ValueError when `rule` uses anything outside the subset `validate` enforces, so a seat never certifies
    an answer against a partly applied schema."""
    if not isinstance(rule, dict):
        raise ValueError("schema node must be an object")
    extra = set(rule) - SUPPORTED
    if extra:
        raise ValueError("unsupported schema keyword: " + ", ".join(sorted(extra)))
    kinds = rule.get("type")
    for kind in ([kinds] if isinstance(kinds, str) else kinds or []):
        if kind not in _TYPES:
            raise ValueError(f"unsupported schema type: {kind}")
    if "minItems" in rule and (not isinstance(rule["minItems"], int) or isinstance(rule["minItems"], bool)
                               or rule["minItems"] < 0):
        raise ValueError("minItems must be a non-negative integer")
    if not isinstance(rule.get("additionalProperties", False), bool):
        raise ValueError("additionalProperties must be a boolean")
    for child in (rule.get("properties") or {}).values():
        check_schema(child)
    if "items" in rule:
        check_schema(rule["items"])
    if "oneOf" in rule:
        if not isinstance(rule["oneOf"], list) or not rule["oneOf"]:
            raise ValueError("oneOf must be a non-empty schema array")
        for child in rule["oneOf"]:
            check_schema(child)


if __name__ == "__main__":
    import json
    import sys
    try:
        with open(sys.argv[1], encoding="utf-8") as fh:
            check_schema(json.load(fh))
    except (ValueError, OSError, IndexError) as exc:
        print(f"-S schema refused: {exc}", file=sys.stderr)
        sys.exit(2)
