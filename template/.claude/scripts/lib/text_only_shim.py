"""text_only_shim.py — a request-shaping hop between a `claude` child and its provider proxy.

    python3 text_only_shim.py --listen <port> --upstream <port> [--log <file>]

Claude Code's auto-compaction asks for the conversation summary with a user turn that says "Respond with
TEXT ONLY. Do NOT call any tools" — and still attaches the full tool list and no `tool_choice`. A model
that answers that turn with a tool call (measured 2026-09-09: gpt-5.6-luna at effort low, 3 of 3) makes
the client read an empty summary and kill the run: "automatic compaction failed: summarization produced
empty response". The instruction is advisory; the tool list is what makes the failure possible.

This shim makes the text-only contract mechanical, for any model:
  1. A request whose last user turn carries the text-only signature is forwarded WITHOUT `tools` /
     `tool_choice`. No tool is offered, so no tool call can come back.
  2. If its response still carries no text (nothing but thinking, or nothing at all), the request is
     re-issued once with the demand restated inside that user turn, and the second response is relayed.
Every other request passes through byte-for-byte, streamed as it arrives.

Only stdlib, so the launcher can start it beside the proxy with no install step. Health: GET /healthz
is forwarded to the proxy. Logs one line per decision to --log (or stderr).
"""
import argparse
import http.client
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT_ONLY_MARKERS = ("Respond with TEXT ONLY", "<analysis>", "<summary>")
NUDGE = ("\n\nTool calls are unavailable in this turn and no tool is offered. Respond now with plain text only: "
         "an <analysis> block followed by a <summary> block.")
HOP_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "keep-alive"}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join((b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_text_only_request(body):
    """The last user turn carries Claude Code's compaction signature: an instruction to answer in prose only."""
    msgs = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(msgs, list):
        return False
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            t = _text_of(m.get("content"))
            return all(k in t for k in TEXT_ONLY_MARKERS)
    return False


def strip_tools(body):
    """-> (new_body, n_tools_removed). Never mutates the caller's object."""
    b = dict(body)
    n = len(b.get("tools") or []) if isinstance(b.get("tools"), list) else 0
    b.pop("tools", None)
    b.pop("tool_choice", None)
    return b, n


def with_nudge(body):
    """Restate the demand inside the last user turn (roles must keep alternating, so no new turn)."""
    b = json.loads(json.dumps(body))
    for m in reversed(b.get("messages") or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                m["content"] = c + NUDGE
            elif isinstance(c, list):
                c.append({"type": "text", "text": NUDGE.strip()})
            break
    return b


def response_has_text(status, content_type, payload):
    """True when a /v1/messages response carries at least one non-empty text block (SSE or JSON)."""
    if status != 200:
        return True      # an error is relayed as-is; only a clean-but-empty answer is retried
    text = payload.decode("utf-8", errors="replace")
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                e = json.loads(line[5:].strip())
            except Exception:
                continue
            d = e.get("delta") if isinstance(e, dict) else None
            if isinstance(d, dict) and d.get("type") == "text_delta" and (d.get("text") or "").strip():
                return True
        return False
    try:
        j = json.loads(text)
    except Exception:
        return True      # not a shape we judge; relay
    for b in j.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip():
            return True
    return False


class Shim(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_port = None
    log_path = None

    def log_message(self, fmt, *args):      # keep the server quiet; decisions go to _note
        pass

    def _note(self, msg):
        line = f"[text-only-shim {time.strftime('%H:%M:%S')}] {msg}\n"
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        else:
            sys.stderr.write(line)

    def _upstream(self, method, body_bytes, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=600)
        h = {k: v for k, v in headers.items() if k.lower() not in HOP_HEADERS}
        h["Content-Length"] = str(len(body_bytes))
        h["Connection"] = "close"
        conn.request(method, self.path, body=body_bytes, headers=h)
        return conn, conn.getresponse()

    def _send_head(self, resp, length=None):
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in HOP_HEADERS:
                continue
            self.send_header(k, v)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Connection", "close")
        self.end_headers()

    def _relay_stream(self, conn, resp):
        self._send_head(resp)
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            conn.close()

    def _relay_buffered(self, resp, payload):
        self._send_head(resp, length=len(payload))
        self.wfile.write(payload)
        self.wfile.flush()

    def do_GET(self):
        conn, resp = self._upstream("GET", b"", dict(self.headers))
        self._relay_stream(conn, resp)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        body = None
        if self.path.startswith("/v1/messages") and "count_tokens" not in self.path:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = None
        if not (isinstance(body, dict) and is_text_only_request(body)):
            conn, resp = self._upstream("POST", raw, dict(self.headers))
            self._relay_stream(conn, resp)
            return

        stripped, n_tools = strip_tools(body)
        self._note(f"text-only request: {n_tools} tool(s) stripped, tool_choice cleared")
        conn, resp = self._upstream("POST", json.dumps(stripped).encode("utf-8"), dict(self.headers))
        payload = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        if response_has_text(resp.status, ctype, payload):
            self._relay_buffered(resp, payload)
            return
        self._note(f"text-only response carried no text (status {resp.status}, {len(payload)} bytes); re-issuing once with the demand restated")
        nudged = with_nudge(stripped)
        conn2, resp2 = self._upstream("POST", json.dumps(nudged).encode("utf-8"), dict(self.headers))
        payload2 = resp2.read()
        conn2.close()
        self._note(f"re-issued response: status {resp2.status}, text={'yes' if response_has_text(resp2.status, resp2.getheader('Content-Type') or '', payload2) else 'NO'}")
        self._relay_buffered(resp2, payload2)


def serve(listen, upstream, log_path=None):
    handler = type("BoundShim", (Shim,), {"upstream_port": upstream, "log_path": log_path})
    # Four Workflow agents plus their count_tokens calls open connections in bursts; the stdlib default
    # backlog of 5 refused one under that burst (measured 2026-09-09: "ConnectionRefused" on a judge panel).
    ThreadingHTTPServer.request_queue_size = 128
    srv = ThreadingHTTPServer(("127.0.0.1", listen), handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--upstream", type=int, required=True)
    ap.add_argument("--log", default=None)
    a = ap.parse_args(argv)
    srv = serve(a.listen, a.upstream, a.log)
    sys.stderr.write(f"[text-only-shim] listening on :{a.listen} -> :{a.upstream}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
