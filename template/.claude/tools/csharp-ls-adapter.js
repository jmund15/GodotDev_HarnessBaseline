#!/usr/bin/env node
// csharp-ls-adapter.js — Node.js adapter between Claude Code and csharp-ls
//
// Fixes two Claude Code ↔ csharp-ls incompatibilities:
//   1. workspace/configuration: csharp-ls asks for settings (solution path);
//      Claude Code doesn't implement this request. Adapter intercepts and responds.
//   2. file:// → file:///: Claude Code sends rootUri as file://C:/... (2 slashes)
//      but textDocument URIs as file:///C:/... (3 slashes). csharp-ls can't match
//      documents to workspace with mismatched URI schemes. Adapter normalizes.
//
// Also intercepts client/registerCapability and window/workDoneProgress requests
// that csharp-ls sends but Claude Code doesn't handle.
//
// Usage: node csharp-ls-adapter.js [--solution <path.sln>] [other csharp-ls args]
// Debug: LSP_ADAPTER_DEBUG=1 → logs to csharp-ls-adapter.log in the same directory
//
// Canonical copy: .claude/tools/csharp-ls-adapter.js (tracked in git)
// Deployed copy:  ~/.dotnet/tools/csharp-ls-adapter.js (on each machine)
"use strict";

const { spawn } = require("child_process");
const { join, dirname } = require("path");
const fs = require("fs");

const SCRIPT_DIR = dirname(process.argv[1] || __filename);
const ORIGINAL = join(SCRIPT_DIR, "csharp-ls-original.exe");
const DEBUG = process.env.LSP_ADAPTER_DEBUG === "1";
const LOG_FILE = join(SCRIPT_DIR, "csharp-ls-adapter.log");

// Per-request timeout watchdog. csharp-ls v0.22.0 had a workspace/diagnostic
// CPU-loop bug (fixed in v0.23.0/v0.24.0) that could leave individual requests
// hanging indefinitely — most acutely findReferences on heavy call graphs.
// Without a timeout, a hung request blocks the calling client tool with no
// visible error. This watchdog synthesizes a JSON-RPC error response after
// REQUEST_TIMEOUT_MS so the client unblocks and reports a useful failure.
// Visible failure beats silent multi-minute hang.
const REQUEST_TIMEOUT_MS = parseInt(process.env.LSP_REQUEST_TIMEOUT_MS || "60000", 10);
const PENDING_REQUESTS = new Map();  // id -> { method, timer, startedAt }

// Parse --solution from CLI args (set by plugin.json)
const args = process.argv.slice(2);
const solutionIdx = args.indexOf("--solution");
const SOLUTION_PATH = solutionIdx !== -1 && args[solutionIdx + 1]
  ? args[solutionIdx + 1] : null;

function log(...args) {
  if (!DEBUG) return;
  const line = `[${new Date().toISOString()}] ${args.join(" ")}\n`;
  fs.appendFileSync(LOG_FILE, line);
  process.stderr.write(`[ADAPTER] ${args.join(" ")}\n`);
}

// LSP message parser — handles Content-Length framed JSON-RPC
class LspParser {
  constructor() {
    this.buffer = Buffer.alloc(0);
    this.contentLength = -1;
  }

  feed(data) {
    this.buffer = Buffer.concat([this.buffer, data]);
    const messages = [];
    while (true) {
      if (this.contentLength === -1) {
        const headerEnd = this.buffer.indexOf("\r\n\r\n");
        if (headerEnd === -1) break;
        const header = this.buffer.slice(0, headerEnd).toString("utf-8");
        const match = header.match(/Content-Length:\s*(\d+)/i);
        if (!match) {
          this.buffer = this.buffer.slice(headerEnd + 4);
          continue;
        }
        this.contentLength = parseInt(match[1], 10);
        this.buffer = this.buffer.slice(headerEnd + 4);
      }
      if (this.buffer.length < this.contentLength) break;
      const body = this.buffer.slice(0, this.contentLength).toString("utf-8");
      this.buffer = this.buffer.slice(this.contentLength);
      this.contentLength = -1;
      try {
        messages.push(JSON.parse(body));
      } catch (e) {
        log("JSON parse error:", e.message);
      }
    }
    return messages;
  }
}

