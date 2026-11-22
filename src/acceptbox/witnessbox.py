"""Прогон одного коммита через ``witnessbox gate`` с fail-closed проверкой схемы (L21).

Импорт внутренних модулей witnessbox запрещён: используется только CLI
``witnessbox gate <ebt-dir> <clone> <run-dir>`` и его файлы gate.json/report.json.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .command import run_process_group
from .git import strip_local_env_vars
from .verdict import RunOutcome

WITNESSBOX_MIN_VERSION = (0, 10, 0)
WITNESSBOX_SUPPORTED_TEXT = ">=0.10.0"

# reasons, означающие сбой окружения/гейта, а не осмысленный красный.
ERROR_REASONS = frozenset({"gate_error", "checker_error", "tests_error"})
# reasons, при которых красный не засчитывается как «по нужной причине».
RED_OTHER_REASONS = frozenset({"gate_error", "checker_error", "pinned_drift",
                                "protected_paths_changed"})
# Находки, подтверждающие нарушение инварианта (L9); CHECKER_DISAGREES не засчитывается.
TARGET_KINDS = frozenset({"FALSE_PROOF", "CONFIRMED_VIOLATION"})

# Финальный выпуск PEP 440 (возможны post/local); pre-release разбирается как ошибка.
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?(?:\+[0-9A-Za-z._-]+)?\Z")

LOG_NAME = "witnessbox.log"


@dataclass
class WitnessboxStatus:
    """Диагностика установленного witnessbox для --installation и стадии 3."""

    status: str  # "compatible" | "missing" | "incompatible" | "error"
    path: str | None
    version: str | None
    message: str
    info: dict | None = None  # валидный JSON --installation при совместимости


def parse_version_tuple(value: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) финального выпуска; иначе None (fail-closed)."""
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def check_witnessbox_installation(env: dict[str, str] | None = None) -> WitnessboxStatus:
    """Наличие и совместимость witnessbox: ``>=0.10.0`` без верхней границы (L21)."""
    executable = shutil.which("witnessbox", path=env.get("PATH") if env else None)
    if executable is None:
        return WitnessboxStatus("missing", None, None, "witnessbox command not found in PATH")
    run_env = strip_local_env_vars(env) if env is not None else None
    try:
        completed = subprocess.run(
            [executable, "--installation"], env=run_env,
            capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as run_error:
        return WitnessboxStatus(
            "error", executable, None, f"witnessbox --installation failed: {run_error}")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        message = f"witnessbox --installation exited with code {completed.returncode}"
        return WitnessboxStatus("error", executable, None,
                                f"{message}: {detail}" if detail else message)
    try:
        info = json.loads(completed.stdout.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return WitnessboxStatus("error", executable, None,
                                "witnessbox --installation emitted invalid JSON")
    if not isinstance(info, dict) or info.get("distribution") != "witnessbox":
        return WitnessboxStatus(
            "error", executable, None,
            '--installation schema mismatch: distribution != "witnessbox"')
    for key in ("version", "gate_sha256", "checker_sha256"):
        if not isinstance(info.get(key), str) or not info[key]:
            return WitnessboxStatus(
                "error", executable, None, f"--installation schema: key {key} is missing or empty")
    parsed = parse_version_tuple(info["version"])
    if parsed is None:
        return WitnessboxStatus(
            "error", executable, info["version"],
            f"failed to parse witnessbox version: {info['version']!r}")
    if parsed < WITNESSBOX_MIN_VERSION:
        return WitnessboxStatus(
            "incompatible", executable, info["version"],
            f"witnessbox {info['version']} is below supported version {WITNESSBOX_SUPPORTED_TEXT}")
    return WitnessboxStatus("compatible", executable, info["version"],
                            f"witnessbox {info['version']} is compatible", info=info)


def _read_json(path: Path) -> dict | None:
    """JSON-объект или None при любой проблеме чтения/разбора (fail-closed)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_findings_inv(run_dir: Path) -> list[str]:
    """``inv`` целевых находок report.json прогона — для coverage.accept.json."""
    report = _read_json(Path(run_dir) / "report.json")
    if report is None:
        return []
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    return [entry["inv"] for entry in findings if _is_target_finding(entry)]


def _is_target_finding(entry: object, invariants: set[str] | None = None) -> bool:
    """Целевая находка L9; нестроковые kind/inv (сломанная схема) не засчитываются."""
    if not isinstance(entry, dict):
        return False
    kind, inv = entry.get("kind"), entry.get("inv")
    if not isinstance(kind, str) or not isinstance(inv, str) or kind not in TARGET_KINDS:
        return False
    return invariants is None or inv in invariants


def classify_gate_run(run_dir: Path, commit_sha: str, approved_spec_sha256: str,
                      expected_invariants, installation_info: dict, *,
                      gate_exit_code: int | None, failure_message: str | None,
                      duration_ms: int, log_path: str | None) -> RunOutcome:
    """Классификация одного прогона по gate.json/report.json (L9, L21).

    Чистая функция от run_dir: тестируется на файлах без subprocess.
    """
    run_dir = Path(run_dir)
    expected = None if expected_invariants is None else set(expected_invariants)
    gate_path = run_dir / "gate.json"
    report_path = run_dir / "report.json"

    def outcome(status: str, reason: str, artifacts: dict | None = None) -> RunOutcome:
        return RunOutcome(status=status, exit_code=gate_exit_code, reason=reason,
                          duration_ms=duration_ms, log_path=log_path, artifacts=artifacts)

    # Сбой запуска гейта: OSError, таймаут, сигнал.
    if failure_message is not None:
        return outcome("error", failure_message)
    if gate_exit_code is None:
        return outcome("error", "witnessbox gate returned no exit code")
    if gate_exit_code < 0:
        return outcome("error", f"witnessbox gate terminated by signal {-gate_exit_code}")

    # Fail-closed проверка схемы gate.json (формат 1) и перекрёстных полей.
    gate = _read_json(gate_path)
    if gate is None:
        return outcome("error", "gate.json is missing or not a valid JSON object")
    if type(gate.get("format")) is not int or gate["format"] != 1:
        return outcome("error",
                       f"incompatible gate.json schema: format={gate.get('format')!r}, expected 1")
    reasons = gate.get("reasons")
    if type(gate.get("exit_code")) is not int \
            or not isinstance(reasons, list) \
            or not all(isinstance(item, str) for item in reasons) \
            or not isinstance(gate.get("commit"), str) \
            or not isinstance(gate.get("spec_sha256"), str):
        return outcome("error", "incompatible gate.json schema: invalid keys or types")
    verifier = gate.get("verifier")
    if not isinstance(verifier, dict) or verifier.get("distribution") != "witnessbox" \
            or not isinstance(verifier.get("version"), str) or not verifier["version"] \
            or not isinstance(verifier.get("gate_sha256"), str) or not verifier["gate_sha256"]:
        return outcome("error", "incompatible gate.json schema: invalid verifier block")
    if gate["commit"].lower() != commit_sha.lower():
        return outcome("error", "gate.json: commit does not match target commit")
    if gate["spec_sha256"].lower() != approved_spec_sha256.lower():
        return outcome("error", "gate.json: spec_sha256 does not match ebt/spec.toml")
    if verifier["version"] != installation_info.get("version") \
            or verifier["gate_sha256"] != installation_info.get("gate_sha256"):
        return outcome("error",
                       "gate.json: verifier does not match installed witnessbox --installation")
    if gate["exit_code"] != gate_exit_code:
        return outcome("error",
                       f"gate.json: exit_code ({gate['exit_code']}) does not match process exit code ({gate_exit_code})")

    reason_set = set(reasons)
    if gate_exit_code == 2 or ERROR_REASONS & reason_set:
        return outcome("error", f"witnessbox gate reported an error: reasons={reasons}")

    artifacts = {"gate.json": {"path": gate_path.name, "sha256": _sha256_file(gate_path)},
                 "spec_sha256": gate["spec_sha256"]}
    if report_path.is_file():
        artifacts["report.json"] = {"path": report_path.name, "sha256": _sha256_file(report_path)}

    if gate_exit_code == 0:
        report = _read_json(report_path)
        if report is None or not isinstance(report.get("findings"), list) \
                or not all(isinstance(entry, dict) for entry in report["findings"]):
            return outcome("error", "exit_code 0, but report.json is missing or invalid")
        return outcome("green", "witnessbox gate: PASS (exit_code 0)", artifacts)

    if gate_exit_code == 1:
        if "checker_failed" not in reason_set or RED_OTHER_REASONS & reason_set:
            return outcome("red-other",
                           f"witnessbox gate is red for the wrong reason: reasons={reasons}",
                           artifacts)
        report = _read_json(report_path)
        if report is None or not isinstance(report.get("findings"), list) \
                or not all(isinstance(entry, dict) for entry in report["findings"]):
            return outcome("invalid-report",
                           "red without valid report.json: findings unavailable", artifacts)
        matched = [entry for entry in report["findings"]
                   if _is_target_finding(entry, expected)]
        if matched:
            kinds = ", ".join(sorted({str(entry.get("kind")) for entry in matched}))
            return outcome("red", f"witnessbox gate red for expected reason: {kinds}", artifacts)
        return outcome("red-other",
                       "red without target finding (FALSE_PROOF/CONFIRMED_VIOLATION for --inv)",
                       artifacts)

    return outcome("error", f"unexpected witnessbox gate exit code: {gate_exit_code}")


def run_commit(worktree: Path, commit_sha: str, run_dir: Path, env: dict[str, str],
               timeout: float, *, ebt_dir: Path, approved_spec_sha256: str,
               expected_invariants: list[str] | None, installation_info: dict,
               witnessbox_bin: str = "witnessbox") -> RunOutcome:
    """Прогон одного коммита: ``witnessbox gate <ebt_dir> <worktree> <run_dir>``."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    clean_env = strip_local_env_vars(env)
    argv = [witnessbox_bin, "gate", str(ebt_dir), str(worktree), str(run_dir)]
    started = time.monotonic()
    gate_exit_code: int | None = None
    failure_message: str | None = None
    stdout = stderr = b""
    try:
        completed = run_process_group(argv, cwd=str(run_dir), env=clean_env,
                                      timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        stdout, stderr = expired.stdout or b"", expired.stderr or b""
        failure_message = f"witnessbox gate timed out after {timeout:g}s"
    except OSError as run_error:
        failure_message = f"failed to start witnessbox gate: {run_error}"
    else:
        stdout, stderr = completed.stdout, completed.stderr
        gate_exit_code = completed.returncode
        if completed.returncode < 0:
            failure_message = f"witnessbox gate terminated by signal {-completed.returncode}"
    duration_ms = int((time.monotonic() - started) * 1000)
    (run_dir / LOG_NAME).write_bytes(stdout + stderr)
    return classify_gate_run(
        run_dir, commit_sha, approved_spec_sha256, expected_invariants, installation_info,
        gate_exit_code=gate_exit_code, failure_message=failure_message,
        duration_ms=duration_ms, log_path=LOG_NAME,
    )
