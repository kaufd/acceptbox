"""Отображение отчётов accept.json и calibrate.json (format 1) в Markdown и HTML5 (L32).

Чистая функция от разобранного JSON-отчёта: не обращается к git, не запускает
подпроцессы и не читает внешние логи прогонов с диска.
"""

from __future__ import annotations

import html
import json
import os
import re
import stat
import tempfile
from pathlib import Path

REPORT_FORMAT = 1

_CHECK_ORDER = ("base_ref", "ebt_trust", "ancestry", "protected_paths", "repro_tests", "refactor")
_RUN_ORDER = ("base", "repro", "head")
_BACKTICK_RUN_RE = re.compile(r"`+")


class RenderError(ValueError):
    """Ошибка чтения, валидации или записи при рендеринге отчёта (код 2)."""


def detect_report_kind(payload: object) -> str:
    """Проверить структуру отчёта format 1 и вернуть ``'accept'`` или ``'calibrate'``."""
    if not isinstance(payload, dict):
        raise RenderError("корень отчёта должен быть JSON-объектом")
    fmt = payload.get("format")
    if type(fmt) is not int or fmt != REPORT_FORMAT:
        raise RenderError(
            f"неподдерживаемый формат отчёта: ожидается format == {REPORT_FORMAT}, получено {fmt!r}"
        )
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise RenderError("в отчёте отсутствует строковое поле result")

    has_accept = ("checks" in payload) or ("runs" in payload)
    has_calibrate = "results" in payload
    if has_accept and has_calibrate:
        raise RenderError("неоднозначный отчёт: одновременно присутствуют поля accept и calibrate")
    if has_accept:
        if not isinstance(payload.get("checks"), dict) or not isinstance(payload.get("runs"), dict):
            raise RenderError("отчёт accept должен содержать объекты checks и runs")
        return "accept"
    if has_calibrate:
        results = payload.get("results")
        if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
            raise RenderError("отчёт calibrate должен содержать список объектов results")
        return "calibrate"
    raise RenderError(
        "неизвестный тип отчёта: ожидаются checks/runs (accept.json) или results (calibrate.json)"
    )


def load_report(path: Path | str) -> dict:
    """Прочитать и провалидировать отчёт accept.json / calibrate.json (fail-closed)."""
    raw_path = str(path)
    if not raw_path.strip():
        raise RenderError("укажите путь к файлу отчёта")
    if "\x00" in raw_path or any(ord(ch) < 32 for ch in raw_path):
        raise RenderError("путь к отчёту содержит недопустимые управляющие символы")

    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(raw_path, flags)
    except FileNotFoundError as missing:
        raise RenderError(f"файл отчёта не найден: {raw_path}") from missing
    except OSError as io_error:
        raise RenderError(f"не удалось открыть отчёт {raw_path}: {io_error}") from io_error

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RenderError(f"путь к отчёту должен быть обычным файлом: {raw_path}")
        handle = os.fdopen(descriptor, "rb")
    except RenderError:
        os.close(descriptor)
        raise
    except OSError as io_error:
        os.close(descriptor)
        raise RenderError(f"не удалось открыть отчёт {raw_path}: {io_error}") from io_error

    with handle:
        try:
            raw_bytes = handle.read()
        except OSError as io_error:
            raise RenderError(f"не удалось прочитать отчёт {raw_path}: {io_error}") from io_error

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as decode_error:
        raise RenderError(f"некорректная кодировка UTF-8 в {raw_path}: {decode_error}") from decode_error

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as json_error:
        raise RenderError(f"некорректный JSON в {raw_path}: {json_error}") from json_error

    detect_report_kind(payload)
    return payload


def write_rendered_output(out_path: Path | str, content: str) -> Path:
    """Атомарно записать сгенерированный отчёт: временный файл в том же каталоге + fsync + os.replace."""
    raw_path = str(out_path)
    if not raw_path.strip():
        raise RenderError("--out: укажите путь к выходному файлу")
    if "\x00" in raw_path or any(ord(ch) < 32 for ch in raw_path):
        raise RenderError("--out: путь содержит недопустимые управляющие символы")

    target = Path(raw_path)
    if target.exists() and target.is_dir():
        raise RenderError(f"--out указывает на каталог, а не на файл: {target}")
    parent = target.parent
    if not parent.exists() or not parent.is_dir():
        raise RenderError(f"--out: родительский каталог не существует: {parent}")

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as io_error:
        raise RenderError(f"не удалось записать {target}: {io_error}") from io_error
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return target