function encodeMessage(obj) {
  const body = JSON.stringify(obj);
  const len = Buffer.byteLength(body, "utf-8");
  return `Content-Length: ${len}\r\n\r\n${body}`;
}

// Watchdog: track outbound requests so we can synthesize a timeout error if
// csharp-ls never responds. Only requests (have id + method) are tracked;
// notifications (no id) are fire-and-forget per JSON-RPC 2.0.
function trackRequest(msg) {
  if (msg.id == null || !msg.method) return;
  const timer = setTimeout(() => onTimeout(msg.id), REQUEST_TIMEOUT_MS);
  PENDING_REQUESTS.set(msg.id, { method: msg.method, timer, startedAt: Date.now() });
}

// Watchdog: clear timer when csharp-ls responds. Responses have id but no method.
function clearRequest(msg) {
  if (msg.id == null || msg.method) return;
  const entry = PENDING_REQUESTS.get(msg.id);
  if (!entry) return;
  clearTimeout(entry.timer);
  PENDING_REQUESTS.delete(msg.id);
}

// Watchdog: fires when csharp-ls hasn't responded within REQUEST_TIMEOUT_MS.
// Synthesizes a JSON-RPC error response so the client unblocks. Note: if the
// real response arrives later, the client may receive a duplicate response
// for this id — acceptable corner case; LSP clients tolerate late responses.
function onTimeout(id) {
  const entry = PENDING_REQUESTS.get(id);
  if (!entry) return;
  const elapsedMs = Date.now() - entry.startedAt;
  log(`TIMEOUT: ${entry.method} id:${id} after ${elapsedMs}ms`);
  process.stderr.write(`[ADAPTER] TIMEOUT ${entry.method} id:${id} after ${elapsedMs}ms\n`);
  process.stdout.write(encodeMessage({
    jsonrpc: "2.0",
    id,
    error: {
      code: -32001,  // Server-defined error space per JSON-RPC 2.0
      message: `csharp-ls request timeout after ${elapsedMs}ms (LSP_REQUEST_TIMEOUT_MS=${REQUEST_TIMEOUT_MS})`,
    },
  }));
  PENDING_REQUESTS.delete(id);
}

// Methods that csharp-ls sends as requests but Claude Code doesn't handle.
// Requests have an `id` — we respond with `result: null` to satisfy the protocol.
const INTERCEPTED_METHODS = new Set([
  "window/workDoneProgress/create",
  "window/workDoneProgress/cancel",
  "client/registerCapability",
]);

// Notifications that csharp-ls sends but we want to discard entirely.
// Notifications have a `method` but no `id` — drop them without responding.
// To re-disable a notification, add its entry to this set and redeploy via
// .claude/tools/setup-csharp-ls.sh.
//
// History: textDocument/publishDiagnostics was previously dropped (2026-04-20)
// because csharp-ls v0.22.0 emitted dense CS0102 false-positive duplicate-
// definition diagnostics for types defined exactly once (BBDataSig, Wizard).
// Root cause was a workspace/diagnostic CPU loop fixed upstream in v0.23.0
// (runaway diagnostic traffic) and v0.24.0 (workspace/diagnostic busy loop).
// Re-enabled 2026-05-03 alongside csharp-ls 0.22→0.24 upgrade.
//
// Re-disabled 2026-05-07: even on v0.24, csharp-ls still indexes Godot's
// generated obj/.../*.g.cs partial-class output alongside the source `.cs`
// file, producing CS0102/CS0111 "already defined" false positives whenever
// [Export]/[Signal] attributes trigger source generation. The flood is
// structural (two on-disk files declare the same partial), not a server bug,
// so the v0.24 fix can't address it. dotnet build is authoritative per
// csharp_lsp.md:69 — push diagnostics buy nothing here.
const DROPPED_NOTIFICATIONS = new Set([
  "textDocument/publishDiagnostics",
]);

