"""Негативные пробы режима witnessbox (L9, L10, L17, L21) без реального witnessbox.

Фикстуры самодостаточны: временные git-репозитории и фейковый ``witnessbox``
(исполняемый скрипт в отдельном каталоге, первым в ``PATH``; поведение гейта
задаётся файлом ``wb_state`` в прогоняемом коммите и ``fake-config.json`` рядом
со скриптом). Пропусков нет: каждая проба обязана выполняться и проходить.
"""

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

# Состояния «красный не по нужной причине» и «битый report.json» (wb_state).
WRONG_REASON_STATES = (
    "red-records0",       # checker_failed при records == 0
    "red-weak",           # weak_required не пуст, findings пуст
    "red-disagrees",      # только CHECKER_DISAGREES
    "red-known",          # только подавленные known_findings, findings пуст
    "red-foreign",        # FALSE_PROOF по чужому inv (не из --inv)
)
INVALID_REPORT_STATES = (
    "red-invalid-report",   # report.json отсутствует
    "red-broken-report",    # report.json не разбирается как JSON
    "red-findings-nolist",  # findings не список
    "red-findings-notdict",  # findings — список не-словарей
)

FAKE_WITNESSBOX_SOURCE = '''#!/usr/bin/env python3
"""Фейковый witnessbox: gate-контракт формата 1; состояние задаёт wb_state в клоне."""
import hashlib
import json
import os
import subprocess
import sys

CONFIG = {}
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-config.json"),
              encoding="utf-8") as handle:
        CONFIG = json.load(handle)
except (OSError, ValueError):
    CONFIG = {}

if len(sys.argv) >= 2 and sys.argv[1] == "--installation":
    if CONFIG.get("install_raw") is not None:
        sys.stdout.write(str(CONFIG["install_raw"]))
        sys.stdout.flush()
        sys.exit(0)
    info = {
        "distribution": "witnessbox",
        "version": CONFIG.get("version", "0.10.0"),
        "package": "/fake-witnessbox",
        "resources": "/fake-witnessbox/resources",
        "python": sys.executable,
        "checker": "/fake-witnessbox/checker/ebt_check.py",
        "checker_sha256": CONFIG.get("checker_sha256", "1" * 64),
        "gate_sha256": CONFIG.get("gate_sha256", "2" * 64),
    }
    missing = CONFIG.get("install_missing_key")
    if missing:
        info.pop(missing, None)
    print(json.dumps(info))
    sys.exit(0)

if len(sys.argv) != 5 or sys.argv[1] != "gate":
    print("usage: fake witnessbox gate <ebt-dir> <clone> <run-dir>", file=sys.stderr)
    sys.exit(2)

ebt_dir, clone, run_dir = sys.argv[2], sys.argv[3], sys.argv[4]
os.makedirs(run_dir, exist_ok=True)
# Окружение, которое увидел гейт: проверка L17 (EBT_* отбрасываются, BASE/EBT_INV заданы).
with open(os.path.join(run_dir, "fake-env.json"), "w", encoding="utf-8") as handle:
    handle.write(json.dumps(dict(os.environ), ensure_ascii=True))

commit = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True,
                        text=True, check=True).stdout.strip()
with open(os.path.join(ebt_dir, "spec.toml"), "rb") as handle:
    spec_sha = hashlib.sha256(handle.read()).hexdigest()
state_path = os.path.join(clone, "wb_state")
state = open(state_path, encoding="utf-8").read().strip() if os.path.exists(state_path) else "missing"
inv = (os.environ.get("EBT_INV") or "INV-X").split(",")[0].strip()

gate = {
    "format": 1,
    "result": "PASS",
    "exit_code": 0,
    "reasons": [],
    "message": None,
    "commit": commit,
    "spec_sha256": spec_sha,
    "verifier": {
        "distribution": "witnessbox",
        "version": CONFIG.get("version", "0.10.0"),
        "gate_sha256": CONFIG.get("gate_sha256", "2" * 64),
    },
}
report = {"records": 1, "findings": [], "known_findings": []}

if state == "green":
    pass
elif state == "red":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="target finding")
    report["findings"] = [{"kind": "FALSE_PROOF", "inv": inv, "line": 1}]
elif state == "red-records0":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="records 0")
    report.update(records=0)
elif state == "red-weak":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="weak checker")
    report["weak_required"] = {"INV-1": ["value"]}
elif state == "red-disagrees":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="checker disagrees")
    report["findings"] = [{"kind": "CHECKER_DISAGREES", "inv": inv, "line": 1}]
elif state == "red-known":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="known findings only")
    report["known_findings"] = [{"kind": "FALSE_PROOF", "inv": inv, "line": 1}]
elif state == "red-foreign":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="foreign inv")
    report["findings"] = [{"kind": "FALSE_PROOF", "inv": "INV-FOREIGN", "line": 1}]
elif state == "red-invalid-report":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="no report.json")
elif state == "red-broken-report":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="broken report.json")
elif state == "red-findings-nolist":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"], message="findings is not a list")
    report = {"records": 1, "findings": "oops"}
elif state == "red-findings-notdict":
    gate.update(result="FAIL", exit_code=1, reasons=["checker_failed"],
                message="findings entries are not dicts")
    report = {"records": 1, "findings": [42]}
else:
    print("fake witnessbox: unknown wb_state " + repr(state), file=sys.stderr)
    sys.exit(2)

# Подмены контракта из конфигурации (fail-closed пробы).
if CONFIG.get("gate_commit") is not None:
    gate["commit"] = CONFIG["gate_commit"]
if CONFIG.get("gate_spec_sha256") is not None:
    gate["spec_sha256"] = CONFIG["gate_spec_sha256"]
if CONFIG.get("gate_verifier_version") is not None:
    gate["verifier"]["version"] = CONFIG["gate_verifier_version"]
if CONFIG.get("gate_verifier_sha") is not None:
    gate["verifier"]["gate_sha256"] = CONFIG["gate_verifier_sha"]
if CONFIG.get("gate_format") is not None:
    gate["format"] = CONFIG["gate_format"]
for key in CONFIG.get("gate_drop_keys", []):
    gate.pop(key, None)
if CONFIG.get("gate_exit_code") is not None:
    gate["exit_code"] = CONFIG["gate_exit_code"]

with open(os.path.join(run_dir, "gate.json"), "w", encoding="utf-8") as handle:
    json.dump(gate, handle, indent=2)
if state != "red-invalid-report":
    with open(os.path.join(run_dir, "report.json"), "w", encoding="utf-8") as handle:
        if state == "red-broken-report":
            handle.write('{"broken": ')
        else:
            json.dump(report, handle, indent=2)
sys.exit(0 if state == "green" else 1)
'''


