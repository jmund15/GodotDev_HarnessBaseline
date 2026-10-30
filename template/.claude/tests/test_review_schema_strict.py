#!/usr/bin/env python3
"""Proof that `.claude/schemas/review_findings.json` survives the CLI's schema validator.

`sidecar_fanout.plan_job` hands this file to every `shape: review` job as `--json-schema`, and the
CLI validates it in ajv STRICT mode BEFORE the child does any work. An unknown keyword is not a
warning there: the dispatch dies with `--json-schema is not a valid JSON Schema: strict mode:
unknown keyword: "X"`, and a whole fan-out fails at launch. That is how it shipped the first time,
with a `_comment` key at the root -- argv construction was proven, the schema itself never was.

Run: python3 .claude/tests/test_review_schema_strict.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "schemas", "review_findings.json")

# Standard JSON Schema keywords ajv accepts in strict mode. Deliberately NOT exhaustive for drafts
# this project does not write: a keyword missing here fails the proof loudly, which is the safe
# direction -- the unsafe one is a non-keyword passing because the list was permissive.
KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "$comment", "definitions",
    "type", "properties", "items", "required", "additionalProperties", "enum", "const",
    "description", "title", "default", "examples", "format",
    "oneOf", "anyOf", "allOf", "not", "nullable",
    "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern",
    "patternProperties", "propertyNames", "uniqueItems",
}

# Where a mapping's KEYS are user-chosen names rather than keywords, so nothing under it is checked.
NAME_BEARING = {"properties", "$defs", "definitions", "patternProperties"}


def walk(node, path, bad):
    if isinstance(node, dict):
        for key, val in node.items():
            here = "%s/%s" % (path, key)
            if key not in KEYWORDS:
                bad.append(here)
                continue
            if key in NAME_BEARING and isinstance(val, dict):
                for name, sub in val.items():
                    walk(sub, "%s/%s" % (here, name), bad)
            elif key == "enum":
                continue  # values, not schema
            else:
                walk(val, here, bad)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            walk(item, "%s/%d" % (path, i), bad)


def unknown_keywords(doc):
    bad = []
    walk(doc, "", bad)
    return bad


CASES = []


def case(name, fn):
    CASES.append((name, fn))


def live():
    with open(SCHEMA, encoding="utf-8") as fh:
        return json.load(fh)


case("the shipped schema parses as JSON", lambda: isinstance(live(), dict))
case("the shipped schema carries NO non-keyword at any depth",
     lambda: unknown_keywords(live()) == [])

# PLANTED: the exact defect that shipped. A check that only ever sees the fixed file proves nothing.
case("a root `_comment` IS reported -- the shape that broke every review dispatch",
     lambda: unknown_keywords(dict(live(), _comment="x")) == ["/_comment"])
case("a NESTED unknown keyword is reported too",
     lambda: any(b.endswith("/notAKeyword") for b in unknown_keywords(
         {"type": "object", "properties": {"f": {"type": "string", "notAKeyword": 1}}})))
case("a field literally NAMED `_comment` under `properties` is fine -- names are not keywords",
     lambda: unknown_keywords(
         {"type": "object", "properties": {"_comment": {"type": "string"}}}) == [])

# The caps rule this schema exists to honour (feedback_schema_caps_must_not_invalidate_delegate_work):
# a length or item cap discards a complete paid deliverable wholesale.
case("no maxLength / maxItems anywhere -- a cap discards the whole return",
     lambda: not any(k in json.dumps(live()) for k in ('"maxLength"', '"maxItems"')))
case("`findings` and `checked` are required, so an empty review still proves what it examined",
     lambda: live().get("required") == ["findings", "checked"])
case("checked uses the shared tools/stopping-point/basis coverage contract",
     lambda: live()["properties"]["checked"].get("required") == ["toolsUsed", "stoppedAt", "basis"])
case("finding schema exposes optional source-refutation and quote fields",
     lambda: live()["properties"]["findings"]["items"]["properties"].get("refuting", {}).get("type") == "boolean"
             and live()["properties"]["findings"]["items"]["properties"].get("evidence", {}).get("type") == "array")
case("evidence rows use the evidence contract's canonical kinds",
     lambda: live()["properties"]["findings"]["items"]["properties"]["evidence"]
             == {**json.load(open(os.path.join(HERE, "..", "schemas", "evidence.json"), encoding="utf-8")), "minItems": 1})


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
