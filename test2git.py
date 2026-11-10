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
import fnmatch
import json
import os
import re
import shutil
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

# Pseudo-subproject for pytest files that live outside every SUBPROJECTS dir.
ROOT_SUBPROJECT = {
    "dir": REPO_ROOT, "python": None,
    "pytest_args": ["--tb=short", "-v"], "test_dirs": ["."],
}

# Blast-radius runners by language (edit here like SUBPROJECTS): which source
# extensions belong to the runner and which file names are its test files.
RUNNERS: dict[str, dict] = {
    "pytest": {"source_ext": [".py"], "test_globs": ["test_*.py", "*_test.py"]},
    "go": {"source_ext": [".go"], "test_globs": ["*_test.go"]},
    "js": {"source_ext": [".js", ".ts"],
           "test_globs": ["*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts"]},
}
COVERS_TOKEN = "LHC:COVERS"


# ── Blast radius: pure logic ─────────────────────────────────────────────
def parse_covers(text: str) -> list[str]:
    """Source paths named by ``LHC:COVERS`` in the first 10 lines (any comment prefix)."""
    for line in text.splitlines()[:10]:
        head, token, rest = line.partition(COVERS_TOKEN)
        if token and re.fullmatch(r"\W*", head):
            return [p for p in rest.split() if p not in ("*/", "-->", "--%>")]
    return []


def classify(path: str) -> tuple[str | None, bool]:
    """Return (runner, is_test) for a path; runner is None for non-code files."""
    name = path.rsplit("/", 1)[-1]
    for runner, cfg in RUNNERS.items():
        if any(fnmatch.fnmatch(name, g) for g in cfg["test_globs"]):
            return runner, True
    for runner, cfg in RUNNERS.items():
        if any(name.endswith(e) for e in cfg["source_ext"]):
            return runner, False
    return None, False


def select_tests(changed: list[str], covers: dict[str, list[str]]) -> dict:
    """Tests to run for a change set, plus source files no test names."""
    tests: set[str] = set()
    uncovered: list[str] = []
    for f in changed:
        runner, is_test = classify(f)
        if runner is None:
            continue
        if is_test:
            tests.add(f)
            continue
        hit = [t for t, srcs in covers.items() if f in srcs]
        if hit:
            tests.update(hit)
        else:
            uncovered.append(f)
    return {"tests": sorted(tests), "uncovered": sorted(set(uncovered))}


def subproject_of(path: str, subprojects: dict | None = None) -> str | None:
    """Name of the configured subproject containing ``path`` (repo-relative)."""
    for name, cfg in (subprojects or SUBPROJECTS).items():
        if path.startswith(cfg["dir"].name + "/"):
            return name
    return None


def plan_runs(tests: list[str], uncovered: list[str]) -> list[dict]:
    """Turn a selection into runs. Uncovered files fall back to the full suite
    of their subproject/runner; tests inside a falling-back suite are dropped
    because the full run already contains them."""
    full_py: dict[str, list[str]] = {}
    full_other: dict[str, list[str]] = {}
    for f in uncovered:
        runner = classify(f)[0]
        if runner == "pytest":
            full_py.setdefault(subproject_of(f) or "root", []).append(f)
        else:
            full_other.setdefault(str(runner), []).append(f)
    specs = [{"runner": "pytest", "subproject": s, "files": [], "mode": "full", "uncovered": u}
             for s, u in sorted(full_py.items())]
    specs += [{"runner": r, "subproject": r, "files": [], "mode": "full", "uncovered": u}
              for r, u in sorted(full_other.items())]
    grouped: dict[tuple[str, str], list[str]] = {}
    for t in tests:
        runner = classify(t)[0]
        sub = (subproject_of(t) or "root") if runner == "pytest" else str(runner)
        if (runner == "pytest" and sub in full_py) or (runner != "pytest" and sub in full_other):
            continue
        grouped.setdefault((str(runner), sub), []).append(t)
    specs += [{"runner": r, "subproject": s, "files": fs, "mode": "blast", "uncovered": []}
              for (r, s), fs in sorted(grouped.items())]
    return specs


