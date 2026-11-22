"""Вердикт приёмки: чистые правила стадий без git и subprocess.

Позволяет тестировать полную матрицу решений напрямую: порядок причин и коды
выхода заданы здесь и только здесь (раздел 3.4 плана, стадии 6–10).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunOutcome:
    """Исход прогона одного коммита; классификация выполнена до verdict.py."""

    status: str  # "green" | "red" | "red-other" | "invalid-report" | "error" | "skipped"
    exit_code: int | None
    reason: str  # пояснение для отчёта; verdict.py его не разбирает
    duration_ms: int
    log_path: str | None
    artifacts: dict | None


@dataclass
class Verdict:
    """Итог приёмки: результат, код выхода, machine-readable причина и сообщение."""

    result: str  # "ACCEPTED" | "REJECTED" | "ERROR"
    exit_code: int  # 0 | 1 | 2
    reason_code: str | None
    message: str


def accepted() -> Verdict:
    return Verdict("ACCEPTED", 0, None, "все условия приёмки выполнены")


def rejected(reason_code: str, message: str) -> Verdict:
    return Verdict("REJECTED", 1, reason_code, message)


def error(reason_code: str, message: str) -> Verdict:
    return Verdict("ERROR", 2, reason_code, message)


def no_invariants() -> Verdict:
    """Стадия 4: пустой --inv — явный отказ, а не молчание (L11)."""
    return rejected("no-invariants", "--inv не содержит ни одного инварианта; приёмка невозможна")


def base_not_on_ref(message: str) -> Verdict:
    """Стадия 5a: --base не лежит в доверенной ветке --base-ref (L29)."""
    return rejected("base-not-on-ref", message)


def ref_source_local(message: str) -> Verdict:
    """Стадии 5a/6: --strict-refs запрещает слабый локальный источник ветки (L30)."""
    return rejected("ref-source-local", message)


def inputs_untrusted(message: str) -> Verdict:
    """Стадия 6: нарушение доверия к ebt/ (L10)."""
    return rejected("inputs-untrusted", f"доверенные входы EBT не подтверждены: {message}")


def ancestry_verdict(*, base_ancestor_of_repro: bool,
                     repro_ancestor_of_head: bool) -> Verdict | None:
    """Стадия 7: base предок repro, затем repro предок head; None — продолжать."""
    if not base_ancestor_of_repro:
        return rejected("repro-not-descendant",
                        "base не является предком repro: ancestry base → repro → head нарушена")
    if not repro_ancestor_of_head:
        return rejected("repro-not-ancestor",
                        "repro не является предком head: ancestry base → repro → head нарушена")
    return None


def refactor_ancestry_verdict(*, base_ancestor_of_head: bool) -> Verdict | None:
    """Стадия 7 (policy=refactor, L24/L30): base должен быть предком head."""
    if not base_ancestor_of_head:
        return rejected("head-not-descendant",
                        "base не является предком head: ancestry base → head нарушена")
    return None


def refactor_empty_verdict(*, head_changed: list[str]) -> Verdict | None:
    """Стадия 9 (policy=refactor, L24/L30): дифф base..head не должен быть пустым."""
    if not head_changed:
        return rejected("refactor-empty", "дифф base..head пуст: рефакторинг не меняет файлы")
    return None


def repro_ambiguous(message: str) -> Verdict:
    """Авто-резолв --repro (L30): несколько коммитов помечены AcceptBox-Role: repro."""
    return rejected("repro-ambiguous", message)


def protected_paths_verdict(*, changed_base_repro: list[str],
                            changed_base_head: list[str]) -> Verdict | None:
    """Стадия 8: защищённые пути не меняются в base..repro и base..head."""
    if changed_base_repro or changed_base_head:
        paths = ", ".join(sorted(set(changed_base_repro) | set(changed_base_head)))
        return rejected("protected-paths-changed", f"изменены защищённые пути: {paths}")
    return None


def repro_tests_verdict(*, repro_changed: list[str], tests_changed: list[str],
                        modified_after_repro: list[str]) -> Verdict | None:
    """Стадия 9 (только command, L19): репро-тест существует, внутри --tests
    и не тронут в head."""
    if not repro_changed:
        return rejected("repro-empty", "дифф base..repro пуст: репро-коммит не меняет файлы")
    outside = sorted(set(repro_changed) - set(tests_changed))
    if outside:
        return rejected("repro-outside-tests",
                        f"repro меняет пути вне --tests: {', '.join(outside)}")
    if modified_after_repro:
        paths = ", ".join(sorted(set(modified_after_repro)))
        return rejected("repro-tests-modified", f"head изменяет файлы репро-теста: {paths}")
    return None


def run_verdict(run_name: str, outcome: RunOutcome, *,
                policy: str = "bugfix") -> Verdict | None:
    """Стадия 10, один прогон: None — исход соответствует ожиданию, приёмка продолжается."""
    if outcome.status == "error":
        return error("gate-error", f"прогон {run_name} завершился ошибкой: {outcome.reason}")
    if policy == "refactor":
        if run_name == "base":
            if outcome.status == "green":
                return None
            return rejected("base-not-green", f"base не зелёный: {outcome.status} ({outcome.reason})")
        if run_name == "head":
            if outcome.status == "green":
                return None
            return rejected("head-not-green", f"head не зелёный: {outcome.status} ({outcome.reason})")
        return error("gate-error", f"неизвестный прогон {run_name}")
    if run_name == "base":
        if outcome.status == "red":
            return None
        reasons = {
            "green": ("base-green", "base зелёный: баг воспроизводится без репро-коммита"),
            "invalid-report": ("base-report-invalid",
                               "report.json прогона base отсутствует или некорректен"),
            "red-other": ("base-red-wrong-reason", "base красный не по нужной причине"),
        }
    elif run_name == "repro":
        if outcome.status == "red":
            return None
        reasons = {
            "green": ("repro-green", "repro зелёный: репро-коммит не воспроизводит баг"),
            "invalid-report": ("repro-report-invalid",
                               "report.json прогона repro отсутствует или некорректен"),
            "red-other": ("repro-red-wrong-reason", "repro красный не по нужной причине"),
        }
    elif run_name == "head":
        if outcome.status == "green":
            return None
        return rejected("head-not-green", f"head не зелёный: {outcome.status} ({outcome.reason})")
    else:
        return error("gate-error", f"неизвестный прогон {run_name}")
    reason = reasons.get(outcome.status)
    if reason is None:
        return error("gate-error", f"неожиданный статус прогона {run_name}: {outcome.status}")
    return rejected(*reason)


def evaluate_runs(runs: list[tuple[str, RunOutcome]], *,
                  policy: str = "bugfix") -> Verdict:
    """Стадия 10: последовательные fail-fast прогоны base → repro → head (или base → head).

    Первый неудовлетворённый исход задаёт вердикт; все удовлетворены — ACCEPTED.
    """
    for run_name, outcome in runs:
        verdict = run_verdict(run_name, outcome, policy=policy)
        if verdict is not None:
            return verdict
    return accepted()


def coverage(mode: str, invariants: list[str],
             findings_inv: list[str] | None = None) -> dict:
    """Охват приёмки (L11): проверенные инварианты, пропущенные проверки,
    непроверенные требования."""
    if mode != "witnessbox" or not invariants:
        return {"checked": [], "skipped": [], "unchecked": list(invariants)}
    found = set(findings_inv or [])
    checked = [inv for inv in invariants if inv in found]
    return {"checked": checked, "skipped": [],
            "unchecked": [inv for inv in invariants if inv not in checked]}
