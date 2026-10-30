'use strict';
const assert = require('node:assert/strict');
// Isolated reproduction of the inspected deterministic dedup block in
// .claude/workflows/review_fanout.js at commit 84ed72123439e903f7f6726720aa23b05e5257a9.
// This does not invoke the Workflow runtime or claim to run the repository test suite.
const merged = [
  {agent: 'lifecycle', file: 'Example.cs:42', critical: true,
   description: 'Subscription remains active after disposal.', old: 'x', new: 'y'},
  {agent: 'validation', file: 'Example.cs:42', critical: true,
   description: 'Input can be null before dereference.', old: 'x', new: 'z'},
];
function specificity(f) { return (f.old ? 1 : 0) + (f.new ? 1 : 0); }
const byLoc = new Map();
const noLoc = [];
for (const f of merged) {
  const loc = (typeof f.file === 'string' && f.file.trim()) ? f.file.trim() : null;
  if (!loc) { noLoc.push(f); continue; }
  const existing = byLoc.get(loc);
  if (!existing) { byLoc.set(loc, f); continue; }
  const critF = !!f.critical, critE = !!existing.critical;
  const better = (critF !== critE) ? critF : (specificity(f) > specificity(existing));
  if (better) { byLoc.set(loc, f); }
}
const deduped = [...byLoc.values(), ...noLoc];
assert.equal(merged.length, 2);
assert.equal(deduped.length, 1);
assert(!deduped.some(f => f.agent === 'validation'));
const counts = {raw: deduped.length, merged: deduped.length};
console.log(JSON.stringify({
  defectReproduced: true,
  suppliedDistinctFindings: merged.length,
  retainedFindings: deduped.length,
  rawCounterAsImplemented: counts.raw,
  lostFinding: merged[1].description,
  scope: 'isolated inspected algorithm, not the full Workflow runtime'
}, null, 2));
