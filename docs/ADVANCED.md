# Advanced usage

## Custom subprojects

`test2git.py` ships with a built-in registry for the common multi-repo layout
(`tgc`, `atv`, `orchestrator`). For other layouts, edit `SUBPROJECTS` at the
top of the file:

```python
SUBPROJECTS: dict[str, dict] = {
    "backend": {
        "dir": REPO_ROOT / "backend",
        "python": REPO_ROOT / "backend" / ".venv" / "bin" / "python",  # or None
        "pytest_args": ["-p", "no:rerunfailures", "--tb=no", "-q"],
        "test_dirs": ["tests/"],
    },
    "frontend": {
        "dir": REPO_ROOT / "frontend",
        "python": None,  # uses system python3
        "pytest_args": ["--tb=no", "-q"],
        "test_dirs": ["__tests__/", "tests/"],
    },
}
```

| Field         | Purpose                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `dir`         | Absolute path to the subproject root. `git rev-parse` runs here.        |
| `python`      | Path to a venv's python, or `None` to use the script's own interpreter. |
| `pytest_args` | Args passed to `pytest`. `-q --tb=no` is recommended for fast parsing.  |
| `test_dirs`   | Directories pytest will collect from. Skipped if any is missing.        |

The hook auto-detects the subproject key from the basename of `git rev-parse
--show-toplevel`. Add your own `case` arm in `test2git-pre-push.sh` if the
directory name doesn't match a built-in alias:

```bash
case "$BASENAME" in
  my-backend) SUB="backend" ;;
  my-frontend) SUB="frontend" ;;
  *) SUB="" ;;
esac
```

## `--fail-on-error`

Make `test2git.py` exit non-zero when any test failed or errored. The hook
turns this into a real CI gate:

```bash
python3 scripts/test2git.py tgc --fail-on-error
```

Combined with the hook's `TEST2GIT_BLOCK=1` env var, a red `git push` is
rejected before it leaves the machine. Use this for CI runners or release
branches; leave the default non-blocking mode for everyday development where
an AI agent triages after the push.

## `--keep` — track results in git

By default `.test2git/` is gitignored — every agent / every machine has its
own private view. To commit results alongside code (full test versioning —
`git log -- .test2git/tgc.txt` shows every run's raw output over time):

```bash
python3 scripts/test2git.py tgc --keep
```

The flag strips `.test2git/` from `.gitignore` so the next `git add .test2git/`
succeeds. The flag is one-shot; re-running without `--keep` re-adds the
gitignore entry.

A common pattern:

1. CI runs `test2git.py tgc --keep`.
2. CI commits `.test2git/tgc.txt` and `.test2git/tgc.json` to the branch.
3. Reviewers see the test status inline in the PR, and the repo carries a
   per-commit history of every raw pytest run.

## Manual run

You don't have to wait for a push — run the script directly:

```bash
# Auto-detect from changed files
python scripts/test2git.py

# Specific subproject
python scripts/test2git.py tgc

# All subprojects that have test dirs
python scripts/test2git.py all

# Block on failure
python scripts/test2git.py tgc --fail-on-error
```

Output goes to stdout (one line per subproject with the GREEN/RED status)
**and** to `.test2git/<sub>.json` + `.test2git/history.jsonl`.

## Hook logic

`test2git-pre-push.sh` does four things:

1. Resolves `REPO_ROOT` via `git rev-parse --show-toplevel`.
2. Locates `scripts/test2git.py` by trying:
   - `$REPO_ROOT/scripts/test2git.py` (root install),
   - `$REPO_ROOT/../scripts/test2git.py` (subproject install),
   - `$REPO_ROOT/../../scripts/test2git.py` (deeply nested).
3. Maps the directory basename to a subproject key (`TGC → tgc`, etc.).
4. Runs `python3 "$SCRIPT" "$SUB"` — or with `--fail-on-error` if
   `TEST2GIT_BLOCK=1`.

If the script can't be found, the hook exits 0 silently — pushes still go
through, just without test results. This keeps a fresh-clone `git push` from
failing because someone forgot to run the installer.

## Troubleshooting

### "no changed files detected for any subproject"

You ran `python scripts/test2git.py` (no argument) in a tree with no
staged/unstaged/untracked changes. Either stage something, or pass a
subproject name explicitly: `python scripts/test2git.py tgc`.

### Hook fires but the JSON file is never written

- Check the script is executable: `ls -l scripts/test2git.py` (should show
  `-rwxr-xr-x`).
- Run it manually to see the error: `python3 scripts/test2git.py tgc`.
- Make sure `pytest` is importable in the subproject's venv (the script
  imports `pytest` indirectly via `python -m pytest`).

### Counts look wrong (e.g. `failed: 0` but tests clearly failed)

`test2git` parses pytest's own summary line plus the `FAILED` / `ERROR` line
counts. If pytest's output format ever changes (unlikely; it's been stable
since 2018), the script falls back to ground-truth line counts. If you see a
mismatch, please open an issue with the raw pytest output (one `-v` run is
enough).

### `pytest` is missing in the chosen interpreter

The `python` field in `SUBPROJECTS` points at a specific venv. If that venv
doesn't have pytest installed, the run fails immediately. Either install
pytest into the venv or set `python: None` to use the system interpreter.

### Hook blocks pushes unexpectedly

You installed with `TEST2GIT_BLOCK=1` (or set the env var in your shell).
Reinstall without it, or unset `TEST2GIT_BLOCK` for the session:

```bash
unset TEST2GIT_BLOCK
git push
```

### I want to disable the hook temporarily

```bash
chmod -x .git/hooks/pre-push    # git won't run non-executable hooks
# later:
chmod +x .git/hooks/pre-push
```

Or rename it: `mv .git/hooks/pre-push .git/hooks/pre-push.disabled`.

## Performance notes

`test2git` runs the **full** pytest suite on every push. There is no
incremental-test mode (yet). For most projects this is fine — pytest is fast
enough on modern hardware — but if your suite takes >10 minutes, consider:

- Running pytest with `-x` to abort on first failure (custom `pytest_args`).
- Running `test2git.py` only on a schedule (e.g. a CI job, not the local
  hook).
- Splitting the suite into `unit/` and `integration/` and pointing
  `test_dirs` at `unit/` only.
