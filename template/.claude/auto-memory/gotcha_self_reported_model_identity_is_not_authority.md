---
name: gotcha-self-reported-model-identity-is-not-authority
description: "A client-reported served-model field is authority ONLY against a vendor endpoint reached directly — behind a translating proxy it echoes the client's own pin, so an identity check on it returns a false pass."
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-20T05:45:14.256Z
---

**Never accept a delegate's own report of which model served it.** Two independent layers lie,
and each one looks like evidence.

**The agent's prose lies.** Claude Code's system prompt asserts a Claude identity, so a child
served by a GPT model answers "I'm a Claude model from Anthropic's Claude 5 family." Asking the
arm what it is measures the system prompt, never the backend.

**The client's structured field lies too, but only on some transports** — which is the part that
turns a check into a false pass. `modelUsage[].canonicalModel` is genuinely server-confirmed
against a vendor endpoint reached DIRECTLY: a bogus id hard-400s rather than falling back, so the
field cannot name a model that did not serve. Put a translating proxy in the path and the same
field becomes the child's own `--model` pin echoed back through the translation. Measured
2026-08-20: a run with the upstream forced to `gpt-5.6-luna` reported `gpt-5.4-mini`.

So the rule is not "trust the field" or "distrust the field" — it is **the field's authority is a
property of the transport**, and a check that ignores that is worse than no check, because it
scores work under a coordinate it never ran on.

**Authority is whatever the arm cannot influence.** For a proxied dispatch that is the proxy's own
server-side capture of the upstream request. Select the rule by whether the transport *declares*
an attestation path, never by its name — a transport declaring one is declaring its client-side
field untrustworthy.

**A disagreement between the two is not automatically a failure.** Where the model is forced
server-side, a mismatch means the force overrode a mispinned child: the arm really is the attested
model, and the disagreement is evidence the mechanism worked. The failure conditions are exactly
two — no attestation at all, or an attestation naming a different model. Voiding on mere
disagreement discards valid work.

Enforcement: `.claude/tools/void_check.py`; the ruling lives in the benchmark instrument's
`MANIFEST.md` §5. Related: [[feedback_trusting_delegate_output_verify_claims]],
[[feedback_dispatch_transport_is_session_bound]].
