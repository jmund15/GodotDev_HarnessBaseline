#!/usr/bin/env python3
"""Proof for scripts/lib/text_only_shim.py: a compaction-summary request reaches the proxy with no tools, an
empty answer is re-issued once, and everything else passes through byte-for-byte.

    python3 .claude/tests/test_text_only_shim.py
"""
import http.client
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import text_only_shim as shim  # noqa: E402

SSE_TEXT = ('event: message_start\ndata: {"type":"message_start"}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"<analysis>ok</analysis>"}}\n\n'
            'event: message_stop\ndata: {"type":"message_stop"}\n\n')
SSE_TOOL_ONLY = ('event: message_start\ndata: {"type":"message_start"}\n\n'
                 'event: content_block_start\ndata: {"type":"content_block_start","content_block":{"type":"tool_use","name":"Read"}}\n\n'
                 'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{}"}}\n\n'
                 'event: message_stop\ndata: {"type":"message_stop"}\n\n')

SUMMARY_TURN = ("CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.\n\nYour task is to create a detailed summary... "
                "an <analysis> block followed by a <summary> block.")


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []
    script = []          # per-request SSE bodies or (status, content_type, body), consumed in order; last one repeats
    delay_s = 0.0        # seconds of silence before the response head, like a server compaction
    truncate = False     # send one good chunk, then a malformed chunk header: the stream breaks mid-read
    closed = 0           # requests whose caller closed the connection during the silence

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        Upstream.seen.append(json.loads(raw.decode("utf-8")))
        item = Upstream.script.pop(0) if len(Upstream.script) > 1 else Upstream.script[0]
        status, ctype, body = item if isinstance(item, tuple) else (200, "text/event-stream", item)
        body = body.encode("utf-8")
        if self._caller_left_within(Upstream.delay_s):
            Upstream.closed += 1
            return
        self.send_response(status); self.send_header("Content-Type", ctype)
        if status != 200:
            self.send_header("Content-Length", str(len(body)))
        elif Upstream.truncate:
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close"); self.end_headers()
            self.wfile.write(b"%x\r\n%s\r\nnot-a-chunk-size\r\n" % (len(body[:64]), body[:64])); self.wfile.flush()
            return
        self.send_header("Connection", "close"); self.end_headers()
        for i in range(0, len(body), 64):        # dribble, like a real stream
            self.wfile.write(body[i:i + 64]); self.wfile.flush()


    def _caller_left_within(self, seconds):
        import select
        import socket
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _w, _x = select.select([self.connection], [], [], max(0.0, min(0.05, end - time.monotonic())))
            if ready:
                try:
                    return self.connection.recv(1, socket.MSG_PEEK) == b""
                except OSError:
                    return True
        return False


def free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def post(port, body, path="/v1/messages", timeout=10):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    raw = json.dumps(body).encode("utf-8")
    c.request("POST", path, body=raw, headers={"Content-Type": "application/json", "Content-Length": str(len(raw)), "x-api-key": "k"})
    r = c.getresponse(); data = r.read(); c.close()
    return r.status, r.getheader("Content-Type"), data.decode("utf-8")


