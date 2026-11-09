// Prompt masks the engine proofs share. Each maps a later prompt revision back to the text a proof pins, so a
// parity check compares only what the change under test moved. engine_blind_test.js and engine_replay_test.js
// both import them.
// M3.1 rewrote the mechanical prompt's HARD DISCIPLINE sentence and added the base re-run command after BASE_REV; both
// regions are masked here, and engine_v16_test.js M3.1 pins their content.
const maskM31 = (p) => p.replace(/HARD DISCIPLINE: [\s\S]*?Always pass/, 'HARD DISCIPLINE: <M3.1> Always pass')
  .replace(/ Run each RED-on-base test with exactly this command[^\n]*\n[^\n]*/, '')
// 1.10 (F18/F27, F26) asks fpPenalty verifiers for each fabrication's arm quote (an optional schema field) and rules
// oracle-tampered by the sealed key's integrity rule; both regions are masked here, and engine_v16_test.js X10/F26 pin them.
const QUOTE_ASK = " Give each fabrication a quote: the sentence of the arm output the cite sits in, verbatim."
const TAMPER_NOW = "'oracle-tampered' when a pre-existing test-file diff violates the sealed key's integrity rule, excluding edits the key permits"
const TAMPER_17 = "'oracle-tampered' when the arm edited a pre-existing test to accommodate its change"
// W6-2-1 (9c11be855) added the judge's partialRead prompt sentence; masked here as engine_replay_test.js masks it.
const PARTIAL_READ_ASK = " Set partialRead true if you stopped short of reading a path above end to end; then name the lines you read in couldNotSatisfy. Otherwise partialRead is false."
// 1.11 (D9) asks verifiers for a line-drift's foundAt (prompt clause and schema field); masked here, and
// engine_fab_slip_test.js pins both.
const FOUNDAT_ASK = '; give foundAt, the integer line where that content sits'
// 1.13 checks a count over the set the claim names (one verifier prompt sentence); masked here, and
// verifier_input_scope_test.js case 3b pins it.
const COUNT_SCOPE_ASK = ' Check a count or quantity over the set the claim itself names (the folders, layer or exclusions it states, such as non-test game code): a total over a wider set, such as a vendored framework, a submodule or tests, is not the cited value and charges nothing.'
const mask110 = (p) => p.split(QUOTE_ASK).join('').split(TAMPER_NOW).join(TAMPER_17).split(PARTIAL_READ_ASK).join('').split(FOUNDAT_ASK).join('').split(COUNT_SCOPE_ASK).join('')
// 1.14 lists every top-level field the mechanical schema requires in the report block, and 1.15 drops the block's
// "rejects a return missing any" clause; both masked back to the 1.7 block here, and mechanical_prompt_fields_test.js
// pins the list.
const REPORT_114 = /Report these top-level fields, every one(?:; the output schema rejects a return missing any)?:\n- axes: every axis exactly once, scored against the key's maxima \(\/(\d+)\)\.\n- total: the sum of the axis points\.\n- denominator: the key's total minus the maxima of rig-excluded axes\. The engine recomputes total and denominator from the axes\.\n- outcome: per OUTCOME above\.\n(- oracle_integrity: [^\n]*)\n- couldNotSatisfy: anything you could not satisfy, never papered over \(''\s?if none\)\.\n([\s\S]*?)(\n\nAXIS IDS[\s\S]*)?$/
const mask114 = (p) => p.replace(REPORT_114, (m, den, oi, rest, ids) => `Report, in addition to the axes:\n${oi}\n${rest}\n\nScore every axis exactly once against the key's maxima (/${den}). Report as denominator the key's total minus the maxima of rig-excluded axes; the engine recomputes both from the axes. Anything you could not satisfy goes in couldNotSatisfy, never papered over.${ids || ''}`)

module.exports = { maskM31, mask110, mask114 }
