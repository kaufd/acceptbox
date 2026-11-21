"""Юнит-тесты ядра AcceptBox: pin, inputs, witnessbox-классификация, verdict, report, --installation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from acceptbox import cli, inputs, pin, report, verdict, witnessbox  # noqa: E402
from acceptbox.verdict import RunOutcome  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    res = subprocess.run(["git", "-C", str(cwd), *args], env=env,
                         capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit_all(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


class TestPin(unittest.TestCase):
    def test_tree_sha256_ignores_uncommitted_and_outside_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "ebt").mkdir()
            (repo / "ebt" / "spec.toml").write_text("[meta]\nversion = '1'\n", encoding="utf-8")
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            sha1 = _commit_all(repo, "init")
            h1 = pin.tree_sha256(str(repo), sha1)
            self.assertEqual(len(h1), 64)

            # Uncommitted changes in ebt/ do not affect hash of committed tree
            (repo / "ebt" / "spec.toml").write_text("dirty\n", encoding="utf-8")
            self.assertEqual(pin.tree_sha256(str(repo), sha1), h1)

            # Committing outside ebt/ keeps tree_sha256 identical
            (repo / "ebt" / "spec.toml").write_text("[meta]\nversion = '1'\n", encoding="utf-8")
            (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
            sha2 = _commit_all(repo, "app change")
            self.assertEqual(pin.tree_sha256(str(repo), sha2), h1)

            # Committing inside ebt/ changes tree_sha256
            (repo / "ebt" / "spec.toml").write_text("[meta]\nversion = '2'\n", encoding="utf-8")
            sha3 = _commit_all(repo, "ebt change")
            self.assertNotEqual(pin.tree_sha256(str(repo), sha3), h1)

    def test_tree_sha256_rejects_short_sha(self) -> None:
        with self.assertRaises(ValueError):
            pin.tree_sha256("/tmp", "abcdef12")


class TestInputs(unittest.TestCase):
    def test_validate_commit_sha(self) -> None:
        sha40 = "A" * 40
        sha64 = "b" * 64
        self.assertEqual(inputs.validate_commit_sha(sha40, "--base"), "a" * 40)
        self.assertEqual(inputs.validate_commit_sha(sha64, "--head"), sha64)
        for bad in ("", "1234567", "g" * 40, "a" * 39, "a" * 41, "HEAD"):
            with self.assertRaises(inputs.InputError):
                inputs.validate_commit_sha(bad, "--base")

    def test_validate_pathspec_rejects_exclude_magic(self) -> None:
        self.assertEqual(inputs.validate_pathspec("tests/", "--tests"), "tests/")
        self.assertEqual(inputs.validate_pathspec(":(glob)tests/*.py", "--tests"), ":(glob)tests/*.py")
        for bad in (
            "",
            "   ",
            ":!tests/bad.py",
            ":^tests/bad.py",
            ":/!tests/bad.py",
            ":/^tests/bad.py",
            ":(exclude)tests/bad.py",
            ":(top,exclude)tests/bad.py",
            ":(icase,!glob)tests/bad.py",
            ":(unclosed",
        ):
            with self.assertRaises(inputs.InputError, msg=f"should reject {bad!r}"):
                inputs.validate_pathspec(bad, "--protect")

    def test_validate_timeout(self) -> None:
        self.assertEqual(inputs.validate_timeout("600"), 600.0)
        self.assertEqual(inputs.validate_timeout("0.5"), 0.5)
        for bad in ("0", "-5", "nan", "inf", "abc"):
            with self.assertRaises(inputs.InputError):
                inputs.validate_timeout(bad)

    def test_env_filtering_and_forbidden_keys(self) -> None:
        for forbidden in ("BASE", "BASE=123", "EBT_CHECK=/bin/true", "EBT_INV=X",
                          "EBT_TESTS=a", "GIT_DIR=/tmp", "GIT_WORK_TREE=/tmp"):
            with self.assertRaises(inputs.InputError, msg=f"should forbid {forbidden!r}"):
                inputs.parse_env_entry(forbidden)

        entries = [
            inputs.parse_env_entry("MY_VAR=hello"),
            inputs.parse_env_entry("INHERITED_OK"),
            inputs.parse_env_entry("MISSING_KEY"),
        ]
        caller = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/u",
            "SECRET": "should-not-leak",
            "INHERITED_OK": "from-caller",
            "EBT_CHECK": "/evil",
            "EBT_TESTS": "evil",
            "EBT_RUNNER": "evil",
            "EBT_VERIFICATION_PYTHON": "/evil/python",
            "GIT_DIR": "/evil/.git",
            "GIT_WORK_TREE": "/evil",
        }
        env, keys = inputs.build_runtime_env(caller, entries)
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertEqual(env["MY_VAR"], "hello")
        self.assertEqual(env["INHERITED_OK"], "from-caller")
        self.assertNotIn("MISSING_KEY", env)
        self.assertNotIn("SECRET", env)
        self.assertNotIn("EBT_CHECK", env)
        self.assertNotIn("EBT_TESTS", env)
        self.assertNotIn("EBT_RUNNER", env)
        self.assertNotIn("EBT_VERIFICATION_PYTHON", env)
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertNotIn("BASE", env)
        self.assertEqual(keys, ["INHERITED_OK", "MISSING_KEY", "MY_VAR"])

        # CLI passthrough of an absent variable removes the earlier config value.
        merged, _ = inputs.build_runtime_env(caller, [("MY_VAR", "from-config"), ("MY_VAR", None)])
        self.assertNotIn("MY_VAR", merged)

        wb_env, _ = inputs.build_runtime_env(
            caller, entries, base_sha="a" * 40, inv_csv="INV-1,INV-2",
        )
        self.assertEqual(wb_env["BASE"], "a" * 40)
        self.assertEqual(wb_env["EBT_INV"], "INV-1,INV-2")
        self.assertNotIn("EBT_CHECK", wb_env)


class TestWitnessboxClassify(unittest.TestCase):
    def _write_gate(self, run_dir: Path, gate: dict, rep: dict | str | None = None) -> None:
        (run_dir / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
        if isinstance(rep, dict):
            (run_dir / "report.json").write_text(json.dumps(rep), encoding="utf-8")
        elif isinstance(rep, str):
            (run_dir / "report.json").write_text(rep, encoding="utf-8")

    def _base_gate(self, *, exit_code: int = 1, reasons=None, commit="a" * 40,
                   spec_sha="b" * 64, version="0.10.0", gate_sha="g" * 64, fmt=1) -> dict:
        return {
            "format": fmt,
            "result": {0: "PASS", 1: "FAIL"}.get(exit_code, "ERROR"),
            "exit_code": exit_code,
            "reasons": ["checker_failed"] if reasons is None else reasons,
            "commit": commit,
            "spec_sha256": spec_sha,
            "verifier": {
                "distribution": "witnessbox",
                "version": version,
                "gate_sha256": gate_sha,
            },
        }

    def test_version_parsing(self) -> None:
        self.assertEqual(witnessbox.parse_version_tuple("0.10.0"), (0, 10, 0))
        self.assertEqual(witnessbox.parse_version_tuple("0.10.3.post1+local"), (0, 10, 3))
        self.assertEqual(witnessbox.parse_version_tuple("1.2.3"), (1, 2, 3))
        self.assertIsNone(witnessbox.parse_version_tuple("0.10.0a1"))
        self.assertIsNone(witnessbox.parse_version_tuple("bad"))

    def test_classify_green_red_and_failures(self) -> None:
        info = {"version": "0.10.0", "gate_sha256": "g" * 64, "checker_sha256": "c" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            # Green
            d = Path(tmp) / "g"
            d.mkdir()
            self._write_gate(d, self._base_gate(exit_code=0, reasons=[]), {"findings": []})
            out = witnessbox.classify_gate_run(
                d, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=0, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "green")
            self.assertEqual(
                out.artifacts["gate.json"]["sha256"],
                hashlib.sha256((d / "gate.json").read_bytes()).hexdigest(),
            )

            # Red with FALSE_PROOF
            d2 = Path(tmp) / "r1"
            d2.mkdir()
            self._write_gate(d2, self._base_gate(exit_code=1),
                             {"findings": [{"kind": "FALSE_PROOF", "inv": "INV-1"}]})
            out = witnessbox.classify_gate_run(
                d2, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "red")

            # Red with CONFIRMED_VIOLATION
            d3 = Path(tmp) / "r2"
            d3.mkdir()
            self._write_gate(d3, self._base_gate(exit_code=1),
                             {"findings": [{"kind": "CONFIRMED_VIOLATION", "inv": "INV-1"}]})
            out = witnessbox.classify_gate_run(
                d3, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "red")

            # Red with CHECKER_DISAGREES -> red-other
            d4 = Path(tmp) / "r3"
            d4.mkdir()
            self._write_gate(d4, self._base_gate(exit_code=1),
                             {"findings": [{"kind": "CHECKER_DISAGREES", "inv": "INV-1"}]})
            out = witnessbox.classify_gate_run(
                d4, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "red-other")

            # Red with pinned_drift -> red-other
            d5 = Path(tmp) / "r4"
            d5.mkdir()
            self._write_gate(d5, self._base_gate(exit_code=1, reasons=["checker_failed", "pinned_drift"]),
                             {"findings": [{"kind": "FALSE_PROOF", "inv": "INV-1"}]})
            out = witnessbox.classify_gate_run(
                d5, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "red-other")

            # Invalid report.json on red -> invalid-report
            d6 = Path(tmp) / "r5"
            d6.mkdir()
            self._write_gate(d6, self._base_gate(exit_code=1), "{broken")
            out = witnessbox.classify_gate_run(
                d6, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "invalid-report")

            # Future format != 1 -> error
            d7 = Path(tmp) / "f2"
            d7.mkdir()
            self._write_gate(d7, self._base_gate(exit_code=0, reasons=[], fmt=2), {"findings": []})
            out = witnessbox.classify_gate_run(
                d7, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=0, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "error")

            # gate.json exit_code disagrees with process exit_code -> error
            d8 = Path(tmp) / "mismatch_rc"
            d8.mkdir()
            self._write_gate(d8, self._base_gate(exit_code=0, reasons=[]), {"findings": []})
            out = witnessbox.classify_gate_run(
                d8, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=10, log_path="witnessbox.log",
            )
            self.assertEqual(out.status, "error")


class TestWitnessboxMalformedFindings(unittest.TestCase):
    def test_non_string_kind_or_inv_is_not_a_target_finding(self) -> None:
        info = {"version": "0.10.0", "gate_sha256": "g" * 64}
        gate = {"format": 1, "exit_code": 1, "reasons": ["checker_failed"], "commit": "a" * 40,
                "spec_sha256": "b" * 64,
                "verifier": {"distribution": "witnessbox", "version": "0.10.0",
                             "gate_sha256": "g" * 64}}
        report_data = {"findings": [{"kind": ["FALSE_PROOF"], "inv": "INV-1"},
                                    {"kind": "FALSE_PROOF", "inv": ["INV-1"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
            (run_dir / "report.json").write_text(json.dumps(report_data), encoding="utf-8")
            outcome = witnessbox.classify_gate_run(
                run_dir, "a" * 40, "b" * 64, ["INV-1"], info,
                gate_exit_code=1, failure_message=None, duration_ms=1, log_path=None)
            self.assertEqual(outcome.status, "red-other")
            self.assertEqual(witnessbox.read_findings_inv(run_dir), [])


class TestProcessGroupAndRefs(unittest.TestCase):
    def test_timeout_kills_grandchildren(self) -> None:
        from acceptbox.command import run_process_group
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            script = f"sleep 60 & echo $! > {pid_file}; wait"
            with self.assertRaises(subprocess.TimeoutExpired):
                run_process_group(["sh", "-c", script], cwd=tmp,
                                  env=dict(os.environ), timeout=1)
            child = int(pid_file.read_text().strip())
            # Убитый внук ненадолго остаётся зомби, пока его не подберёт init.
            for _ in range(50):
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail(f"процесс-внук {child} пережил таймаут")

    def test_fetch_ref_prefers_branch_over_same_named_tag(self) -> None:
        from acceptbox.git import base_env, fetch_ref
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            _init_repo(src)
            old = _commit_all(src, "old")
            new = _commit_all(src, "new")
            _git(src, "tag", "main", old)
            dst = Path(tmp) / "dst"
            _init_repo(dst)
            self.assertEqual(fetch_ref(dst, base_env(), str(src), "main"), new)


class TestTrustedRefSource(unittest.TestCase):
    def test_remote_origin_and_local_fallback(self) -> None:
        from acceptbox.git import base_env, trusted_ref_source
        env = base_env()
        self.assertEqual(trusted_ref_source("https://h/r.git", env), ("https://h/r.git", "remote"))
        self.assertEqual(trusted_ref_source("git@h:o/r.git", env), ("git@h:o/r.git", "remote"))
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            self.assertEqual(trusted_ref_source(str(repo), env), (str(repo), "local"))
            # Относительный origin отсчитывается от репозитория, а не от каталога запуска.
            _git(repo, "remote", "add", "origin", "../upstream.git")
            self.assertEqual(trusted_ref_source(str(repo), env),
                             (os.path.join(tmp, "upstream.git"), "origin"))
            _git(repo, "remote", "set-url", "origin", "/srv/upstream.git")
            self.assertEqual(trusted_ref_source(str(repo), env), ("/srv/upstream.git", "origin"))

    def test_option_like_origin_url_rejected(self) -> None:
        from acceptbox.git import GitError, base_env, trusted_ref_source
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            _git(repo, "config", "remote.origin.url", "--upload-pack=touch PWN;false :")
            with self.assertRaises(GitError):
                trusted_ref_source(str(repo), base_env())


class TestVerdictMatrix(unittest.TestCase):
    def _o(self, status: str) -> RunOutcome:
        return RunOutcome(status=status, exit_code=0, reason=status,
                          duration_ms=1, log_path="x.log", artifacts=None)

    def test_ancestry_priority(self) -> None:
        v = verdict.ancestry_verdict(base_ancestor_of_repro=False, repro_ancestor_of_head=False)
        self.assertEqual((v.result, v.exit_code, v.reason_code), ("REJECTED", 1, "repro-not-descendant"))
        v = verdict.ancestry_verdict(base_ancestor_of_repro=True, repro_ancestor_of_head=False)
        self.assertEqual((v.result, v.exit_code, v.reason_code), ("REJECTED", 1, "repro-not-ancestor"))
        self.assertIsNone(verdict.ancestry_verdict(base_ancestor_of_repro=True, repro_ancestor_of_head=True))

    def test_protected_and_repro_tests_priority(self) -> None:
        v = verdict.protected_paths_verdict(changed_base_repro=["ebt/spec.toml"], changed_base_head=[])
        self.assertEqual((v.exit_code, v.reason_code), (1, "protected-paths-changed"))
        v = verdict.repro_tests_verdict(repro_changed=[], tests_changed=[], modified_after_repro=[])
        self.assertEqual((v.exit_code, v.reason_code), (1, "repro-empty"))
        v = verdict.repro_tests_verdict(repro_changed=["src/a.py"], tests_changed=[], modified_after_repro=["src/a.py"])
        self.assertEqual((v.exit_code, v.reason_code), (1, "repro-outside-tests"))
        v = verdict.repro_tests_verdict(repro_changed=["tests/t.py"], tests_changed=["tests/t.py"],
                                        modified_after_repro=["tests/t.py"])
        self.assertEqual((v.exit_code, v.reason_code), (1, "repro-tests-modified"))
        self.assertIsNone(verdict.repro_tests_verdict(repro_changed=["tests/t.py"],
                                                      tests_changed=["tests/t.py"],
                                                      modified_after_repro=[]))

    def test_evaluate_runs_l9_and_l19(self) -> None:
        # L9 happy path
        v = verdict.evaluate_runs([("base", self._o("red")), ("repro", self._o("red")), ("head", self._o("green"))])
        self.assertEqual((v.result, v.exit_code, v.reason_code), ("ACCEPTED", 0, None))
        # L19 happy path
        v = verdict.evaluate_runs([("repro", self._o("red")), ("head", self._o("green"))])
        self.assertEqual((v.result, v.exit_code, v.reason_code), ("ACCEPTED", 0, None))

        cases = [
            ([("base", self._o("error"))], ("ERROR", 2, "gate-error")),
            ([("base", self._o("green"))], ("REJECTED", 1, "base-green")),
            ([("base", self._o("red-other"))], ("REJECTED", 1, "base-red-wrong-reason")),
            ([("base", self._o("invalid-report"))], ("REJECTED", 1, "base-report-invalid")),
            ([("base", self._o("red")), ("repro", self._o("green"))], ("REJECTED", 1, "repro-green")),
            ([("base", self._o("red")), ("repro", self._o("red-other"))], ("REJECTED", 1, "repro-red-wrong-reason")),
            ([("base", self._o("red")), ("repro", self._o("invalid-report"))], ("REJECTED", 1, "repro-report-invalid")),
            ([("base", self._o("red")), ("repro", self._o("error"))], ("ERROR", 2, "gate-error")),
            ([("repro", self._o("red")), ("head", self._o("red"))], ("REJECTED", 1, "head-not-green")),
            ([("repro", self._o("red")), ("head", self._o("red-other"))], ("REJECTED", 1, "head-not-green")),
            ([("repro", self._o("red")), ("head", self._o("error"))], ("ERROR", 2, "gate-error")),
        ]
        for seq, expected in cases:
            v = verdict.evaluate_runs(seq)
            self.assertEqual((v.result, v.exit_code, v.reason_code), expected)


class TestReportAndInstallation(unittest.TestCase):
    def test_outdir_lock_and_atomic_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            with report.OutDirLock(out):
                self.assertTrue((out / report.LOCK_NAME).exists())
                # Nested lock fails with OutNotEmptyError and does not delete lock
                with self.assertRaises(report.OutNotEmptyError):
                    with report.OutDirLock(out):
                        pass
                self.assertTrue((out / report.LOCK_NAME).exists())
                report.atomic_write_json(out, "accept.json", {"format": 1})
            self.assertFalse((out / report.LOCK_NAME).exists())
            self.assertTrue((out / "accept.json").exists())

            # Non-empty dir rejected
            with self.assertRaises(report.OutNotEmptyError):
                with report.OutDirLock(out):
                    pass

    def test_installation_exit_codes(self) -> None:
        # git ok + witnessbox missing -> exit 0
        with mock.patch.object(cli, "probe_git", return_value={"path": "/usr/bin/git", "version": "2.40", "status": "ok"}), \
             mock.patch.object(cli, "check_witnessbox_installation",
                               return_value=witnessbox.WitnessboxStatus("missing", None, None, "missing")):
            from io import StringIO
            buf = StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli.main(["--installation"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["format"], 1)
            self.assertEqual(data["report_format"], 1)
            self.assertEqual(data["git"]["status"], "ok")
            self.assertEqual(data["witnessbox"]["status"], "missing")
            self.assertEqual(data["witnessbox"]["supported"], ">=0.10.0")

        # git missing -> exit 2
        with mock.patch.object(cli, "probe_git", return_value={"path": None, "version": None, "status": "missing"}), \
             mock.patch.object(cli, "check_witnessbox_installation",
                               return_value=witnessbox.WitnessboxStatus("compatible", "/bin/wb", "0.10.0", "ok")):
            from io import StringIO
            buf = StringIO()
            with mock.patch("sys.stdout", buf):
                rc = cli.main(["--installation"])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
