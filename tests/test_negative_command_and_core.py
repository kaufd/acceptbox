"""Негативные пробы и e2e для режима command (L19), ядра Git, отчёта, гонки --out и calibrate."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
PYTHON = sys.executable


def _clean_git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if extra:
        env.update(extra)
    return env


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(cwd), *args], env=_clean_git_env(),
                         capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _run_cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ if env is None else env)
    existing_pp = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run([PYTHON, "-m", "acceptbox.cli", *args],
                          env=run_env, capture_output=True, text=True)


def _path_without_witnessbox(tmp_dir: Path) -> str:
    """PATH со всеми системными утилитами (git, python), но без witnessbox."""
    bin_dir = tmp_dir / "no-wb-bin"
    bin_dir.mkdir(exist_ok=True)
    git_path = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
    for name, target in (("git", git_path), ("python3", PYTHON)):
        link = bin_dir / name
        if not link.exists():
            link.symlink_to(target)
    return str(bin_dir)


def _make_command_fixture(root: Path) -> dict[str, str | Path]:
    """Создать фикстуру: баг в base, репро-тест в tests/ в repro, фикс в head."""
    repo = root / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "config.toml").write_text("protected = true\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text(
        "import unittest\nclass Smoke(unittest.TestCase):\n    def test_ok(self): pass\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base with bug")

    (repo / "tests" / "test_repro.py").write_text(
        "import unittest\nfrom app import add\n"
        "class Repro(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    repro = _commit(repo, "add repro test")

    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    head = _commit(repo, "fix add")
    head_tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    return {"repo": repo, "base": base, "repro": repro, "head": head, "head_tree": head_tree}


class TestCommandModeAndCore(unittest.TestCase):
    def test_e2e_command_accepted_without_witnessbox_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            out = root / "out"
            env = {
                "PATH": _path_without_witnessbox(root),
                "HOME": os.environ.get("HOME", str(root)),
            }

            # --installation exits 0 with witnessbox.status == "missing"
            inst = _run_cli(["--installation"], env=env)
            self.assertEqual(inst.returncode, 0, inst.stderr)
            inst_data = json.loads(inst.stdout)
            self.assertEqual(inst_data["git"]["status"], "ok")
            self.assertEqual(inst_data["witnessbox"]["status"], "missing")

            res = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(fx["repo"]),
                "--base", str(fx["base"]),
                "--repro", str(fx["repro"]),
                "--head", str(fx["head"]),
                "--tests", "tests/",
                "--protect", "config.toml",
                "--out", str(out),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ], env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["format"], 1)
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["exit_code"], 0)
            self.assertIsNone(data["reason_code"])
            self.assertEqual(data["scope"], "command-only")
            self.assertIsNone(data["witnessbox"])
            self.assertEqual(data["repo"]["head_tree"], fx["head_tree"])
            self.assertEqual(data["runs"]["base"]["status"], "skipped")
            self.assertEqual(data["runs"]["repro"]["status"], "red")
            self.assertEqual(data["runs"]["head"]["status"], "green")
            self.assertTrue((out / data["runs"]["repro"]["log_path"]).is_file())
            self.assertTrue((out / data["runs"]["head"]["log_path"]).is_file())

    def test_repro_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            base = _commit(repo, "base")
            repro = _commit(repo, "empty repro")
            (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
            head = _commit(repo, "head")
            out = root / "out"
            res = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(repo), "--base", base, "--repro", repro, "--head", head,
                "--tests", "tests/", "--out", str(out), "--", PYTHON, "-c", "import sys; sys.exit(0)",
            ])
            self.assertEqual(res.returncode, 1)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reason_code"], "repro-empty")
            self.assertEqual(data["runs"]["repro"]["status"], "skipped")

    def test_repro_outside_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            base = _commit(repo, "base")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_a.py").write_text("# test\n", encoding="utf-8")
            (repo / "app.py").write_text("x = 999\n", encoding="utf-8")
            repro = _commit(repo, "repro touches app.py")
            (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
            head = _commit(repo, "head")
            out = root / "out"
            res = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(repo), "--base", base, "--repro", repro, "--head", head,
                "--tests", "tests/", "--out", str(out), "--", PYTHON, "-c", "import sys; sys.exit(0)",
            ])
            self.assertEqual(res.returncode, 1)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reason_code"], "repro-outside-tests")
            self.assertEqual(data["runs"]["repro"]["status"], "skipped")

    def test_repro_tests_modified_edit_delete_and_rename(self) -> None:
        for variant in ("edit", "delete", "rename"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fx = _make_command_fixture(root)
                repo = Path(fx["repo"])
                if variant == "edit":
                    (repo / "tests" / "test_repro.py").write_text(
                        "import unittest\nclass Repro(unittest.TestCase):\n    def test_add(self): pass\n",
                        encoding="utf-8",
                    )
                elif variant == "delete":
                    (repo / "tests" / "test_repro.py").unlink()
                else:
                    (repo / "tests" / "test_repro.py").rename(repo / "tests" / "test_repro_renamed.py")
                head2 = _commit(repo, f"head {variant} repro test")
                out = root / "out"
                res = _run_cli([
                    "accept", "--mode", "command",
                    "--repo", str(repo), "--base", str(fx["base"]),
                    "--repro", str(fx["repro"]), "--head", head2,
                    "--tests", "tests/", "--out", str(out),
                    "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
                ])
                self.assertEqual(res.returncode, 1)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["reason_code"], "repro-tests-modified")
                self.assertEqual(data["runs"]["repro"]["status"], "skipped")

    def test_protected_paths_changed_in_repro_and_head(self) -> None:
        for where in ("repro", "head"):
            with self.subTest(where=where), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "repo"
                _init_repo(repo)
                (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
                (repo / "config.toml").write_text("v = 1\n", encoding="utf-8")
                base = _commit(repo, "base")
                (repo / "tests").mkdir()
                (repo / "tests" / "test_r.py").write_text("# t\n", encoding="utf-8")
                if where == "repro":
                    (repo / "config.toml").write_text("v = 2\n", encoding="utf-8")
                repro = _commit(repo, "repro")
                (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
                if where == "head":
                    (repo / "config.toml").write_text("v = 3\n", encoding="utf-8")
                head = _commit(repo, "head")
                out = root / "out"
                res = _run_cli([
                    "accept", "--mode", "command",
                    "--repo", str(repo), "--base", base, "--repro", repro, "--head", head,
                    "--tests", "tests/", "--protect", "config.toml", "--out", str(out),
                    "--", PYTHON, "-c", "import sys; sys.exit(0)",
                ])
                self.assertEqual(res.returncode, 1)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["reason_code"], "protected-paths-changed")
                self.assertEqual(data["runs"]["repro"]["status"], "skipped")

    def test_command_exit2_signal_and_timeout_give_gate_error(self) -> None:
        cases = [
            ("exit2", [PYTHON, "-c", "import sys; sys.exit(2)"], "60"),
            ("signal", [PYTHON, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"], "60"),
            ("timeout", [PYTHON, "-c", "import time; time.sleep(5)"], "0.3"),
        ]
        for name, cmd, timeout in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fx = _make_command_fixture(root)
                out = root / "out"
                res = _run_cli([
                    "accept", "--mode", "command",
                    "--repo", str(fx["repo"]), "--base", str(fx["base"]),
                    "--repro", str(fx["repro"]), "--head", str(fx["head"]),
                    "--tests", "tests/", "--timeout", timeout, "--out", str(out),
                    "--", *cmd,
                ])
                self.assertEqual(res.returncode, 2, res.stderr)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["result"], "ERROR")
                self.assertEqual(data["reason_code"], "gate-error")
                self.assertEqual(data["runs"]["repro"]["status"], "error")

    def test_ancestry_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            repo = Path(fx["repo"])
            # Divergent commit not descending from base
            _git(repo, "checkout", "-q", "--orphan", "other")
            (repo / "other.txt").write_text("other\n", encoding="utf-8")
            orphan = _commit(repo, "orphan")

            out1 = root / "out1"
            res1 = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(repo), "--base", str(fx["base"]),
                "--repro", orphan, "--head", str(fx["head"]),
                "--tests", "tests/", "--out", str(out1),
                "--", PYTHON, "-c", "import sys; sys.exit(0)",
            ])
            self.assertEqual(res1.returncode, 1)
            self.assertEqual(json.loads((out1 / "accept.json").read_text())["reason_code"], "repro-not-descendant")

            # repro is NOT ancestor of head (swap head and base)
            out2 = root / "out2"
            res2 = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(repo), "--base", str(fx["base"]),
                "--repro", str(fx["head"]), "--head", str(fx["repro"]),
                "--tests", "tests/", "--out", str(out2),
                "--", PYTHON, "-c", "import sys; sys.exit(0)",
            ])
            self.assertEqual(res2.returncode, 1)
            self.assertEqual(json.loads((out2 / "accept.json").read_text())["reason_code"], "repro-not-ancestor")

    def test_non_default_branch_and_caller_git_env_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            _init_repo(repo)
            (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            base = _commit(repo, "main base")
            # Create repro + head on a non-default branch, then switch back to main!
            _git(repo, "checkout", "-q", "-b", "feature/fix")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_repro.py").write_text(
                "import unittest\nfrom app import add\n"
                "class R(unittest.TestCase):\n    def test_a(self): self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            repro = _commit(repo, "feature repro")
            (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            head = _commit(repo, "feature head")
            _git(repo, "checkout", "-q", "main")

            # Poison caller env with GIT_DIR / GIT_WORK_TREE pointing at a decoy dir!
            decoy = root / "decoy"
            decoy.mkdir()
            env = dict(os.environ)
            env["GIT_DIR"] = str(decoy / ".git")
            env["GIT_WORK_TREE"] = str(decoy)

            out = root / "out"
            res = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(repo), "--base", base, "--repro", repro, "--head", head,
                "--tests", "tests/", "--out", str(out),
                "--", PYTHON, "-c",
                "import os, subprocess, sys; "
                "assert 'GIT_DIR' not in os.environ and 'GIT_WORK_TREE' not in os.environ; "
                "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))",
            ], env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(json.loads((out / "accept.json").read_text())["result"], "ACCEPTED")
            # Caller checkout stayed on main!
            self.assertEqual(_git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")

    def test_out_not_empty_and_concurrent_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)

            # Non-empty --out -> exit 2, untouched
            existing = root / "existing"
            existing.mkdir()
            sentinel = existing / "keep.txt"
            sentinel.write_text("mine\n", encoding="utf-8")
            res = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(fx["repo"]), "--base", str(fx["base"]),
                "--repro", str(fx["repro"]), "--head", str(fx["head"]),
                "--tests", "tests/", "--out", str(existing),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ])
            self.assertEqual(res.returncode, 2)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "mine\n")
            self.assertFalse((existing / "accept.json").exists())

            # Two concurrent processes racing for the same --out
            race_out = root / "race-out"
            slow_cmd = [
                PYTHON, "-c",
                "import subprocess, sys, time; time.sleep(0.5); "
                "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))",
            ]
            args = [
                "accept", "--mode", "command",
                "--repo", str(fx["repo"]), "--base", str(fx["base"]),
                "--repro", str(fx["repro"]), "--head", str(fx["head"]),
                "--tests", "tests/", "--out", str(race_out), "--", *slow_cmd,
            ]
            results: list[subprocess.CompletedProcess[str]] = []
            barrier = threading.Barrier(2)

            def worker() -> None:
                barrier.wait()
                results.append(_run_cli(args))

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            codes = sorted(r.returncode for r in results)
            self.assertEqual(codes, [0, 2], [(r.returncode, r.stderr) for r in results])
            data = json.loads((race_out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertFalse((race_out / ".acceptbox.lock").exists())

    def test_interrupted_run_leaves_no_accept_json_or_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            out = root / "interrupted"
            run_env = dict(os.environ)
            run_env["PYTHONPATH"] = SRC_DIR
            with subprocess.Popen(
                [
                    PYTHON, "-m", "acceptbox.cli", "accept", "--mode", "command",
                    "--repo", str(fx["repo"]), "--base", str(fx["base"]),
                    "--repro", str(fx["repro"]), "--head", str(fx["head"]),
                    "--tests", "tests/", "--out", str(out),
                    "--", PYTHON, "-c", "import time; time.sleep(10)",
                ],
                env=run_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as proc:
                # Wait until lock file appears
                deadline = time.monotonic() + 10.0
                while not (out / ".acceptbox.lock").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue((out / ".acceptbox.lock").exists())
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=10)
            self.assertFalse((out / "accept.json").exists())
            self.assertFalse((out / "accept.json.tmp").exists())
            self.assertFalse((out / ".acceptbox.lock").exists())

    def test_calibrate_command_outcomes_and_no_accept_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            repo = Path(fx["repo"])
            # Mutant descending from head (would fail ancestry in accept, works in calibrate!)
            (repo / "app.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
            mutant = _commit(repo, "mutant descending from head")

            # CALIBRATED (0)
            out_ok = root / "cal-ok"
            res = _run_cli([
                "calibrate", "--mode", "command",
                "--repo", str(repo),
                "--green", str(fx["head"]),
                "--red", mutant,
                "--out", str(out_ok),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertFalse((out_ok / "accept.json").exists())
            cal = json.loads((out_ok / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(cal["result"], "CALIBRATED")
            self.assertTrue(all(item["matched"] for item in cal["results"]))

            # FAILED (1)
            out_fail = root / "cal-fail"
            res = _run_cli([
                "calibrate", "--mode", "command",
                "--repo", str(repo),
                "--green", mutant,
                "--out", str(out_fail),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ])
            self.assertEqual(res.returncode, 1)
            self.assertFalse((out_fail / "accept.json").exists())
            cal = json.loads((out_fail / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(cal["result"], "FAILED")

            # ERROR (2)
            out_err = root / "cal-err"
            res = _run_cli([
                "calibrate", "--mode", "command",
                "--repo", str(repo),
                "--green", str(fx["head"]),
                "--out", str(out_err),
                "--", PYTHON, "-c", "import sys; sys.exit(3)",
            ])
            self.assertEqual(res.returncode, 2)
            self.assertFalse((out_err / "accept.json").exists())
            cal = json.loads((out_err / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(cal["result"], "ERROR")

    def test_cli_flag_mixing_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            sha = "a" * 40
            # Mixing --inv into --mode command
            res1 = _run_cli([
                "accept", "--mode", "command",
                "--repo", str(root), "--base", sha, "--repro", sha, "--head", sha,
                "--tests", "tests/", "--inv", "INV-1", "--out", str(out),
                "--", PYTHON, "-c", "pass",
            ])
            self.assertEqual(res1.returncode, 2)
            self.assertFalse(out.exists())

            # Mixing --tests into --mode witnessbox
            res2 = _run_cli([
                "accept", "--mode", "witnessbox",
                "--repo", str(root), "--base", sha, "--repro", sha, "--head", sha,
                "--tests", "tests/", "--inv", "INV-1",
                "--ebt-repo", str(root), "--ebt-ref", "main", "--ebt-sha", sha,
                "--approved-tree", "b" * 64, "--out", str(out),
            ])
            self.assertEqual(res2.returncode, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
