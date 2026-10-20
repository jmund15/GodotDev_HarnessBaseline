# Retrieving a dispatch's full result

Read when consuming a Workflow or native job's result (`orchestration` §4 states the return contract).

- **A Workflow result's complete set:** capture `transcriptDir` from the result and run `python3 .claude/tools/session_digest.py --workflow-dir <transcriptDir> --workflow-kind <explore|review> --workflow-manifest`, then `--workflow-select <ID>`, `--workflow-page {items,lenses,reports,gaps,merges} --page <N> --page-size <N>` or `--workflow-full`, each with `--expect-journal-sha256 <hash from the manifest>`.
- **A Workflow deliverable file:** a job with Write and `spillDir` writes `<spillDir>/<label>.md`. A job without Write or without `spillDir` returns its full deliverable inline in the journal. The parent materializes it with `python3 .claude/tools/dispatch_result.py <transcriptDir>`. Add `--out-dir <directory>` when the run record has no `spillDir`.
- **A script-parsed result:** its fenced JSON Lines blocks are validated by `dispatch_result.py check <spill.md> --block <name>=<key,key...>`. The delegate runs it when its profile has Bash, else the parent runs it before reading a row.
