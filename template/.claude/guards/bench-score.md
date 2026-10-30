# Guard: bench-score — the benchmark score daemon's driver

Read ONLY the section your dispatch names; if none is named, read `## detailed`. `any.md`'s section of the same tier binds you too and arrives with this file.

## detailed

- Your whole job is the one `Workflow` call your brief names, with the brief's script and args unchanged. Make no other `Workflow` call and author no script of your own: this shape is the only dispatch that carries `Workflow`, and only for this call. [reference/sidecar_dispatch.md; tools/tool_profiles.py `RESERVED_BUILTINS`]
- Never read, summarize or re-derive the workflow's result. The daemon persists it from the output file; an echo is paid output that nothing reads. [scoring/daemon_lib.py `build_brief`]

## condensed

- One `Workflow` call, exactly as the brief gives it; no other use of `Workflow`, and no echo of its result.

## minimal

Read this file's `## condensed` section.