// Spawn the real csharp-ls
log("Starting adapter, pid:", process.pid, "args:", args.join(" "),
  "solution:", SOLUTION_PATH || "(none)");

const server = spawn(ORIGINAL, args, {
  stdio: ["pipe", "pipe", "inherit"],
  windowsHide: true,
});

log("Spawned csharp-ls, pid:", server.pid);

// Fix file:// URIs to file:/// (RFC 8089 local path on Windows)
function fixFileUri(uri) {
  if (typeof uri === "string" && uri.startsWith("file://") && !uri.startsWith("file:///")) {
    return "file:///" + uri.slice(7);
  }
  return uri;
}

// Client (Claude Code) → Server (csharp-ls): fix URIs, then forward
const clientParser = new LspParser();
process.stdin.on("data", (data) => {
  const messages = clientParser.feed(data);
  if (messages.length === 0) {
    server.stdin.write(data);
    return;
  }
  for (const msg of messages) {
    if (msg.method === "initialize" && msg.params) {
      if (msg.params.rootUri) msg.params.rootUri = fixFileUri(msg.params.rootUri);
      if (msg.params.workspaceFolders) {
        for (const folder of msg.params.workspaceFolders) {
          if (folder.uri) folder.uri = fixFileUri(folder.uri);
        }
      }
      log("CLIENT->SERVER: initialize (URI-fixed) rootUri:", msg.params.rootUri);
    } else {
      log("CLIENT->SERVER:", msg.method || msg.id || "response");
    }
    trackRequest(msg);
    server.stdin.write(encodeMessage(msg));
  }
});

// Server (csharp-ls) → Client (Claude Code): intercept problematic requests
const serverParser = new LspParser();
server.stdout.on("data", (data) => {
  const messages = serverParser.feed(data);
  for (const msg of messages) {
    clearRequest(msg);  // Clears watchdog timer if this msg is a response to a tracked request
    if (msg.method === "workspace/configuration") {
      // csharp-ls asks "what are my settings?" — respond with solution path
      // Claude Code doesn't implement this request, so we intercept and reply
      log("INTERCEPTED: workspace/configuration id:", msg.id);
      const result = (msg.params?.items || []).map((item) => {
        if (item.section === "csharp") {
          return SOLUTION_PATH ? { solution: SOLUTION_PATH } : {};
        }
        return {};
      });
      const response = encodeMessage({
        jsonrpc: "2.0",
        id: msg.id,
        result: result,
      });
      server.stdin.write(response);
    } else if (msg.method && INTERCEPTED_METHODS.has(msg.method)) {
      if (msg.method === "client/registerCapability") {
        log("INTERCEPTED: client/registerCapability id:", msg.id,
          "params:", JSON.stringify(msg.params, null, 2));
      } else {
        log("INTERCEPTED:", msg.method, "id:", msg.id);
      }
      const response = encodeMessage({
        jsonrpc: "2.0",
        id: msg.id,
        result: null,
      });
      server.stdin.write(response);
    } else if (msg.method && DROPPED_NOTIFICATIONS.has(msg.method)) {
      // Notification with no `id` — discard silently. No response needed
      // (notifications are fire-and-forget per JSON-RPC 2.0 spec).
      const diagCount = msg.params?.diagnostics?.length ?? 0;
      log("DROPPED:", msg.method, diagCount > 0 ? `(${diagCount} diagnostics)` : "");
    } else {
      const preview = msg.params ? JSON.stringify(msg.params).slice(0, 200) : "";
      log("SERVER->CLIENT:", msg.method || `response:${msg.id}` || "notification",
        preview ? `params: ${preview}` : "");
      process.stdout.write(encodeMessage(msg));
    }
  }
});

// Handle process lifecycle
server.on("close", (code) => {
  log("csharp-ls exited with code:", code);
  process.exit(code || 0);
});

process.on("SIGTERM", () => server.kill("SIGTERM"));
process.on("SIGINT", () => server.kill("SIGINT"));
process.stdin.on("end", () => {
  log("stdin ended, killing server");
  server.kill();
});
