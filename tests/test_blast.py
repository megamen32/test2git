# LHC:COVERS test2git.py
"""Blast-radius selection: pure logic plus end-to-end runs in a temp git repo."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "test2git.py"
spec = importlib.util.spec_from_file_location("test2git_mod", SCRIPT)
t2g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t2g)


# ── pure logic ───────────────────────────────────────────────────────────
def test_parse_covers_hash_and_slashes():
    assert t2g.parse_covers("# LHC:COVERS src/a.py src/b.py\nimport x") == ["src/a.py", "src/b.py"]
    assert t2g.parse_covers("// LHC:COVERS internal/price/price.go\npackage x") == ["internal/price/price.go"]
    assert t2g.parse_covers("/* LHC:COVERS web/x.js */") == ["web/x.js"]


def test_parse_covers_missing_or_late_header():
    assert t2g.parse_covers("import x\n") == []
    assert t2g.parse_covers("\n" * 10 + "# LHC:COVERS src/a.py") == []
    assert t2g.parse_covers("x = 1  # LHC:COVERS src/a.py") == []


def test_select_covered_change():
    covers = {"tests/test_a.py": ["src/a.py"], "tests/test_b.py": ["src/b.py"]}
    assert t2g.select_tests(["src/a.py"], covers) == {"tests": ["tests/test_a.py"], "uncovered": []}


def test_select_changed_test_file_is_selected():
    assert t2g.select_tests(["tests/test_b.py"], {}) == {"tests": ["tests/test_b.py"], "uncovered": []}


def test_select_uncovered_source():
    covers = {"tests/test_a.py": ["src/a.py"]}
    assert t2g.select_tests(["src/c.py"], covers) == {"tests": [], "uncovered": ["src/c.py"]}


def test_select_ignores_non_code():
    assert t2g.select_tests(["README.md", "config.yaml", "Makefile"], {}) == {"tests": [], "uncovered": []}


def test_go_packages_and_plan_runs():
    assert t2g.go_packages(["internal/price/price_test.go", "internal/price/b_test.go", "x_test.go"]) == [
        "./...", "./internal/price/..."]
    plan = t2g.plan_runs(["tests/test_a.py", "web/x.test.js"], ["src/c.py"])
    assert [(p["runner"], p["subproject"], p["mode"]) for p in plan] == [
        ("pytest", "root", "full"), ("js", "js", "blast")]  # test_a is inside the full root run


# ── end to end ───────────────────────────────────────────────────────────
def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "web").mkdir()
    shutil.copy(SCRIPT, repo / "scripts" / "test2git.py")
    for n in "abc":
        (repo / "src" / f"{n}.py").write_text("X = 1\n")
    for n in "ab":
        (repo / "tests" / f"test_{n}.py").write_text(
            f"# LHC:COVERS src/{n}.py\ndef test_{n}():\n    assert True\n")
    (repo / "web" / "x.js").write_text("1\n")
    (repo / "web" / "x.test.js").write_text("// LHC:COVERS web/x.js\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def run_tool(repo, *args):
    proc = subprocess.run([sys.executable, "scripts/test2git.py", *args], cwd=repo,
                          capture_output=True, text=True)
    lines = (repo / ".test2git" / "history.jsonl").read_text().splitlines()
    return proc, [json.loads(l) for l in lines]


def test_e2e_blast_runs_only_covering_test(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "a.py").write_text("X = 2\n")
    proc, records = run_tool(repo, "--blast")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(records) == 1
    rec = records[0]
    assert rec["mode"] == "blast" and rec["runner"] == "pytest"
    assert rec["selected_tests"] == ["tests/test_a.py"] and rec["uncovered"] == []
    assert rec["passed"] == 1
    raw = (repo / ".test2git" / "root.txt").read_text()
    assert "test_a" in raw and "test_b" not in raw


def test_e2e_uncovered_falls_back_to_full(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "c.py").write_text("X = 3\n")
    proc, records = run_tool(repo, "--blast")
    assert "no test names these files" in proc.stdout
    rec = records[-1]
    assert rec["mode"] == "full" and rec["uncovered"] == ["src/c.py"]
    assert rec["passed"] == 2  # test_a and test_b both ran


def test_e2e_range_and_nothing_relevant(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "b.py").write_text("X = 2\n")
    (repo / "README.md").write_text("docs\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change b")
    _, records = run_tool(repo, "--blast", "--range", "HEAD~1..HEAD")
    assert records[-1]["selected_tests"] == ["tests/test_b.py"]
    (repo / "README.md").write_text("more docs\n")
    proc, records = run_tool(repo, "--blast")
    assert "nothing to run" in proc.stdout
    assert records[-1]["selected_tests"] == [] and records[-1]["mode"] == "blast"


def test_e2e_full_flag_wins_and_missing_js_runner_is_error(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "web" / "x.js").write_text("2\n")
    proc, records = run_tool(repo, "--blast", "--fail-on-error")
    rec = records[-1]
    assert rec["runner"] == "js" and "no JS runner found" in rec["error"]
    assert proc.returncode == 1
    proc = subprocess.run([sys.executable, "scripts/test2git.py", "--blast", "--full"],
                          cwd=repo, capture_output=True, text=True)
    assert "No changed files detected" in proc.stdout  # --full took the old path
