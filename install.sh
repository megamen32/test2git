#!/usr/bin/env bash
# test2git one-command installer.
#
# What it does:
#   1. Copies test2git.py into ./scripts/ (creating the dir if missing).
#   2. Installs the pre-push hook into .git/hooks/pre-push.
#   3. Adds .test2git/ to .gitignore if not already present.
#   4. Injects a "test2git" block into AGENTS.md and CLAUDE.md (if they exist)
#      so AI agents know to read .test2git/<sub>.json after every push.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | bash
#   curl ... | TEST2GIT_BLOCK=1 bash   # make the hook block pushes on test failures
#
# Uninstall:
#   curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | bash -s -- --uninstall
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-push"
AGENTS_FILE="$REPO_ROOT/AGENTS.md"
CLAUDE_FILE="$REPO_ROOT/CLAUDE.md"

UNINSTALL="${1:-}"
if [ "$UNINSTALL" = "--uninstall" ]; then
  echo "[test2git] uninstalling..."

  # Remove the hook.
  rm -f "$HOOK_PATH" "$HOOK_PATH.disabled" "$HOOK_PATH.block"
  echo "[test2git] removed pre-push hook."

  # Remove the test2git block from AGENTS.md and CLAUDE.md.
  for f in "$AGENTS_FILE" "$CLAUDE_FILE"; do
    if [ -f "$f" ] && grep -q "<!-- test2git:begin -->" "$f"; then
      # Delete from begin marker to end marker (inclusive).
      sed -i '/<!-- test2git:begin -->/,/<!-- test2git:end -->/d' "$f"
      echo "[test2git] removed block from $(basename "$f")."
    fi
  done

  echo "[test2git] uninstalled. (.test2git/ output dir left in place — delete manually if desired.)"
  exit 0
fi

# ── Locate source files ──────────────────────────────────────────────────
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || echo "")"
if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/test2git.py" ]; then
  echo "[test2git] installer source files not found next to install.sh." >&2
  echo "[test2git] Run from a clone of the repo, or download both files first:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git.py -o scripts/test2git.py" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git-pre-push.sh -o .git/hooks/pre-push" >&2
  echo "  chmod +x .git/hooks/pre-push scripts/test2git.py" >&2
  exit 1
fi

mkdir -p "$SCRIPTS_DIR"
cp -f "$SRC_DIR/test2git.py" "$SCRIPTS_DIR/test2git.py"
cp -f "$SRC_DIR/test2git-pre-push.sh" "$HOOK_PATH"
chmod +x "$SCRIPTS_DIR/test2git.py" "$HOOK_PATH"

# ── Gitignore ────────────────────────────────────────────────────────────
GITIGNORE="$REPO_ROOT/.gitignore"
touch "$GITIGNORE"
if ! grep -qx ".test2git/" "$GITIGNORE"; then
  echo ".test2git/" >> "$GITIGNORE"
fi

# ── Inject agent-instructions block ──────────────────────────────────────
# A self-contained block between markers that tells AI agents how to read
# test results. Safe to re-run: if the block already exists, it's replaced.
AGENT_BLOCK='<!-- test2git:begin -->
## test2git — test results after every push

After every `git push`, a pre-push hook runs the test suite and writes
results to `.test2git/`. Read these files to know which tests are red
WITHOUT re-running the suite:

- `.test2git/<sub>.json` — structured summary: passed/failed/errors counts,
  sorted failure IDs, commit hash, duration.
- `.test2git/<sub>.txt` — full raw pytest output (with tracebacks).

Always read `.test2git/<sub>.json` first when resuming work to see the
last known test state. Run `python scripts/test2git.py <sub>` manually
to refresh. Uninstall: see scripts/test2git-repo/install.sh --uninstall.
<!-- test2git:end -->'

inject_block() {
  local file="$1"
  if [ ! -f "$file" ]; then
    return
  fi
  # Remove existing block if present (idempotent re-install).
  if grep -q "<!-- test2git:begin -->" "$file"; then
    sed -i '/<!-- test2git:begin -->/,/<!-- test2git:end -->/d' "$file"
  fi
  # Append the block at the end of the file.
  printf '\n%s\n' "$AGENT_BLOCK" >> "$file"
  echo "[test2git] injected block into $(basename "$file")."
}

inject_block "$AGENTS_FILE"
inject_block "$CLAUDE_FILE"

# ── Blocking mode ────────────────────────────────────────────────────────
if [ "${TEST2GIT_BLOCK:-0}" = "1" ]; then
  WRAPPER="$REPO_ROOT/.git/hooks/pre-push.block"
  cat > "$WRAPPER" <<'EOF'
#!/usr/bin/env bash
export TEST2GIT_BLOCK=1
exec "$(git rev-parse --show-toplevel)/.git/hooks/pre-push" "$@"
EOF
  chmod +x "$WRAPPER"
  mv "$HOOK_PATH" "$HOOK_PATH.disabled"
  mv "$WRAPPER" "$HOOK_PATH"
  echo "[test2git] installed in BLOCKING mode (TEST2GIT_BLOCK=1)."
else
  echo "[test2git] installed in NON-BLOCKING mode (results written, push always proceeds)."
fi

echo "[test2git] done. Files:"
echo "  - $SCRIPTS_DIR/test2git.py"
echo "  - $HOOK_PATH"
echo "[test2git] try: git push   (hook will write .test2git/<sub>.json on each push)"
echo "[test2git] uninstall: bash scripts/test2git-repo/install.sh --uninstall"
