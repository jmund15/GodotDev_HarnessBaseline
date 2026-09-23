#!/usr/bin/env python3
"""Liveness and attestation probes for raine/claude-code-proxy.

Split out of codex_proxy_sidecar.sh because both jobs need an HTTP client and a glob over
timestamped capture directories -- shell has neither without adding curl and find to the
launcher's dependency set, while python3 is already load-bearing for the run-record.

Subcommands:
  health <port>            exit 0 iff the proxy answers {"ok": true}
  wait <port> <seconds>    poll until healthy or the budget expires; exit 0 on ready
  freeport                 print a port the OS just confirmed is bindable
  attest <since_epoch> [session_id]
                           print "<model>\t<effort>" from this session's capture;
                           without an id, read the CLI result/stream from stdin;
                           exit 0 on proof, 1 when unknown, or 2 for bad arguments
  record-effort <record> <attested_effort> [ledger]
                           add proxy-attested effort and agreement to a run record; the
                           ledger gets exactly one row, unannotated if the rewrite fails
  continuation <proxy_exe> print 1 iff that build is safe for WebSocket continuation, else 0
  server-compaction <proxy_exe>
                           print 1 iff that build supports tested native compaction, else 0
  user-scope-bin           print the owner's user-scope CCP_BIN (Windows); exit 1 when unset
  serves <proxy_exe> <model_id>
                           exit 0 iff `<proxy_exe> models` lists that id; 1 when it does not,
                           3 when the list is unreadable; the reason goes to stdout
"""
import glob
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cli_envelope  # noqa: E402

# `/healthz`, not `/health` -- measured 2026-08-20 against v0.1.35, where `/health` returns
# 404. A launcher polling the wrong path never sees ready and times out on a proxy that came
# up fine, which reads as "the proxy is broken" rather than "the path is wrong".
HEALTH_PATH = "/healthz"

# The proxy writes captures under the XDG state dir even on Windows, where it renders as a
# mixed separator (`C:\Users\x/.local/state/...`) -- glob handles both, os.path.join would not.
TRAFFIC_ROOT = os.path.expanduser("~/.local/state/claude-code-proxy/traffic")

# Without previous_response_id the proxy opens a fresh WebSocket every turn -- the handshake churn
# behind intermittent upstream 403s (raine/claude-code-proxy#87). Builds before 0.1.36 carry the
# continuation bugs that make turning it on unsafe, and the proxy re-reads the setting per request,
# so launchers pass it explicitly both ways. Unknown version reads as unsupported.
CONTINUATION_MIN_VERSION = (0, 1, 36)
SERVER_COMPACTION_MIN_VERSION = (0, 1, 39)
COMPACT_MESSAGE_PREFIX = "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools."
COMPACT_MESSAGE_TASK = "Your task is to create a detailed summary of the conversation so far"


def version_at_least(version_text, minimum):
    m = re.fullmatch(r"\s*claude-code-proxy\s+v?(\d+)\.(\d+)\.(\d+)\s*", version_text or "")
    return bool(m) and tuple(int(x) for x in m.groups()) >= minimum


def continuation_supported(version_text):
    return version_at_least(version_text, CONTINUATION_MIN_VERSION)


def server_compaction_supported(version_text):
    return version_at_least(version_text, SERVER_COMPACTION_MIN_VERSION)


def proxy_version_text(exe):
    try:
        result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def proxy_models_text(exe):
    try:
        result = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=20)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def serves(models_text, model_id):
    """The model list is compiled into each proxy build, so a registry remap to a newer id needs a
    build that lists it; an unlisted id dies upstream on `400 Unknown model`."""
    return model_id in re.split(r"[\s,;:]+", models_text or "")


def user_scope_ccp_bin():
    """CCP_BIN as the owner last set it. A long-lived session keeps the value it inherited at launch."""
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "CCP_BIN")[0] or None
    except OSError:
        return None


def serves_check(exe, model_id):
    """-> (rc, reason): 0 served, 1 not served, 3 unverifiable."""
    text = proxy_models_text(exe)
    if not text.strip():
        return 3, f"cannot read the model list of {exe}: `{exe} models` failed or printed nothing"
    if serves(text, model_id):
        return 0, ""
    reason = f"proxy {exe} does not serve {model_id}; every dispatch would fail with 400 Unknown model."
    scoped = user_scope_ccp_bin()
    if scoped and os.path.normcase(os.path.normpath(scoped)) != os.path.normcase(os.path.normpath(exe)):
        reason += f" The user-scope CCP_BIN is {scoped}; this process inherited an older value, so relaunch with CCP_BIN={scoped}."
    else:
        reason += " Set CCP_BIN to a build whose `models` lists it."
    return 1, reason


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2) as r:
            return json.loads(r.read(256).decode("utf-8", "replace")).get("ok") is True
    except Exception:
        return False


