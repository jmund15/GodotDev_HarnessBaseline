'use strict';
// Node proofs need the same admission as Python proofs in a delegate's shell.
const path = require('path');
const script = path.basename(process.argv[1] || '');
if (process.env.HARNESS_SLOT_BUDGET !== '1' && (script.startsWith('test_') || script.includes('_test.'))) {
  const {spawnSync} = require('child_process');
  const result = spawnSync(process.env.HARNESS_SLOT_PYTHON,
    [process.env.HARNESS_SLOT_SCRIPT, 'run', '--', process.execPath, ...process.execArgv, ...process.argv.slice(1)],
    {stdio: 'inherit', env: process.env, windowsHide: true});
  if (result.error) {
    process.stderr.write('CANNOT-RUN machine slots: ' + result.error.message + '\n');
  }
  process.exit(result.status === null ? 2 : result.status);
}
