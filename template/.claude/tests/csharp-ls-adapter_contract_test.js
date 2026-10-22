// Contract proof for .claude/tools/csharp-ls-adapter.js: the launcher's argument path and its missing-server
// failure. The server is a planted copy of node that records its argv and exits, never csharp-ls. Writes
// only under its temp root.
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

async function main() {
  fs.mkdirSync(tools, { recursive: true });
  fs.copyFileSync(ADAPTER_SRC, adapter);
  fs.writeFileSync(stub, "require('fs').writeFileSync(" + JSON.stringify(argsOut) +
    ", JSON.stringify(process.argv.slice(2)));\nprocess.exit(0);\n");
  const cases = [["missing server is refused", missingServerIsRefused],
    ["arguments reach the server", argumentsReachTheServer]];
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
