#!/usr/bin/env python3
"""Check that a claim's `evidence` quote actually occurs in the bytes it was taken from.

ADVISORY, never blocking. It reports; the orchestrator decides. Two measured failure modes forced
that shape:

  * Normalization shrinks false negatives but cannot eliminate them — a quote spanning two XML
    elements, or composed from adjacent sentences, still fails a substring test. Auto-rewriting such
    a claim into a gap makes a TRUE claim indistinguishable from a real absence, which is the exact
    defect this tooling exists to remove.
  * context7 is a P1 source that produces no local artifact. A blanket "unverifiable -> gap" would
    mechanically demote a tier `source_trust.md` calls citable alone.

Hence three verdicts, and NOT-APPLICABLE is not a failure:
  VERIFIED        evidence found in the artifact
  UNVERIFIED      artifact exists, evidence absent -> escalate for a human/orchestrator look
  NOT-APPLICABLE  no local artifact (context7, WebSearch, memory) -> out of scope, not a defect

Input: a JSON array (file or stdin) of claims. Each needs `evidence`; a local artifact comes from
`artifact`, or from `file` when that is a path rather than a URL. `fetch_source.sh`'s manifest
carries the URL->artifact mapping when the claim cites a URL.

  verify_claims.py claims.json [--manifest <tsv>] [--strict]

`--strict` exits 1 when any claim is UNVERIFIED (for a gate that wants teeth). Default exit is 0
unless the input itself is unusable.
"""
import argparse
import html
import json
import os
import re
import sys
import unicodedata

_TAGS = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style).*?</\1>", re.S | re.I)
# Godot doc BBCode + markdown emphasis: present in the source, absent from a sensibly-quoted line.
_BB = re.compile(r"\[/?(?:b|i|u|s|code|codeblock|codeblocks|gdscript|csharp|url|param|member|"
                 r"method|constant|enum|signal|theme_item|annotation|class)[^\]]*\]", re.I)
_EMPH = re.compile(r"[*_`]+")


def normalize(text: str) -> str:
    """Collapse a source and a quote onto common ground.

    Measured necessity: a correct quote false-negatived because the README wrapped the version in
    <strong>. Matching raw bytes rejects true quotes; this is the minimum that stops that.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _SCRIPT.sub(" ", text)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    text = _BB.sub("", text)
    text = _EMPH.sub("", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def load_manifest(path):
    """fetch_source.sh TSV: status, http, bytes, artifact, url."""
    mapping = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 5 and parts[0] == "OK" and parts[3] != "-":
                mapping[parts[4]] = parts[3]
    return mapping


def resolve_artifact(claim, manifest):
    for key in ("artifact", "file"):
        val = claim.get(key)
        if val and not str(val).startswith(("http://", "https://")) and os.path.isfile(val):
            return val
    return manifest.get(claim.get("file"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("claims", nargs="?", default="-")
    ap.add_argument("--manifest")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.claims == "-" else open(args.claims, encoding="utf-8").read()
    try:
        claims = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"verify_claims: input is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if isinstance(claims, dict):
        claims = claims.get("claims", [])
    if not isinstance(claims, list):
        print("verify_claims: expected a JSON array of claims", file=sys.stderr)
        return 2

    manifest = load_manifest(args.manifest) if args.manifest else {}
    cache = {}
    rows, counts = [], {"VERIFIED": 0, "UNVERIFIED": 0, "NOT-APPLICABLE": 0}

    for idx, claim in enumerate(claims):
        subject = str(claim.get("subject") or claim.get("claim") or f"claim[{idx}]")[:70]
        evidence = (claim.get("evidence") or "").strip()
        artifact = resolve_artifact(claim, manifest)

        if not evidence:
            verdict, note = "NOT-APPLICABLE", "no evidence field"
        elif not artifact:
            verdict, note = "NOT-APPLICABLE", "no local artifact (context7/search/memory)"
        else:
            if artifact not in cache:
                try:
                    with open(artifact, encoding="utf-8", errors="replace") as fh:
                        cache[artifact] = normalize(fh.read())
                except OSError as exc:
                    cache[artifact] = None
                    note = f"unreadable artifact: {exc}"
            body = cache.get(artifact)
            if body is None:
                verdict, note = "NOT-APPLICABLE", "artifact unreadable"
            elif normalize(evidence) in body:
                verdict, note = "VERIFIED", os.path.basename(artifact)
            else:
                verdict = "UNVERIFIED"
                note = (f"quote absent from {os.path.basename(artifact)} - may be a composed or "
                        f"cross-element quote rather than a fabrication; read it before discarding")
        counts[verdict] += 1
        rows.append((verdict, subject, note))

    width = max((len(r[1]) for r in rows), default=10)
    for verdict, subject, note in rows:
        print(f"{verdict:15} {subject:<{width}}  {note}")
    print(f"\n{counts['VERIFIED']} verified, {counts['UNVERIFIED']} unverified, "
          f"{counts['NOT-APPLICABLE']} not-applicable  (advisory - UNVERIFIED means LOOK, not discard)")

    return 1 if (args.strict and counts["UNVERIFIED"]) else 0


if __name__ == "__main__":
    sys.exit(main())
