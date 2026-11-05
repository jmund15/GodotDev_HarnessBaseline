// Contract proof for .claude/tools/csharp-ls-adapter.js: the launcher's argument path, its missing-server
// failure, framing (a frame split across stdin writes reaches the server once) and a server exit answering
// every pending request. The server is a planted copy of node running a stub, never csharp-ls. Writes only
// under its temp root.
"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn, spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..", "..");
const ADAPTER_SRC = path.join(ROOT, ".claude", "tools", "csharp-ls-adapter.js");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "csharp-ls-adapter-contract-"));
const tools = path.join(temp, ".claude", "tools");
const adapter = path.join(tools, "csharp-ls-adapter.js");
const original = path.join(tools, "csharp-ls-original.exe");
const stub = path.join(temp, "stub_server.js");
const argsOut = path.join(temp, "args.json");
const solution = "contract.sln";

function plantServer() {
  // One planted copy serves every case: rewriting it while an earlier case's server still runs it is ETXTBSY on Linux.
  if (fs.existsSync(original)) return;
  try {
    fs.linkSync(process.execPath, original);
  } catch (error) {
    fs.copyFileSync(process.execPath, original);
  }
}

function runAdapter(args) {
  return spawn(process.execPath, [adapter, ...args], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
}

function exitOf(child, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("adapter did not exit within " + timeoutMs + " ms"));
    }, timeoutMs);
    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

async function missingServerIsRefused() {
  const done = spawnSync(process.execPath, [adapter, stub, "--solution", solution], { encoding: "utf8", timeout: 60000 });
  assert.notStrictEqual(done.status, 0, "adapter must fail without " + path.basename(original));
  assert.match(done.stderr, /ENOENT/);
  assert.ok(!fs.existsSync(argsOut), "stub must not run when the server is missing");
}

async function argumentsReachTheServer() {
  plantServer();
  const child = runAdapter([stub, "--solution", solution]);
  const exited = exitOf(child, 60000);
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const deadline = Date.now() + 60000;
  while (!fs.existsSync(argsOut) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.ok(fs.existsSync(argsOut), "planted server never recorded its argv: " + stderr);
  const code = await exited;
  assert.strictEqual(code, 0, stderr);
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(argsOut, "utf8")), ["--solution", solution]);
}

function frame(obj) {
  const body = JSON.stringify(obj);
  return "Content-Length: " + Buffer.byteLength(body, "utf-8") + "\r\n\r\n" + body;
}

function parseFrames(text) {
  const out = [];
  let rest = text;
  while (rest.length) {
    const match = rest.match(/^Content-Length: (\d+)\r\n\r\n/);
    if (!match) throw new Error("unframed bytes: " + JSON.stringify(rest.slice(0, 60)));
    const start = match[0].length;
    out.push(JSON.parse(rest.slice(start, start + Number(match[1]))));
    rest = rest.slice(start + Number(match[1]));
  }
  return out;
}

const recorded = path.join(temp, "server_stdin.txt");
const recorder = path.join(temp, "recorder_server.js");
const dropper = path.join(temp, "dropper_server.js");

async function splitFrameReachesTheServerOnce() {
  plantServer();
  const child = runAdapter([recorder]);
  const exited = exitOf(child, 60000);
  const deadline = Date.now() + 60000;
  while (!fs.existsSync(recorded) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const message = { jsonrpc: "2.0", id: 3, method: "textDocument/implementation", params: { pad: "x".repeat(200) } };
  const bytes = frame(message);
  const cut = bytes.indexOf("\r\n\r\n") + 4;
  child.stdin.write(bytes.slice(0, cut));
  await new Promise((resolve) => setTimeout(resolve, 300));
  child.stdin.write(bytes.slice(cut));
  await new Promise((resolve) => setTimeout(resolve, 300));
  child.stdin.end();
  await exited;
  assert.deepStrictEqual(parseFrames(fs.readFileSync(recorded, "utf8")), [message]);
}

async function exitAnswersPendingRequests() {
  plantServer();
  const child = runAdapter([dropper]);
  const exited = exitOf(child, 60000);
  let stdout = "";
  child.stdout.on("data", (chunk) => { stdout += chunk; });
  child.stdin.write(frame({ jsonrpc: "2.0", id: 7, method: "textDocument/implementation", params: {} }));
  await exited;
  const answers = parseFrames(stdout).filter((msg) => msg.id === 7);
  assert.strictEqual(answers.length, 1, "pending request 7 got no answer: " + JSON.stringify(stdout));
  assert.ok(answers[0].error && /exited/.test(answers[0].error.message), JSON.stringify(answers[0]));
}

async function main() {
  fs.mkdirSync(tools, { recursive: true });
  fs.copyFileSync(ADAPTER_SRC, adapter);
  fs.writeFileSync(stub, "require('fs').writeFileSync(" + JSON.stringify(argsOut) +
    ", JSON.stringify(process.argv.slice(2)));\nprocess.exit(0);\n");
  fs.writeFileSync(recorder, "const fs = require('fs');\nfs.writeFileSync(" + JSON.stringify(recorded) + ", '');\n" +
    "setInterval(() => {}, 1000);\n" +
    "process.stdin.on('data', (d) => fs.appendFileSync(" + JSON.stringify(recorded) + ", d));\n" +
    "process.stdin.on('end', () => process.exit(0));\n");
  fs.writeFileSync(dropper, "process.stdin.once('data', () => process.exit(0));\n");
  const cases = [["missing server is refused", missingServerIsRefused],
    ["arguments reach the server", argumentsReachTheServer],
    ["a frame split across two writes reaches the server once", splitFrameReachesTheServerOnce],
    ["a server exit answers each pending request with an error", exitAnswersPendingRequests]];
  let failed = 0;
  for (const [name, run] of cases) {
    try {
      await run();
      console.log("ok   " + name);
    } catch (error) {
      failed += 1;
      console.log("FAIL " + name + ": " + error.message);
    }
  }
  fs.rmSync(temp, { recursive: true, force: true });
  process.exitCode = failed ? 1 : 0;
}

main();
