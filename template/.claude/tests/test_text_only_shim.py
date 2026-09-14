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
    script = []          # per-request SSE bodies, consumed in order; last one repeats

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
        body = (Upstream.script.pop(0) if len(Upstream.script) > 1 else Upstream.script[0]).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers()
        for i in range(0, len(body), 64):        # dribble, like a real stream
            self.wfile.write(body[i:i + 64]); self.wfile.flush()


def free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def post(port, body, path="/v1/messages"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    raw = json.dumps(body).encode("utf-8")
    c.request("POST", path, body=raw, headers={"Content-Type": "application/json", "Content-Length": str(len(raw)), "x-api-key": "k"})
    r = c.getresponse(); data = r.read(); c.close()
    return r.status, r.getheader("Content-Type"), data.decode("utf-8")


def main():
    up_port, shim_port = free_port(), free_port()
    up = ThreadingHTTPServer(("127.0.0.1", up_port), Upstream); up.daemon_threads = True
    threading.Thread(target=up.serve_forever, daemon=True).start()
    log = Path(os.environ.get("TEMP") or "/tmp") / f"text_only_shim_test_{shim_port}.log"
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

    print(f"\ntest_text_only_shim: {passed} passed, {fails} failed")
    sv.shutdown(); up.shutdown()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
