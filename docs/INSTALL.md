# Install

`test2git` ships as two files plus a one-command installer. Drop it into any
git repo and the next `git push` writes machine-readable test results.

## One-command install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | bash
```

The installer:
1. Copies `test2git.py` into `./scripts/`.
2. Installs `test2git-pre-push.sh` as `./.git/hooks/pre-push`.
3. Adds `.test2git/` to `.gitignore`.
4. Reports the two paths it touched.

Run it from the repo root. For subprojects (see below), run it from inside the
subproject directory.

## Manual install (no curl)

If you prefer to inspect every byte before it lands:

```bash
# 1. Drop the script
mkdir -p scripts
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git.py \
  -o scripts/test2git.py
chmod +x scripts/test2git.py

# 2. Install the hook
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git-pre-push.sh \
  -o .git/hooks/pre-push
chmod +x .git/hooks/pre-push

# 3. Gitignore the output dir
grep -qx ".test2git/" .gitignore 2>/dev/null || echo ".test2git/" >> .gitignore
```

## Blocking vs non-blocking mode

By default the hook is **non-blocking**: pytest runs, `.test2git/<sub>.json`
is written, the push proceeds even on red tests. This is the right default
when an AI agent will triage failures after the push.

To make the hook **block** pushes on test failure, reinstall with the env var:

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh \
  | TEST2GIT_BLOCK=1 bash
```

Or export it in your shell and re-run the installer, or wrap the hook manually:

```bash
cat > .git/hooks/pre-push <<'EOF'
#!/usr/bin/env bash
export TEST2GIT_BLOCK=1
exec "$(git rev-parse --show-toplevel)/.git/hooks/pre-push.disabled" "$@"
EOF
chmod +x .git/hooks/pre-push
mv .git/hooks/pre-push .git/hooks/pre-push.live
# rename existing hook to .disabled, symlink live -> pre-push
```

`TEST2GIT_BLOCK=1` is equivalent to calling `test2git.py --fail-on-error`
inside the hook — exit code propagates and `git push` aborts.

## Multi-project / monorepo setup

`test2git` supports a root repo containing multiple subprojects (e.g. `TGC/`,
`ATV/`, `comment-orchestrator/`). The hook auto-detects the subproject name
from the current working directory and dispatches to the right pytest run.

**Layout:**

```
my-monorepo/
  scripts/test2git.py          # shared
  .git/hooks/pre-push          # one hook for the whole monorepo
  TGC/
    tests/                     # pytest will run these
  ATV/
    autotests/
  comment-orchestrator/
    tests/
```

Install once at the monorepo root. The hook reads the basename of the
repository root and maps it to a subproject key:

| Directory name             | Subproject key |
| -------------------------- | -------------- |
| `TGC` / `TG_Commentator`   | `tgc`          |
| `ATV` / `AutoTelegramViews`| `atv`          |
| `comment-orchestrator`     | `orchestrator` |
| anything else              | auto-detect    |

If the hook can't determine a subproject (e.g. you push from the monorepo root
itself), it falls back to **auto-detect from changed files** — only subprojects
with staged/unstaged/untracked files get run.

## Adding a custom subproject

See [ADVANCED.md — Custom subprojects](ADVANCED.md#custom-subprojects).

## Uninstall

```bash
rm .git/hooks/pre-push scripts/test2git.py
# remove the line from .gitignore if you want a clean diff
```

Existing `.test2git/` files are left in place; delete them manually if desired.
