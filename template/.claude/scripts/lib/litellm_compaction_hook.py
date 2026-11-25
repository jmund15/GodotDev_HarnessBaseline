"""LiteLLM proxy hook: a Claude Code compaction request runs at its own reasoning effort and thinking budget.

opencode_sidecar.sh copies this file beside the proxy config when OC_COMPACTION_EFFORT or OC_COMPACTION_THINK_BUDGET
is set, and names it in `litellm_settings.callbacks`. On a request whose newest user turn carries Claude Code's
compaction instruction, it adds `chat_template_kwargs.reasoning_effort` and `thinking_budget_tokens` to the upstream
body (llama-server reads both per request); every other request passes unchanged. Each match prints one
`[compaction-hook]` line to the proxy log, so a run shows whether its compactions were reached.
"""
import json
import os
import sys

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:  # the proof imports compaction_overrides without litellm installed
    CustomLogger = object

MARKERS = ("create a detailed summary of the conversation", "summary of the conversation so far")


def _newest_user_text(messages):
    for message in reversed(messages or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return json.dumps(message.get("content"), ensure_ascii=False).lower()
    return ""


def compaction_overrides(data, effort, budget):
    """The upstream-body additions for this request, or None when it is not a compaction."""
    text = _newest_user_text(data.get("messages"))
    if not any(marker in text for marker in MARKERS):
        return None
    extra = {}
    if effort:
        extra["chat_template_kwargs"] = {"reasoning_effort": effort}
    if budget:
        extra["thinking_budget_tokens"] = int(budget)
    return extra or None


class CompactionHook(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        extra = compaction_overrides(data, os.environ.get("OC_COMPACTION_EFFORT", ""),
                                     os.environ.get("OC_COMPACTION_THINK_BUDGET", ""))
        if extra:
            body = data.setdefault("extra_body", {}) or {}
            body.update(extra)
            data["extra_body"] = body
            print("[compaction-hook] compaction request: %s" % json.dumps(extra), file=sys.stderr, flush=True)
        return data


proxy_handler_instance = CompactionHook()
