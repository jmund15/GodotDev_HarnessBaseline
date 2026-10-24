'use strict';
// Node twin of sitecustomize.py: the runner sets HARNESS_READ_LOG and puts `--require <this file>` on NODE_OPTIONS,
// so a JS proof and every node child that inherits the environment append `["r"|"w"|"l"|"p"|"s", value]` lines
// to `<dir>/<pid>.jsonl`. ESM module loads bypass `fs` and stay unrecorded.
const LOG = process.env.HARNESS_READ_LOG;
if (LOG) {
  const fs = require('fs');
  const path = require('path');
  const { fileURLToPath } = require('url');
  const writeLine = fs.appendFileSync;
  const seen = new Set();
  const logFile = path.join(LOG, process.pid + '.jsonl');
  let ready = false;
  const emit = (kind, value) => {
    const key = kind + '\0' + (typeof value === 'string' ? value : JSON.stringify(value));
    if (seen.has(key)) return;
    seen.add(key);
    try {
      if (!ready) { fs.mkdirSync(LOG, { recursive: true }); ready = true; }
      writeLine(logFile, JSON.stringify([kind, value]) + '\n');
    } catch (e) { /* a recorder fault never fails the proof */ }
  };
  const abs = (p) => {
    try {
      if (p instanceof URL) p = fileURLToPath(p);
      if (Buffer.isBuffer(p)) p = p.toString();
      return typeof p === 'string' ? path.resolve(p) : null;
    } catch (e) { return null; }
  };
  const note = (kind, p) => {
    const a = abs(p);
    if (a && !a.startsWith(path.resolve(LOG))) emit(kind, a);
  };
  const WRITE_FLAGS = /[wa+]/;
  const O_WRITE = fs.constants.O_WRONLY | fs.constants.O_RDWR | fs.constants.O_APPEND | fs.constants.O_CREAT;
  const opens = (flags) => (typeof flags === 'string' ? WRITE_FLAGS.test(flags)
    : typeof flags === 'number' ? (flags & O_WRITE) !== 0 : false);
  const wrap = (target, name, record) => {
    const original = target[name];
    if (typeof original !== 'function') return;
    const wrapper = function (...args) {
      try { record(args); } catch (e) { /* see emit */ }
      return original.apply(this, args);
    };
    Object.assign(wrapper, original);
    target[name] = wrapper;
  };
  const READ = ['readFileSync', 'readFile', 'createReadStream'];
  const LIST = ['readdirSync', 'readdir', 'opendirSync', 'opendir'];
  const PROBE = ['statSync', 'stat', 'lstatSync', 'lstat', 'existsSync', 'accessSync', 'access', 'realpathSync', 'readlinkSync', 'readlink'];
  const WRITE = ['writeFileSync', 'writeFile', 'appendFileSync', 'appendFile', 'createWriteStream', 'mkdirSync', 'mkdir',
    'rmSync', 'rm', 'unlinkSync', 'unlink', 'rmdirSync', 'rmdir', 'truncateSync', 'truncate', 'mkdtempSync', 'mkdtemp'];
  for (const target of [fs, fs.promises]) {
    for (const n of READ) wrap(target, n, (a) => note('r', a[0]));
    for (const n of LIST) wrap(target, n, (a) => note('l', a[0]));
    for (const n of PROBE) wrap(target, n, (a) => note('p', a[0]));
    for (const n of WRITE) wrap(target, n, (a) => note('w', a[0]));
    for (const n of ['openSync', 'open']) {
      wrap(target, n, (a) => {
        const flags = typeof a[1] === 'function' ? undefined : a[1];
        note(flags !== undefined && opens(flags) ? 'w' : 'r', a[0]);
      });
    }
    for (const n of ['renameSync', 'rename']) wrap(target, n, (a) => { note('w', a[0]); note('w', a[1]); });
    for (const n of ['copyFileSync', 'copyFile', 'cpSync', 'cp']) wrap(target, n, (a) => { note('r', a[0]); note('w', a[1]); });
  }

  const NODE_OPTION = '--require ' + JSON.stringify(__filename);
  const norm = (p) => path.resolve(String(p || '.')).toLowerCase();
  const inherits = (file, opts) => {
    if (opts && opts.shell) return false;
    const env = opts && opts.env ? opts.env : process.env;
    if (env.HARNESS_READ_LOG !== LOG) return false;
    const name = path.basename(String(file)).toLowerCase();
    if (/^node(\.exe)?$/.test(name)) return String(env.NODE_OPTIONS || '').includes(NODE_OPTION);
    if (/^(python[\d.]*|py)(\.exe)?$/.test(name)) {
      return !!env.HARNESS_READ_RECORDER && norm(String(env.PYTHONPATH || '').split(path.delimiter)[0]) === norm(env.HARNESS_READ_RECORDER);
    }
    return false;
  };
  const spawned = (argv, opts) => {
    const cwd = opts && opts.cwd ? path.resolve(String(opts.cwd)) : process.cwd();
    emit('s', { argv: argv.map(String), cwd, recorded: inherits(argv[0], opts) });
  };
  const cp = require('child_process');
  const optionsOf = (rest) => rest.find((x) => x && typeof x === 'object' && !Array.isArray(x));
  for (const n of ['spawn', 'spawnSync', 'execFile', 'execFileSync']) {
    wrap(cp, n, (a) => spawned([a[0]].concat(Array.isArray(a[1]) ? a[1] : []), optionsOf(a.slice(1))));
  }
  for (const n of ['exec', 'execSync']) {
    wrap(cp, n, (a) => spawned([a[0]], Object.assign({ shell: true }, optionsOf(a.slice(1)))));
  }
  wrap(cp, 'fork', (a) => spawned([process.execPath, a[0]].concat(Array.isArray(a[1]) ? a[1] : []), optionsOf(a.slice(1))));
}