# --- Вспомогательные функции извлечения и форматирования ---


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _format_scalar(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _ordered_keys(mapping: dict, preferred: tuple[str, ...]) -> list[str]:
    ordered = [key for key in preferred if key in mapping]
    extras = sorted(key for key in mapping if key not in preferred)
    return ordered + extras


def _warnings_list(payload: dict) -> list[str]:
    raw = payload.get("warnings")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item is not None and str(item) != ""]


def _check_detail_items(name: str, entry: dict) -> list[tuple[str, object]]:
    """Вернуть упорядоченный список (ключ, значение) подробностей проверки."""
    preferred_map: dict[str, tuple[str, ...]] = {
        "base_ref": ("ref", "ref_source", "ref_source_kind", "ref_commit", "resolved_base_auto"),
        "ebt_trust": (
            "reason",
            "pinned_from_ref",
            "ref_source",
            "ref_source_kind",
            "actual_tree_sha256",
            "spec_sha256",
        ),
        "ancestry": ("base_ancestor_of_repro", "repro_ancestor_of_head", "base_ancestor_of_head"),
        "protected_paths": ("specs", "changed_base_repro", "changed_base_head"),
        "repro_tests": ("reason", "changed_base_repro", "outside_tests", "modified_after_repro"),
        "refactor": ("changed_base_head",),
    }
    preferred = preferred_map.get(name, ())
    keys = [k for k in preferred if k in entry and k != "status"]
    keys.extend(sorted(k for k in entry if k not in preferred and k != "status"))
    return [(k, entry[k]) for k in keys]


# --- Markdown ---


def _md_single_line(value: object) -> str:
    text = str(value)
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _md_cell(value: object) -> str:
    """Экранировать текст для ячейки Markdown-таблицы (без разрыва строк и столбцов)."""
    return _md_escape_pipes(_md_single_line(value))


def _md_escape_pipes(text: str) -> str:
    out: list[str] = []
    backslashes = 0
    for ch in text:
        if ch == "\\":
            backslashes += 1
            out.append(ch)
        elif ch == "|":
            # Удвоить предшествующие обратные слеши, чтобы они не экранировали \|
            if backslashes:
                out.append("\\" * backslashes)
                backslashes = 0
            out.append(r"\|")
        else:
            backslashes = 0
            out.append(ch)
    if backslashes:
        # Хвостовой обратный слеш не должен экранировать разделитель столбца ` |`
        out.append(" ")
    return "".join(out)


def _md_code_span(value: object) -> str:
    """Безопасный inline-code фрагмент для Markdown (учитывает обратные кавычки и |)."""
    escaped = _md_cell(value)
    runs = _BACKTICK_RUN_RE.findall(escaped)
    fence = "`" * (max((len(r) for r in runs), default=0) + 1)
    if escaped.startswith("`") or escaped.endswith("`") or (escaped.startswith(" ") and escaped.endswith(" ")):
        return f"{fence} {escaped} {fence}"
    return f"{fence}{escaped}{fence}"


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _md_code_or_dash(value: object) -> str:
    if value is None:
        return "—"
    return _md_code_span(value)


def _md_list_codes(items: list) -> str:
    if not items:
        return "—"
    return ", ".join(_md_code_span(item) for item in items)


def _md_check_details(name: str, entry: dict) -> str:
    items = _check_detail_items(name, entry)
    if not items:
        return "—"
    parts: list[str] = []
    for key, val in items:
        if isinstance(val, list):
            rendered = _md_list_codes(val) if val else "(none)"
            parts.append(f"{_md_code_span(key)}: {rendered}")
        elif val is None:
            parts.append(f"{_md_code_span(key)}: —")
        elif isinstance(val, bool):
            parts.append(f"{_md_code_span(key)}: `{_format_scalar(val)}`")
        else:
            parts.append(f"{_md_code_span(key)}: {_md_code_span(val)}")
    return "; ".join(parts)


