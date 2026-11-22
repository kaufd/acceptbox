"""Tests for GitHub Actions helper scripts under actions/setup and actions/accept."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = str(ROOT / "src")
PYTHON = sys.executable

VERIFY_RELEASE = ROOT / "actions" / "setup" / "verify_release.py"
RESOLVE_PR = ROOT / "actions" / "accept" / "resolve_pr.py"
FORMAT_REPORT = ROOT / "actions" / "accept" / "format_report.py"


def _clean_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


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


class TestSetupVerifyRelease(unittest.TestCase):
    def test_wheel_and_installation_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            wheel = tmp / "acceptbox-0.3.0-py3-none-any.whl"
            wheel.write_bytes(b"fake wheel payload")
            wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()

            release = tmp / "release.json"
            release.write_text(json.dumps({
                "version": "0.3.0",
                "result": "PASS",
                "wheel": {"file": wheel.name, "sha256": wheel_sha},
                "installation": {
                    "format": 1,
                    "report_format": 1,
                    "distribution": "acceptbox",
                    "version": "0.3.0",
                    "git_status": "ok",
                },
            }), encoding="utf-8")

            ok_w = subprocess.run([PYTHON, "-I", str(VERIFY_RELEASE), "wheel",
                                   str(release), str(wheel)], capture_output=True, text=True)
            self.assertEqual(ok_w.returncode, 0, ok_w.stderr)

            # Tampered wheel fails closed
            wheel.write_bytes(b"tampered")
            bad_w = subprocess.run([PYTHON, "-I", str(VERIFY_RELEASE), "wheel",
                                    str(release), str(wheel)], capture_output=True, text=True)
            self.assertEqual(bad_w.returncode, 1)

            # Valid installation passes
            inst = tmp / "installation.json"
            inst.write_text(json.dumps({
                "format": 1, "report_format": 1, "distribution": "acceptbox",
                "version": "0.3.0", "git": {"status": "ok"},
            }), encoding="utf-8")
            ok_i = subprocess.run([PYTHON, "-I", str(VERIFY_RELEASE), "installation",
                                   str(release), str(inst)], capture_output=True, text=True)
            self.assertEqual(ok_i.returncode, 0, ok_i.stderr)

            # Broken git status fails closed
            inst.write_text(json.dumps({
                "format": 1, "report_format": 1, "distribution": "acceptbox",
                "version": "0.3.0", "git": {"status": "missing"},
            }), encoding="utf-8")
            bad_i = subprocess.run([PYTHON, "-I", str(VERIFY_RELEASE), "installation",
                                    str(release), str(inst)], capture_output=True, text=True)
            self.assertEqual(bad_i.returncode, 1)


class TestResolvePr(unittest.TestCase):
    def test_first_commit_and_trailer_repro_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo = tmp / "repo"
            _init_repo(repo)
            (repo / "a.txt").write_text("0\n", encoding="utf-8")
            base = _commit(repo, "base")
            (repo / "a.txt").write_text("1\n", encoding="utf-8")
            c1 = _commit(repo, "test: repro")
            (repo / "a.txt").write_text("2\n", encoding="utf-8")
            c2 = _commit(repo, "fix: bug")

            # Default first-commit strategy picks c1
            res = subprocess.run(
                [PYTHON, "-I", str(RESOLVE_PR), "--repo", str(repo),
                 "--base", base, "--head", c2, "--base-ref", "main"],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            plan = json.loads(res.stdout)
            self.assertEqual(plan["policy"], "bugfix")
            self.assertEqual(plan["repro"], c1)
            self.assertEqual(plan["repro_source"], "first-commit")

            # trailer-only fails when no trailer exists
            res_missing = subprocess.run(
                [PYTHON, "-I", str(RESOLVE_PR), "--repo", str(repo),
                 "--base", base, "--head", c2, "--repro-strategy", "trailer-only"],
                capture_output=True, text=True,
            )
            self.assertEqual(res_missing.returncode, 2)

            # Commit with AcceptBox-Role: repro trailer wins over first commit
            (repo / "a.txt").write_text("3\n", encoding="utf-8")
            c3 = _commit(repo, "test: real repro\n\nAcceptBox-Role: repro")
            (repo / "a.txt").write_text("4\n", encoding="utf-8")
            c4 = _commit(repo, "fix: real fix")
            res_trailer = subprocess.run(
                [PYTHON, "-I", str(RESOLVE_PR), "--repo", str(repo),
                 "--base", base, "--head", c4, "--repro-strategy", "trailer-only"],
                capture_output=True, text=True,
            )
            self.assertEqual(res_trailer.returncode, 0, res_trailer.stderr)
            plan2 = json.loads(res_trailer.stdout)
            self.assertEqual(plan2["repro"], c3)
            self.assertEqual(plan2["repro_source"], "trailer")

    def test_refactor_detection_and_witnessbox_auto_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repo = tmp / "repo"
            _init_repo(repo)
            (repo / "ebt").mkdir()
            (repo / "ebt" / "spec.toml").write_text('[inv.INV-1]\nstatement = "ok"\n', encoding="utf-8")
            base = _commit(repo, "base")
            _add_origin(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            head = _commit(repo, "refactor\n\nAcceptBox-Inv: INV-1")

            # Wrapper executable invoking acceptbox from src/
            shim = tmp / "acceptbox-shim"
            shim.write_text(
                f"#!/bin/sh\nPYTHONPATH={SRC_DIR} exec {PYTHON} -m acceptbox.cli \"$@\"\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)

            res = subprocess.run(
                [PYTHON, "-I", str(RESOLVE_PR), "--repo", str(repo),
                 "--base", base, "--head", head, "--base-ref", "main",
                 "--pr-labels-json", json.dumps([{"name": "refactor"}]),
                 "--acceptbox-bin", str(shim)],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            plan = json.loads(res.stdout)
            self.assertEqual(plan["policy"], "refactor")
            self.assertIsNone(plan["repro"])
            self.assertEqual(plan["mode"], "witnessbox")
            self.assertEqual(plan["inv"], "INV-1")
            self.assertEqual(plan["ebt_sha"], base)
            self.assertEqual(len(plan["approved_tree"]), 64)


class TestFormatReport(unittest.TestCase):
    def test_strict_refs_guard_downgrades_local_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            report = tmp / "accept.json"
            report.write_text(json.dumps({
                "format": 1, "mode": "command", "policy": "bugfix",
                "result": "ACCEPTED", "exit_code": 0, "reason_code": None,
                "message": "ok",
                "repo": {"base": "a" * 40, "repro": "b" * 40, "head": "c" * 40},
                "checks": {"base_ref": {"status": "ok", "ref_source_kind": "local"}},
                "runs": {},
            }), encoding="utf-8")
            summary = tmp / "summary.md"
            meta = tmp / "meta.json"
            res = subprocess.run(
                [PYTHON, "-I", str(FORMAT_REPORT), "--report", str(report),
                 "--raw-exit-code", "0", "--strict-refs",
                 "--summary-out", str(summary), "--meta-out", str(meta)],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(res.returncode, 0)
            info = json.loads(meta.read_text(encoding="utf-8"))
            self.assertEqual(info["state"], "error")
            self.assertEqual(info["exit_code"], 2)
            self.assertEqual(info["reason_code"], "ci-guard")

    def test_rejected_report_includes_log_tail_and_sticky_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            (tmp / "runs" / "head").mkdir(parents=True)
            (tmp / "runs" / "head" / "command.log").write_text(
                "AssertionError: expected 6, got 9\n", encoding="utf-8"
            )
            report = tmp / "accept.json"
            report.write_text(json.dumps({
                "format": 1, "mode": "command", "policy": "refactor",
                "result": "REJECTED", "exit_code": 1, "reason_code": "head-not-green",
                "message": "head not green",
                "repo": {"base": "a" * 40, "repro": None, "head": "c" * 40},
                "checks": {"base_ref": {"status": "ok", "ref_source_kind": "origin"}},
                "runs": {"head": {"status": "red", "log_path": "runs/head/command.log"}},
            }), encoding="utf-8")
            summary = tmp / "summary.md"
            meta = tmp / "meta.json"
            subprocess.run(
                [PYTHON, "-I", str(FORMAT_REPORT), "--report", str(report),
                 "--raw-exit-code", "1", "--strict-refs",
                 "--summary-out", str(summary), "--meta-out", str(meta)],
                capture_output=True, text=True, check=True,
            )
            info = json.loads(meta.read_text(encoding="utf-8"))
            self.assertEqual(info["state"], "failure")
            self.assertEqual(info["exit_code"], 1)
            md = summary.read_text(encoding="utf-8")
            self.assertIn("<!-- acceptbox-verdict -->", md)
            self.assertIn("AssertionError: expected 6, got 9", md)


if __name__ == "__main__":
    unittest.main()