def main():
    up_port, shim_port = free_port(), free_port()
    up = ThreadingHTTPServer(("127.0.0.1", up_port), Upstream); up.daemon_threads = True
    threading.Thread(target=up.serve_forever, daemon=True).start()
    log = Path(os.environ.get("TEMP") or "/tmp") / f"text_only_shim_test_{shim_port}.log"
    shim.Shim.ping_interval_s = 0.2
    sv = shim.serve(shim_port, up_port, str(log))
    threading.Thread(target=sv.serve_forever, daemon=True).start()

    passed = fails = 0
    def ck(name, cond, detail=""):
        nonlocal passed, fails
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"\n        {detail}"))
        if cond: passed += 1
        else: fails += 1

    tools = [{"name": "Read", "input_schema": {"type": "object"}}]
    # 1. an ordinary tool-bearing turn passes through with its tools and its stream intact
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TOOL_ONLY]
    st, ct, data = post(shim_port, {"model": "m", "max_tokens": 10, "stream": True, "tools": tools,
                                    "messages": [{"role": "user", "content": "read the file"}]})
    ck("1a ordinary request keeps its tools upstream", Upstream.seen and "tools" in Upstream.seen[0], str(Upstream.seen[:1])[:200])
    ck("1b ordinary stream relayed byte-for-byte", st == 200 and data == SSE_TOOL_ONLY and "event-stream" in (ct or ""), data[:120])

    # 2. the compaction signature: tools stripped, tool_choice cleared, text answer relayed
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TEXT]
    st, ct, data = post(shim_port, {"model": "m", "max_tokens": 10, "stream": True, "tools": tools, "tool_choice": {"type": "auto"},
                                    "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "x"},
                                                 {"role": "user", "content": [{"type": "text", "text": SUMMARY_TURN}]}]})
    ck("2a text-only request reaches upstream with no tools", Upstream.seen and "tools" not in Upstream.seen[0] and "tool_choice" not in Upstream.seen[0], str(Upstream.seen[:1])[:200])
    ck("2b the text answer is relayed", st == 200 and "text_delta" in data and len(Upstream.seen) == 1, data[:120])

    # 3. an empty (tool-only) answer to a text-only request is re-issued once with the demand restated
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TOOL_ONLY, SSE_TEXT]
    st, ct, data = post(shim_port, {"model": "m", "max_tokens": 10, "stream": True, "tools": tools,
                                    "messages": [{"role": "user", "content": SUMMARY_TURN}]})
    ck("3a re-issued exactly once", len(Upstream.seen) == 2, str(len(Upstream.seen)))
    last_user = Upstream.seen[-1]["messages"][-1]["content"] if len(Upstream.seen) == 2 else ""
    ck("3b the restated demand rides inside the last user turn", isinstance(last_user, str) and last_user.endswith(shim.NUDGE) and "tools" not in Upstream.seen[-1], str(last_user)[-100:])
    ck("3c the client receives the second (text) answer", st == 200 and "text_delta" in data, data[:120])

    # 4. count_tokens and health pass through untouched
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TEXT]
    post(shim_port, {"model": "m", "messages": [{"role": "user", "content": SUMMARY_TURN}], "tools": tools}, path="/v1/messages/count_tokens?beta=true")
    ck("4a count_tokens keeps its tools (never shaped)", Upstream.seen and "tools" in Upstream.seen[0], str(Upstream.seen[:1])[:120])
    c = http.client.HTTPConnection("127.0.0.1", shim_port, timeout=10); c.request("GET", "/healthz"); r = c.getresponse(); h = r.read(); c.close()
    ck("4b GET /healthz is forwarded", r.status == 200 and b'"ok":true' in h, h[:60])

    # 5. the signature is the whole marker set, not one word
    ck("5 a turn that merely mentions <summary> is not text-only",
       not shim.is_text_only_request({"messages": [{"role": "user", "content": "please put it in a <summary> section"}]}))

    # 6. a slow text-only answer (a server compaction) still gets its response head at once, and pings
    #    keep the stream alive, so a client timeout shorter than the upstream's silence never fires
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TEXT]; Upstream.delay_s = 2.0
    compaction = {"model": "m", "max_tokens": 10, "stream": True, "tools": tools,
                  "messages": [{"role": "user", "content": SUMMARY_TURN}]}
    try:
        st, ct, data = post(shim_port, compaction, timeout=1.0)
    except Exception as e:
        st, ct, data = None, None, f"client timed out: {e!r}"
    ck("6a slow text-only answer: head arrives before the client times out", st == 200 and "event-stream" in (ct or ""), data[:160])
    ck("6b pings precede the relayed text answer", "event: ping" in data and data.index("event: ping") < data.find("text_delta"), data[:160])
    ck("6c exactly one upstream call", len(Upstream.seen) == 1, str(len(Upstream.seen)))
    Upstream.delay_s = 0.0

    # 7. an upstream error on a streamed text-only request reaches the client as an SSE error event
    err = json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "upstream busy"}})
    Upstream.seen.clear(); Upstream.script[:] = [(529, "application/json", err)]
    st, ct, data = post(shim_port, compaction)
    ck("7 upstream error becomes an SSE error event carrying its body",
       st == 200 and "event: error" in data and "overloaded_error" in data, f"{st} {data[:160]}")

    # 8. a non-streamed text-only request keeps the upstream status
    Upstream.seen.clear(); Upstream.script[:] = [(529, "application/json", err)]
    st, ct, data = post(shim_port, dict(compaction, stream=False))
    ck("8 non-streamed text-only request relays the upstream status", st == 529 and "overloaded_error" in data, f"{st} {data[:120]}")

    # 9. a slow ORDINARY streamed turn (a codex turn whose first byte takes minutes) gets its head and pings
    #    once the head grace passes, then the upstream stream itself; the client never times out and re-sends
    #    the whole context
    shim.Shim.head_grace_s = 0.3
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TOOL_ONLY]; Upstream.delay_s = 2.0
    ordinary = {"model": "m", "max_tokens": 10, "stream": True, "tools": tools,
                "messages": [{"role": "user", "content": "read the file"}]}
    try:
        st, ct, data = post(shim_port, ordinary, timeout=1.0)
    except Exception as e:
        st, ct, data = None, None, f"client timed out: {e!r}"
    ck("9a slow ordinary turn: head arrives before the client times out", st == 200 and "event-stream" in (ct or ""), data[:160])
    ck("9b pings precede the relayed upstream stream, which arrives intact",
       data.startswith("event: ping") and data.endswith(SSE_TOOL_ONLY), data[:160])
    ck("9c the ordinary turn keeps its tools and makes exactly one upstream call",
       len(Upstream.seen) == 1 and "tools" in Upstream.seen[0], str(len(Upstream.seen)))
    Upstream.delay_s = 0.0

    # 10. an upstream status that answers inside the head grace keeps its real status (a 429 stays a 429,
    #     so the client's own retry and the launcher's usage-limit count still read it)
    rl = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
    Upstream.seen.clear(); Upstream.script[:] = [(429, "application/json", rl)]
    st, ct, data = post(shim_port, ordinary)
    ck("10 a fast upstream error on an ordinary turn keeps its status", st == 429 and "rate_limit_error" in data, f"{st} {data[:120]}")

    # 11. a slow upstream error after the head grace reaches the client as an SSE error event
    Upstream.seen.clear(); Upstream.script[:] = [(502, "application/json", err)]; Upstream.delay_s = 1.0
    st, ct, data = post(shim_port, ordinary)
    ck("11 a slow upstream error on an ordinary turn becomes an SSE error event",
       st == 200 and "event: error" in data and "overloaded_error" in data, f"{st} {data[:160]}")
    Upstream.delay_s = 0.0

    # 12. an upstream stream that breaks after the shim's own head reaches the client as an SSE error event,
    #     and the shim log blames the upstream, never the client
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TOOL_ONLY]; Upstream.delay_s = 1.0; Upstream.truncate = True
    try:
        st, ct, data = post(shim_port, ordinary)
    except Exception as e:
        st, ct, data = None, None, f"client read failed: {e!r}"
    shim_log = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    ck("12a a mid-stream upstream break after the head becomes an SSE error event",
       st == 200 and "event: error" in data and "upstream stream broke" in data, f"{st} {data[-200:]}")
    ck("12b the shim log names the upstream break, not a client that left",
       "upstream stream broke" in shim_log and "client left" not in shim_log, shim_log[-300:])
    Upstream.delay_s = 0.0; Upstream.truncate = False

    # 13. a client that leaves a streamed text-only wait closes the in-flight upstream call, and the shim
    #     never re-issues the request for a client that is gone
    import socket
    Upstream.seen.clear(); Upstream.script[:] = [SSE_TOOL_ONLY, SSE_TEXT]; Upstream.delay_s = 3.0; Upstream.closed = 0
    raw = json.dumps(compaction).encode("utf-8")
    client = socket.create_connection(("127.0.0.1", shim_port), timeout=5)
    client.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                   % (len(raw), raw))
    head = client.recv(4096)
    client.close()
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and not Upstream.closed and len(Upstream.seen) < 2:
        time.sleep(0.1)
    time.sleep(0.5)
    ck("13a a client leaving a text-only wait closes the upstream call",
       head.startswith(b"HTTP/1.1 200") and Upstream.closed == 1, f"closed={Upstream.closed} head={head[:40]!r}")
    ck("13b ... and the shim does not re-issue it", len(Upstream.seen) == 1, str(len(Upstream.seen)))
    Upstream.delay_s = 0.0

    print(f"\ntest_text_only_shim: {passed} passed, {fails} failed")
    sv.shutdown(); up.shutdown()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
