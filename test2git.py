#!/usr/bin/env python3
"""test2git — run tests, write results next to your commit for AI agents.

Runs pytest, saves the FULL raw output to a file, and also writes a structured
JSON summary. The output lands in ``.test2git/`` (gitignored by default, or
committed with ``--keep`` for full test versioning).

AI agents read the file after every commit/push to instantly know which tests
were failing and when, without re-running the suite.

Usage
-----
    # Auto-detect subproject from changed files, run its tests:
    python scripts/test2git.py

    # Run a specific subproject:
    python scripts/test2git.py tgc
    python scripts/test2git.py atv
    python scripts/test2git.py orchestrator

    # Run all subprojects:
    python scripts/test2git.py all

    # Exit non-zero if any test failed (useful as a CI gate):
    python scripts/test2git.py --fail-on-error

    # Commit the results (full test versioning in git history):
    python scripts/test2git.py --keep

Output
------
Each run produces THREE files in ``.test2git/``:

1. ``<subproject>.txt`` — the FULL raw pytest output (every line, untruncated).
2. ``<subproject>.json`` — structured summary:
    {
      "ts": "2026-08-06T22:40:00+03:00",
      "commit": "abc1234",
      "branch": "main",
      "subproject": "tgc",
      "passed": 1490, "failed": 30, "errors": 4, "skipped": 9,
      "duration_s": 162.2,
      "pytest_exit_code": 1,
      "raw_output_file": "<subproject>.txt",
      "failures": [{"id": "tests/test_x.py::test_y", "kind": "failed"}]
    }
3. Appended to ``history.jsonl`` — one JSON object per line for chronological
   inspection.

The ``--keep`` flag un-gitignores ``.test2git/`` so results are committed.
This gives you full test versioning: ``git log -- .test2git/tgc.txt`` shows
every test run's full output over time.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Subproject registry: (dir, venv-python-or-None, pytest-args, test-dirs)
SUBPROJECTS: dict[str, dict] = {
    "tgc": {
        "dir": REPO_ROOT / "TGC",
        "python": REPO_ROOT / "TGC" / ".venv" / "bin" / "python",
        "pytest_args": ["-p", "no:rerunfailures", "--tb=short", "-v"],
        "test_dirs": ["tests/"],
    },
    "atv": {
        "dir": REPO_ROOT / "ATV",
        "python": None,  # system python3 with peewee
        "pytest_args": ["--tb=short", "-v"],
        "test_dirs": ["autotests/"],
    },
    "orchestrator": {
        "dir": REPO_ROOT / "comment-orchestrator",
        "python": REPO_ROOT / "comment-orchestrator" / ".venv" / "bin" / "python",
        "pytest_args": ["--tb=short", "-v"],
        "test_dirs": ["tests/"],
    },
}

OUTPUT_DIR = REPO_ROOT / ".test2git"


def detect_subprojects_from_git() -> list[str]:
    """Detect which subprojects have staged/changed files."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        all_files = [line[3:] for line in result.stdout.strip().splitlines()] if result.stdout.strip() else []
    except Exception:
        return []

    found: list[str] = []
    for name, cfg in SUBPROJECTS.items():
        sub_dir = cfg["dir"].name + "/"
        if any(f.startswith(sub_dir) or f.startswith(f"./{sub_dir}") for f in all_files):
            found.append(name)
    return found