def wait_healthy(port, budget_s):
    deadline = time.time() + budget_s
    while time.time() < deadline:
        if healthy(port):
            return True
        time.sleep(0.4)
    return healthy(port)


def freeport():
    """Ask the OS for a port, then release it.

    Inherently racy -- another process can claim it in the gap -- but the alternative is a
    fixed port, and a fixed port cannot be per-dispatch: the proxy reads its model and effort
    pins from its OWN environment at startup, so two dispatches wanting different pins need
    two proxies. A lost race surfaces immediately as a bind failure in the proxy log, which is
    a better failure than two dispatches silently sharing one pin.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _session_id(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def session_from_output(raw):
    """Read CLI envelope identity, never session-like text inside a model's answer."""
    return cli_envelope.session_id(cli_envelope.events(raw))


def _is_compaction_request(body):
    if not isinstance(body, dict):
        return False
    for item in body.get("input") or []:
        if not (isinstance(item, dict)
                and item.get("type") == "message"
                and item.get("role") == "developer"):
            continue
        content = item.get("content")
        if isinstance(content, str):
            texts = [content]
        elif isinstance(content, list):
            texts = [part.get("text") for part in content
                     if isinstance(part, dict) and part.get("type") == "input_text"]
        else:
            texts = []
        if any(isinstance(text, str)
               and text.startswith(COMPACT_MESSAGE_PREFIX)
               and COMPACT_MESSAGE_TASK in text
               for text in texts):
            return True
    return False


def attest(since, session_id=None):
    """Session-scoped proxy request metadata; this does not prove generation acceptance."""
    sid = _session_id(session_id)
    if not sid:
        return None
    candidates = []
    pattern = os.path.join(TRAFFIC_ROOT, sid, "*", "*-upstream-request.json")
    for path in glob.iglob(pattern):
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        if mt >= since:
            candidates.append((mt, path))
    for _, path in sorted(candidates, reverse=True):
        try:
            with open(path, encoding="utf-8") as fh:
                body = json.load(fh)
            if _is_compaction_request(body):
                continue
            model = body.get("model")
            reasoning = body.get("reasoning") or {}
            if not isinstance(model, str) or not model:
                continue
            effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
            return model, effort
        except (OSError, ValueError, AttributeError):
            continue
    return None


def annotate_record_effort(record_path, attested_effort, ledger_path=None):
    with open(record_path, encoding="utf-8") as fh:
        record = json.load(fh)
    if not isinstance(record, dict):
        raise ValueError("sidecar record is not an object")
    unannotated = dict(record)
    try:
        if attested_effort:
            record["attestedEffort"] = attested_effort
            record["effortAttestationAgrees"] = record.get("effort") == attested_effort

        record_path = os.fspath(record_path)
        record_dir = os.path.dirname(os.path.abspath(record_path)) or "."
        fd, temp_path = tempfile.mkstemp(prefix=".ccp-record-", dir=record_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, record_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    except Exception:
        # The launcher wrote the record with its ledger disabled; this call is the run's only
        # ledger publication, so a failed annotation still publishes the unannotated row once.
        if ledger_path:
            _append_ledger(ledger_path, unannotated)
        raise

    if ledger_path:
        _append_ledger(ledger_path, record)
    return record


def _append_ledger(ledger_path, record):
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "health":
        return 0 if healthy(int(argv[2])) else 1
    if cmd == "wait":
        return 0 if wait_healthy(int(argv[2]), float(argv[3])) else 1
    if cmd == "freeport":
        print(freeport())
        return 0
    if cmd == "attest":
        if len(argv) not in (3, 4):
            return 2
        try:
            since = float(argv[2])
        except (TypeError, ValueError):
            return 2
        if not math.isfinite(since) or since < 0:
            return 2
        sid = argv[3] if len(argv) == 4 else (
            session_from_output(sys.stdin.read()) if not sys.stdin.isatty() else None)
        got = attest(since, sid)
        if not got:
            return 1
        print(f"{got[0] or ''}\t{got[1] or ''}")
        return 0
    if cmd == "record-effort":
        if len(argv) not in (4, 5):
            return 2
        try:
            annotate_record_effort(argv[2], argv[3], argv[4] if len(argv) == 5 else None)
        except Exception as exc:
            print(f"record-effort failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if cmd == "continuation":
        if len(argv) < 3:
            return 2
        print(1 if continuation_supported(proxy_version_text(argv[2])) else 0)
        return 0
    if cmd == "server-compaction":
        if len(argv) < 3:
            return 2
        print(1 if server_compaction_supported(proxy_version_text(argv[2])) else 0)
        return 0
    if cmd == "user-scope-bin":
        scoped = user_scope_ccp_bin()
        if not scoped:
            return 1
        print(scoped)
        return 0
    if cmd == "serves":
        if len(argv) != 4:
            return 2
        rc, reason = serves_check(argv[2], argv[3])
        if reason:
            print(reason)
        return rc
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