def render_markdown(payload: dict) -> str:
    """Сформировать Markdown-отчёт из словаря accept.json или calibrate.json (format 1)."""
    kind = detect_report_kind(payload)
    result = str(payload["result"])
    policy = payload.get("policy") if kind == "accept" else None
    mode = payload.get("mode")
    scope = payload.get("scope")
    reason_code = payload.get("reason_code")
    exit_code = payload.get("exit_code")
    message = payload.get("message")
    warnings = _warnings_list(payload)
    repo = _as_dict(payload.get("repo"))
    inputs = _as_dict(payload.get("inputs"))
    config_block = payload.get("inputs", {}).get("config") if isinstance(payload.get("inputs"), dict) else None
    witnessbox_block = payload.get("witnessbox") if isinstance(payload.get("witnessbox"), dict) else None

    title_parts = [f"AcceptBox: {result}"]
    if policy is not None:
        title_parts.append(f"[policy={_md_single_line(policy)}]")
    if reason_code:
        title_parts.append(f"({_md_single_line(reason_code)})")

    lines: list[str] = [f"# {' '.join(title_parts)}", ""]

    # Сводка вердикта наверху (L31)
    verdict_rows: list[list[str]] = [
        ["Result", _md_code_span(result)],
    ]
    if kind == "accept":
        verdict_rows.append(["Policy", _md_code_or_dash(policy)])
    verdict_rows.extend([
        ["Mode", _md_code_or_dash(mode)],
        ["Reason code", _md_code_or_dash(reason_code)],
        ["Exit code", _md_code_or_dash(exit_code)],
        ["Message", _md_cell(message) if message is not None else "—"],
    ])
    lines.extend(_md_table(["Field", "Value"], verdict_rows))
    lines.append("")

    # Предупреждения — всегда заметно наверху, никогда не сворачиваются (L31)
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for idx, warning in enumerate(warnings):
            if idx > 0:
                lines.append(">")
            lines.append(f"> **WARNING:** {_md_single_line(warning)}")
        lines.append("")

    # Репозиторий, коммиты и метаданные запуска
    lines.append("## Repository & Commits")
    lines.append("")
    repo_rows: list[list[str]] = [
        ["source", _md_code_or_dash(repo.get("source"))],
        ["base", _md_code_or_dash(repo.get("base"))],
    ]
    if kind == "accept":
        repo_rows.extend([
            ["repro", _md_code_or_dash(repo.get("repro"))],
            ["head", _md_code_or_dash(repo.get("head"))],
            ["head_tree", _md_code_or_dash(repo.get("head_tree"))],
        ])
    repo_rows.extend([
        ["scope", _md_code_or_dash(scope)],
        ["acceptbox_version", _md_code_or_dash(payload.get("acceptbox_version"))],
        ["run_id", _md_code_or_dash(payload.get("run_id"))],
        ["started_at", _md_code_or_dash(payload.get("started_at"))],
        ["finished_at", _md_code_or_dash(payload.get("finished_at"))],
    ])
    lines.extend(_md_table(["Key", "Value"], repo_rows))
    lines.append("")

    # Входные параметры и конфигурация
    lines.append("## Inputs & Configuration")
    lines.append("")
    input_rows: list[list[str]] = []
    if isinstance(config_block, dict):
        input_rows.extend([
            ["config.path", _md_code_or_dash(config_block.get("path"))],
            ["config.commit", _md_code_or_dash(config_block.get("commit"))],
            ["config.sha256", _md_code_or_dash(config_block.get("sha256"))],
        ])
    else:
        input_rows.append(["config", "—"])

    if kind == "accept":
        input_rows.extend([
            ["base_ref", _md_code_or_dash(inputs.get("base_ref"))],
            ["strict_refs", _md_code_or_dash(_format_scalar(inputs.get("strict_refs")) if "strict_refs" in inputs else None)],
            ["repro_source", _md_code_or_dash(inputs.get("repro_source"))],
        ])

    input_rows.append(["timeout_sec", _md_code_or_dash(inputs.get("timeout_sec"))])
    if "protect" in inputs:
        input_rows.append(["protect", _md_list_codes(_as_list(inputs.get("protect")))])
    if "env_keys" in inputs:
        input_rows.append(["env_keys", _md_list_codes(_as_list(inputs.get("env_keys")))])
    if "tests" in inputs:
        tests_val = inputs.get("tests")
        input_rows.append(["tests", _md_list_codes(tests_val) if isinstance(tests_val, list) else "—"])
    if "command" in inputs:
        cmd_val = inputs.get("command")
        input_rows.append(["command", _md_code_span(" ".join(str(x) for x in cmd_val)) if isinstance(cmd_val, list) else "—"])
    if "inv" in inputs:
        inv_val = inputs.get("inv")
        input_rows.append(["inv", _md_list_codes(inv_val) if isinstance(inv_val, list) else "—"])

    ebt_block = inputs.get("ebt")
    if isinstance(ebt_block, dict):
        for ebt_key in (
            "repo",
            "ref",
            "sha",
            "pinned_from_ref",
            "approved_tree_sha256",
            "actual_tree_sha256",
            "spec_sha256",
            "ref_source",
            "ref_source_kind",
        ):
            if ebt_key in ebt_block:
                val = ebt_block.get(ebt_key)
                input_rows.append([
                    f"ebt.{ebt_key}",
                    _md_code_or_dash(_format_scalar(val) if val is not None else None),
                ])

    lines.extend(_md_table(["Parameter", "Value"], input_rows))
    lines.append("")

    # Блок WitnessBox (если присутствует)
    if witnessbox_block is not None:
        lines.append("## WitnessBox")
        lines.append("")
        wb_rows = [
            ["version", _md_code_or_dash(witnessbox_block.get("version"))],
            ["gate_sha256", _md_code_or_dash(witnessbox_block.get("gate_sha256"))],
            ["checker_sha256", _md_code_or_dash(witnessbox_block.get("checker_sha256"))],
        ]
        lines.extend(_md_table(["Field", "Value"], wb_rows))
        lines.append("")

    if kind == "accept":
        # Таблица проверок
        checks = _as_dict(payload.get("checks"))
        lines.append("## Checks")
        lines.append("")
        check_rows: list[list[str]] = []
        for check_name in _ordered_keys(checks, _CHECK_ORDER):
            entry = _as_dict(checks.get(check_name))
            status = entry.get("status")
            check_rows.append([
                _md_code_span(check_name),
                _md_code_or_dash(status),
                _md_check_details(check_name, entry),
            ])
        lines.extend(_md_table(["Check", "Status", "Details"], check_rows))
        lines.append("")

        # Таблица прогонов
        runs = _as_dict(payload.get("runs"))
        lines.append("## Runs")
        lines.append("")
        run_rows: list[list[str]] = []
        for run_name in _ordered_keys(runs, _RUN_ORDER):
            entry = _as_dict(runs.get(run_name))
            run_rows.append([
                _md_code_span(run_name),
                _md_code_or_dash(entry.get("commit")),
                _md_code_or_dash(entry.get("status")),
                _md_code_or_dash(entry.get("exit_code")),
                _md_code_or_dash(entry.get("duration_ms")),
                _md_cell(entry.get("reason")) if entry.get("reason") is not None else "—",
                _md_code_or_dash(entry.get("log_path")),
            ])
        lines.extend(_md_table(
            ["Run", "Commit", "Status", "Exit Code", "Duration (ms)", "Reason", "Log Path"],
            run_rows,
        ))
        lines.append("")

        # Покрытие инвариантов
        cov = payload.get("coverage")
        if isinstance(cov, dict):
            lines.append("## Coverage")
            lines.append("")
            cov_rows = [
                ["checked", _md_list_codes(_as_list(cov.get("checked")))],
                ["skipped", _md_list_codes(_as_list(cov.get("skipped")))],
                ["unchecked", _md_list_codes(_as_list(cov.get("unchecked")))],
            ]
            lines.extend(_md_table(["Category", "Items"], cov_rows))
            lines.append("")
    else:
        # Таблица результатов калибровки
        results = _as_list(payload.get("results"))
        lines.append("## Results")
        lines.append("")
        res_rows: list[list[str]] = []
        for entry in results:
            item = _as_dict(entry)
            matched_val = item.get("matched")
            res_rows.append([
                _md_code_or_dash(item.get("commit")),
                _md_code_or_dash(item.get("expected")),
                _md_code_or_dash(item.get("status")),
                _md_code_or_dash(_format_scalar(matched_val) if matched_val is not None else None),
                _md_code_or_dash(item.get("exit_code")),
                _md_code_or_dash(item.get("duration_ms")),
                _md_cell(item.get("reason")) if item.get("reason") is not None else "—",
                _md_code_or_dash(item.get("log_path")),
            ])
        lines.extend(_md_table(
            ["Commit", "Expected", "Status", "Matched", "Exit Code", "Duration (ms)", "Reason", "Log Path"],
            res_rows,
        ))
        lines.append("")

    return "\n".join(lines)


