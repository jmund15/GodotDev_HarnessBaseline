"""Proof for fetch_source.sh: a stub `curl` (a function loaded through BASH_ENV) answers with a planted status and body.
Asserts the usage refusal (64), a landed page (OK, exit 0), an HTTP 404 (refused, exit 1), and a bot-challenge body
returned with a 200 (refused, exit 1). No network."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sidecar_launch import launch_env, run_bash  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "scripts" / "fetch_source.sh"

CURL = r'''curl() {
  local out="" a prev=""
  for a in "$@"; do [ "$prev" = -o ] && out="$a"; prev="$a"; done
  printf '%s' "$STUB_BODY" > "$out"
  printf '%s' "$STUB_HTTP"
}
'''
URL = "https://example.invalid/docs/page.html"


def contract(temp_root):
    temp_root = Path(temp_root)
    stub = temp_root / "curl_stub.sh"
    stub.write_text(CURL, encoding="utf-8", newline="\n")
    out_dir = temp_root / "out"
    env = launch_env(temp_root, BASH_ENV=stub.as_posix())
    env["BASH_ENV"] = stub.as_posix()

    usage = run_bash(SCRIPT, ROOT, env, [])
    assert usage.returncode == 64 and "usage:" in usage.stderr, (usage.returncode, usage.stderr[-300:])

    landed = run_bash(SCRIPT, ROOT, dict(env, STUB_HTTP="200", STUB_BODY="# planted page\n"),
                      ["--dir", str(out_dir), URL])
    fields = landed.stdout.rstrip("\n").split("\t")
    assert landed.returncode == 0 and fields[:3] == ["OK", "200", "15"] and fields[4] == URL, (landed.returncode, landed.stdout)
    assert Path(fields[3]).read_text(encoding="utf-8") == "# planted page\n", fields[3]

    missing = run_bash(SCRIPT, ROOT, dict(env, STUB_HTTP="404", STUB_BODY="not found"),
                       ["--dir", str(out_dir), URL])
    assert missing.returncode == 1 and missing.stdout.startswith("HTTP_404\t404\t0\t-\t"), (missing.returncode, missing.stdout)

    challenge = run_bash(SCRIPT, ROOT, dict(env, STUB_HTTP="200", STUB_BODY="<title>Just a moment...</title>"),
                         ["--dir", str(out_dir), URL])
    assert challenge.returncode == 1 and challenge.stdout.startswith("CHALLENGE\t"), (challenge.returncode, challenge.stdout)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fetch_source_proof_") as tmp:
        contract(tmp)
    print("ok fetch_source.sh")
