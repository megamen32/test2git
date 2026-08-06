# test2git

> Run your test suite on every push, write the results to a file your AI agent can read.

**The problem.** AI coding agents can't see what tests were red at commit N.
After a long pause they have to re-run the entire suite just to know which tests
were broken — wasting minutes and tokens on every resume.

**The solution.** `test2git` runs pytest on every `git push`, parses the output,
and writes a small JSON file (`.test2git/<project>.json`) plus an append-only
`.test2git/history.jsonl`. After the hook fires, your agent reads the file in
milliseconds and knows exactly which tests were failing — without re-running
anything.

## What you get

- One-command install (curl-pipe-bash).
- `pre-push` hook fires on every push, non-blocking by default — push always
  succeeds, results are always written.
- Sorted, deduplicated list of failure IDs (`tests/foo.py::test_bar`) plus
  counts of passed / failed / errors / skipped, pytest exit code, commit,
  branch, and a pointer to the full raw output.
- Three files per run: `<sub>.json` (structured summary), `<sub>.txt` (full
  raw pytest output), `history.jsonl` (append-only timeline).
- `--fail-on-error` flag turns the hook into a real CI gate.
- `--keep` flag commits the results for full test versioning in git history.
- Auto-detects which subproject changed (`TGC/`, `ATV/`,
  `comment-orchestrator/`) — no manual configuration needed for the common case.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | bash
```

That's it. The installer drops `scripts/test2git.py` and
`.git/hooks/pre-push` into your repo, marks the latter executable, and adds
`.test2git/` to `.gitignore`. Next `git push` writes the first results file.

Want the hook to **block** pushes when tests fail? Same command, one env var:

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | TEST2GIT_BLOCK=1 bash
```

## Read what an agent sees

After a push, this is all the agent needs:

```bash
$ cat .test2git/tgc.json
```

```json
{
  "ts": "2026-08-06T22:40:00+03:00",
  "commit": "abc1234",
  "branch": "main",
  "subproject": "tgc",
  "passed": 1490,
  "failed": 30,
  "errors": 4,
  "skipped": 9,
  "duration_s": 162.2,
  "pytest_exit_code": 1,
  "raw_output_file": "tgc.txt",
  "failures": [
    {"id": "tests/test_task_queue.py::test_retry_policy_classification_and_backoff", "kind": "failed"},
    {"id": "tests/test_views.py::test_buy_view", "kind": "error"}
  ]
}
```

Three files land in `.test2git/` on every push: `<sub>.json` (above),
`<sub>.txt` (the **full raw pytest output**), and a shared `history.jsonl`.
Sorted, deduplicated, machine-readable, written by every push. The agent
never needs to run pytest itself.

## Start in minutes

1. Run the install command above inside any git repo.
2. `git push` — even a no-op push to a fresh branch writes the first results
   file.
3. Read `.test2git/<project>.json` (or stream `.test2git/history.jsonl`).

## Learn more

- [Install details & multi-repo setup](docs/INSTALL.md)
- [Output format & how agents should read it](docs/OUTPUT_FORMAT.md)
- [Custom subprojects, `--keep`, `--fail-on-error`, troubleshooting](docs/ADVANCED.md)

## License

MIT — see [LICENSE](LICENSE).