# --- HTML5 ---


_HTML_CSS = """\
:root {
  --bg: #f8fafc;
  --card: #ffffff;
  --text: #0f172a;
  --muted: #475569;
  --border: #e2e8f0;
  --code-bg: #f1f5f9;
  --ok-bg: #ecfdf5;
  --ok-border: #10b981;
  --ok-text: #065f46;
  --fail-bg: #fef2f2;
  --fail-border: #ef4444;
  --fail-text: #991b1b;
  --err-bg: #fff7ed;
  --err-border: #ea580c;
  --err-text: #9a3412;
  --warn-bg: #fffbeb;
  --warn-border: #f59e0b;
  --warn-text: #92400e;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px 16px 48px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.5;
  color: var(--text);
  background: var(--bg);
}
main {
  max-width: 1100px;
  margin: 0 auto;
}
.banner {
  border-radius: 10px;
  padding: 20px 24px;
  border-left: 6px solid var(--border);
  background: var(--card);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  margin-bottom: 20px;
}
.banner-ok { background: var(--ok-bg); border-left-color: var(--ok-border); color: var(--ok-text); }
.banner-fail { background: var(--fail-bg); border-left-color: var(--fail-border); color: var(--fail-text); }
.banner-error { background: var(--err-bg); border-left-color: var(--err-border); color: var(--err-text); }
.banner h1 {
  margin: 0 0 8px;
  font-size: 1.5rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.85rem;
  font-weight: 600;
  background: rgba(15, 23, 42, 0.08);
  color: inherit;
}
.banner-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 0.92rem;
  margin-bottom: 8px;
}
.banner-message {
  margin: 6px 0 0;
  font-size: 1rem;
  font-weight: 500;
}
.warnings {
  background: var(--warn-bg);
  border: 1px solid var(--warn-border);
  border-left: 6px solid var(--warn-border);
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 20px;
  color: var(--warn-text);
}
.warnings h2 {
  margin: 0 0 8px;
  font-size: 1.05rem;
}
.warnings ul {
  margin: 0;
  padding-left: 20px;
}
.warnings li {
  margin: 4px 0;
}
section.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
  margin-bottom: 18px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
section.card h2 {
  margin: 0 0 12px;
  font-size: 1.15rem;
  color: var(--text);
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}
th, td {
  text-align: left;
  vertical-align: top;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
}
th {
  font-weight: 600;
  color: var(--muted);
  background: #f8fafc;
  white-space: nowrap;
}
tr:last-child td {
  border-bottom: none;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.88em;
  background: var(--code-bg);
  padding: 1px 5px;
  border-radius: 4px;
  word-break: break-word;
}
.kv-key {
  width: 240px;
  white-space: nowrap;
  font-weight: 600;
  color: var(--muted);
}
.status {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.status-ok { background: var(--ok-bg); color: var(--ok-text); }
.status-fail { background: var(--fail-bg); color: var(--fail-text); }
.status-warn { background: var(--warn-bg); color: var(--warn-text); }
.status-muted { background: var(--code-bg); color: var(--muted); }
.detail-list {
  margin: 0;
  padding-left: 16px;
}
.detail-list li {
  margin: 2px 0;
}
.muted { color: var(--muted); }
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _html_code_or_dash(value: object) -> str:
    if value is None:
        return '<span class="muted">—</span>'
    return f"<code>{_e(value)}</code>"


def _html_list_codes(items: list) -> str:
    if not items:
        return '<span class="muted">—</span>'
    return ", ".join(f"<code>{_e(item)}</code>" for item in items)


def _status_badge(status: object) -> str:
    if status is None:
        return '<span class="muted">—</span>'
    text = str(status)
    if text in ("ok", "green", "ACCEPTED", "CALIBRATED", "true"):
        cls = "status-ok"
    elif text in ("failed", "red", "red-other", "invalid-report", "REJECTED", "FAILED", "false"):
        cls = "status-fail"
    elif text in ("error", "ERROR"):
        cls = "status-warn"
    else:
        cls = "status-muted"
    return f'<span class="status {cls}">{_e(text)}</span>'


def _banner_class(result: str) -> str:
    if result in ("ACCEPTED", "CALIBRATED"):
        return "banner-ok"
    if result in ("REJECTED", "FAILED"):
        return "banner-fail"
    return "banner-error"


def _html_kv_table(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td class="kv-key">{_e(label)}</td><td>{value_html}</td></tr>'
        for label, value_html in rows
    )
    return f'<div class="table-wrap"><table><tbody>{body}</tbody></table></div>'


def _html_check_details(name: str, entry: dict) -> str:
    items = _check_detail_items(name, entry)
    if not items:
        return '<span class="muted">—</span>'
    parts: list[str] = []
    for key, val in items:
        if isinstance(val, list):
            if val:
                rendered = ", ".join(f"<code>{_e(item)}</code>" for item in val)
            else:
                rendered = '<span class="muted">(none)</span>'
        elif val is None:
            rendered = '<span class="muted">—</span>'
        elif isinstance(val, bool):
            rendered = f"<code>{_e(_format_scalar(val))}</code>"
        else:
            rendered = f"<code>{_e(val)}</code>"
        parts.append(f"<li><strong>{_e(key)}</strong>: {rendered}</li>")
    return f'<ul class="detail-list">{"".join(parts)}</ul>'


def render_html(payload: dict) -> str:
    """Сформировать автономный HTML5-отчёт (без внешних ресурсов) из accept.json / calibrate.json."""
    kind = detect_report_kind(payload)
    result = str(payload["result"])
    policy = payload.get("policy") if kind == "accept" else None
    mode = payload.get("mode")
    scope = payload.get("scope")
    reason_code = payload.get("reason_code")
    exit_code = payload.get("exit_code")
    message = payload.get("message")
    warnings = _warnings_list(payload)
    repo = _as_dict(payload.get("repo"))
    inputs = _as_dict(payload.get("inputs"))
    config_block = inputs.get("config") if isinstance(inputs.get("config"), dict) else None
    witnessbox_block = payload.get("witnessbox") if isinstance(payload.get("witnessbox"), dict) else None

    title_parts = [f"AcceptBox: {result}"]
    if policy is not None:
        title_parts.append(f"[policy={policy}]")
    if reason_code:
        title_parts.append(f"({reason_code})")
    title_text = " ".join(title_parts)

    badges: list[str] = []
    if policy is not None:
        badges.append(f'<span class="badge">[policy={_e(policy)}]</span>')
    if mode is not None:
        badges.append(f'<span class="badge">mode={_e(mode)}</span>')
    if reason_code:
        badges.append(f'<span class="badge">{_e(reason_code)}</span>')

    meta_items: list[str] = []
    if exit_code is not None:
        meta_items.append(f"<span><strong>Exit code:</strong> <code>{_e(exit_code)}</code></span>")
    if scope is not None:
        meta_items.append(f"<span><strong>Scope:</strong> <code>{_e(scope)}</code></span>")
    if payload.get("acceptbox_version") is not None:
        meta_items.append(
            f"<span><strong>AcceptBox:</strong> <code>{_e(payload.get('acceptbox_version'))}</code></span>"
        )

    sections: list[str] = []

    # Баннер вердикта (L31)
    message_html = (
        f'<p class="banner-message">{_e(message)}</p>'
        if message is not None else ""
    )
    sections.append(
        f'<header class="banner {_banner_class(result)}">'
        f'<h1>{_e(result)} {" ".join(badges)}</h1>'
        f'<div class="banner-meta">{"".join(meta_items)}</div>'
        f'{message_html}'
        f'</header>'
    )

    # Предупреждения — заметно наверху, никогда не сворачиваются (L31)
    if warnings:
        warn_items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
        sections.append(
            f'<section class="warnings" aria-label="Warnings">'
            f'<h2>Warnings ({len(warnings)})</h2>'
            f'<ul>{warn_items}</ul>'
            f'</section>'
        )

    # Репозиторий и коммиты
    repo_rows: list[tuple[str, str]] = [
        ("source", _html_code_or_dash(repo.get("source"))),
        ("base", _html_code_or_dash(repo.get("base"))),
    ]
    if kind == "accept":
        repo_rows.extend([
            ("repro", _html_code_or_dash(repo.get("repro"))),
            ("head", _html_code_or_dash(repo.get("head"))),
            ("head_tree", _html_code_or_dash(repo.get("head_tree"))),
        ])
    repo_rows.extend([
        ("run_id", _html_code_or_dash(payload.get("run_id"))),
        ("started_at", _html_code_or_dash(payload.get("started_at"))),
        ("finished_at", _html_code_or_dash(payload.get("finished_at"))),
    ])
    sections.append(
        '<section class="card"><h2>Repository &amp; Commits</h2>'
        + _html_kv_table(repo_rows)
        + "</section>"
    )

    # Входные параметры и конфигурация
    input_rows: list[tuple[str, str]] = []
    if config_block is not None:
        input_rows.extend([
            ("config.path", _html_code_or_dash(config_block.get("path"))),
            ("config.commit", _html_code_or_dash(config_block.get("commit"))),
            ("config.sha256", _html_code_or_dash(config_block.get("sha256"))),
        ])
    else:
        input_rows.append(("config", '<span class="muted">—</span>'))

    if kind == "accept":
        input_rows.extend([
            ("base_ref", _html_code_or_dash(inputs.get("base_ref"))),
            ("strict_refs", _html_code_or_dash(
                _format_scalar(inputs.get("strict_refs")) if "strict_refs" in inputs else None
            )),
            ("repro_source", _html_code_or_dash(inputs.get("repro_source"))),
        ])

    input_rows.append(("timeout_sec", _html_code_or_dash(inputs.get("timeout_sec"))))
    if "protect" in inputs:
        input_rows.append(("protect", _html_list_codes(_as_list(inputs.get("protect")))))
    if "env_keys" in inputs:
        input_rows.append(("env_keys", _html_list_codes(_as_list(inputs.get("env_keys")))))
    if "tests" in inputs:
        tests_val = inputs.get("tests")
        input_rows.append(("tests", _html_list_codes(tests_val) if isinstance(tests_val, list) else '<span class="muted">—</span>'))
    if "command" in inputs:
        cmd_val = inputs.get("command")
        input_rows.append((
            "command",
            _html_code_or_dash(" ".join(str(x) for x in cmd_val)) if isinstance(cmd_val, list) else '<span class="muted">—</span>',
        ))
    if "inv" in inputs:
        inv_val = inputs.get("inv")
        input_rows.append(("inv", _html_list_codes(inv_val) if isinstance(inv_val, list) else '<span class="muted">—</span>'))

    ebt_block = inputs.get("ebt")
    if isinstance(ebt_block, dict):
        for ebt_key in (
            "repo",
            "ref",
            "sha",
            "pinned_from_ref",
            "approved_tree_sha256",
            "actual_tree_sha256",
            "spec_sha256",
            "ref_source",
            "ref_source_kind",
        ):
            if ebt_key in ebt_block:
                val = ebt_block.get(ebt_key)
                input_rows.append((
                    f"ebt.{ebt_key}",
                    _html_code_or_dash(_format_scalar(val) if val is not None else None),
                ))

    sections.append(
        '<section class="card"><h2>Inputs &amp; Configuration</h2>'
        + _html_kv_table(input_rows)
        + "</section>"
    )

    # Блок WitnessBox (если присутствует)
    if witnessbox_block is not None:
        wb_rows = [
            ("version", _html_code_or_dash(witnessbox_block.get("version"))),
            ("gate_sha256", _html_code_or_dash(witnessbox_block.get("gate_sha256"))),
            ("checker_sha256", _html_code_or_dash(witnessbox_block.get("checker_sha256"))),
        ]
        sections.append(
            '<section class="card"><h2>WitnessBox</h2>'
            + _html_kv_table(wb_rows)
            + "</section>"
        )

    if kind == "accept":
        checks = _as_dict(payload.get("checks"))
        check_trs: list[str] = []
        for check_name in _ordered_keys(checks, _CHECK_ORDER):
            entry = _as_dict(checks.get(check_name))
            check_trs.append(
                "<tr>"
                f"<td><code>{_e(check_name)}</code></td>"
                f"<td>{_status_badge(entry.get('status'))}</td>"
                f"<td>{_html_check_details(check_name, entry)}</td>"
                "</tr>"
            )
        sections.append(
            '<section class="card"><h2>Checks</h2>'
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Check</th><th>Status</th><th>Details</th></tr></thead>"
            f'<tbody>{"".join(check_trs)}</tbody>'
            "</table></div></section>"
        )

        runs = _as_dict(payload.get("runs"))
        run_trs: list[str] = []
        for run_name in _ordered_keys(runs, _RUN_ORDER):
            entry = _as_dict(runs.get(run_name))
            reason_val = entry.get("reason")
            reason_html = _e(reason_val) if reason_val is not None else '<span class="muted">—</span>'
            run_trs.append(
                "<tr>"
                f"<td><code>{_e(run_name)}</code></td>"
                f"<td>{_html_code_or_dash(entry.get('commit'))}</td>"
                f"<td>{_status_badge(entry.get('status'))}</td>"
                f"<td>{_html_code_or_dash(entry.get('exit_code'))}</td>"
                f"<td>{_html_code_or_dash(entry.get('duration_ms'))}</td>"
                f"<td>{reason_html}</td>"
                f"<td>{_html_code_or_dash(entry.get('log_path'))}</td>"
                "</tr>"
            )
        sections.append(
            '<section class="card"><h2>Runs</h2>'
            '<div class="table-wrap"><table>'
            "<thead><tr>"
            "<th>Run</th><th>Commit</th><th>Status</th><th>Exit Code</th>"
            "<th>Duration (ms)</th><th>Reason</th><th>Log Path</th>"
            "</tr></thead>"
            f'<tbody>{"".join(run_trs)}</tbody>'
            "</table></div></section>"
        )

        cov = payload.get("coverage")
        if isinstance(cov, dict):
            cov_rows = [
                ("checked", _html_list_codes(_as_list(cov.get("checked")))),
                ("skipped", _html_list_codes(_as_list(cov.get("skipped")))),
                ("unchecked", _html_list_codes(_as_list(cov.get("unchecked")))),
            ]
            sections.append(
                '<section class="card"><h2>Coverage</h2>'
                + _html_kv_table(cov_rows)
                + "</section>"
            )
    else:
        results = _as_list(payload.get("results"))
        res_trs: list[str] = []
        for entry in results:
            item = _as_dict(entry)
            matched_val = item.get("matched")
            matched_html = (
                _status_badge(_format_scalar(matched_val))
                if matched_val is not None else '<span class="muted">—</span>'
            )
            reason_val = item.get("reason")
            reason_html = _e(reason_val) if reason_val is not None else '<span class="muted">—</span>'
            res_trs.append(
                "<tr>"
                f"<td>{_html_code_or_dash(item.get('commit'))}</td>"
                f"<td>{_html_code_or_dash(item.get('expected'))}</td>"
                f"<td>{_status_badge(item.get('status'))}</td>"
                f"<td>{matched_html}</td>"
                f"<td>{_html_code_or_dash(item.get('exit_code'))}</td>"
                f"<td>{_html_code_or_dash(item.get('duration_ms'))}</td>"
                f"<td>{reason_html}</td>"
                f"<td>{_html_code_or_dash(item.get('log_path'))}</td>"
                "</tr>"
            )
        sections.append(
            '<section class="card"><h2>Results</h2>'
            '<div class="table-wrap"><table>'
            "<thead><tr>"
            "<th>Commit</th><th>Expected</th><th>Status</th><th>Matched</th>"
            "<th>Exit Code</th><th>Duration (ms)</th><th>Reason</th><th>Log Path</th>"
            "</tr></thead>"
            f'<tbody>{"".join(res_trs)}</tbody>'
            "</table></div></section>"
        )

    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title_text)}</title>\n"
        f"<style>\n{_HTML_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        "<main>\n"
        + "\n".join(sections)
        + "\n</main>\n"
        "</body>\n"
        "</html>\n"
    )
