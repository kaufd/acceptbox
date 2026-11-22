"""Тесты для acceptbox render (L32): Markdown/HTML рендеринг, экранирование, fail-closed и CLI."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from acceptbox import render  # noqa: E402

PYTHON = sys.executable

SHA_BASE = "1" * 40
SHA_REPRO = "2" * 40
SHA_HEAD = "3" * 40
SHA_TREE = "4" * 40
SHA_EBT = "5" * 40
HEX64_A = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64


def _md_visible(markdown: str) -> str:
    """Text a Markdown reader sees: backslash escapes removed."""
    return re.sub(r"\\(.)", r"\1", markdown)


def _run_cli(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run(
        [PYTHON, "-m", "acceptbox.cli", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_real_accept_report(root: Path) -> Path:
    """Run a real `acceptbox accept` (command mode, local repo without origin) and return accept.json."""
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    check = "import sys; sys.exit(0 if open('app.txt').read().strip() == 'fixed' else 1)"
    (repo / ".acceptbox.toml").write_text(
        f'format = 1\n[command]\ntests = ["tests/"]\nargv = [{json.dumps(PYTHON)}, "-c", {json.dumps(check)}]\n',
        encoding="utf-8")
    (repo / "app.txt").write_text("broken\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "fix")
    (repo / "tests").mkdir()
    (repo / "tests" / "repro.txt").write_text("repro\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "repro")
    (repo / "app.txt").write_text("fixed\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "fix")
    out = root / "accept-out"
    res = _run_cli(["accept", "--base-ref", "main", "--out", str(out)], cwd=repo)
    assert res.returncode == 0, res.stderr
    return out / "accept.json"


def _accept_bugfix_payload() -> dict:
    return {
        "format": 1,
        "run_id": "11111111-2222-3333-4444-555555555555",
        "started_at": "2026-11-22T10:00:00+00:00",
        "finished_at": "2026-11-22T10:00:02+00:00",
        "acceptbox_version": "0.4.0",
        "mode": "command",
        "policy": "bugfix",
        "scope": "command-only",
        "witnessbox": None,
        "repo": {
            "source": "/work/demo-repo",
            "base": SHA_BASE,
            "repro": SHA_REPRO,
            "head": SHA_HEAD,
            "head_tree": SHA_TREE,
        },
        "inputs": {
            "timeout_sec": 600.0,
            "protect": ["pyproject.toml"],
            "env_keys": ["PYTHONPATH"],
            "config": {
                "path": ".acceptbox.toml",
                "commit": SHA_BASE,
                "sha256": HEX64_A,
            },
            "base_ref": "main",
            "strict_refs": True,
            "repro_source": "trailer",
            "command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
            "tests": ["tests/"],
        },
        "checks": {
            "base_ref": {
                "status": "ok",
                "ref": "main",
                "ref_source": "https://example.invalid/demo.git",
                "ref_source_kind": "origin",
                "ref_commit": SHA_BASE,
            },
            "ebt_trust": {"status": "skipped"},
            "ancestry": {
                "status": "ok",
                "base_ancestor_of_repro": True,
                "repro_ancestor_of_head": True,
            },
            "protected_paths": {
                "status": "ok",
                "specs": ["pyproject.toml", ".acceptbox.toml"],
                "changed_base_repro": [],
                "changed_base_head": [],
            },
            "repro_tests": {
                "status": "ok",
                "changed_base_repro": ["tests/test_bug.py"],
                "outside_tests": [],
                "modified_after_repro": [],
            },
        },
        "runs": {
            "base": {
                "commit": SHA_BASE,
                "status": "skipped",
                "exit_code": None,
                "reason": "в режиме command базовый коммит не прогоняется",
                "duration_ms": None,
                "log_path": None,
                "artifacts": None,
            },
            "repro": {
                "commit": SHA_REPRO,
                "status": "red",
                "exit_code": 1,
                "reason": "команда завершилась с кодом 1",
                "duration_ms": 142,
                "log_path": "runs/repro/output.log",
                "artifacts": None,
            },
            "head": {
                "commit": SHA_HEAD,
                "status": "green",
                "exit_code": 0,
                "reason": "команда завершилась с кодом 0",
                "duration_ms": 130,
                "log_path": "runs/head/output.log",
                "artifacts": None,
            },
        },
        "result": "ACCEPTED",
        "exit_code": 0,
        "reason_code": None,
        "message": "все условия приёмки выполнены",
        "coverage": {"checked": [], "skipped": [], "unchecked": []},
        "warnings": [],
    }


def _accept_refactor_with_warnings_payload() -> dict:
    return {
        "format": 1,
        "run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "started_at": "2026-11-22T11:00:00+00:00",
        "finished_at": "2026-11-22T11:00:03+00:00",
        "acceptbox_version": "0.4.0",
        "mode": "command",
        "policy": "refactor",
        "scope": "command-only",
        "witnessbox": None,
        "repo": {
            "source": "/work/local-repo",
            "base": SHA_BASE,
            "repro": None,
            "head": SHA_HEAD,
            "head_tree": SHA_TREE,
        },
        "inputs": {
            "timeout_sec": 300.0,
            "protect": [],
            "env_keys": [],
            "config": {
                "path": "/external/acceptbox.toml",
                "commit": None,
                "sha256": HEX64_B,
            },
            "base_ref": "main",
            "strict_refs": False,
            "repro_source": None,
            "command": ["pytest", "-q"],
            "tests": ["tests/"],
        },
        "checks": {
            "base_ref": {
                "status": "ok",
                "ref": "main",
                "ref_source": "/work/local-repo",
                "ref_source_kind": "local",
                "ref_commit": SHA_BASE,
                "resolved_base_auto": True,
            },
            "ebt_trust": {"status": "skipped"},
            "ancestry": {"status": "ok", "base_ancestor_of_head": True},
            "protected_paths": {
                "status": "ok",
                "specs": [".acceptbox.toml", "tests/"],
                "changed_base_repro": [],
                "changed_base_head": [],
            },
            "repro_tests": {"status": "skipped", "reason": "policy=refactor"},
            "refactor": {
                "status": "ok",
                "changed_base_head": ["src/service.py", "src/helper.py"],
            },
        },
        "runs": {
            "base": {
                "commit": SHA_BASE,
                "status": "green",
                "exit_code": 0,
                "reason": "команда завершилась с кодом 0",
                "duration_ms": 210,
                "log_path": "runs/base/output.log",
                "artifacts": None,
            },
            "repro": {
                "commit": None,
                "status": "skipped",
                "exit_code": None,
                "reason": "в политике refactor репро-коммит не используется",
                "duration_ms": None,
                "log_path": None,
                "artifacts": None,
            },
            "head": {
                "commit": SHA_HEAD,
                "status": "green",
                "exit_code": 0,
                "reason": "команда завершилась с кодом 0",
                "duration_ms": 195,
                "log_path": "runs/head/output.log",
                "artifacts": None,
            },
        },
        "result": "ACCEPTED",
        "exit_code": 0,
        "reason_code": None,
        "message": "регрессий по существующим проверкам нет; сохранение поведения не проверяется (policy=refactor)",
        "coverage": {"checked": [], "skipped": [], "unchecked": []},
        "warnings": [
            "--base-ref: ветка взята из локального репозитория без origin (ref_source_kind=local) — слабая проверка",
        ],
    }


def _accept_rejected_diffs_payload() -> dict:
    payload = _accept_bugfix_payload()
    payload["result"] = "REJECTED"
    payload["exit_code"] = 1
    payload["reason_code"] = "protected-paths-changed"
    payload["message"] = "изменены защищённые пути: .acceptbox.toml, pyproject.toml"
    payload["checks"]["protected_paths"] = {
        "status": "failed",
        "specs": ["pyproject.toml", ".acceptbox.toml"],
        "changed_base_repro": [".acceptbox.toml"],
        "changed_base_head": [".acceptbox.toml", "pyproject.toml"],
    }
    payload["checks"]["repro_tests"] = {
        "status": "failed",
        "changed_base_repro": ["tests/test_bug.py", "src/sneaky.py"],
        "outside_tests": ["src/sneaky.py"],
        "modified_after_repro": ["tests/test_bug.py"],
    }
    return payload


def _accept_config_invalid_payload() -> dict:
    return {
        "format": 1,
        "run_id": "22222222-3333-4444-5555-666666666666",
        "started_at": "2026-11-22T12:00:00+00:00",
        "finished_at": "2026-11-22T12:00:01+00:00",
        "acceptbox_version": "0.4.0",
        "mode": "command",
        "policy": "bugfix",
        "scope": "command-only",
        "witnessbox": None,
        "repo": {
            "source": "/work/bad-config-repo",
            "base": SHA_BASE,
            "repro": SHA_REPRO,
            "head": SHA_HEAD,
            "head_tree": None,
        },
        "inputs": {
            "timeout_sec": 600.0,
            "protect": [],
            "env_keys": [],
            "config": {
                "path": ".acceptbox.toml",
                "commit": SHA_BASE,
                "sha256": HEX64_C,
            },
            "base_ref": None,
            "strict_refs": False,
            "repro_source": "explicit",
            "command": None,
            "tests": None,
        },
        "checks": {
            "base_ref": {"status": "skipped"},
            "ebt_trust": {"status": "skipped"},
            "ancestry": {"status": "not-run"},
            "protected_paths": {"status": "not-run"},
            "repro_tests": {"status": "not-run"},
        },
        "runs": {
            "base": {"commit": SHA_BASE, "status": "skipped", "exit_code": None, "reason": "прогон не выполнялся", "duration_ms": None, "log_path": None, "artifacts": None},
            "repro": {"commit": SHA_REPRO, "status": "skipped", "exit_code": None, "reason": "прогон не выполнялся", "duration_ms": None, "log_path": None, "artifacts": None},
            "head": {"commit": SHA_HEAD, "status": "skipped", "exit_code": None, "reason": "прогон не выполнялся", "duration_ms": None, "log_path": None, "artifacts": None},
        },
        "result": "ERROR",
        "exit_code": 2,
        "reason_code": "config-invalid",
        "message": ".acceptbox.toml (верхний уровень): неизвестные ключи: bad_key",
        "coverage": {"checked": [], "skipped": [], "unchecked": []},
        "warnings": [],
    }


def _accept_witnessbox_pin_from_ref_payload() -> dict:
    return {
        "format": 1,
        "run_id": "33333333-4444-5555-6666-777777777777",
        "started_at": "2026-11-22T12:10:00+00:00",
        "finished_at": "2026-11-22T12:10:08+00:00",
        "acceptbox_version": "0.4.0",
        "mode": "witnessbox",
        "policy": "bugfix",
        "scope": "ebt-only",
        "witnessbox": {
            "version": "0.10.2",
            "gate_sha256": HEX64_A,
            "checker_sha256": HEX64_B,
        },
        "repo": {
            "source": "/work/ebt-repo",
            "base": SHA_BASE,
            "repro": SHA_REPRO,
            "head": SHA_HEAD,
            "head_tree": SHA_TREE,
        },
        "inputs": {
            "timeout_sec": 600.0,
            "protect": [],
            "env_keys": ["BASE"],
            "config": {
                "path": ".acceptbox.toml",
                "commit": SHA_BASE,
                "sha256": HEX64_C,
            },
            "base_ref": "main",
            "strict_refs": False,
            "repro_source": "first-commit",
            "inv": ["INV-1", "INV-2"],
            "ebt": {
                "repo": "/work/ebt-repo",
                "ref": "main",
                "sha": SHA_EBT,
                "approved_tree_sha256": None,
                "pinned_from_ref": True,
                "ref_source": "https://example.invalid/ebt.git",
                "ref_source_kind": "origin",
                "actual_tree_sha256": HEX64_D,
                "spec_sha256": HEX64_A,
            },
        },
        "checks": {
            "base_ref": {
                "status": "ok",
                "ref": "main",
                "ref_source": "https://example.invalid/ebt.git",
                "ref_source_kind": "origin",
                "ref_commit": SHA_BASE,
            },
            "ebt_trust": {
                "status": "ok",
                "actual_tree_sha256": HEX64_D,
                "spec_sha256": HEX64_A,
                "ref_source": "https://example.invalid/ebt.git",
                "ref_source_kind": "origin",
                "pinned_from_ref": True,
            },
            "ancestry": {
                "status": "ok",
                "base_ancestor_of_repro": True,
                "repro_ancestor_of_head": True,
            },
            "protected_paths": {
                "status": "ok",
                "specs": [".acceptbox.toml", "ebt", "tool/ebt_check"],
                "changed_base_repro": [],
                "changed_base_head": [],
            },
            "repro_tests": {"status": "skipped"},
        },
        "runs": {
            "base": {
                "commit": SHA_BASE,
                "status": "red",
                "exit_code": 1,
                "reason": "подтверждено нарушение INV-1",
                "duration_ms": 510,
                "log_path": "runs/base/witnessbox.log",
                "artifacts": None,
            },
            "repro": {
                "commit": SHA_REPRO,
                "status": "red",
                "exit_code": 1,
                "reason": "подтверждено нарушение INV-1",
                "duration_ms": 490,
                "log_path": "runs/repro/witnessbox.log",
                "artifacts": None,
            },
            "head": {
                "commit": SHA_HEAD,
                "status": "green",
                "exit_code": 0,
                "reason": "все проверки пройдены",
                "duration_ms": 480,
                "log_path": "runs/head/witnessbox.log",
                "artifacts": None,
            },
        },
        "result": "ACCEPTED",
        "exit_code": 0,
        "reason_code": None,
        "message": "все условия приёмки выполнены",
        "coverage": {
            "checked": ["INV-1"],
            "skipped": ["INV-3"],
            "unchecked": ["INV-2"],
        },
        "warnings": [
            f"ebt/ закреплён по вершине --ebt-ref 'main' ({SHA_EBT[:12]}), а не по одобренному --approved-tree (--pin-from-ref)",
            f"repro выбран автоматически как первый коммит base..head ({SHA_REPRO[:12]})",
        ],
    }


def _calibrate_payload(*, matched_second: bool = True) -> dict:
    return {
        "format": 1,
        "run_id": "44444444-5555-6666-7777-888888888888",
        "started_at": "2026-11-22T12:20:00+00:00",
        "finished_at": "2026-11-22T12:20:05+00:00",
        "acceptbox_version": "0.4.0",
        "mode": "command",
        "scope": "command-only",
        "witnessbox": None,
        "repo": {"source": "/work/cal-repo", "base": SHA_BASE},
        "inputs": {
            "timeout_sec": 600.0,
            "env_keys": [],
            "config": {
                "path": ".acceptbox.toml",
                "commit": SHA_BASE,
                "sha256": HEX64_A,
            },
            "command": ["python3", "-m", "unittest"],
        },
        "results": [
            {
                "commit": SHA_HEAD,
                "expected": "green",
                "status": "green",
                "matched": True,
                "exit_code": 0,
                "reason": "команда завершилась с кодом 0",
                "duration_ms": 115,
                "log_path": "runs/green-1/output.log",
                "artifacts": None,
            },
            {
                "commit": SHA_REPRO,
                "expected": "red",
                "status": "red" if matched_second else "green",
                "matched": matched_second,
                "exit_code": 1 if matched_second else 0,
                "reason": "команда завершилась с кодом 1" if matched_second else "команда завершилась с кодом 0",
                "duration_ms": 120,
                "log_path": "runs/red-2/output.log",
                "artifacts": None,
            },
        ],
        "result": "CALIBRATED" if matched_second else "FAILED",
        "exit_code": 0 if matched_second else 1,
        "reason_code": None if matched_second else "mismatch",
        "message": "все коммиты дали ожидаемые исходы" if matched_second else f"исход не совпал: {SHA_REPRO} (red → green)",
    }


def _count_unescaped_pipes(line: str) -> int:
    """Посчитать число неэкранированных `|` в строке Markdown-таблицы."""
    count = 0
    backslashes = 0
    for ch in line:
        if ch == "\\":
            backslashes += 1
        elif ch == "|":
            if backslashes % 2 == 0:
                count += 1
            backslashes = 0
        else:
            backslashes = 0
    return count


def _assert_valid_markdown_tables(testcase: unittest.TestCase, markdown: str) -> None:
    """Проверить, что каждая Markdown-таблица имеет согласованное число столбцов без обрывов."""
    lines = markdown.splitlines()
    idx = 0
    table_count = 0
    while idx < len(lines):
        if lines[idx].startswith("| "):
            table_count += 1
            header_pipes = _count_unescaped_pipes(lines[idx])
            testcase.assertGreaterEqual(header_pipes, 3, f"bad header row: {lines[idx]!r}")
            testcase.assertTrue(lines[idx].endswith(" |"), f"unterminated row: {lines[idx]!r}")
            idx += 1
            testcase.assertLess(idx, len(lines), "missing separator row")
            testcase.assertEqual(_count_unescaped_pipes(lines[idx]), header_pipes)
            idx += 1
            while idx < len(lines) and lines[idx].strip():
                row = lines[idx]
                testcase.assertTrue(row.startswith("| ") and row.endswith(" |"),
                                    f"broken table row: {row!r}")
                testcase.assertEqual(
                    _count_unescaped_pipes(row),
                    header_pipes,
                    f"column mismatch in row: {row!r}",
                )
                idx += 1
        else:
            idx += 1
    testcase.assertGreater(table_count, 0, "expected at least one Markdown table")


class TestRenderCore(unittest.TestCase):
    def test_accept_bugfix_markdown_and_html(self) -> None:
        payload = _accept_bugfix_payload()
        md = render.render_markdown(payload)
        html_doc = render.render_html(payload)

        _assert_valid_markdown_tables(self, md)
        self.assertIn("# AcceptBox: ACCEPTED [policy=bugfix]", md)
        self.assertIn("`command`", md)
        self.assertIn("все условия приёмки выполнены", md)
        self.assertIn(SHA_BASE, md)
        self.assertIn(SHA_REPRO, md)
        self.assertIn(SHA_HEAD, md)
        self.assertIn(SHA_TREE, md)
        self.assertIn(HEX64_A, md)
        self.assertIn("`trailer`", md)
        self.assertIn("runs/repro/output.log", md)
        self.assertIn("runs/head/output.log", md)
        self.assertNotIn("## Warnings", md)

        self.assertTrue(html_doc.startswith("<!doctype html>\n"))
        self.assertIn("ACCEPTED", html_doc)
        self.assertIn("[policy=bugfix]", html_doc)
        self.assertIn("mode=command", html_doc)
        self.assertIn("все условия приёмки выполнены", html_doc)
        self.assertIn(SHA_TREE, html_doc)
        self.assertIn("runs/head/output.log", html_doc)
        self.assertNotIn("<script", html_doc.lower())
        self.assertNotIn("<link", html_doc.lower())
        self.assertNotIn("@import", html_doc.lower())

    def test_accept_refactor_with_warnings_prominent_near_top(self) -> None:
        payload = _accept_refactor_with_warnings_payload()
        md = render.render_markdown(payload)
        html_doc = render.render_html(payload)

        _assert_valid_markdown_tables(self, md)
        warning_text = payload["warnings"][0]
        self.assertIn("[policy=refactor]", md)
        visible = _md_visible(md)
        self.assertIn(payload["message"], visible)
        self.assertIn("## Warnings", md)
        self.assertIn(warning_text, visible)
        # Warnings appear near the top — before Repository/Checks/Runs sections
        self.assertLess(visible.index(warning_text), visible.index("## Repository & Commits"))
        self.assertIn("`refactor`", md)
        self.assertIn("src/service.py", md)
        self.assertIn("src/helper.py", md)

        self.assertIn("[policy=refactor]", html_doc)
        self.assertIn(warning_text, html_doc)
        self.assertNotIn("<details", html_doc.lower())
        self.assertLess(html_doc.index(warning_text), html_doc.index("Repository &amp; Commits"))
        self.assertIn("src/service.py", html_doc)

    def test_accept_rejected_with_protected_and_repro_test_diffs(self) -> None:
        payload = _accept_rejected_diffs_payload()
        md = render.render_markdown(payload)
        html_doc = render.render_html(payload)

        _assert_valid_markdown_tables(self, md)
        self.assertIn("REJECTED", md)
        self.assertIn("protected-paths-changed", md)
        self.assertIn(".acceptbox.toml", md)
        self.assertIn("pyproject.toml", md)
        self.assertIn("src/sneaky.py", md)
        self.assertIn("tests/test_bug.py", md)

        self.assertIn("banner-fail", html_doc)
        self.assertIn("protected-paths-changed", html_doc)
        self.assertIn("src/sneaky.py", html_doc)
        self.assertIn("tests/test_bug.py", html_doc)

    def test_accept_error_config_invalid(self) -> None:
        payload = _accept_config_invalid_payload()
        md = render.render_markdown(payload)
        html_doc = render.render_html(payload)

        _assert_valid_markdown_tables(self, md)
        self.assertIn("ERROR", md)
        self.assertIn("config-invalid", md)
        self.assertIn(HEX64_C, md)
        self.assertIn("неизвестные ключи: bad_key", _md_visible(md))

        self.assertIn("banner-error", html_doc)
        self.assertIn("config-invalid", html_doc)
        self.assertIn(HEX64_C, html_doc)

    def test_accept_witnessbox_pin_from_ref_and_coverage(self) -> None:
        payload = _accept_witnessbox_pin_from_ref_payload()
        md = render.render_markdown(payload)
        html_doc = render.render_html(payload)

        _assert_valid_markdown_tables(self, md)
        for text in (md, html_doc):
            self.assertIn("0.10.2", text)
            self.assertIn(HEX64_A, text)
            self.assertIn(HEX64_B, text)
            self.assertIn(HEX64_D, text)
            self.assertIn("ebt.pinned_from_ref", text)
            self.assertIn("ebt.approved_tree_sha256", text)
            self.assertIn("ebt.actual_tree_sha256", text)
            self.assertIn("ebt.ref_source_kind", text)
            self.assertIn("INV-1", text)
            self.assertIn("INV-2", text)
            self.assertIn("INV-3", text)
        self.assertIn("## WitnessBox", md)
        self.assertIn("## Coverage", md)
        self.assertIn("<h2>WitnessBox</h2>", html_doc)
        self.assertIn("<h2>Coverage</h2>", html_doc)
        # Both warnings appear prominently before Repository section (escaped in HTML)
        for warning in payload["warnings"]:
            escaped_warning = html.escape(warning, quote=True)
            visible = _md_visible(md)
            self.assertIn(warning, visible)
            self.assertIn(escaped_warning, html_doc)
            self.assertLess(visible.index(warning), visible.index("## Repository & Commits"))
            self.assertLess(html_doc.index(escaped_warning), html_doc.index("Repository &amp; Commits"))

    def test_calibrate_calibrated_and_failed(self) -> None:
        ok_payload = _calibrate_payload(matched_second=True)
        fail_payload = _calibrate_payload(matched_second=False)

        md_ok = render.render_markdown(ok_payload)
        html_ok = render.render_html(ok_payload)
        _assert_valid_markdown_tables(self, md_ok)
        self.assertIn("# AcceptBox: CALIBRATED", md_ok)
        self.assertNotIn("[policy=", md_ok)
        self.assertIn("## Results", md_ok)
        self.assertIn("runs/green-1/output.log", md_ok)
        self.assertIn("runs/red-2/output.log", md_ok)
        self.assertIn("banner-ok", html_ok)
        self.assertIn("CALIBRATED", html_ok)

        md_fail = render.render_markdown(fail_payload)
        html_fail = render.render_html(fail_payload)
        _assert_valid_markdown_tables(self, md_fail)
        self.assertIn("# AcceptBox: FAILED (mismatch)", md_fail)
        self.assertIn("`false`", md_fail)
        self.assertIn("banner-fail", html_fail)
        self.assertIn("mismatch", html_fail)

    def test_escaping_html_and_markdown_tables(self) -> None:
        xss = "<script>alert(1)</script> & \"quote\" 'single' | col\nnextline\r\n`tick`"
        payload = _accept_witnessbox_pin_from_ref_payload()
        payload["message"] = f"msg {xss}"
        payload["warnings"] = [f"warn1 {xss}", f"warn2 {xss}"]
        payload["repo"]["source"] = f"/tmp/{xss}"
        payload["inputs"]["config"]["path"] = f"/cfg/{xss}"
        payload["checks"]["protected_paths"]["changed_base_head"] = [f"dir/{xss}"]
        payload["checks"]["repro_tests"] = {
            "status": "failed",
            "changed_base_repro": [f"tests/{xss}"],
            "outside_tests": [f"src/{xss}"],
            "modified_after_repro": [f"tests/mod_{xss}"],
        }
        payload["runs"]["head"]["reason"] = f"reason {xss}"
        payload["runs"]["head"]["log_path"] = f"runs/head/{xss}.log"
        payload["coverage"] = {
            "checked": [f"INV-{xss}"],
            "skipped": [f"SKIP-{xss}"],
            "unchecked": [f"UNCHK-{xss}"],
        }

        html_doc = render.render_html(payload)
        # Zero raw script tags anywhere in HTML
        self.assertIsNone(re.search(r"<script\b", html_doc, re.IGNORECASE))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_doc)
        self.assertIn("&amp;", html_doc)
        self.assertIn("&quot;quote&quot;", html_doc)
        self.assertIn("&#x27;single&#x27;", html_doc)

        md = render.render_markdown(payload)
        _assert_valid_markdown_tables(self, md)
        self.assertIn(r"\|", md)

    def test_markdown_escapes_gfm_specials_in_plain_text(self) -> None:
        tricky = "изменены защищённые пути: src/pkg/__init__.py <module> @team $x$ [l](u) *a*"
        payload = _accept_rejected_diffs_payload()
        payload["message"] = tricky
        payload["warnings"] = [tricky]
        md = render.render_markdown(payload)

        _assert_valid_markdown_tables(self, md)
        for escaped in (r"src/pkg/\_\_init\_\_.py", r"\<module\>", r"\@team", r"\$x\$",
                        r"\[l\](u)", r"\*a\*"):
            self.assertIn(escaped, md)
        self.assertEqual(_md_visible(md).count(tricky), 2)  # message + warning
        # Code spans stay literal: paths in Details/Runs are not backslash-escaped.
        self.assertIn("`src/sneaky.py`", md)

    def test_html_links_run_logs_relative_to_page(self) -> None:
        payload = _accept_bugfix_payload()
        self.assertIn('<a href="runs/head/output.log"><code>runs/head/output.log</code></a>',
                      render.render_html(payload))
        self.assertIn('href="../out/runs/head/output.log"',
                      render.render_html(payload, log_base="../out"))
        for unsafe in ("/abs/run.log", "runs/../../etc/passwd", "javascript:alert(1)"):
            payload["runs"]["head"]["log_path"] = unsafe
            html_doc = render.render_html(payload)
            self.assertIn(f"<code>{html.escape(unsafe)}</code>", html_doc)
            self.assertNotIn(f'href="{html.escape(unsafe)}"', html_doc)

    def test_pure_render_does_not_read_logs_or_spawn_subprocess(self) -> None:
        payload = _accept_bugfix_payload()
        payload["runs"]["head"]["log_path"] = "/nonexistent/should/never/be/opened.log"
        with mock.patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess forbidden")):
            md = render.render_markdown(payload)
            html_doc = render.render_html(payload)
        self.assertIn("/nonexistent/should/never/be/opened.log", md)
        self.assertIn("/nonexistent/should/never/be/opened.log", html_doc)

    def test_fail_closed_validation(self) -> None:
        for bad in (
            None,
            [],
            "str",
            {},
            {"format": 2, "result": "ACCEPTED", "checks": {}, "runs": {}},
            {"format": True, "result": "ACCEPTED", "checks": {}, "runs": {}},
            {"format": 1, "checks": {}, "runs": {}},
            {"format": 1, "result": "", "checks": {}, "runs": {}},
            {"format": 1, "result": "ACCEPTED"},
            {"format": 1, "result": "ACCEPTED", "checks": {}, "runs": {}, "results": []},
            {"format": 1, "result": "ACCEPTED", "checks": [], "runs": {}},
            {"format": 1, "result": "CALIBRATED", "results": ["not-a-dict"]},
        ):
            with self.assertRaises(render.RenderError, msg=f"should reject {bad!r}"):
                render.render_markdown(bad)  # type: ignore[arg-type]
            with self.assertRaises(render.RenderError, msg=f"should reject {bad!r}"):
                render.render_html(bad)  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(render.RenderError):
                render.load_report(root / "missing.json")
            with self.assertRaises(render.RenderError):
                render.load_report(root)  # directory
            bad_json = root / "bad.json"
            bad_json.write_text("{not json", encoding="utf-8")
            with self.assertRaises(render.RenderError):
                render.load_report(bad_json)


class TestRenderCLI(unittest.TestCase):
    def test_cli_render_md_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "accept.json"
            report_path.write_text(json.dumps(_accept_bugfix_payload()), encoding="utf-8")
            res = _run_cli(["render", str(report_path), "--format", "md"])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stderr, "")
            self.assertIn("# AcceptBox: ACCEPTED [policy=bugfix]", res.stdout)
            _assert_valid_markdown_tables(self, res.stdout)

    def test_cli_render_html_atomic_out_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "calibrate.json"
            out_file = root / "report.html"
            report_path.write_text(json.dumps(_calibrate_payload(matched_second=False)), encoding="utf-8")

            res = _run_cli(["render", str(report_path), "--format", "html", "--out", str(out_file)])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout, "")
            self.assertTrue(out_file.is_file())
            content = out_file.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("<!doctype html>\n"))
            self.assertIn("FAILED", content)
            self.assertIn("mismatch", content)
            # No leftover temp files in directory
            leftovers = [p.name for p in root.iterdir() if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_cli_render_invalid_or_missing_report_returns_2_without_creating_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_file = root / "should_not_exist.html"

            # Missing report
            res_missing = _run_cli([
                "render", str(root / "missing.json"), "--format", "html", "--out", str(out_file),
            ])
            self.assertEqual(res_missing.returncode, 2)
            self.assertTrue(res_missing.stderr.strip())
            self.assertFalse(out_file.exists())

            # Invalid JSON
            broken = root / "broken.json"
            broken.write_text("{broken", encoding="utf-8")
            res_broken = _run_cli([
                "render", str(broken), "--format", "md", "--out", str(out_file),
            ])
            self.assertEqual(res_broken.returncode, 2)
            self.assertTrue(res_broken.stderr.strip())
            self.assertFalse(out_file.exists())

            # Unsupported format != 1
            wrong_fmt = root / "v2.json"
            payload = _accept_bugfix_payload()
            payload["format"] = 2
            wrong_fmt.write_text(json.dumps(payload), encoding="utf-8")
            res_fmt = _run_cli([
                "render", str(wrong_fmt), "--format", "html", "--out", str(out_file),
            ])
            self.assertEqual(res_fmt.returncode, 2)
            self.assertTrue(res_fmt.stderr.strip())
            self.assertFalse(out_file.exists())

    def test_cli_render_refuses_to_overwrite_source_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "accept.json"
            original = json.dumps(_accept_bugfix_payload())
            report_path.write_text(original, encoding="utf-8")
            res = _run_cli(["render", str(report_path), "--format", "md", "--out", str(report_path)])
            self.assertEqual(res.returncode, 2)
            self.assertIn("совпадает с исходным отчётом", res.stderr)
            self.assertEqual(report_path.read_text(encoding="utf-8"), original)

    def test_cli_renders_real_accept_report_with_working_log_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = _make_real_accept_report(root)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["warnings"])

            res_md = _run_cli(["render", str(report_path), "--format", "md"])
            self.assertEqual(res_md.returncode, 0, res_md.stderr)
            _assert_valid_markdown_tables(self, res_md.stdout)
            self.assertIn("# AcceptBox: ACCEPTED [policy=bugfix]", res_md.stdout)
            for warning in report["warnings"]:
                self.assertIn(warning, _md_visible(res_md.stdout))

            # The page lives elsewhere: links must still reach the run logs.
            out_file = root / "pages" / "report.html"
            out_file.parent.mkdir()
            res_html = _run_cli(["render", str(report_path), "--format", "html", "--out", str(out_file)])
            self.assertEqual(res_html.returncode, 0, res_html.stderr)
            hrefs = re.findall(r'href="([^"]+)"', out_file.read_text(encoding="utf-8"))
            self.assertEqual(len(hrefs), 2)  # repro and head runs
            for href in hrefs:
                self.assertTrue((out_file.parent / html.unescape(href)).is_file(), href)

    def test_cli_render_rejects_missing_parent_and_extra_args_after_dashdash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "accept.json"
            report_path.write_text(json.dumps(_accept_bugfix_payload()), encoding="utf-8")

            # Missing parent directory
            res_parent = _run_cli([
                "render", str(report_path), "--format", "html",
                "--out", str(root / "no-such-dir" / "out.html"),
            ])
            self.assertEqual(res_parent.returncode, 2)
            self.assertTrue(res_parent.stderr.strip())

            # Extra args after --
            res_dash = _run_cli([
                "render", str(report_path), "--format", "md", "--", "extra",
            ])
            self.assertEqual(res_dash.returncode, 2)
            self.assertTrue(res_dash.stderr.strip())


if __name__ == "__main__":
    unittest.main()
