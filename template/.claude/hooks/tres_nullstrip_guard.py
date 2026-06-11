#!/usr/bin/env python3
"""Guard against value-type Export null-strips in .tres files.

Godot's editor resave silently rewrites every .tres referencing a script that
grew a new [Export], writing previously-omitted value-type Exports as explicit
`Field = null`. On load, null coerces to type-zero (int->0, float->0.0) rather
than the C# field-init default -- breaking thresholds, multipliers, and feel
knobs with a green build and no warning. (Incident: 9b16d361 null-stripped
DefaultCritMultiplier/KnockbackVelocityScaling/MaxRange/MaxAngleDegrees across
~10 combat archetypes; only the *Required fields were ever restored.)

The regression shape is a pure line *addition* (`+Field = null`) -- the field
was omitted before, so there is no `-Field = <number>` to pair against. So we
flag added `Field = null` lines whose name carries numeric intent, and rely on
diff-scoping (only the resave event adds such lines) to keep noise low. Lines
already committed in the repo are not re-flagged; this catches the *next* strip.

Usage:
    tres_nullstrip_guard.py            # scan staged changes (git diff --cached)
    tres_nullstrip_guard.py --range A..B   # scan a commit range (PR review / CI)

Exit 0 = clean, exit 1 = suspect null-strip additions found (findings on stderr).
Reference-type Exports (Resource/Node/NodePath/Script/Array/Curve) legitimately
go null and are excluded by the numeric-stem filter below.
"""
import re
import subprocess
import sys

# Numeric-intent name stems. A PascalCase Export whose name contains one of
# these almost always backs an int/float/enum -- the value-types that break
# when null-coerced to zero. Curated from the memorialized incident plus the
# generic feel-knob vocabulary. Reference-type fields (Effects, Tags,
# Contributions, Outcome, Animation, Falloff curves) deliberately omitted.
NUMERIC_STEMS = (
    "Required", "Threshold", "Count", "Priority", "Duration", "Multiplier",
    "Scale", "Scaling", "Weight", "Stage", "Order", "Range", "Degrees",
    "Velocity", "Angle", "Speed", "Damage", "Radius", "Force", "Mass",
    "Height", "Distance", "Cooldown", "Chance", "Factor", "Amount", "Ratio",
    "Percent", "Level", "Size", "Rate", "Min", "Max",
)

ADDED_NULL = re.compile(r"^\+([A-Z][A-Za-z0-9_]*)\s*=\s*null\s*$")
FILE_HDR = re.compile(r"^\+\+\+ b/(.*)$")


def diff_lines(args):
    cmd = ["git", "diff", "-U0", "--no-color"]
    if args and args[0] == "--range" and len(args) > 1:
        cmd.append(args[1])
    else:
        cmd.append("--cached")
    cmd += ["--", "*.tres"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout.splitlines()


def has_numeric_stem(name):
    return any(stem in name for stem in NUMERIC_STEMS)


def main():
    findings = []
    current = None
    for line in diff_lines(sys.argv[1:]):
        hdr = FILE_HDR.match(line)
        if hdr:
            current = hdr.group(1)
            continue
        m = ADDED_NULL.match(line)
        if m and has_numeric_stem(m.group(1)):
            findings.append((current, m.group(1)))

    if not findings:
        return 0

    print(
        "[tres-nullstrip-guard] Suspected value-type Export null-strip "
        f"({len(findings)} line(s)):",
        file=sys.stderr,
    )
    for path, field in findings:
        print(f"  {path}: {field} = null", file=sys.stderr)
    print(
        "\nThese names carry numeric intent -- `= null` loads as 0, not the C# "
        "default. Set the explicit intended value (e.g. `KnockbackVelocityScaling "
        "= 1.0`). If the field is genuinely a nullable override, this is a false "
        "positive -- confirm against the C# declaration before committing.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