def _clean_git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if extra:
        env.update(extra)
    return env


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], env=_clean_git_env(),
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _commit(path: Path, message: str) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _run_cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ if env is None else env)
    existing = run_env.get("PYTHONPATH", "")
    run_env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else SRC_DIR
    return subprocess.run([PYTHON, "-m", "acceptbox.cli", *args],
                          env=run_env, capture_output=True, text=True)


class WitnessboxNegativeProbes(unittest.TestCase):
    """Каждая негативная проба шага 4.2 плана для режима witnessbox."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory(prefix="acceptbox-neg-")
        cls.addClassCleanup(cls._temp.cleanup)
        cls.root = Path(cls._temp.name)

        # Фейковый witnessbox первым в PATH.
        cls.fakebin = cls.root / "fakebin"
        cls.fakebin.mkdir()
        script = cls.fakebin / "witnessbox"
        script.write_text(FAKE_WITNESSBOX_SOURCE, encoding="utf-8")
        script.chmod(0o755)
        cls._write_fake_config({})

        # Репозиторий EBT: main + коммит на side-ветке (не предок main).
        cls.ebt_repo = cls.root / "ebt-repo"
        _init_repo(cls.ebt_repo)
        (cls.ebt_repo / "ebt").mkdir()
        (cls.ebt_repo / "ebt" / "spec.toml").write_text("inv_demo = 1\n", encoding="utf-8")
        cls.ebt_sha = _commit(cls.ebt_repo, "ebt approved")
        _git(cls.ebt_repo, "checkout", "-q", "-b", "ebt-side")
        (cls.ebt_repo / "ebt" / "spec.toml").write_text("inv_demo = 2\n", encoding="utf-8")
        cls.ebt_side_sha = _commit(cls.ebt_repo, "ebt side change")
        _git(cls.ebt_repo, "checkout", "-q", "main")

        # Репозиторий продукта: цепочки base → repro → head с состояниями wb_state.
        cls.product_repo = cls.root / "product-repo"
        _init_repo(cls.product_repo)
        cls.root_sha = _commit(cls.product_repo, "root")

        def chain(name: str, base_state: str, repro_state: str, head_state: str,
                  repro_paths: tuple[str, ...] = (), head_paths: tuple[str, ...] = ()) -> dict[str, str]:
            _git(cls.product_repo, "checkout", "-q", "-B", name, cls.root_sha)
            _git(cls.product_repo, "clean", "-fdq")
            (cls.product_repo / "wb_state").write_text(base_state + "\n", encoding="utf-8")
            base = _commit(cls.product_repo, f"{name}: base")
            (cls.product_repo / "wb_state").write_text(repro_state + "\n", encoding="utf-8")
            for relative in repro_paths:
                target = cls.product_repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x\n", encoding="utf-8")
            repro = _commit(cls.product_repo, f"{name}: repro")
            (cls.product_repo / "wb_state").write_text(head_state + "\n", encoding="utf-8")
            for relative in head_paths:
                target = cls.product_repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("y\n", encoding="utf-8")
            head = _commit(cls.product_repo, f"{name}: head")
            return {"base": base, "repro": repro, "head": head}

        cls.chains = {
            "nominal": chain("nominal", "red", "red", "green"),
            "fabricated": chain("fabricated", "green", "red", "green"),
            "head-red": chain("head-red", "red", "red", "red"),
            "protect-repro": chain("protect-repro", "red", "red", "green",
                                   repro_paths=("ebt/x.txt",)),
            "protect-head": chain("protect-head", "red", "red", "green",
                                  head_paths=("tool/ebt_check/y.txt",)),
        }
        for state in WRONG_REASON_STATES + INVALID_REPORT_STATES:
            cls.chains[f"repro-{state}"] = chain(f"repro-{state}", "red", state, "green")
            cls.chains[f"base-{state}"] = chain(f"base-{state}", state, "red", "green")
        _git(cls.product_repo, "checkout", "-q", "main")

        pin = _run_cli(["pin", "--ebt-repo", str(cls.ebt_repo), "--ebt-sha", cls.ebt_sha])
        assert pin.returncode == 0, pin.stderr
        cls.approved = pin.stdout.strip()
        assert len(cls.approved) == 64 and cls.approved == cls.approved.lower()

        # PATH со всеми системными утилитами, но без witnessbox.
        cls.minimal_bin = cls.root / "minimal-bin"
        cls.minimal_bin.mkdir()
        git_path = subprocess.run(["which", "git"], capture_output=True, text=True,
                                  check=True).stdout.strip()
        for name, target in (("git", git_path), ("python3", PYTHON)):
            link = cls.minimal_bin / name
            if not link.exists():
                link.symlink_to(target)

    @classmethod
    def _write_fake_config(cls, config: dict) -> None:
        (cls.fakebin / "fake-config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def setUp(self) -> None:
        self._write_fake_config({})

    # ── Вспомогательные прогон и проверки ──────────────────────────────

    def _fake_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "PATH": f"{self.fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.root)),
        }
        if extra:
            env.update(extra)
        return env

    def _accept(self, chain: dict[str, str], out: Path, *,
                inv: str = "INV-1,INV-2", ebt_sha: str | None = None,
                approved: str | None = None, env: dict[str, str] | None = None,
                extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
        args = [
            "accept", "--mode", "witnessbox", "--repo", str(self.product_repo),
            "--base", chain["base"], "--repro", chain["repro"], "--head", chain["head"],
            "--inv", inv, "--ebt-repo", str(self.ebt_repo), "--ebt-ref", "main",
            "--ebt-sha", self.ebt_sha if ebt_sha is None else ebt_sha,
            "--approved-tree", self.approved if approved is None else approved,
            "--out", str(out), *extra,
        ]
        return _run_cli(args, env=self._fake_env() if env is None else env)

    def _calibrate(self, out: Path, greens: list[str], reds: list[str], *,
                   base: str | None = None, ebt_sha: str | None = None,
                   approved: str | None = None, inv: str = "INV-1,INV-2",
                   env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        args = ["calibrate", "--mode", "witnessbox", "--repo", str(self.product_repo),
                "--out", str(out)]
        if base is not None:
            args += ["--base", base]
        for sha in greens:
            args += ["--green", sha]
        for sha in reds:
            args += ["--red", sha]
        args += ["--inv", inv, "--ebt-repo", str(self.ebt_repo), "--ebt-ref", "main",
                 "--ebt-sha", self.ebt_sha if ebt_sha is None else ebt_sha,
                 "--approved-tree", self.approved if approved is None else approved]
        return _run_cli(args, env=self._fake_env() if env is None else env)

    def _assert_accept(self, out: Path, proc: subprocess.CompletedProcess[str], *,
                       exit_code: int, result: str, reason_code: str | None,
                       run_statuses: dict[str, str] | None = None,
                       runs_dir: bool | None = None) -> dict:
        message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        self.assertEqual(proc.returncode, exit_code, message)
        data = json.loads((out / "accept.json").read_text(encoding="utf-8"))
        self.assertEqual(data["result"], result, message)
        self.assertEqual(data["exit_code"], exit_code, message)
        self.assertEqual(data["reason_code"], reason_code, message)
        self.assertEqual(data["mode"], "witnessbox")
        self.assertEqual(data["scope"], "ebt-only")
        for name, status in (run_statuses or {}).items():
            self.assertEqual(data["runs"][name]["status"], status, name)
        if runs_dir is not None:
            self.assertEqual((out / "runs").exists(), runs_dir)
        return data

    def _assert_no_accept_json(self, out: Path) -> None:
        self.assertFalse((out / "accept.json").exists(), "calibrate не создаёт accept.json")

    # ── 1. Сфабрикованное репро ───────────────────────────────────────

    def test_fabricated_repro_rejected_base_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["fabricated"], out)
            self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                reason_code="base-green",
                                run_statuses={"base": "green", "repro": "skipped",
                                              "head": "skipped"})

    # ── 2. checker_failed без целевой находки ────────────────────────

    def test_checker_failed_without_target_finding(self) -> None:
        for state in WRONG_REASON_STATES:
            for side in ("repro", "base"):
                with self.subTest(state=state, side=side), \
                        tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out"
                    chain = self.chains[f"{side}-{state}"]
                    proc = self._accept(chain, out)
                    if side == "repro":
                        statuses = {"base": "red", "repro": "red-other", "head": "skipped"}
                    else:
                        statuses = {"base": "red-other", "repro": "skipped", "head": "skipped"}
                    self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                        reason_code=f"{side}-red-wrong-reason",
                                        run_statuses=statuses)

    # ── 3. Битый/отсутствующий report.json ────────────────────────────

    def test_report_invalid_on_base_and_repro(self) -> None:
        for state in INVALID_REPORT_STATES:
            for side in ("repro", "base"):
                with self.subTest(state=state, side=side), \
                        tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out"
                    chain = self.chains[f"{side}-{state}"]
                    proc = self._accept(chain, out)
                    if side == "repro":
                        statuses = {"base": "red", "repro": "invalid-report", "head": "skipped"}
                    else:
                        statuses = {"base": "invalid-report", "repro": "skipped", "head": "skipped"}
                    self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                        reason_code=f"{side}-report-invalid",
                                        run_statuses=statuses)

    # ── 4. Подмена полей gate.json ────────────────────────────────────

    def _assert_gate_error_on_base(self, out: Path,
                                   proc: subprocess.CompletedProcess[str]) -> dict:
        return self._assert_accept(out, proc, exit_code=2, result="ERROR",
                                   reason_code="gate-error",
                                   run_statuses={"base": "error", "repro": "skipped",
                                                 "head": "skipped"})

    def test_tampered_gate_commit(self) -> None:
        self._write_fake_config({"gate_commit": "0" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_tampered_gate_spec_sha256(self) -> None:
        self._write_fake_config({"gate_spec_sha256": "a" * 64})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_tampered_gate_verifier_version(self) -> None:
        self._write_fake_config({"gate_verifier_version": "9.9.9"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_tampered_gate_verifier_sha(self) -> None:
        self._write_fake_config({"gate_verifier_sha": "b" * 64})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_gate_exit_code_mismatch_with_process(self) -> None:
        # gate.json объявляет exit_code 0, процесс завершился с 1 → fail-closed.
        self._write_fake_config({"gate_exit_code": 0})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    # ── 5. Поломка схемы будущей версией witnessbox ──────────────────

    def test_gate_format_two_rejected(self) -> None:
        self._write_fake_config({"gate_format": 2})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_gate_format_boolean_rejected(self) -> None:
        self._write_fake_config({"gate_format": True})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_gate_missing_reasons_key(self) -> None:
        self._write_fake_config({"gate_drop_keys": ["reasons"]})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def test_gate_missing_verifier_key(self) -> None:
        self._write_fake_config({"gate_drop_keys": ["verifier"]})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_on_base(out, self._accept(self.chains["nominal"], out))

    def _assert_gate_error_before_runs(self, out: Path,
                                       proc: subprocess.CompletedProcess[str]) -> None:
        # Сбой диагностики witnessbox --installation происходит до прогонов.
        self._assert_accept(out, proc, exit_code=2, result="ERROR", reason_code="gate-error",
                            run_statuses={"base": "skipped", "repro": "skipped",
                                          "head": "skipped"},
                            runs_dir=False)

    def test_installation_malformed_json(self) -> None:
        self._write_fake_config({"install_raw": "not-json{"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_before_runs(out, self._accept(self.chains["nominal"], out))

    def test_installation_missing_gate_sha256(self) -> None:
        self._write_fake_config({"install_missing_key": "gate_sha256"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            self._assert_gate_error_before_runs(out, self._accept(self.chains["nominal"], out))

    # ── 6. Недоверенные входы EBT (L10) ───────────────────────────────

    def test_tampered_ebt_tree_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["nominal"], out, approved="c" * 64)
            data = self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                       reason_code="inputs-untrusted",
                                       run_statuses={"base": "skipped", "repro": "skipped",
                                                     "head": "skipped"},
                                       runs_dir=False)
            self.assertEqual(data["checks"]["ebt_trust"]["status"], "failed")

    def test_ebt_sha_not_ancestor_of_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["nominal"], out, ebt_sha=self.ebt_side_sha)
            self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                reason_code="inputs-untrusted",
                                run_statuses={"base": "skipped", "repro": "skipped",
                                              "head": "skipped"},
                                runs_dir=False)

    # ── 7. Изоляция окружения гейта (L17) ─────────────────────────────

    def test_env_hardening_strips_ebt_vars(self) -> None:
        env = self._fake_env({"EBT_CHECK": "/tmp/evil", "EBT_TESTS": "evil",
                              "EBT_RUNNER": "evil"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["nominal"], out, env=env)
            self._assert_accept(out, proc, exit_code=0, result="ACCEPTED", reason_code=None,
                                run_statuses={"base": "red", "repro": "red", "head": "green"})
            seen = json.loads((out / "runs" / "base" / "fake-env.json").read_text(encoding="utf-8"))
            for name in ("EBT_CHECK", "EBT_TESTS", "EBT_RUNNER"):
                self.assertNotIn(name, seen)
            self.assertEqual(seen.get("BASE"), self.chains["nominal"]["base"])
            self.assertEqual(seen.get("EBT_INV"), "INV-1,INV-2")

    def test_env_cli_rejects_forbidden_keys(self) -> None:
        for entry in ("EBT_CHECK=/tmp/evil", "BASE=abc", "GIT_DIR=/tmp/x"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                proc = self._accept(self.chains["nominal"], out, extra=("--env", entry))
                message = f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
                self.assertEqual(proc.returncode, 2, message)
                self.assertFalse(out.exists(), "ошибка CLI не создаёт --out")

    # ── 8. Пустой --inv ──────────────────────────────────────────────

    def test_empty_inv_no_invariants(self) -> None:
        for inv in ("", ",,,"):
            with self.subTest(inv=inv), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "out"
                proc = self._accept(self.chains["nominal"], out, inv=inv)
                data = self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                           reason_code="no-invariants",
                                           run_statuses={"base": "skipped", "repro": "skipped",
                                                         "head": "skipped"},
                                           runs_dir=False)
                self.assertEqual(data["coverage"]["checked"], [])
                self.assertEqual(data["coverage"]["unchecked"], ["no-invariants"])

    # ── 9. Отсутствующий и несовместимый witnessbox ───────────────────

    def test_witnessbox_missing(self) -> None:
        env = {"PATH": str(self.minimal_bin), "HOME": os.environ.get("HOME", str(self.root))}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["nominal"], out, env=env)
            self._assert_accept(out, proc, exit_code=2, result="ERROR",
                                reason_code="witnessbox-missing",
                                run_statuses={"base": "skipped", "repro": "skipped",
                                              "head": "skipped"},
                                runs_dir=False)

    def test_witnessbox_incompatible(self) -> None:
        self._write_fake_config({"version": "0.9.9"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["nominal"], out)
            self._assert_accept(out, proc, exit_code=2, result="ERROR",
                                reason_code="witnessbox-incompatible",
                                run_statuses={"base": "skipped", "repro": "skipped",
                                              "head": "skipped"},
                                runs_dir=False)

    # ── 10. Защищённые пути ebt и tool/ebt_check ─────────────────────

    def test_protected_paths_changed_in_repro(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["protect-repro"], out)
            self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                reason_code="protected-paths-changed",
                                run_statuses={"base": "skipped", "repro": "skipped",
                                              "head": "skipped"},
                                runs_dir=False)

    def test_protected_paths_changed_in_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["protect-head"], out)
            self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                reason_code="protected-paths-changed",
                                run_statuses={"base": "skipped", "repro": "skipped",
                                              "head": "skipped"},
                                runs_dir=False)

    # ── 11. head не зелёный ──────────────────────────────────────────

    def test_head_not_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._accept(self.chains["head-red"], out)
            self._assert_accept(out, proc, exit_code=1, result="REJECTED",
                                reason_code="head-not-green",
                                run_statuses={"base": "red", "repro": "red", "head": "red"})

    # ── 12. calibrate ────────────────────────────────────────────────

    def test_calibrate_calibrated(self) -> None:
        chain = self.chains["nominal"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._calibrate(out, greens=[chain["head"]], reds=[chain["repro"]],
                                   base=chain["base"])
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            data = json.loads((out / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "CALIBRATED")
            self.assertEqual(data["exit_code"], 0)
            self.assertIsNone(data["reason_code"])
            self.assertEqual([item["status"] for item in data["results"]], ["green", "red"])
            self.assertTrue(all(item["matched"] for item in data["results"]))
            self.assertEqual(data["repo"]["base"], chain["base"])
            self._assert_no_accept_json(out)

    def test_calibrate_failed_on_mismatch(self) -> None:
        chain = self.chains["nominal"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._calibrate(out, greens=[chain["repro"]], reds=[])
            self.assertEqual(proc.returncode, 1, proc.stderr + proc.stdout)
            data = json.loads((out / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "FAILED")
            self.assertEqual(data["reason_code"], "mismatch")
            self.assertEqual(data["results"][0]["status"], "red")
            self.assertFalse(data["results"][0]["matched"])
            self._assert_no_accept_json(out)

    def test_calibrate_inputs_untrusted(self) -> None:
        chain = self.chains["nominal"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._calibrate(out, greens=[chain["head"]], reds=[],
                                   approved="c" * 64)
            self.assertEqual(proc.returncode, 1, proc.stderr + proc.stdout)
            data = json.loads((out / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "FAILED")
            self.assertEqual(data["reason_code"], "inputs-untrusted")
            self._assert_no_accept_json(out)

    def test_calibrate_error_on_gate_failure(self) -> None:
        self._write_fake_config({"gate_commit": "0" * 40})
        chain = self.chains["nominal"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = self._calibrate(out, greens=[chain["head"]], reds=[])
            self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
            data = json.loads((out / "calibrate.json").read_text(encoding="utf-8"))
            self.assertEqual(data["result"], "ERROR")
            self.assertEqual(data["reason_code"], "gate-error")
            self.assertEqual(data["results"][0]["status"], "error")
            self._assert_no_accept_json(out)


if __name__ == "__main__":
    unittest.main()
