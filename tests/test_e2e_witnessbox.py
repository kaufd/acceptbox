"""Сквозные тесты режима witnessbox на реальном witnessbox (>= 0.10.0, L21).

Фикстура самодостаточна: временный git-репозиторий с приложением ``app.py`` и
каталогом ``ebt/`` (``spec.toml``, ``checks.py``, исполняемый ``test.sh``).
Баг воспроизводится на ``base`` и ``repro``, чинится в ``head`` и повторно
вносится в ``mutant`` (для калибровки). Пропусков нет: отсутствующий или
несовместимый witnessbox — ошибка тестов, а не пропуск.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
PYTHON = sys.executable

APP_BUGGY = 'def double(value):\n    # BUG: возвращает value + 100 вместо value * 2\n    return value + 100\n'
APP_FIXED = "def double(value):\n    return value * 2\n"

SPEC_TOML = """# Спецификация e2e-фикстуры acceptbox: result == value * 2.
[meta]
version = "2026-11-20"

[req.REQ-1]
text = "double(value) возвращает value * 2"
source = "acceptbox e2e fixture"

[inv.INV-1]
req = "REQ-1"
statement = "result == value * 2"
checker = "double_rule"
witness = ["value", "result"]
partitions = ["zero", "positive", "negative"]
"""

CHECKS_PY = '''"""Независимый чекер e2e-фикстуры: result == value * 2 (по образцу scaffold)."""
from __future__ import annotations

from witnessbox.api import Verdict, WitnessError


def _int(witness: dict, name: str) -> int:
    if name not in witness:
        raise WitnessError(f"{name}: field is required")
    value = witness[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise WitnessError(f"{name}: expected an integer, got {value!r}")
    return value


def double_rule(witness: dict, inv: dict) -> Verdict:
    """INV-1: result == value * 2; разбиения zero/positive/negative."""
    value = _int(witness, "value")
    result = _int(witness, "result")
    parts = {"zero"} if value == 0 else {"positive"} if value > 0 else {"negative"}
    expected = value * 2
    if result != expected:
        return Verdict(False, f"result={result}, by the rule value * 2 expected {expected}", parts)
    return Verdict(True, "", parts)


CHECKERS = {"double_rule": double_rule}
'''

TEST_SH = r'''#!/bin/bash
# Свидетельства e2e-фикстуры: double(value) по разбиениям zero/positive/negative.
set -uo pipefail
[ -n "${OUT_DIR:-}" ] || { echo "test.sh: OUT_DIR is required" >&2; exit 2; }
python3 - "$OUT_DIR" <<'PYEOF'
import json, os, sys
out_dir = sys.argv[1]
sys.path.insert(0, os.getcwd())
from app import double
records, failed = [], False
for seq, value in enumerate((0, 7, -3), start=1):
    result = double(value)
    ok = result == value * 2
    failed = failed or not ok
    records.append({"inv": "INV-1", "req": "REQ-1", "scope": "e2e", "seq": seq,
                    "ok": ok, "data": {"value": value, "result": result}})
with open(os.path.join(out_dir, "evidence.jsonl"), "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps(record) + "\n")
sys.exit(1 if failed else 0)
PYEOF
'''


def _clean_git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], env=_clean_git_env(),
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    existing = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else SRC_DIR
    return subprocess.run([PYTHON, "-m", "acceptbox.cli", *args],
                          env=run_env, capture_output=True, text=True)


class E2EWitnessboxReal(unittest.TestCase):
    """Живой witnessbox gate: pin, accept (ACCEPTED и base-green), calibrate."""

    @classmethod
    def setUpClass(cls) -> None:
        # Реальный witnessbox обязателен; пропусков нет (L21: >= 0.10.0 без верхней границы).
        try:
            installation = subprocess.run(["witnessbox", "--installation"],
                                          capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise AssertionError(
                "e2e-тестам нужен установленный witnessbox >= 0.10.0 (uv tool install witnessbox)"
            ) from error
        info = json.loads(installation.stdout)
        parsed = tuple(int(part) for part in str(info["version"]).split(".")[:3])
        if parsed < (0, 10, 0):
            raise AssertionError(
                f"witnessbox {info['version']} ниже поддерживаемой версии >=0.10.0 (L21)")
        cls.witnessbox_version = str(info["version"])

        cls._temp = tempfile.TemporaryDirectory(prefix="acceptbox-e2e-")
        cls.addClassCleanup(cls._temp.cleanup)
        cls.root = Path(cls._temp.name)

        repo = cls.root / "repo"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test")

        # base: баг в приложении + утверждённый ebt/ на main.
        (repo / "app.py").write_text(APP_BUGGY, encoding="utf-8")
        (repo / "ebt").mkdir()
        (repo / "ebt" / "spec.toml").write_text(SPEC_TOML, encoding="utf-8")
        (repo / "ebt" / "checks.py").write_text(CHECKS_PY, encoding="utf-8")
        (repo / "ebt" / "test.sh").write_text(TEST_SH, encoding="utf-8")
        os.chmod(repo / "ebt" / "test.sh", 0o755)
        cls.base = _commit(repo, "base: buggy app, approved ebt")

        # repro: потомок base, всё ещё баг для INV-1, ebt/ не тронут.
        (repo / "app.py").write_text(APP_BUGGY + "\n# repro: без функциональных изменений\n",
                                     encoding="utf-8")
        cls.repro = _commit(repo, "repro: bug still present")

        # head: чинит баг — гейт зелёный.
        (repo / "app.py").write_text(APP_FIXED, encoding="utf-8")
        cls.head = _commit(repo, "head: fix double")

        # mutant: потомок head, снова вносит баг (для калибровки).
        (repo / "app.py").write_text(APP_BUGGY, encoding="utf-8")
        cls.mutant = _commit(repo, "mutant: bug reintroduced")

        # Цепочка сфабрикованного репро: зелёная base, красный repro, зелёный head.
        _git(repo, "checkout", "-q", "-b", "fabricated", cls.base)
        (repo / "app.py").write_text(APP_FIXED, encoding="utf-8")
        cls.fab_base = _commit(repo, "fabricated base: app already fixed")
        (repo / "app.py").write_text(APP_BUGGY, encoding="utf-8")
        cls.fab_repro = _commit(repo, "fabricated repro: bug claimed")
        (repo / "app.py").write_text(APP_FIXED, encoding="utf-8")
        cls.fab_head = _commit(repo, "fabricated head: fixed again")
        _git(repo, "checkout", "-q", "main")

        cls.repo = repo
        cls.head_tree = _git(repo, "rev-parse", f"{cls.head}^{{tree}}")
        pin = _run_cli(["pin", "--ebt-repo", str(repo), "--ebt-sha", cls.base])
        assert pin.returncode == 0, pin.stderr
        cls.approved = pin.stdout.strip()

    def _accept(self, base: str, repro: str, head: str, out: Path) -> subprocess.CompletedProcess[str]:
        return _run_cli([
            "accept", "--mode", "witnessbox", "--repo", str(self.repo),
            "--base", base, "--repro", repro, "--head", head,
            "--inv", "INV-1", "--ebt-repo", str(self.repo), "--ebt-ref", "main",
            "--ebt-sha", self.base, "--approved-tree", self.approved,
            "--out", str(out),
        ])

    # ── 1. pin ───────────────────────────────────────────────────────

    def test_pin_computes_tree_sha256_of_ebt(self) -> None:
        local = _run_cli(["pin", "--ebt-repo", str(self.repo), "--ebt-sha", self.base])
        self.assertEqual(local.returncode, 0, local.stderr)
        tree = local.stdout.strip()
        self.assertRegex(tree, r"^[0-9a-f]{64}$")
        remote = _run_cli(["pin", "--ebt-repo", f"file://{self.repo}", "--ebt-sha", self.base])
        self.assertEqual(remote.returncode, 0, remote.stderr)
        self.assertEqual(remote.stdout.strip(), tree)

    # ── 2. Полная цепочка: ACCEPTED ─────────────────────────────────

    def test_accept_full_chain_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.base, self.repro, self.head, out)
            message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            self.assertEqual(proc.returncode, 0, message)

            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["format"], 1)
            self.assertEqual(data["result"], "ACCEPTED")
            self.assertEqual(data["exit_code"], 0)
            self.assertIsNone(data["reason_code"])
            self.assertEqual(data["mode"], "witnessbox")
            self.assertEqual(data["scope"], "ebt-only")
            self.assertEqual(data["witnessbox"]["version"], self.witnessbox_version)
            self.assertEqual(data["repo"]["source"], str(self.repo))
            self.assertEqual(data["repo"]["base"], self.base)
            self.assertEqual(data["repo"]["repro"], self.repro)
            self.assertEqual(data["repo"]["head"], self.head)
            self.assertEqual(data["repo"]["head_tree"], self.head_tree)

            # Доверие EBT (L10): дерево совпадает с pin, spec.toml захеширован.
            self.assertEqual(data["checks"]["ebt_trust"]["status"], "ok")
            self.assertEqual(data["checks"]["ebt_trust"]["actual_tree_sha256"], self.approved)
            self.assertEqual(data["inputs"]["ebt"]["approved_tree_sha256"], self.approved)
            self.assertEqual(data["inputs"]["ebt"]["actual_tree_sha256"], self.approved)
            self.assertRegex(data["inputs"]["ebt"]["spec_sha256"], r"^[0-9a-f]{64}$")

            # Прогоны: base красный, repro красный, head зелёный.
            statuses = {"base": ("red", 1), "repro": ("red", 1), "head": ("green", 0)}
            for name, (status, exit_code) in statuses.items():
                run = data["runs"][name]
                self.assertEqual(run["commit"], getattr(self, name))
                self.assertEqual(run["status"], status, f"{name}: {run}")
                self.assertEqual(run["exit_code"], exit_code)
                self.assertIsNotNone(run["duration_ms"])

            # Охват: INV-1 подтверждён находками красных прогонов.
            self.assertEqual(data["coverage"]["checked"], ["INV-1"])
            self.assertEqual(data["coverage"]["unchecked"], [])

            # Артефакты: sha256 совпадает с фактическими файлами на диске.
            for name in ("base", "repro", "head"):
                artifacts = data["runs"][name]["artifacts"]
                for key in ("gate.json", "report.json"):
                    path = out / artifacts[key]["path"]
                    self.assertTrue(path.is_file(), f"{name}/{key}")
                    self.assertEqual(artifacts[key]["sha256"],
                                     hashlib.sha256(path.read_bytes()).hexdigest())

            # Гейт реального witnessbox оставил и собственные артефакты прогона.
            self.assertTrue((out / "runs" / "head" / "gate.md").is_file())
            self.assertTrue((out / "runs" / "repro" / "report.md").is_file())

    # ── 3. Сфабрикованное репро: base зелёный ────────────────────────

    def test_accept_fabricated_repro_base_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.fab_base, self.fab_repro, self.fab_head, out)
            message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            self.assertEqual(proc.returncode, 1, message)
            data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "REJECTED")
            self.assertEqual(data["exit_code"], 1)
            self.assertEqual(data["reason_code"], "base-green")
            self.assertEqual(data["runs"]["base"]["status"], "green")
            self.assertEqual(data["runs"]["repro"]["status"], "skipped")
            self.assertEqual(data["runs"]["head"]["status"], "skipped")

    # ── 4. Калибровка: head зелёный, mutant красный ─────────────────

    def test_calibrate_head_green_mutant_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = _run_cli([
                "calibrate", "--mode", "witnessbox", "--repo", str(self.repo),
                "--base", self.base, "--green", self.head, "--red", self.mutant,
                "--inv", "INV-1", "--ebt-repo", str(self.repo), "--ebt-ref", "main",
                "--ebt-sha", self.base, "--approved-tree", self.approved,
                "--out", str(out),
            ])
            message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
            self.assertEqual(proc.returncode, 0, message)

            data = json.loads((out / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["format"], 1)
            self.assertEqual(data["result"], "CALIBRATED")
            self.assertEqual(data["exit_code"], 0)
            self.assertIsNone(data["reason_code"])
            self.assertEqual(data["mode"], "witnessbox")
            self.assertEqual(data["witnessbox"]["version"], self.witnessbox_version)
            self.assertEqual(data["repo"]["base"], self.base)

            self.assertEqual(len(data["results"]), 2)
            green, red = data["results"]
            self.assertEqual(green["expected"], "green")
            self.assertEqual(green["commit"], self.head)
            self.assertEqual(green["status"], "green")
            self.assertTrue(green["matched"])
            self.assertEqual(red["expected"], "red")
            self.assertEqual(red["commit"], self.mutant)
            self.assertEqual(red["status"], "red")
            self.assertTrue(red["matched"])

            # Калибровка никогда не создаёт accept.json.
            self.assertFalse((out / "accept.json").exists())
            # Артефакты прогонов на диске соответствуют отчёту.
            for item in (green, red):
                gate_file = out / item["artifacts"]["gate.json"]["path"]
                self.assertTrue(gate_file.is_file())
                self.assertEqual(item["artifacts"]["gate.json"]["sha256"],
                                 hashlib.sha256(gate_file.read_bytes()).hexdigest())

    # ── 5. Авто-резолв локального one-liner без --base/--repro/--inv/--ebt-sha ──

    def test_accept_one_liner_without_inv_or_explicit_shas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            # В фикстуре main стоит на self.base, а ветка fix — на self.head.
            _git(self.repo, "checkout", "-q", "-b", "fix-oneliner", self.head)
            _git(self.repo, "update-ref", "refs/heads/main", self.base)
            try:
                proc = _run_cli([
                    "accept", "--mode", "witnessbox", "--repo", str(self.repo),
                    "--base-ref", "main", "--ebt-ref", "main", "--pin-from-ref",
                    "--out", str(out),
                ])
                message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
                self.assertEqual(proc.returncode, 0, message)
                data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
                self.assertEqual(data["result"], "ACCEPTED")
                self.assertEqual(data["repo"]["base"], self.base)
                self.assertEqual(data["repo"]["repro"], self.repro)
                self.assertEqual(data["repo"]["head"], self.head)
                self.assertEqual(data["coverage"]["checked"], ["INV-1"])
                self.assertTrue(data["inputs"]["ebt"]["pinned_from_ref"])
            finally:
                _git(self.repo, "checkout", "-q", "main")
                _git(self.repo, "update-ref", "refs/heads/main", self.head)
                _git(self.repo, "branch", "-q", "-D", "fix-oneliner")


if __name__ == "__main__":
    unittest.main()