def go_packages(tests: list[str]) -> list[str]:
    """Unique ``./dir/...`` package patterns for Go test files."""
    dirs = sorted({t.rsplit("/", 1)[0] if "/" in t else "." for t in tests})
    return ["./..." if d == "." else f"./{d}/..." for d in dirs]


def is_red(record: dict) -> bool:
    """True when a run failed: failing tests, an error, or a non-zero exit of a non-pytest runner."""
    if record.get("error") or record.get("failed") or record.get("errors"):
        return True
    return record.get("runner") not in (None, "pytest") and record.get("exit_code") != 0


def porcelain_files() -> list[str]:
    """Changed/staged/untracked files from ``git status``."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
    except Exception:
        return []
    return [line[3:].split(" -> ")[-1].strip('"') for line in result.stdout.splitlines()]


def range_files(ranges: list[str]) -> list[str]:
    """Files changed in git ranges (``a..b``), deleted ones excluded."""
    found: set[str] = set()
    for rng in ranges:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=d", rng],
            capture_output=True, text=True, cwd=REPO_ROOT,
        ).stdout
        found.update(out.splitlines())
    return sorted(found)


def covers_index() -> dict[str, list[str]]:
    """``{test file: covered sources}`` from every test file carrying LHC:COVERS."""
    if shutil.which("rg"):
        cmd = ["rg", "-l", "--no-messages", COVERS_TOKEN]
    else:
        cmd = ["git", "grep", "-l", COVERS_TOKEN]
    # stdin=DEVNULL: rg searches a piped stdin (the hook's) instead of the tree.
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT,
                         stdin=subprocess.DEVNULL).stdout
    index: dict[str, list[str]] = {}
    for path in out.splitlines():
        if not classify(path)[1]:
            continue
        try:
            head = (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paths = parse_covers(head)
        if paths:
            index[path] = paths
    return index


def detect_subprojects_from_git() -> list[str]:
    """Detect which subprojects have staged/changed files."""
    all_files = porcelain_files()

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


def run_pytest(subproject: str, files: list[str] | None = None,
               uncovered: list[str] | None = None, mode: str = "full") -> dict:
    """Run pytest for a subproject (or only ``files``, repo-relative), return structured result."""
    cfg = SUBPROJECTS.get(subproject) or ROOT_SUBPROJECT
    sub_dir = cfg["dir"]
    python = str(cfg["python"] or sys.executable)
    if files:
        test_paths = [str(REPO_ROOT / f) for f in files]
    else:
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
        # In -q/-rf mode the short summary starts the line with "FAILED ".
        # Both shapes must match: the old condition required a leading space
        # before FAILED, so "FAILED <id>" summary lines were silently dropped
        # and the JSON ended up with counts but an empty failure list.
        if (" FAILED" in stripped or stripped.startswith("FAILED ")) and "::" in stripped:
            # Extract test ID: everything before " FAILED"
            if stripped.startswith("FAILED "):
                test_id = stripped[len("FAILED "):].split(" ")[0].strip()
            else:
                test_id = stripped.split(" FAILED")[0].strip()
            failure_ids.append({"id": test_id, "kind": "failed"})
        elif stripped.startswith("ERROR ") or " ERROR" in stripped:
            if stripped.startswith("ERROR "):
                test_id = stripped[len("ERROR "):].split(" ")[0].strip()
                # Summary lines are "ERROR tests/test_x.py::test_y". Captured
                # log lines like "ERROR root:post.py:1066 message" have no
                # "::" and are not test results.
                if "::" not in test_id:
                    continue
            else:
                test_id = stripped.split(" ERROR")[0].strip()
                if "::" not in test_id:
                    continue
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
        "exit_code": proc.returncode,
        "raw_output_file": raw_output_file.name,
        "failures": sorted(failure_ids, key=lambda f: f["id"]),
        "mode": mode,
        "runner": "pytest",
        "selected_tests": list(files or []),
        "uncovered": list(uncovered or []),
    }
    return record


def other_record(spec: dict, **fields) -> dict:
    """Record for a non-pytest run (or an error): counts stay null, exit code is the truth."""
    commit, branch = get_git_info(REPO_ROOT)
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": commit, "branch": branch,
        "subproject": spec["subproject"],
        "passed": None, "failed": None, "errors": None, "skipped": None,
        "duration_s": 0.0, "exit_code": None, "raw_output_file": None, "failures": [],
        "mode": spec["mode"], "runner": spec["runner"],
        "selected_tests": list(spec["files"]), "uncovered": list(spec["uncovered"]),
    }
    record.update(fields)
    return record


def other_command(spec: dict) -> tuple[list[str] | None, str]:
    """(command, error) for a go/js spec; the command is None when the runner is unavailable."""
    if spec["runner"] == "go":
        if not shutil.which("go"):
            return None, "go executable not found"
        pkgs = ["./..."] if spec["mode"] == "full" else go_packages(spec["files"])
        return ["go", "test", *pkgs], ""
    if spec["runner"] == "js":
        if spec["mode"] == "full":
            return None, "no full-suite fallback for JS: add LHC:COVERS to a test of the changed files"
        bins = REPO_ROOT / "node_modules" / ".bin"
        if not shutil.which("npx"):
            return None, "no JS runner found (npx missing)"
        if (bins / "vitest").exists():
            return ["npx", "--no-install", "vitest", "run", *spec["files"]], ""
        if (bins / "jest").exists():
            return ["npx", "--no-install", "jest", *spec["files"]], ""
        return None, "no JS runner found (neither vitest nor jest in node_modules/.bin)"
    return None, f"no runner for {spec['runner']}"


def run_other(spec: dict) -> dict:
    """Run a go/js spec, store exit code, duration and raw output."""
    cmd, error = other_command(spec)
    if cmd is None:
        return other_record(spec, error=error, errors=1)
    OUTPUT_DIR.mkdir(exist_ok=True)
    raw_file = OUTPUT_DIR / f"{spec['subproject']}.txt"
    print(f"[test2git] running {' '.join(cmd[:5])}... in {REPO_ROOT}")
    start = datetime.now()
    try:
        with open(raw_file, "w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True, cwd=REPO_ROOT)
    except OSError as exc:
        return other_record(spec, error=f"cannot start {cmd[0]}: {exc}", errors=1)
    return other_record(spec, exit_code=proc.returncode, raw_output_file=raw_file.name,
                        duration_s=round((datetime.now() - start).total_seconds(), 1))


def run_spec(spec: dict) -> dict:
    if spec["runner"] == "pytest":
        return run_pytest(spec["subproject"], spec["files"], spec["uncovered"], spec["mode"])
    return run_other(spec)


def write_results(record: dict) -> None:
    """Write the JSON summary file + append to history."""
    sub = record["subproject"]
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / f"{sub}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False))

    history = OUTPUT_DIR / "history.jsonl"
    with open(history, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_history(subproject: str, history_path=None) -> list:
    """Previous history records for a subproject, oldest first."""
    path = history_path or (OUTPUT_DIR / "history.jsonl")
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("subproject") == subproject:
            records.append(rec)
    records.sort(key=lambda r: str(r.get("ts", "")))
    return records


def _is_green(rec: dict) -> bool:
    return not is_red(rec)


def attach_blame(record: dict, history_path=None) -> None:
    """On a RED run, add the blame block: who broke it and when it last passed.

    ``introduced_by_this_run`` is True only when the immediately previous run
    of this subproject was GREEN — i.e. the world was fine until this push.
    When False, the suite was already RED before this push, and ``last_green``
    names the last commit where everything passed. Must be called BEFORE
    write_results() appends the current record.
    """
    if not is_red(record):
        return

    prev = load_history(record["subproject"], history_path)
    if not prev:
        record["blame"] = {
            "last_green": None,
            "runs_since_last_green": None,
            "previous_run": None,
            "introduced_by_this_run": None,
            "note": "no previous runs recorded for this subproject",
        }
        return

    last_run = prev[-1]
    greens = [r for r in prev if _is_green(r)]
    blame = {
        "previous_run": {
            "ts": last_run.get("ts"),
            "commit": last_run.get("commit"),
            "green": _is_green(last_run),
        },
    }
    if greens:
        lg = greens[-1]
        blame["last_green"] = {
            "ts": lg.get("ts"),
            "commit": lg.get("commit"),
            "branch": lg.get("branch"),
        }
        blame["runs_since_last_green"] = len(prev) - 1 - prev.index(lg)
        blame["introduced_by_this_run"] = _is_green(last_run)
    else:
        blame["last_green"] = None
        blame["runs_since_last_green"] = None
        blame["introduced_by_this_run"] = False
        blame["note"] = "no GREEN run in recorded history"
    record["blame"] = blame


def format_blame_line(record: dict) -> str:
    """Human one-liner for the console (pre-push hook output)."""
    blame = record.get("blame")
    if not blame:
        return ""
    prev = blame.get("previous_run")
    lg = blame.get("last_green")
    if blame.get("introduced_by_this_run") is None and prev is None:
        return "; no previous runs in history — cannot attribute"
    if blame.get("introduced_by_this_run"):
        return (f"; introduced by THIS push — previous run "
                f"{prev['commit']} ({prev['ts']}) was GREEN")
    if lg is None:
        return "; already RED before this push — no GREEN run in history"
    runs = blame.get("runs_since_last_green")
    runs_txt = f"{runs} run(s) ago" if runs is not None else "earlier"
    return (f"; already RED before this push — last GREEN was "
            f"{lg['commit']} ({lg['ts']}), {runs_txt}")


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


def report(record: dict) -> int:
    """Print one result line; return how many failures it adds."""
    sub = record["subproject"]
    status = "RED" if is_red(record) else "GREEN"
    blame_line = format_blame_line(record) if status == "RED" else ""
    if record.get("passed") is None:
        counts = record.get("error") or f"exit code {record.get('exit_code')}"
    else:
        counts = (f"{record['passed']} passed, {record['failed']} failed, "
                  f"{record['errors']} errors")
    print(f"[test2git] {sub}: {status} [{record.get('mode')}] — {counts} "
          f"({record['duration_s']}s) → .test2git/{sub}.txt + {sub}.json{blame_line}")
    return (record.get("failed") or 0) + (record.get("errors") or 0) + (
        1 if status == "RED" and not (record.get("failed") or record.get("errors")) else 0)


def run_blast(ranges: list[str]) -> list[dict]:
    """Run only the tests that name the changed files; see README «Blast radius»."""
    changed = range_files(ranges) if ranges else [
        f for f in porcelain_files() if (REPO_ROOT / f).exists()]
    sel = select_tests(changed, covers_index())
    if sel["uncovered"]:
        print("[test2git] WARNING: no test names these files (add `LHC:COVERS` to a test): "
              + ", ".join(sel["uncovered"]) + " — running the full suite for them")
    specs = plan_runs(sel["tests"], sel["uncovered"])
    if not specs:
        print("[test2git] blast: no relevant code changes, nothing to run")
        return [other_record(
            {"subproject": "blast", "mode": "blast", "runner": None, "files": [], "uncovered": []},
            passed=0, failed=0, errors=0, skipped=0, exit_code=0,
            note="no relevant code changes")]
    return [run_spec(spec) for spec in specs]


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
    parser.add_argument("--blast", action="store_true",
                        help="Run only tests whose LHC:COVERS header names a changed file "
                             "(uncovered source files fall back to the full suite).")
    parser.add_argument("--full", action="store_true",
                        help="Force the full suite (overrides --blast).")
    parser.add_argument("--range", action="append", default=[], metavar="A..B",
                        help="Git range of changed files for --blast (pre-push hook); "
                             "default: git status.")
    args = parser.parse_args()

    update_gitignore(keep=False)

    if args.blast and not args.full:
        total_failures = 0
        for record in run_blast(args.range):
            attach_blame(record)
            write_results(record)
            total_failures += report(record)
        if args.keep:
            update_gitignore(keep=True)
        return 1 if args.fail_on_error and total_failures > 0 else 0

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
        if "error" in record and "runner" not in record:
            print(f"[test2git] {sub}: {record['error']}")
            continue
        attach_blame(record)
        write_results(record)
        total_failures += report(record)

    if args.keep:
        update_gitignore(keep=True)

    if args.fail_on_error and total_failures > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