def get_git_info(sub_dir: Path) -> tuple[str, str]:
    """Return (short_commit, branch) for a subproject."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=sub_dir,
        ).stdout.strip() or "uncommitted"
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, cwd=sub_dir,
        ).stdout.strip() or "unknown"
        return commit, branch
    except Exception:
        return "unknown", "unknown"


def run_pytest(subproject: str) -> dict:
    """Run pytest for a subproject, return structured result."""
    cfg = SUBPROJECTS[subproject]
    sub_dir = cfg["dir"]
    python = str(cfg["python"] or sys.executable)
    test_paths = [str(sub_dir / td) for td in cfg["test_dirs"] if (sub_dir / td).is_dir()]
    if not test_paths:
        return {"subproject": subproject, "error": "no test dirs found"}

    # Run pytest with output to a file for stability.
    raw_output_file = OUTPUT_DIR / f"{subproject}.txt"
    OUTPUT_DIR.mkdir(exist_ok=True)

    cmd = [python, "-m", "pytest", *cfg["pytest_args"], *test_paths]
    print(f"[test2git] running {' '.join(cmd[:5])}... in {sub_dir}")
    start = datetime.now()

    # Run pytest and capture output to file simultaneously.
    with open(raw_output_file, "w", encoding="utf-8") as raw_fh:
        proc = subprocess.run(
            cmd, stdout=raw_fh, stderr=subprocess.STDOUT,
            text=True, cwd=sub_dir,
        )

    duration = (datetime.now() - start).total_seconds()

    # Read back the full output.
    raw_output = raw_output_file.read_text(encoding="utf-8")

    # Parse structured data from the raw output.
    passed = failed = errors = skipped = 0
    failure_ids: list[dict] = []

    for line in raw_output.splitlines():
        stripped = line.strip()
        # In -v mode: "tests/test_x.py::test_y FAILED" or "... PASSED" etc.
        # Also handle "FAILED tests/..." summary lines (non-verbose mode).
        if " FAILED" in stripped and ("::" in stripped or stripped.startswith("FAILED ")):
            # Extract test ID: everything before " FAILED"
            if stripped.startswith("FAILED "):
                test_id = stripped[len("FAILED "):].split(" ")[0].strip()
            else:
                test_id = stripped.split(" FAILED")[0].strip()
            failure_ids.append({"id": test_id, "kind": "failed"})
        elif stripped.startswith("ERROR ") or " ERROR" in stripped:
            if stripped.startswith("ERROR "):
                test_id = stripped[len("ERROR "):].split(" ")[0].strip()
            else:
                test_id = stripped.split(" ERROR")[0].strip()
            failure_ids.append({"id": test_id, "kind": "error"})

    # Deduplicate failure IDs (verbose mode prints them in both the test line and summary).
    seen = set()
    unique_failures = []
    for f in failure_ids:
        if f["id"] not in seen:
            seen.add(f["id"])
            unique_failures.append(f)
    failure_ids = unique_failures
    failed = sum(1 for f in failure_ids if f["kind"] == "failed")
    errors = sum(1 for f in failure_ids if f["kind"] == "error")

    # Parse the FINAL summary line for pass/skip counts.
    # Format: "30 failed, 1490 passed, 9 skipped, 188 warnings in 164.27s"
    for line in reversed(raw_output.splitlines()):
        stripped = line.strip()
        if " passed" in stripped or " failed" in stripped:
            if " in " in stripped or "warning" in stripped:
                parts = stripped.replace(",", " ").split()
                for i, word in enumerate(parts):
                    if word.isdigit() and i + 1 < len(parts):
                        label = parts[i + 1].rstrip(".").rstrip("s")
                        if label == "passed":
                            passed = int(word)
                        elif label == "failed" and int(word) > failed:
                            failed = int(word)
                        elif label in ("error", "errors") and int(word) > errors:
                            errors = int(word)
                        elif label == "skipped":
                            skipped = int(word)
                break

    # If we still don't have passed count, estimate from test count.
    if passed == 0 and failed == 0 and errors == 0:
        # Count PASSED lines in verbose mode as fallback.
        passed = sum(1 for l in raw_output.splitlines() if " PASSED" in l)

    commit, branch = get_git_info(sub_dir)
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": commit,
        "branch": branch,
        "subproject": subproject,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "duration_s": round(duration, 1),
        "pytest_exit_code": proc.returncode,
        "raw_output_file": raw_output_file.name,
        "failures": sorted(failure_ids, key=lambda f: f["id"]),
    }
    return record


def write_results(record: dict) -> None:
    """Write the JSON summary file + append to history."""
    sub = record["subproject"]
    (OUTPUT_DIR / f"{sub}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False))

    history = OUTPUT_DIR / "history.jsonl"
    with open(history, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_gitignore(keep: bool) -> None:
    """Manage .gitignore entry for .test2git/."""
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.exists():
        return
    content = gitignore.read_text()
    if keep:
        # Remove the gitignore entry.
        if ".test2git/" in content:
            content = content.replace("\n.test2git/\n", "\n").replace(".test2git/\n", "")
            gitignore.write_text(content)
    else:
        # Add the gitignore entry.
        if ".test2git/" not in content:
            gitignore.write_text(content.rstrip() + "\n.test2git/\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="test2git — run tests, write results to file for AI agents.")
    parser.add_argument("subproject", nargs="?", default=None,
                        help="tgc | atv | orchestrator | all (default: auto-detect from git)")
    parser.add_argument("--fail-on-error", action="store_true",
                        help="Exit non-zero if any test failed or errored.")
    parser.add_argument("--keep", action="store_true",
                        help="Commit results in git (un-gitignore .test2git/). "
                             "Gives full test versioning: git log -- .test2git/")
    args = parser.parse_args()

    update_gitignore(keep=False)

    if args.subproject is None:
        subs = detect_subprojects_from_git()
        if not subs:
            print("[test2git] No changed files detected for any subproject. "
                  "Specify explicitly: tgc | atv | orchestrator | all")
            return 0
    elif args.subproject == "all":
        subs = [name for name, cfg in SUBPROJECTS.items()
                if any((cfg["dir"] / td).is_dir() for td in cfg["test_dirs"])]
    elif args.subproject in SUBPROJECTS:
        subs = [args.subproject]
    else:
        print(f"[test2git] Unknown subproject '{args.subproject}'. "
              f"Choose from: {', '.join(SUBPROJECTS)}")
        return 2

    total_failures = 0
    for sub in subs:
        record = run_pytest(sub)
        if "error" in record and "subproject" in record:
            print(f"[test2git] {sub}: {record['error']}")
            continue
        write_results(record)
        status = "GREEN" if record["failed"] == 0 and record["errors"] == 0 else "RED"
        print(f"[test2git] {sub}: {status} — "
              f"{record['passed']} passed, {record['failed']} failed, "
              f"{record['errors']} errors ({record['duration_s']}s) "
              f"→ .test2git/{sub}.txt + {sub}.json")
        total_failures += record["failed"] + record["errors"]

    if args.keep:
        update_gitignore(keep=True)

    if args.fail_on_error and total_failures > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
