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
  3. A streamed text-only request gets its response head and SSE `ping` events at once, while the
     shim waits on the upstream answer, which it then relays; an upstream error becomes an SSE `error`
     event. The proxy runs a server compaction before it answers a compaction request, which takes
     minutes at a high effort, and a client that gets no head times out and re-sends, superseding the
     compaction in flight (auto-memory archive/gotcha_codex_server_compaction_superseded.md).
  4. Any other streamed /v1/messages turn whose response head has not come within `head_grace_s` gets the
     same SSE head and pings, then the upstream stream as it arrives; an upstream error after the grace
     becomes an SSE `error` event. The proxy sends no head until the provider's first byte, and a codex
     turn can take minutes to its first byte, past the point where the client drops the turn and re-sends
     the whole context (archive/gotcha_claude_client_drops_headless_request_at_360s.md). A status that
     answers inside the grace (a 429) keeps its real code.
In 3 and 4, a client that leaves while the shim waits closes the shim's upstream call, and a text-only
request is not re-issued for it.
Every other request passes through byte-for-byte, streamed as it arrives.

Only stdlib, so the launcher can start it beside the proxy with no install step. Health: GET /healthz
is forwarded to the proxy. Logs one line per decision to --log (or stderr).
"""
import argparse
import http.client
import json
import os
import socket
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


def sse_error(err_type, message):
    data = json.dumps({"type": "error", "error": {"type": err_type, "message": message}})
    return f"event: error\ndata: {data}\n\n".encode("utf-8")


def sse_error_from(status, payload):
    """An upstream error body as an SSE error event: its own Anthropic error object when it has one."""
    try:
        j = json.loads(payload.decode("utf-8", errors="replace"))
    except Exception:
        j = None
    if isinstance(j, dict) and isinstance(j.get("error"), dict):
        return f"event: error\ndata: {json.dumps({'type': 'error', 'error': j['error']})}\n\n".encode("utf-8")
    text = payload.decode("utf-8", errors="replace")[:2000]
    return sse_error("api_error", f"upstream status {status}: {text}")


class _InFlight:
    """The upstream connection one client request has open; `close` ends it from another thread, and a closed
    holder refuses the next connection."""

    def __init__(self):
        self._lock = threading.Lock()
        self._conn = None
        self._closed = False

    def hold(self, conn):
        with self._lock:
            if self._closed:
                conn.close()
                raise ConnectionAbortedError("the client left; no upstream call is made for it")
            self._conn = conn
        return conn

    def close(self):
        with self._lock:
            self._closed = True
            conn, self._conn = self._conn, None
        if conn is None:
            return
        # shutdown, not close alone: a response being read holds a reference that keeps the socket open
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        conn.close()


class Shim(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_port = None
    log_path = None
    # A ping only has to arrive inside the client's idle window; the Anthropic API sends its own pings
    # at this order of interval during long waits.
    ping_interval_s = 10.0
    # A per-read silence bound on the proxy: it must outlast a server compaction plus the summary, and a
    # dead proxy still ends the wait.
    upstream_timeout_s = 1800
    # How long an ordinary streamed turn may wait for its upstream head before the shim answers the
    # client itself. It must stay well inside the client's own limit, ~360 s whatever API_TIMEOUT_MS says
    # (archive/gotcha_claude_client_drops_headless_request_at_360s.md). Provider refusals (429, 5xx)
    # arrive inside 60 s with their real status.
    head_grace_s = 60.0

    def log_message(self, fmt, *args):      # keep the server quiet; decisions go to _note
        pass

    def _note(self, msg):
        line = f"[text-only-shim {time.strftime('%H:%M:%S')}] {msg}\n"
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        else:
            sys.stderr.write(line)

    def _connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=self.upstream_timeout_s)

    def _upstream(self, method, body_bytes, headers, conn=None):
        conn = conn or self._connection()
        h = {k: v for k, v in headers.items() if k.lower() not in HOP_HEADERS}
        h["Content-Length"] = str(len(body_bytes))
        h["Connection"] = "close"
        conn.request(method, self.path, body=body_bytes, headers=h)
        return conn, conn.getresponse()

    def _send_sse_head(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()

    def _ping_while(self, worker):
        """Ping the client until `worker` ends."""
        while True:
            worker.join(self.ping_interval_s)
            if not worker.is_alive():
                return
            self.wfile.write(b'event: ping\ndata: {"type": "ping"}\n\n')
            self.wfile.flush()

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
            if isinstance(body, dict) and body.get("stream"):
                self._ordinary_streamed(raw)
                return
            conn, resp = self._upstream("POST", raw, dict(self.headers))
            self._relay_stream(conn, resp)
            return

        stripped, n_tools = strip_tools(body)
        self._note(f"text-only request: {n_tools} tool(s) stripped, tool_choice cleared")
        if body.get("stream"):
            self._text_only_streamed(stripped)
            return
        resp, payload = self._text_only_answer(stripped)
        self._relay_buffered(resp, payload)

    def _hold_streamed(self, work, upstream, label, relay, grace=0.0):
        """Run `work()` on a thread. When it ends inside `grace`, return its {"result"} or {"error"} for the caller to
        relay unheld; otherwise send the SSE head, ping until it ends, then `relay(result)` or an SSE error event,
        and return None. A client that leaves closes `upstream`."""
        out = {}
        def run():
            try:
                out["result"] = work()
            except Exception as e:           # the proxy refused, reset or went silent past its bound
                out["error"] = e
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(grace)
        if grace and not worker.is_alive():
            return out
        started = time.monotonic() - grace
        try:
            self._send_sse_head()
            self._ping_while(worker)
            if "error" in out:
                self._note(f"{label}: upstream failed after {time.monotonic() - started:.0f}s: {out['error']!r}")
                self.wfile.write(sse_error("api_error", f"shim: upstream failed: {out['error']!r}"))
            else:
                relay(out["result"])
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            self._note(f"client left a {label} after {time.monotonic() - started:.0f}s: {e!r}; closing its upstream call")
        finally:
            upstream.close()
        return None

    def _ordinary_streamed(self, raw):
        """Relay a streamed turn; past `head_grace_s` with no upstream head, hold the client with an SSE head and pings."""
        upstream = _InFlight()
        conn = upstream.hold(self._connection())
        out = self._hold_streamed(lambda: self._upstream("POST", raw, dict(self.headers), conn)[1], upstream,
                                  "streamed turn", self._relay_held_stream, grace=self.head_grace_s)
        if out is None:
            return
        if "error" in out:
            conn.close()
            raise out["error"]
        self._relay_stream(conn, out["result"])

    def _relay_held_stream(self, resp):
        ctype = resp.getheader("Content-Type") or ""
        if resp.status == 200 and "text/event-stream" in ctype:
            while True:
                try:
                    chunk = resp.read(4096)
                except (OSError, http.client.HTTPException) as e:
                    self._note(f"streamed turn: upstream stream broke after the head grace: {e!r}")
                    self.wfile.write(sse_error("api_error", f"shim: upstream stream broke: {e!r}"))
                    break
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        else:
            self._note(f"streamed turn: upstream answered status {resp.status} ({ctype}) after the head grace; relayed as an SSE error event")
            self.wfile.write(sse_error_from(resp.status, resp.read()))

    def _text_only_answer(self, stripped, upstream=None):
        """-> (response, payload): the upstream answer, re-issued once with the demand restated when it carries no text.
        Raises once `upstream` is closed, so a client that left is never re-issued for."""
        upstream = upstream or _InFlight()
        conn, resp = self._upstream("POST", json.dumps(stripped).encode("utf-8"), dict(self.headers),
                                    upstream.hold(self._connection()))
        payload = resp.read()
        ctype = resp.getheader("Content-Type") or ""
        conn.close()
        if response_has_text(resp.status, ctype, payload):
            return resp, payload
        self._note(f"text-only response carried no text (status {resp.status}, {len(payload)} bytes); re-issuing once with the demand restated")
        nudged = with_nudge(stripped)
        conn2, resp2 = self._upstream("POST", json.dumps(nudged).encode("utf-8"), dict(self.headers),
                                      upstream.hold(self._connection()))
        payload2 = resp2.read()
        conn2.close()
        self._note(f"re-issued response: status {resp2.status}, text={'yes' if response_has_text(resp2.status, resp2.getheader('Content-Type') or '', payload2) else 'NO'}")
        return resp2, payload2

    def _text_only_streamed(self, stripped):
        """Send the SSE head now and ping while the upstream answers, then relay the answer or an SSE error event."""
        upstream = _InFlight()
        self._hold_streamed(lambda: self._text_only_answer(stripped, upstream), upstream,
                            "streamed text-only request", self._relay_held_answer)

    def _relay_held_answer(self, answer):
        resp, payload = answer
        ctype = resp.getheader("Content-Type") or ""
        if resp.status == 200 and "text/event-stream" in ctype:
            self.wfile.write(payload)
        else:
            self._note(f"text-only upstream answered status {resp.status} ({ctype}); relayed as an SSE error event")
            self.wfile.write(sse_error_from(resp.status, payload))


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
