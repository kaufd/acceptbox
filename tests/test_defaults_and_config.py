"""Тесты решений L22 (эргономичные дефолты CLI), L23/L25 (.acceptbox.toml) и L24."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
PYTHON = sys.executable

sys.path.insert(0, SRC_DIR)

from acceptbox import config  # noqa: E402
from acceptbox.git import base_env  # noqa: E402

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


def _run_cli(args: list[str], *, env: dict[str, str] | None = None,
             cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ if env is None else env)
    existing_pp = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run(
        [PYTHON, "-m", "acceptbox.cli", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=run_env, capture_output=True, text=True,
    )


def _extract_report_path(output: str) -> Path:
    match = re.search(r"\(отчёт:\s*(.+?)\)\s*$", output.strip(), re.MULTILINE)
    assert match is not None, f"путь отчёта не найден в выводе: {output!r}"
    return Path(match.group(1))


def _make_command_fixture(root: Path, *, config_toml: str | None = None) -> dict[str, str | Path]:
    repo = root / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "config.toml").write_text("protected = true\n", encoding="utf-8")
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text(
        "import unittest\nclass Smoke(unittest.TestCase):\n    def test_ok(self): pass\n",
        encoding="utf-8",
    )
    if config_toml is not None:
        (repo / ".acceptbox.toml").write_text(config_toml, encoding="utf-8")
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
    return {"repo": repo, "base": base, "repro": repro, "head": head}


def _make_witnessbox_in_repo_fixture(root: Path, *, config_toml: str | None = None) -> dict:
    fakebin = root / "fakebin"
    fakebin.mkdir()
    script = fakebin / "witnessbox"
    script.write_text(FAKE_WITNESSBOX_SOURCE, encoding="utf-8")
    script.chmod(0o755)
    (fakebin / "fake-config.json").write_text("{}", encoding="utf-8")

    repo = root / "repo"
    _init_repo(repo)
    (repo / "ebt").mkdir()
    (repo / "ebt" / "spec.toml").write_text("inv_demo = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_smoke.py").write_text(
        "import unittest\nclass Smoke(unittest.TestCase):\n    def test_ok(self): pass\n",
        encoding="utf-8",
    )
    (repo / "wb_state").write_text("red\n", encoding="utf-8")
    if config_toml is not None:
        (repo / ".acceptbox.toml").write_text(config_toml, encoding="utf-8")
    base = _commit(repo, "base with ebt and bug")

    (repo / "tests" / "test_repro.py").write_text(
        "import unittest\nfrom app import add\n"
        "class Repro(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    repro = _commit(repo, "repro")

    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "wb_state").write_text("green\n", encoding="utf-8")
    head = _commit(repo, "head fix")

    pin = _run_cli(["pin", "--ebt-repo", str(repo), "--ebt-sha", base])
    assert pin.returncode == 0, pin.stderr
    approved = pin.stdout.strip()

    env = {
        "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": os.environ.get("HOME", str(root)),
    }
    return {
        "repo": repo, "base": base, "repro": repro, "head": head,
        "approved": approved, "env": env,
    }


class TestConfigUnit(unittest.TestCase):
    def test_parse_valid_full_and_minimal_config(self) -> None:
        raw = (
            'format = 1\n'
            'mode = "command"\n'
            'timeout = 120\n'
            'protect = ["pyproject.toml", "Makefile"]\n'
            'env = ["FOO=1", "BAR"]\n'
            '\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            'argv = ["python3", "-m", "unittest"]\n'
            '\n'
            '[witnessbox]\n'
            'ebt_repo = "https://example.com/repo.git"\n'
            'ebt_ref = "main"\n'
        ).encode("utf-8")
        cfg = config.parse_config(raw, commit_sha="a" * 40)
        self.assertEqual(cfg.source_commit, "a" * 40)
        self.assertEqual(cfg.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(cfg.mode, "command")
        self.assertEqual(cfg.timeout, 120.0)
        self.assertEqual(cfg.protect, ("pyproject.toml", "Makefile"))
        self.assertEqual(cfg.env, (("FOO", "1"), ("BAR", None)))
        self.assertEqual(cfg.command_tests, ("tests/",))
        self.assertEqual(cfg.command_argv, ("python3", "-m", "unittest"))
        self.assertEqual(cfg.witnessbox_ebt_repo, "https://example.com/repo.git")
        self.assertEqual(cfg.witnessbox_ebt_ref, "main")

        minimal = config.parse_config(b"format = 1\n", commit_sha="b" * 40)
        self.assertIsNone(minimal.mode)
        self.assertIsNone(minimal.timeout)
        self.assertEqual(minimal.protect, ())
        self.assertEqual(minimal.env, ())
        self.assertEqual(minimal.command_tests, ())
        self.assertEqual(minimal.command_argv, ())
        self.assertIsNone(minimal.witnessbox_ebt_repo)
        self.assertIsNone(minimal.witnessbox_ebt_ref)

        for source in ("/srv/ebt-repo", "git@example.com:org/repo.git"):
            with self.subTest(source=source):
                cfg = config.parse_config(
                    f'format = 1\n[witnessbox]\nebt_repo = "{source}"\n'.encode(),
                    commit_sha="c" * 40)
                self.assertEqual(cfg.witnessbox_ebt_repo, source)

    def test_parse_rejects_invalid_schema_types_and_unknown_keys(self) -> None:
        bad_cases = [
            b"\xff\xfe",
            b"format = [broken",
            b'mode = "command"\n',  # missing format
            b"format = 2\n",
            b"format = true\n",
            b'format = "1"\n',
            b'format = 1\nmode = "unknown"\n',
            b"format = 1\ntimeout = true\n",
            b"format = 1\ntimeout = 0\n",
            b"format = 1\ntimeout = -5\n",
            b'format = 1\ntimeout = "60"\n',
            b'format = 1\nprotect = "pyproject.toml"\n',
            b'format = 1\nprotect = [":!secret"]\n',
            b'format = 1\nenv = ["BASE=123"]\n',
            b'format = 1\nenv = ["EBT_CHECK=/bin/true"]\n',
            b'format = 1\nenv = ["GIT_DIR=/tmp"]\n',
            b'format = 1\ninv = ["INV-1"]\n',
            b'format = 1\napproved_tree = "abc"\n',
            b'format = 1\nebt_sha = "abc"\n',
            b'format = 1\nbase = "abc"\n',
            b'format = 1\ntests = ["tests/"]\n',
            b'format = 1\ncommand = "pytest"\n',
            b"format = 1\n[command]\ntests = []\n",
            b'format = 1\n[command]\ntests = [":^tests/bad.py"]\n',
            b'format = 1\n[command]\nargv = "pytest"\n',
            b"format = 1\n[command]\nargv = []\n",
            b'format = 1\n[command]\nargv = ["  "]\n',
            b'format = 1\n[command]\ncommand = ["pytest"]\n',
            b'format = 1\n[witnessbox]\nebt_repo = "-evil"\n',
            b'format = 1\n[witnessbox]\nebt_repo = "../ebt-repo"\n',
            b'format = 1\n[witnessbox]\nebt_repo = "ebt-repo"\n',
            b'format = 1\n[witnessbox]\nebt_ref = "main..bad"\n',
            b'format = 1\n[witnessbox]\ninv = "INV-1"\n',
            b'format = 1\n[witnessbox]\napproved_tree = "' + b"a" * 64 + b'"\n',
        ]
        for raw in bad_cases:
            with self.subTest(raw=raw):
                with self.assertRaises(config.ConfigError):
                    config.parse_config(raw, commit_sha="a" * 40)

    def test_load_config_from_commit_rejects_symlink_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Symlink .acceptbox.toml
            repo1 = root / "repo1"
            _init_repo(repo1)
            (repo1 / "real.toml").write_text("format = 1\n", encoding="utf-8")
            (repo1 / ".acceptbox.toml").symlink_to("real.toml")
            sha1 = _commit(repo1, "symlink config")
            with self.assertRaises(config.ConfigError):
                config.load_config_from_commit(repo1, base_env(), sha1)

            # Directory .acceptbox.toml/
            repo2 = root / "repo2"
            _init_repo(repo2)
            (repo2 / ".acceptbox.toml").mkdir()
            (repo2 / ".acceptbox.toml" / "nested.toml").write_text("format = 1\n", encoding="utf-8")
            sha2 = _commit(repo2, "dir config")
            with self.assertRaises(config.ConfigError):
                config.load_config_from_commit(repo2, base_env(), sha2)

            # Missing .acceptbox.toml -> None
            repo3 = root / "repo3"
            _init_repo(repo3)
            (repo3 / "a.txt").write_text("hi\n", encoding="utf-8")
            sha3 = _commit(repo3, "no config")
            self.assertIsNone(config.load_config_from_commit(repo3, base_env(), sha3))


class TestL22Defaults(unittest.TestCase):
    def test_accept_command_defaults_mode_repo_head_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            repo = Path(fx["repo"])
            custom_tmp = root / "custom-tmp"
            custom_tmp.mkdir()
            decoy = root / "decoy"
            decoy.mkdir()
            env = dict(os.environ)
            env["TMPDIR"] = str(custom_tmp)
            # Poison GIT_DIR to prove default --head resolution strips git local env vars.
            env["GIT_DIR"] = str(decoy / ".git")
            env["GIT_WORK_TREE"] = str(decoy)

            res = _run_cli([
                "accept",
                "--base", str(fx["base"]),
                "--repro", str(fx["repro"]),
                "--tests", "tests/",
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ], env=env, cwd=repo)
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            report_path = _extract_report_path(res.stdout)
            self.assertTrue(report_path.is_file())
            self.assertEqual(report_path.name, "accept.json")
            self.assertEqual(report_path.parent.parent.resolve(), custom_tmp.resolve())

            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["mode"], "command")
            self.assertEqual(data["repo"]["source"], ".")
            self.assertEqual(data["repo"]["head"], fx["head"])
            # Working tree of repo was not polluted
            self.assertEqual(_git(repo, "status", "--porcelain"), "")

    def test_default_head_resolution_failure_is_gate_error_with_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unborn = root / "unborn"
            _init_repo(unborn)
            out = root / "out"
            res = _run_cli([
                "accept", "--repo", str(unborn), "--base", "a" * 40, "--repro", "b" * 40,
                "--out", str(out), "--tests", "tests/", "--", PYTHON, "-c", "pass",
            ])
            self.assertEqual(res.returncode, 2, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reason_code"], "gate-error")
            self.assertIn("HEAD", data["message"])
            self.assertIsNone(data["repo"]["head"])

    def test_calibrate_rejects_tests_flag(self) -> None:
        res = _run_cli(["calibrate", "--green", "a" * 40, "--tests", "tests/",
                        "--", PYTHON, "-c", "pass"])
        self.assertEqual(res.returncode, 2)
        self.assertIn("--tests", res.stderr)

    def test_explicit_head_still_requires_full_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            out = root / "out"
            for bad_head in ("HEAD", "main", str(fx["head"])[:12]):
                with self.subTest(bad_head=bad_head):
                    res = _run_cli([
                        "accept", "--repo", str(fx["repo"]),
                        "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                        "--head", bad_head, "--tests", "tests/", "--out", str(out),
                        "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
                    ])
                    self.assertEqual(res.returncode, 2)
                    self.assertFalse(out.exists())

    def test_omitted_head_resolves_for_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            out = root / "out"
            res = _run_cli([
                "accept", "--repo", f"file://{fx['repo']}",
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                "--tests", "tests/", "--out", str(out),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["repo"]["head"], fx["head"])

    def test_omitted_mode_rejects_mode_mixing_without_creating_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            out1 = root / "out1"
            # --mode omitted (defaults to command), --inv passed
            res1 = _run_cli([
                "accept", "--repo", str(fx["repo"]),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                "--head", str(fx["head"]), "--inv", "INV-1", "--out", str(out1),
            ])
            self.assertEqual(res1.returncode, 2)
            self.assertFalse(out1.exists())

            # --mode omitted, both --tests and --inv passed together
            out2 = root / "out2"
            res2 = _run_cli([
                "accept", "--repo", str(fx["repo"]),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                "--head", str(fx["head"]), "--tests", "tests/", "--inv", "INV-1",
                "--out", str(out2),
            ])
            self.assertEqual(res2.returncode, 2)
            self.assertFalse(out2.exists())

    def test_omitted_out_does_not_leak_tmpdir_on_missing_cli_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root)
            custom_tmp = root / "empty-tmp"
            custom_tmp.mkdir()
            env = dict(os.environ)
            env["TMPDIR"] = str(custom_tmp)
            # Missing --tests and command (and no .acceptbox.toml)
            res = _run_cli([
                "accept", "--repo", str(fx["repo"]),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
            ], env=env)
            self.assertEqual(res.returncode, 2)
            self.assertEqual(list(custom_tmp.iterdir()), [])

    def test_witnessbox_defaults_ebt_repo_head_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_witnessbox_in_repo_fixture(root)
            custom_tmp = root / "wb-tmp"
            custom_tmp.mkdir()
            env = dict(fx["env"])
            env["TMPDIR"] = str(custom_tmp)

            res = _run_cli([
                "accept", "--mode", "witnessbox",
                "--base", fx["base"], "--repro", fx["repro"],
                "--inv", "INV-1", "--ebt-ref", "main",
                "--ebt-sha", fx["base"], "--approved-tree", fx["approved"],
            ], env=env, cwd=fx["repo"])
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            report_path = _extract_report_path(res.stdout)
            self.assertTrue(report_path.is_file())
            self.assertEqual(report_path.parent.parent.resolve(), custom_tmp.resolve())
            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["repo"]["head"], fx["head"])
            self.assertEqual(data["inputs"]["ebt"]["repo"], ".")

    def test_pin_still_requires_ebt_repo(self) -> None:
        res = _run_cli(["pin", "--ebt-sha", "a" * 40])
        self.assertEqual(res.returncode, 2)
        self.assertIn("--ebt-repo", res.stderr)

    def test_calibrate_defaults_command_and_witnessbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_witnessbox_in_repo_fixture(root)
            custom_tmp = root / "cal-tmp"
            custom_tmp.mkdir()
            env = dict(fx["env"])
            env["TMPDIR"] = str(custom_tmp)

            # Command calibrate omitting --mode, --repo, --out
            res1 = _run_cli([
                "calibrate", "--green", fx["head"], "--red", fx["repro"],
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ], env=env, cwd=fx["repo"])
            self.assertEqual(res1.returncode, 0, res1.stderr + res1.stdout)
            cal1_path = _extract_report_path(res1.stdout)
            self.assertEqual(cal1_path.name, "calibrate.json")
            self.assertFalse((cal1_path.parent / "accept.json").exists())
            self.assertEqual(json.loads(cal1_path.read_text(encoding="utf-8"))["result"], "CALIBRATED")

            # Witnessbox calibrate omitting --repo, --ebt-repo, --out
            res2 = _run_cli([
                "calibrate", "--mode", "witnessbox",
                "--green", fx["head"], "--red", fx["repro"],
                "--inv", "INV-1", "--ebt-ref", "main",
                "--ebt-sha", fx["base"], "--approved-tree", fx["approved"],
            ], env=env, cwd=fx["repo"])
            self.assertEqual(res2.returncode, 0, res2.stderr + res2.stdout)
            cal2_path = _extract_report_path(res2.stdout)
            self.assertEqual(cal2_path.name, "calibrate.json")
            self.assertFalse((cal2_path.parent / "accept.json").exists())
            data2 = json.loads(cal2_path.read_text(encoding="utf-8"))
            self.assertEqual(data2["result"], "CALIBRATED")
            self.assertEqual(data2["inputs"]["ebt"]["repo"], ".")


class TestL23ProjectConfigE2E(unittest.TestCase):
    def test_accept_command_from_config_minimal_cli(self) -> None:
        cfg_text = (
            'format = 1\n'
            'timeout = 60\n'
            'protect = ["config.toml"]\n'
            'env = ["FROM_CFG=yes"]\n'
            '\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-c", '
            '"import os, subprocess, sys; '
            "assert os.environ.get('FROM_CFG') == 'yes'; "
            "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))\"]\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root, config_toml=cfg_text)
            custom_tmp = root / "cfg-tmp"
            custom_tmp.mkdir()
            env = dict(os.environ)
            env["TMPDIR"] = str(custom_tmp)

            res = _run_cli([
                "accept", "--base", str(fx["base"]), "--repro", str(fx["repro"]),
            ], env=env, cwd=Path(fx["repo"]))
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            report_path = _extract_report_path(res.stdout)
            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["inputs"]["timeout_sec"], 60.0)
            self.assertEqual(data["inputs"]["protect"], ["config.toml"])
            self.assertEqual(data["inputs"]["env_keys"], ["FROM_CFG"])
            self.assertEqual(data["inputs"]["config"], {
                "path": ".acceptbox.toml",
                "commit": fx["base"],
                "sha256": hashlib.sha256(cfg_text.encode("utf-8")).hexdigest(),
            })

    def test_cli_overrides_and_additive_protect(self) -> None:
        cfg_text = (
            'format = 1\n'
            'timeout = 60\n'
            'protect = ["config.toml"]\n'
            'env = ["K1=from_cfg", "K2=from_cfg"]\n'
            '\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-c", "import sys; sys.exit(2)"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root, config_toml=cfg_text)
            repo = Path(fx["repo"])
            out1 = root / "out1"

            # CLI overrides timeout, env K2, tests, argv; protect is additive!
            res1 = _run_cli([
                "accept", "--repo", str(repo),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                "--head", str(fx["head"]),
                "--timeout", "120",
                "--protect", "extra.txt",
                "--env", "K2=from_cli",
                "--tests", "tests/test_repro.py",
                "--out", str(out1),
                "--", PYTHON, "-c",
                "import os, subprocess, sys; "
                "assert os.environ.get('K1') == 'from_cfg'; "
                "assert os.environ.get('K2') == 'from_cli'; "
                "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))",
            ])
            self.assertEqual(res1.returncode, 0, res1.stderr)
            data1 = json.loads((out1 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data1["result"], "ACCEPTED")
            self.assertEqual(data1["inputs"]["timeout_sec"], 120.0)
            self.assertEqual(data1["inputs"]["protect"], ["config.toml", "extra.txt"])
            self.assertEqual(data1["inputs"]["tests"], ["tests/test_repro.py"])

            # Modifying config.toml (protected by .acceptbox.toml) is STILL caught even when
            # CLI passes --protect extra.txt!
            (repo / "config.toml").write_text("protected = false\n", encoding="utf-8")
            head2 = _commit(repo, "touch config.toml")
            out2 = root / "out2"
            res2 = _run_cli([
                "accept", "--repo", str(repo),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]), "--head", head2,
                "--protect", "extra.txt", "--out", str(out2),
                "--", PYTHON, "-m", "unittest", "discover", "-s", "tests",
            ])
            self.assertEqual(res2.returncode, 1)
            data2 = json.loads((out2 / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data2["reason_code"], "protected-paths-changed")
            self.assertEqual(data2["runs"]["repro"]["status"], "skipped")

    def test_config_read_strictly_from_base_ignores_dirty_working_tree(self) -> None:
        cfg_text = (
            'format = 1\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root, config_toml=cfg_text)
            repo = Path(fx["repo"])
            # Sabotage working tree .acceptbox.toml (uncommitted)
            (repo / ".acceptbox.toml").write_text("not valid toml [[[", encoding="utf-8")
            out = root / "out"
            res = _run_cli([
                "accept", "--repo", str(repo),
                "--base", str(fx["base"]), "--repro", str(fx["repro"]),
                "--head", str(fx["head"]), "--out", str(out),
            ])
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(
                data["inputs"]["config"]["sha256"],
                hashlib.sha256(cfg_text.encode("utf-8")).hexdigest(),
            )

    def test_config_auto_protected_edit_delete_rename_and_add(self) -> None:
        cfg_text = (
            'format = 1\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        for variant in ("repro-edit", "head-edit", "head-delete", "head-rename", "head-add"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                initial_cfg = None if variant == "head-add" else cfg_text
                fx = _make_command_fixture(root, config_toml=initial_cfg)
                repo = Path(fx["repo"])
                base, repro, head = str(fx["base"]), str(fx["repro"]), str(fx["head"])

                if variant == "repro-edit":
                    _git(repo, "checkout", "-q", "-B", "tamper", base)
                    (repo / "tests" / "test_repro.py").write_text(
                        "import unittest\nfrom app import add\n"
                        "class R(unittest.TestCase):\n    def test_a(self): self.assertEqual(add(2,3),5)\n",
                        encoding="utf-8",
                    )
                    (repo / ".acceptbox.toml").write_text(cfg_text + "# tampered\n", encoding="utf-8")
                    repro = _commit(repo, "tamper config in repro")
                    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
                    # Even reverting .acceptbox.toml in head must still fail base..repro check!
                    (repo / ".acceptbox.toml").write_text(cfg_text, encoding="utf-8")
                    head = _commit(repo, "revert config in head")
                elif variant == "head-edit":
                    (repo / ".acceptbox.toml").write_text(cfg_text + "# tampered\n", encoding="utf-8")
                    head = _commit(repo, "edit config in head")
                elif variant == "head-delete":
                    (repo / ".acceptbox.toml").unlink()
                    head = _commit(repo, "delete config in head")
                elif variant == "head-rename":
                    (repo / ".acceptbox.toml").rename(repo / ".acceptbox.toml.bak")
                    head = _commit(repo, "rename config in head")
                else:  # head-add
                    (repo / ".acceptbox.toml").write_text(cfg_text, encoding="utf-8")
                    head = _commit(repo, "sneak config in head")

                out = root / "out"
                extra = (
                    ["--tests", "tests/", "--", PYTHON, "-m", "unittest", "discover", "-s", "tests"]
                    if variant == "head-add" else []
                )
                res = _run_cli([
                    "accept", "--repo", str(repo),
                    "--base", base, "--repro", repro, "--head", head,
                    "--out", str(out), *extra,
                ])
                self.assertEqual(res.returncode, 1, res.stderr + res.stdout)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["reason_code"], "protected-paths-changed")
                self.assertIn(".acceptbox.toml", data["checks"]["protected_paths"]["specs"])
                self.assertEqual(data["runs"]["repro"]["status"], "skipped")

    def test_invalid_config_in_base_returns_config_invalid(self) -> None:
        for variant, content in (
            ("broken-toml", "format = [broken"),
            ("unknown-key", "format = 1\nunknown_field = 42\n"),
            ("symlink", None),
        ):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fx = _make_command_fixture(root, config_toml=None)
                repo = Path(fx["repo"])
                _git(repo, "checkout", "-q", "-B", "badcfg", str(fx["base"]))
                if variant == "symlink":
                    (repo / "real.toml").write_text("format = 1\n", encoding="utf-8")
                    (repo / ".acceptbox.toml").symlink_to("real.toml")
                else:
                    assert content is not None
                    (repo / ".acceptbox.toml").write_text(content, encoding="utf-8")
                bad_base = _commit(repo, "bad config base")
                (repo / "tests" / "test_repro.py").write_text("# t\n", encoding="utf-8")
                bad_repro = _commit(repo, "repro")
                (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
                bad_head = _commit(repo, "head")

                out = root / "out"
                res = _run_cli([
                    "accept", "--repo", str(repo),
                    "--base", bad_base, "--repro", bad_repro, "--head", bad_head,
                    "--out", str(out),
                ])
                self.assertEqual(res.returncode, 2, res.stderr)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["result"], "ERROR")
                self.assertEqual(data["reason_code"], "config-invalid")
                self.assertEqual(_extract_report_path(res.stderr), out / "accept.json")
                self.assertEqual(data["inputs"]["config"]["commit"], bad_base)
                if content is not None:
                    self.assertEqual(data["inputs"]["config"]["sha256"],
                                     hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_dual_mode_config_witnessbox_and_cli_override(self) -> None:
        cfg_text = (
            'format = 1\n'
            'mode = "witnessbox"\n'
            '\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n'
            '\n'
            '[witnessbox]\n'
            'ebt_ref = "main"\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_witnessbox_in_repo_fixture(root, config_toml=cfg_text)

            # 1. Default mode from config ("witnessbox"), ebt_ref from config, ebt_repo defaults to --repo
            out_wb = root / "out-wb"
            res_wb = _run_cli([
                "accept", "--base", fx["base"], "--repro", fx["repro"],
                "--inv", "INV-1", "--ebt-sha", fx["base"],
                "--approved-tree", fx["approved"], "--out", str(out_wb),
            ], env=fx["env"], cwd=fx["repo"])
            self.assertEqual(res_wb.returncode, 0, res_wb.stderr + res_wb.stdout)
            data_wb = json.loads((out_wb / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data_wb["result"], "ACCEPTED")
            self.assertEqual(data_wb["mode"], "witnessbox")
            self.assertEqual(data_wb["inputs"]["ebt"]["ref"], "main")
            self.assertEqual(data_wb["inputs"]["ebt"]["repo"], ".")

            # 2. Config sets mode="witnessbox", CLI passes --tests without --mode -> rejected (exit 2)
            out_mix = root / "out-mix"
            res_mix = _run_cli([
                "accept", "--base", fx["base"], "--repro", fx["repro"],
                "--tests", "tests/", "--out", str(out_mix),
            ], env=fx["env"], cwd=fx["repo"])
            self.assertEqual(res_mix.returncode, 2)
            self.assertFalse(out_mix.exists())

            # 3. Explicit --mode command overrides config mode="witnessbox" and uses [command]
            out_cmd = root / "out-cmd"
            res_cmd = _run_cli([
                "accept", "--mode", "command",
                "--base", fx["base"], "--repro", fx["repro"],
                "--out", str(out_cmd),
            ], env=fx["env"], cwd=fx["repo"])
            self.assertEqual(res_cmd.returncode, 0, res_cmd.stderr + res_cmd.stdout)
            data_cmd = json.loads((out_cmd / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data_cmd["result"], "ACCEPTED")
            self.assertEqual(data_cmd["mode"], "command")

    def test_calibrate_reads_config_only_when_base_provided(self) -> None:
        cfg_text = (
            'format = 1\n'
            '[command]\n'
            'tests = ["tests/"]\n'
            f'argv = [{json.dumps(PYTHON)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _make_command_fixture(root, config_toml=cfg_text)
            repo = Path(fx["repo"])

            # With --base -> reads [command].argv from base:.acceptbox.toml
            out_with = root / "cal-with"
            res_with = _run_cli([
                "calibrate", "--base", str(fx["base"]),
                "--green", str(fx["head"]), "--red", str(fx["repro"]),
                "--out", str(out_with),
            ], cwd=repo)
            self.assertEqual(res_with.returncode, 0, res_with.stderr + res_with.stdout)
            data = json.loads((out_with / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "CALIBRATED")
            self.assertEqual(data["inputs"]["config"]["commit"], fx["base"])

            # Without --base -> does NOT read .acceptbox.toml from working tree or targets
            out_without = root / "cal-without"
            res_without = _run_cli([
                "calibrate", "--green", str(fx["head"]), "--red", str(fx["repro"]),
                "--out", str(out_without),
            ], cwd=repo)
            self.assertEqual(res_without.returncode, 2)
            self.assertFalse(out_without.exists())


if __name__ == "__main__":
    unittest.main()
