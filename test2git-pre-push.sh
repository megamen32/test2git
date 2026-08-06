#!/usr/bin/env bash
# test2git pre-push hook: run tests for the subproject being pushed, write
# results to .test2git/<sub>.json so the next AI agent can read what was red.
#
# Install (from repo root):
#   ln -sf ../../scripts/hooks/test2git-pre-push.sh .git/hooks/pre-push
# For subprojects (TGC, ATV):
#   cd TGC && ln -sf ../../scripts/hooks/test2git-pre-push.sh .git/hooks/pre-push
#
# The hook is non-blocking by default: tests run, results are written, but the
# push proceeds even if tests fail.  Use TEST2GIT_BLOCK=1 to make it blocking.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")"

# Find the test2git script: either in scripts/ (root) or ../../scripts/ (subproject).
SCRIPT=""
for candidate in "$REPO_ROOT/scripts/test2git.py" "$REPO_ROOT/../scripts/test2git.py" "$REPO_ROOT/../../scripts/test2git.py"; do
  if [ -f "$candidate" ]; then
    SCRIPT="$candidate"
    break
  fi
done

if [ -z "$SCRIPT" ]; then
  echo "[test2git] script not found, skipping"
  exit 0
fi

# Determine subproject name from directory.
BASENAME="$(basename "$REPO_ROOT")"
case "$BASENAME" in
  TGC|TG_Commentator) SUB="tgc" ;;
  ATV|AutoTelegramViews) SUB="atv" ;;
  comment-orchestrator) SUB="orchestrator" ;;
  *) SUB="" ;;
esac

if [ -n "$SUB" ]; then
  if [ "${TEST2GIT_BLOCK:-0}" = "1" ]; then
    python3 "$SCRIPT" "$SUB" --fail-on-error
  else
    python3 "$SCRIPT" "$SUB" || true
  fi
else
  # Root repo or unknown — auto-detect.
  python3 "$SCRIPT" || true
fi

exit 0
