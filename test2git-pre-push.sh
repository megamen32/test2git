#!/usr/bin/env bash
# test2git pre-push hook: run tests for the subproject being pushed, write
# results to .test2git/<sub>.json so the next AI agent can read what was red.
#
# Install (from repo root): run install.sh, or copy this file to .git/hooks/pre-push.
# For subprojects (TGC, ATV), copy it to <sub>/.git/hooks/pre-push the same way.
#
# The hook is non-blocking by default: tests run, results are written, but the
# push proceeds even if tests fail.  Use TEST2GIT_BLOCK=1 to make it blocking.
#
# TEST2GIT_BLAST=1 switches on blast-radius mode (default: off, full suite): only
# tests whose `LHC:COVERS` header names a file in the pushed range are run.
# Needs the root layout scripts/test2git.py; a range that cannot be resolved
# (new branch without origin/HEAD) falls back to the full suite.
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

# Blast mode: build --range args from the pushed refs (stdin: "<lref> <lsha> <rref> <rsha>").
BLAST_ARGS=()
if [ "${TEST2GIT_BLAST:-0}" = "1" ] && [ "$SCRIPT" = "$REPO_ROOT/scripts/test2git.py" ]; then
  BLAST_ARGS=(--blast)
  ZERO="0000000000000000000000000000000000000000"
  while read -r _lref lsha _rref rsha; do
    [ "$lsha" = "$ZERO" ] && continue            # branch deletion: nothing to test
    if [ "$rsha" = "$ZERO" ]; then               # new branch: diff against the default branch
      base=""
      for ref in origin/HEAD origin/main origin/master; do
        base="$(git merge-base "$lsha" "$ref" 2>/dev/null || true)"
        [ -n "$base" ] && break
      done
      if [ -z "$base" ]; then BLAST_ARGS=(--full); break; fi
      BLAST_ARGS+=(--range "$base..$lsha")
    else
      BLAST_ARGS+=(--range "$rsha..$lsha")
    fi
  done
fi

# Determine subproject name from directory.
BASENAME="$(basename "$REPO_ROOT")"
case "$BASENAME" in
  TGC|TG_Commentator) SUB="tgc" ;;
  ATV|AutoTelegramViews) SUB="atv" ;;
  comment-orchestrator) SUB="orchestrator" ;;
  *) SUB="" ;;
esac

if [ "${#BLAST_ARGS[@]}" -gt 0 ]; then
  if [ "${TEST2GIT_BLOCK:-0}" = "1" ]; then
    python3 "$SCRIPT" "${BLAST_ARGS[@]}" --fail-on-error
  else
    python3 "$SCRIPT" "${BLAST_ARGS[@]}" || true
  fi
elif [ -n "$SUB" ]; then
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
