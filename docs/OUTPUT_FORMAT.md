# Output format

After every push, `test2git` writes three artefacts under `.test2git/` (which is
gitignored by default):

- `.test2git/<subproject>.txt` — the **full raw pytest output** (every line,
  untruncated).
- `.test2git/<subproject>.json` — the latest run summary, pretty-printed.
- `.test2git/history.jsonl` — append-only log, one JSON object per line.

The split exists on purpose: the `.txt` is the byte-for-byte pytest record (so
AI agents can grep / re-parse if the schema changes), and the `.json` is the
machine-readable summary for instant triage.

## Raw output file: `.test2git/<subproject>.txt`

Everything pytest wrote to stdout and stderr is captured here. Stable
verbatim — no truncation, no reformatting. Useful for:

- Debugging a failure that the JSON summary doesn't capture (e.g. traceback
  excerpted in the summary but full frames here).
- Re-parsing if you upgrade `test2git` and the summary schema gains fields.
- Searching for `WARNING` / `DeprecationWarning` lines that aren't failures.

## Summary file: `.test2git/<subproject>.json`

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

### Fields

| Field             | Type     | Meaning                                                                 |
| ----------------- | -------- | ----------------------------------------------------------------------- |
| `ts`              | string   | ISO-8601 local time the run finished.                                   |
| `commit`          | string   | Short git hash of HEAD at run time, or `"uncommitted"` / `"unknown"`.   |
| `branch`          | string   | Current branch name, or `"unknown"`.                                    |
| `subproject`      | string   | Subproject key (`tgc` / `atv` / `orchestrator` / custom).               |
| `passed`          | int      | Number of tests that passed.                                            |
| `failed`          | int      | Number of test failures (assertion failures).                            |
| `errors`          | int      | Number of collection/setup errors (fixture failures, import errors).     |
| `skipped`         | int      | Number of tests skipped (xfail marks etc. are *not* counted here).      |
| `duration_s`      | float    | Wall-clock seconds for the whole pytest run.                            |
| `pytest_exit_code`| int      | pytest's own exit code (0 = green, 1 = test failed, 2 = interrupted, etc.). |
| `raw_output_file` | string   | Filename of the sibling `.txt` containing full pytest output.            |
| `failures`        | object[] | Sorted, deduplicated list of failing test IDs (see below).              |

### `failures[]` entry

```json
{"id": "tests/test_views.py::test_buy_view", "kind": "failed"}
```

| Field  | Values                | Meaning                                            |
| ------ | --------------------- | -------------------------------------------------- |
| `id`   | string                | Fully-qualified pytest node id.                    |
| `kind` | `"failed"` \| `"error"` | `failed` = assertion failure; `error` = setup/collection/teardown error. |

The list is **sorted by `id`** and **deduplicated** — stable ordering means an
AI agent can diff the `failures` field between runs and see exactly which IDs
entered or left the red set without parsing the raw output.

## History file: `.test2git/history.jsonl`

One line per run. Same schema as the summary file, but **not pretty-printed**:

```
{"ts": "...", "commit": "abc1234", "subproject": "tgc", ...}
{"ts": "...", "commit": "def5678", "subproject": "tgc", ...}
```

Use it to answer questions like:
- "When did `test_buy_view` start failing?" — grep for it, look at `ts`.
- "Which runs had 0 failures on `main`?" — `jq 'select(.failed == 0)' .test2git/history.jsonl`.
- "Show the last 20 runs of `tgc`." — `tail -n 20 .test2git/history.jsonl | jq`.

## How an AI agent should read it

The agent's optimal loop is:

1. `cat .test2git/<subproject>.json` — see the current red set, counts, exit
   code, and which `.txt` file holds the full output.
2. If `failed == 0 && errors == 0 && pytest_exit_code == 0`, the tree is green
   for that subproject at HEAD; no pytest needed.
3. If non-empty, the agent can immediately triage by `id` and `kind` without
   re-running pytest. Each entry already contains everything needed to
   reproduce (`id` is a fully-qualified pytest node id; `kind` distinguishes
   assertion failures from collection errors).
4. For tracebacks, fixture details, or any context the summary doesn't carry,
   open the sibling `.txt` (path given by `raw_output_file`). One
   `grep <test_id> .test2git/<subproject>.txt` jumps straight to the relevant
   lines.
5. For historical context, stream `.test2git/history.jsonl`:
   `tail -n 50 .test2git/history.jsonl | jq` — gives the agent the full
   timeline without burning tokens on a full pytest re-run.

If the files don't exist, the agent can either (a) run `git push` to trigger
the hook, or (b) run `python scripts/test2git.py <subproject>` directly.

## Gitignore vs `--keep`

By default `.test2git/` is added to `.gitignore` — results are local-only, per
machine. This is the right default for AI agents because each agent has its
own `.test2git/` view of the tree.

If you want to **commit the results** (full test versioning — `git log --
.test2git/<sub>.txt` shows every run's raw output over time), run
`test2git.py --keep`. It strips `.test2git/` from `.gitignore` so you can
`git add .test2git/ && git commit`. See [ADVANCED.md — `--keep`](ADVANCED.md#--keep-track-results-in-git).
