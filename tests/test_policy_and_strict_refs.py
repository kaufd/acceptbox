"""Тесты --strict-refs и --policy refactor (решения L24, L30)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
PYTHON = sys.executable

sys.path.insert(0, SRC_DIR)

try:  # noqa: E402
    from test_negative_witnessbox import FAKE_WITNESSBOX_SOURCE
except ImportError:
    from tests.test_negative_witnessbox import FAKE_WITNESSBOX_SOURCE


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


def _add_origin(repo: Path) -> Path:
    upstream = repo.parent / "upstream.git"
    _git(repo.parent, "clone", "-q", "--bare", "--no-local", str(repo), str(upstream))
    _git(repo, "remote", "add", "origin", str(upstream))
    return upstream


def _run_cli(args: list[str], *, env: dict[str, str] | None = None,
             cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ if env is None else env)
    existing_pp = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run(
        [PYTHON, "-m", "acceptbox.cli", *args],
        env=run_env, cwd=cwd, capture_output=True, text=True,
    )


def _install_fake_witnessbox(tmp: Path) -> dict[str, str]:
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    wb = bin_dir / "witnessbox"
    wb.write_text(FAKE_WITNESSBOX_SOURCE, encoding="utf-8")
    wb.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _make_command_refactor_fixture(tmp: Path, *, base_green: bool = True) -> dict:
    repo = tmp / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text(
        "def double(x):\n    return x * 2\n" if base_green else "def double(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_app.py").write_text(
        "import unittest\nfrom app import double\n"
        "class T(unittest.TestCase):\n"
        "    def test_double(self): self.assertEqual(double(3), 6)\n",
        encoding="utf-8",
    )
    (repo / ".acceptbox.toml").write_text(
        'format = 1\nmode = "command"\n\n[command]\ntests = ["tests/"]\n'
        f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n',
        encoding="utf-8",
    )
    base = _commit(repo, "base")

    (repo / "app.py").write_text("def double(x):\n    return x + x\n", encoding="utf-8")
    head = _commit(repo, "refactor: use addition")
    return {"repo": repo, "base": base, "head": head}


def _make_witnessbox_refactor_fixture(tmp: Path, *, head_state: str = "green",
                                      base_state: str = "green") -> dict:
    repo = tmp / "repo"
    _init_repo(repo)
    (repo / "ebt").mkdir()
    (repo / "ebt" / "spec.toml").write_text('[inv.INV-1]\nstatement = "ok"\n', encoding="utf-8")
    (repo / "wb_state").write_text(f"{base_state}\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".acceptbox.toml").write_text(
        'format = 1\nmode = "witnessbox"\n\n[witnessbox]\nebt_ref = "main"\n',
        encoding="utf-8",
    )
    base = _commit(repo, "base")

    (repo / "app.py").write_text("x = 1 + 0\n", encoding="utf-8")
    (repo / "wb_state").write_text(f"{head_state}\n", encoding="utf-8")
    head = _commit(repo, "refactor")

    pin = _run_cli(["pin", "--ebt-repo", str(repo), "--ebt-sha", base])
    assert pin.returncode == 0, pin.stderr
    return {"repo": repo, "base": base, "head": head, "ebt_sha": base,
            "approved_tree": pin.stdout.strip()}


class TestStrictRefs(unittest.TestCase):
    def test_strict_refs_rejects_local_base_ref_before_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base-ref", "main", "--strict-refs",
                "--base", fx["base"], "--head", fx["head"], "--out", str(out),
            ])
            self.assertEqual(res.returncode, 1, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "REJECTED")
            self.assertEqual(data["reason_code"], "ref-source-local")
            self.assertEqual(data["checks"]["base_ref"]["ref_source_kind"], "local")
            self.assertIsNone(data["inputs"]["config"])
            self.assertTrue(data["inputs"]["strict_refs"])

    def test_strict_refs_accepts_origin_base_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            _add_origin(fx["repo"])
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base-ref", "main", "--strict-refs",
                "--base", fx["base"], "--head", fx["head"], "--out", str(out),
            ])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["checks"]["base_ref"]["ref_source_kind"], "origin")

    def test_strict_refs_without_base_ref_in_command_mode_is_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--strict-refs", "--base", fx["base"], "--head", fx["head"],
                "--out", str(out),
            ])
            self.assertEqual(res.returncode, 2)
            self.assertFalse((out / "accept.json").exists())

    def test_strict_refs_rejects_local_ebt_ref_in_witnessbox_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_witnessbox_refactor_fixture(tmp)
            env = _install_fake_witnessbox(tmp)
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--mode", "witnessbox",
                "--repo", str(fx["repo"]), "--strict-refs",
                "--base", fx["base"], "--head", fx["head"],
                "--ebt-sha", fx["ebt_sha"], "--approved-tree", fx["approved_tree"],
                "--out", str(out),
            ], env=env)
            self.assertEqual(res.returncode, 1, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reason_code"], "ref-source-local")
            self.assertEqual(data["checks"]["ebt_trust"]["ref_source_kind"], "local")


class TestRefactorPolicyCommand(unittest.TestCase):
    def test_cli_enforces_base_and_repro_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            # neither --base nor --base-ref -> exit 2
            res0 = _run_cli(["accept", "--repo", str(fx["repo"]), "--head", fx["head"]])
            self.assertEqual(res0.returncode, 2)
            # refactor with --repro -> exit 2
            res2 = _run_cli(["accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                             "--base", fx["base"], "--repro", fx["head"], "--head", fx["head"]])
            self.assertEqual(res2.returncode, 2)
            # --pin-from-ref in command mode -> exit 2
            res3 = _run_cli(["accept", "--mode", "command", "--repo", str(fx["repo"]),
                             "--base", fx["base"], "--head", fx["head"], "--pin-from-ref"])
            self.assertEqual(res3.returncode, 2)

    def test_refactor_command_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base", fx["base"], "--head", fx["head"], "--out", str(out),
            ])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["policy"], "refactor")
            self.assertIsNone(data["repo"]["repro"])
            self.assertEqual(data["runs"]["base"]["status"], "green")
            self.assertEqual(data["runs"]["repro"]["status"], "skipped")
            self.assertEqual(data["runs"]["head"]["status"], "green")
            self.assertEqual(data["checks"]["repro_tests"]["status"], "skipped")
            self.assertEqual(data["checks"]["refactor"]["status"], "ok")

    def test_refactor_command_protects_tests_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            (fx["repo"] / "tests" / "test_app.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
                encoding="utf-8",
            )
            tampered_head = _commit(fx["repo"], "weaken test")
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base", fx["base"], "--head", tampered_head, "--out", str(out),
            ])
            self.assertEqual(res.returncode, 1, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reason_code"], "protected-paths-changed")
            self.assertIn("tests/test_app.py", data["checks"]["protected_paths"]["changed_base_head"])

    def test_refactor_command_rejects_empty_diff_and_bad_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            out1 = tmp / "out1"
            res1 = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base", fx["base"], "--head", fx["base"], "--out", str(out1),
            ])
            self.assertEqual(res1.returncode, 1)
            self.assertEqual(
                json.loads((out1 / "accept.json").read_text(encoding="utf-8"))["reason_code"],
                "refactor-empty",
            )

            out2 = tmp / "out2"
            res2 = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base", fx["head"], "--head", fx["base"], "--out", str(out2),
            ])
            self.assertEqual(res2.returncode, 1)
            self.assertEqual(
                json.loads((out2 / "accept.json").read_text(encoding="utf-8"))["reason_code"],
                "head-not-descendant",
            )

    def test_refactor_command_rejects_red_base_and_red_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx_bad_base = _make_command_refactor_fixture(tmp / "b", base_green=False)
            out1 = tmp / "out1"
            res1 = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx_bad_base["repo"]),
                "--base", fx_bad_base["base"], "--head", fx_bad_base["head"], "--out", str(out1),
            ])
            self.assertEqual(res1.returncode, 1)
            data1 = json.loads((out1 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data1["reason_code"], "base-not-green")
            self.assertEqual(data1["runs"]["head"]["status"], "skipped")

            fx_bad_head = _make_command_refactor_fixture(tmp / "h", base_green=True)
            (fx_bad_head["repo"] / "app.py").write_text(
                "def double(x):\n    return x * 3\n", encoding="utf-8"
            )
            bad_head = _commit(fx_bad_head["repo"], "broken refactor")
            out2 = tmp / "out2"
            res2 = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx_bad_head["repo"]),
                "--base", fx_bad_head["base"], "--head", bad_head, "--out", str(out2),
            ])
            self.assertEqual(res2.returncode, 1)
            data2 = json.loads((out2 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data2["reason_code"], "head-not-green")

    def test_config_rejects_policy_and_strict_refs_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_command_refactor_fixture(tmp)
            (fx["repo"] / ".acceptbox.toml").write_text(
                'format = 1\npolicy = "refactor"\n', encoding="utf-8"
            )
            bad_base = _commit(fx["repo"], "bad config")
            (fx["repo"] / "app.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
            head = _commit(fx["repo"], "next")
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--repo", str(fx["repo"]),
                "--base", bad_base, "--head", head, "--out", str(out),
            ])
            self.assertEqual(res.returncode, 2)
            self.assertEqual(
                json.loads((out / "accept.json").read_text(encoding="utf-8"))["reason_code"],
                "config-invalid",
            )


class TestRefactorPolicyWitnessbox(unittest.TestCase):
    def test_witnessbox_refactor_accepted_without_inv_and_with_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_witnessbox_refactor_fixture(tmp, base_state="green", head_state="green")
            _add_origin(fx["repo"])
            env = _install_fake_witnessbox(tmp)
            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--mode", "witnessbox",
                "--repo", str(fx["repo"]), "--base-ref", "main", "--strict-refs",
                "--base", fx["base"], "--head", fx["head"],
                "--ebt-sha", fx["ebt_sha"], "--approved-tree", fx["approved_tree"],
                "--out", str(out),
            ], env=env)
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["policy"], "refactor")
            self.assertEqual(data["runs"]["base"]["status"], "green")
            self.assertEqual(data["runs"]["head"]["status"], "green")

    def test_witnessbox_refactor_rejects_explicit_empty_inv_and_red_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_witnessbox_refactor_fixture(tmp, base_state="green", head_state="red")
            env = _install_fake_witnessbox(tmp)
            out1 = tmp / "out1"
            res1 = _run_cli([
                "accept", "--policy", "refactor", "--mode", "witnessbox",
                "--repo", str(fx["repo"]), "--inv", " , ",
                "--base", fx["base"], "--head", fx["head"],
                "--ebt-sha", fx["ebt_sha"], "--approved-tree", fx["approved_tree"],
                "--out", str(out1),
            ], env=env)
            self.assertEqual(res1.returncode, 1)
            self.assertEqual(
                json.loads((out1 / "accept.json").read_text(encoding="utf-8"))["reason_code"],
                "no-invariants",
            )

            out2 = tmp / "out2"
            res2 = _run_cli([
                "accept", "--policy", "refactor", "--mode", "witnessbox",
                "--repo", str(fx["repo"]),
                "--base", fx["base"], "--head", fx["head"],
                "--ebt-sha", fx["ebt_sha"], "--approved-tree", fx["approved_tree"],
                "--out", str(out2),
            ], env=env)
            self.assertEqual(res2.returncode, 1)
            self.assertEqual(
                json.loads((out2 / "accept.json").read_text(encoding="utf-8"))["reason_code"],
                "head-not-green",
            )


class TestLocalAutoResolve(unittest.TestCase):
    def test_bugfix_one_liner_auto_resolves_base_repro_and_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo = tmp / "repo"
            _init_repo(repo)
            (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_app.py").write_text(
                "import unittest\nfrom app import add\n"
                "class T(unittest.TestCase):\n"
                "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n",
                encoding="utf-8",
            )
            (repo / ".acceptbox.toml").write_text(
                'format = 1\nmode = "command"\n\n[command]\ntests = ["tests/"]\n'
                f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n',
                encoding="utf-8",
            )
            base = _commit(repo, "base")
            _git(repo, "checkout", "-q", "-b", "fix/add")
            (repo / "tests" / "test_app.py").write_text(
                "import unittest\nfrom app import add\n"
                "class T(unittest.TestCase):\n"
                "    def test_add(self): self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )
            repro = _commit(repo, "test: reproduce add bug")
            (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            head = _commit(repo, "fix: add numbers")

            out = tmp / "out"
            res = _run_cli(["accept", "--base-ref", "main", "--out", str(out)], cwd=repo)
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["repo"]["base"], base)
            self.assertEqual(data["repo"]["repro"], repro)
            self.assertEqual(data["repo"]["head"], head)
            self.assertEqual(data["inputs"]["repro_source"], "first-commit")
            self.assertTrue(data["checks"]["base_ref"]["resolved_base_auto"])

    def test_bugfix_repro_trailer_and_ambiguous_trailer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo = tmp / "repo"
            _init_repo(repo)
            (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests" / "test_app.py").write_text(
                "import unittest\nfrom app import add\n"
                "class T(unittest.TestCase):\n"
                "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n",
                encoding="utf-8",
            )
            (repo / ".acceptbox.toml").write_text(
                'format = 1\nmode = "command"\n\n[command]\ntests = ["tests/"]\n'
                f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n',
                encoding="utf-8",
            )
            _commit(repo, "base")
            _git(repo, "checkout", "-q", "-b", "fix/trailer")
            (repo / "tests" / "helper.py").write_text("VAL = 5\n", encoding="utf-8")
            _commit(repo, "test: add helper")
            (repo / "tests" / "test_app.py").write_text(
                "import unittest\nfrom app import add\nfrom tests.helper import VAL\n"
                "class T(unittest.TestCase):\n"
                "    def test_add(self): self.assertEqual(add(2, 3), VAL)\n",
                encoding="utf-8",
            )
            repro = _commit(repo, "test: reproduce bug\n\nAcceptBox-Role: repro")
            (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            _commit(repo, "fix: bug")

            out1 = tmp / "out1"
            res1 = _run_cli(["accept", "--base-ref", "main", "--out", str(out1)], cwd=repo)
            self.assertEqual(res1.returncode, 0, res1.stderr)
            data1 = json.loads((out1 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data1["repo"]["repro"], repro)
            self.assertEqual(data1["inputs"]["repro_source"], "trailer")

            # Second commit with AcceptBox-Role: repro -> repro-ambiguous
            (repo / "app.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
            _commit(repo, "oops\n\nAcceptBox-Role: repro")
            out2 = tmp / "out2"
            res2 = _run_cli(["accept", "--base-ref", "main", "--out", str(out2)], cwd=repo)
            self.assertEqual(res2.returncode, 1, res2.stderr)
            data2 = json.loads((out2 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data2["reason_code"], "repro-ambiguous")

    def test_refactor_and_witnessbox_pin_from_ref_one_liner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            fx = _make_witnessbox_refactor_fixture(tmp, base_state="green", head_state="green")
            _git(fx["repo"], "checkout", "-q", "-b", "refactor/work")
            # Move main back to base so refactor/work is ahead of main
            _git(fx["repo"], "update-ref", "refs/heads/main", fx["base"])
            env = _install_fake_witnessbox(tmp)

            # Conflict: --pin-from-ref with --ebt-sha -> exit 2
            conflict = _run_cli([
                "accept", "--policy", "refactor", "--mode", "witnessbox",
                "--base-ref", "main", "--pin-from-ref", "--ebt-sha", fx["ebt_sha"],
            ], env=env, cwd=fx["repo"])
            self.assertEqual(conflict.returncode, 2)

            out = tmp / "out"
            res = _run_cli([
                "accept", "--policy", "refactor", "--base-ref", "main",
                "--pin-from-ref", "--out", str(out),
            ], env=env, cwd=fx["repo"])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["repo"]["base"], fx["base"])
            self.assertTrue(data["inputs"]["ebt"]["pinned_from_ref"])
            self.assertEqual(data["inputs"]["ebt"]["sha"], fx["ebt_sha"])
            self.assertEqual(data["inputs"]["ebt"]["approved_tree_sha256"], fx["approved_tree"])


if __name__ == "__main__":
    unittest.main()
