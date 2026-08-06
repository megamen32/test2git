#!/usr/bin/env bash
# test2git one-command installer.
#
# What it does:
#   1. Copies test2git.py into ./scripts/ (creating the dir if missing).
#   2. Installs the pre-push hook into .git/hooks/pre-push.
#   3. Adds .test2git/ to .gitignore if not already present.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/install.sh | bash
#   curl ... | TEST2GIT_BLOCK=1 bash   # make the hook block pushes on test failures
#
# To install into a subproject (TGC, ATV, etc.) run the same command from the
# subproject's directory; the hook will auto-detect the correct subproject name.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-push"

mkdir -p "$SCRIPTS_DIR"

# Locate the source files (next to this script, or downloaded alongside it).
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || echo "")"
if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/test2git.py" ]; then
  # When piped via curl, BASH_SOURCE is empty and we ship files via a temp dir.
  # Fall back to /tmp where the README snippet above expects files to be present.
  echo "[test2git] installer source files not found next to install.sh." >&2
  echo "[test2git] Run from a clone of the repo, or download both files first:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git.py -o scripts/test2git.py" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/megamen32/test2git/main/test2git-pre-push.sh -o .git/hooks/pre-push" >&2
  echo "  chmod +x .git/hooks/pre-push scripts/test2git.py" >&2
  exit 1
fi

cp -f "$SRC_DIR/test2git.py" "$SCRIPTS_DIR/test2git.py"
cp -f "$SRC_DIR/test2git-pre-push.sh" "$HOOK_PATH"
chmod +x "$SCRIPTS_DIR/test2git.py" "$HOOK_PATH"

# Ensure .test2git/ is gitignored.
GITIGNORE="$REPO_ROOT/.gitignore"
touch "$GITIGNORE"
if ! grep -qx ".test2git/" "$GITIGNORE"; then
  echo ".test2git/" >> "$GITIGNORE"
fi

# If invoked with TEST2GIT_BLOCK=1, write a tiny wrapper that sets the env var
# before delegating to the real hook (so `git push` actually blocks on failure).
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
