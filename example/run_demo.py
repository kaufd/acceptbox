#!/usr/bin/env python3
"""Self-contained AcceptBox demonstration using an external --config file.

Creates a temporary target Git repository with ZERO AcceptBox files committed to it,
then runs AcceptBox using `example/acceptbox.toml` via `--config`:
  1. Valid bugfix (`base -> repro -> head`) -> ACCEPTED (exit 0)
  2. Tampered repro test in `head` -> REJECTED (`repro-tests-modified`, exit 1)
  3. Valid refactoring (`--policy refactor`, `base` green -> `head` green) -> ACCEPTED (exit 0)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
CONFIG_FILE = EXAMPLE_DIR / "acceptbox.toml"


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=_git_env(), capture_output=True, text=True, check=True,
    )
    return res.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _run_acceptbox(args: list[str], cwd: Path) -> tuple[int, dict]:
    env = dict(os.environ)
    if SRC_DIR.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else str(SRC_DIR)
        argv = [sys.executable, "-m", "acceptbox.cli", *args]
    else:
        argv = ["acceptbox", *args]
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    out_idx = args.index("--out") + 1
    report = json.loads((Path(args[out_idx]) / "accept.json").read_text(encoding="utf-8"))
    return proc.returncode, report


def _create_target_repo(repo: Path) -> str:
    """Create a clean target repo with NO .acceptbox.toml committed."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "user.name", "AcceptBox Demo")

    (repo / "pyproject.toml").write_text('[project]\nname = "demo-app"\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text(
        "def discount(total: int, pct: int) -> int:\n"
        "    # Bug: uses round() instead of floor division\n"
        "    return round(total * pct / 100)\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "import unittest\n"
        "from calc import discount\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_exact_percent(self):\n"
        "        self.assertEqual(discount(1000, 10), 100)\n",
        encoding="utf-8",
    )
    return _commit(repo, "initial commit")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="acceptbox-example-") as tmp_name:
        tmp = Path(tmp_name)
        repo = tmp / "target-app"
        base = _create_target_repo(repo)
        print(f"Created clean target repo (no .acceptbox.toml inside) at base={base[:12]}")
        print(f"Using external config: {CONFIG_FILE}\n")

        # ── Scenario 1: valid bugfix (base -> repro -> head) ──────────────
        _git(repo, "checkout", "-q", "-b", "fix/rounding")
        (repo / "tests" / "test_calc.py").write_text(
            "import unittest\n"
            "from calc import discount\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_exact_percent(self):\n"
            "        self.assertEqual(discount(1000, 10), 100)\n\n"
            "    def test_rounds_down(self):\n"
            "        self.assertEqual(discount(999, 15), 149)\n",
            encoding="utf-8",
        )
        _commit(repo, "test: reproduce rounding bug")
        (repo / "src" / "calc.py").write_text(
            "def discount(total: int, pct: int) -> int:\n"
            "    return (total * pct) // 100\n",
            encoding="utf-8",
        )
        _commit(repo, "fix: floor percent discount")

        rc1, rep1 = _run_acceptbox(
            ["accept", "--config", str(CONFIG_FILE), "--base-ref", "main",
             "--out", str(tmp / "out-bugfix")],
            cwd=repo,
        )
        print(f"[1] Valid bugfix:     exit={rc1} result={rep1['result']} "
              f"(repro_source={rep1['inputs']['repro_source']})")
        assert rc1 == 0 and rep1["result"] == "ACCEPTED", rep1

        # ── Scenario 2: tampered repro test in head -> REJECTED ───────────
        _git(repo, "checkout", "-q", "-b", "fix/cheating", base)
        (repo / "tests" / "test_calc.py").write_text(
            "import unittest\n"
            "from calc import discount\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_rounds_down(self):\n"
            "        self.assertEqual(discount(999, 15), 149)\n",
            encoding="utf-8",
        )
        _commit(repo, "test: reproduce rounding bug")
        # Agent weakens the test instead of fixing the bug:
        (repo / "tests" / "test_calc.py").write_text(
            "import unittest\n"
            "from calc import discount\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_rounds_down(self):\n"
            "        self.assertEqual(discount(999, 15), 150)\n",
            encoding="utf-8",
        )
        _commit(repo, "fix: adjust test")

        rc2, rep2 = _run_acceptbox(
            ["accept", "--config", str(CONFIG_FILE), "--base-ref", "main",
             "--out", str(tmp / "out-cheat")],
            cwd=repo,
        )
        print(f"[2] Tampered test:    exit={rc2} result={rep2['result']} "
              f"(reason_code={rep2['reason_code']})")
        assert rc2 == 1 and rep2["reason_code"] == "repro-tests-modified", rep2

        # ── Scenario 3: valid refactoring (--policy refactor) ─────────────
        _git(repo, "checkout", "-q", "-b", "refactor/calc", base)
        (repo / "src" / "calc.py").write_text(
            "def discount(total: int, pct: int) -> int:\n"
            "    raw = total * pct / 100\n"
            "    return round(raw)\n",
            encoding="utf-8",
        )
        _commit(repo, "refactor: extract raw variable")

        rc3, rep3 = _run_acceptbox(
            ["accept", "--config", str(CONFIG_FILE), "--policy", "refactor",
             "--base-ref", "main", "--out", str(tmp / "out-refactor")],
            cwd=repo,
        )
        print(f"[3] Valid refactor:   exit={rc3} result={rep3['result']} "
              f"(policy={rep3['policy']})")
        assert rc3 == 0 and rep3["result"] == "ACCEPTED", rep3

    print("\nAll 3 demo scenarios passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
