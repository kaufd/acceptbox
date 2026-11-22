"""Отображение отчётов accept.json и calibrate.json (format 1) в Markdown и HTML5 (L32).

Чистая функция от разобранного JSON-отчёта: не обращается к git, не запускает
подпроцессы и не читает внешние логи прогонов с диска.
"""

from __future__ import annotations

import html
import json
import os
import posixpath
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

REPORT_FORMAT = 1

_CHECK_ORDER = ("base_ref", "ebt_trust", "ancestry", "protected_paths", "repro_tests", "refactor")
_RUN_ORDER = ("base", "repro", "head")
_BACKTICK_RUN_RE = re.compile(r"`+")
# Знаки, из которых GFM делает разметку, HTML, формулы ($) или @упоминания.
_MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]<>&|~!#$@])")


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


def write_rendered_output(out_path: Path | str, content: str, *,
                          source: Path | str | None = None) -> Path:
    """Атомарно записать сгенерированный отчёт: временный файл в том же каталоге + fsync + os.replace.

    ``source`` — исходный отчёт: --out, указывающий на него, отклоняется (иначе отчёт затирается).
    """
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
    if source is not None and target.exists() and os.path.samefile(source, target):
        raise RenderError(f"--out совпадает с исходным отчётом: {target}")

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


def _md_text(value: object) -> str:
    """Обычный текст для Markdown: одна строка, спецсимволы GFM экранированы.

    Иначе путь ``src/pkg/__init__.py`` станет жирным, ``<module>`` исчезнет как тег,
    а ``@name`` в комментарии PR уведомит пользователя.
    """
    return _MD_SPECIAL_RE.sub(r"\\\1", _md_single_line(value))


def _md_details(items: list[tuple[str, object]]) -> str:
    if not items:
        return "—"
    parts: list[str] = []
    for key, val in items:
        if isinstance(val, list):
            rendered = _md_list_codes(val) if val else "(none)"
        elif val is None:
            rendered = "—"
        else:
            rendered = _md_code_span(_format_scalar(val))
        parts.append(f"{_md_code_span(key)}: {rendered}")
    return "; ".join(parts)


# --- Общая модель отчёта: одна структура, два сериализатора (Markdown и HTML) ---


@dataclass(frozen=True)
class _Cell:
    """Значение ячейки. kind: label (статическая подпись) | text | code | codes | status | details | log."""

    kind: str
    value: object = None


@dataclass(frozen=True)
class _Section:
    title: str
    headers: tuple[str, ...]
    rows: list[list[_Cell]]
    kv: bool = False  # таблица «ключ — значение»: в HTML без шапки, ключ выделен


@dataclass(frozen=True)
class _Report:
    kind: str
    result: str
    policy: object
    mode: object
    reason_code: object
    exit_code: object
    scope: object
    version: object
    message: object
    warnings: list[str]
    sections: list[_Section]


def _code(value: object) -> _Cell:
    return _Cell("code", value)


def _codes(value: object) -> _Cell:
    return _Cell("codes", value if isinstance(value, list) else [])


def _scalar(value: object) -> _Cell:
    return _code(None if value is None else _format_scalar(value))


def _kv(title: str, key_header: str, rows: list[tuple[str, _Cell]]) -> _Section:
    return _Section(title, (key_header, "Value" if key_header != "Category" else "Items"),
                    [[_Cell("label", label), cell] for label, cell in rows], kv=True)


def _build_report(payload: dict) -> _Report:
    kind = detect_report_kind(payload)
    repo = _as_dict(payload.get("repo"))
    inputs = _as_dict(payload.get("inputs"))
    sections: list[_Section] = []

    repo_rows = [("source", _code(repo.get("source"))), ("base", _code(repo.get("base")))]
    if kind == "accept":
        repo_rows += [(key, _code(repo.get(key))) for key in ("repro", "head", "head_tree")]
    repo_rows += [(key, _code(payload.get(key))) for key in ("run_id", "started_at", "finished_at")]
    sections.append(_kv("Repository & Commits", "Key", repo_rows))

    input_rows: list[tuple[str, _Cell]] = []
    config_block = inputs.get("config")
    if isinstance(config_block, dict):
        input_rows += [(f"config.{key}", _code(config_block.get(key)))
                       for key in ("path", "commit", "sha256")]
    else:
        input_rows.append(("config", _code(None)))
    if kind == "accept":
        input_rows += [
            ("base_ref", _code(inputs.get("base_ref"))),
            ("strict_refs", _scalar(inputs.get("strict_refs"))),
            ("repro_source", _code(inputs.get("repro_source"))),
        ]
    input_rows.append(("timeout_sec", _code(inputs.get("timeout_sec"))))
    for key in ("protect", "env_keys", "tests"):
        if key in inputs:
            input_rows.append((key, _codes(inputs.get(key))))
    if "command" in inputs:
        command = inputs.get("command")
        input_rows.append(("command", _code(" ".join(str(part) for part in command)
                                            if isinstance(command, list) else None)))
    if "inv" in inputs:
        input_rows.append(("inv", _codes(inputs.get("inv"))))
    ebt_block = inputs.get("ebt")
    if isinstance(ebt_block, dict):
        for key in ("repo", "ref", "sha", "pinned_from_ref", "approved_tree_sha256",
                    "actual_tree_sha256", "spec_sha256", "ref_source", "ref_source_kind"):
            if key in ebt_block:
                input_rows.append((f"ebt.{key}", _scalar(ebt_block.get(key))))
    sections.append(_kv("Inputs & Configuration", "Parameter", input_rows))

    witnessbox_block = payload.get("witnessbox")
    if isinstance(witnessbox_block, dict):
        sections.append(_kv("WitnessBox", "Field", [
            (key, _code(witnessbox_block.get(key)))
            for key in ("version", "gate_sha256", "checker_sha256")]))

    if kind == "accept":
        checks = _as_dict(payload.get("checks"))
        sections.append(_Section("Checks", ("Check", "Status", "Details"), [
            [_code(name), _Cell("status", _as_dict(checks.get(name)).get("status")),
             _Cell("details", _check_detail_items(name, _as_dict(checks.get(name))))]
            for name in _ordered_keys(checks, _CHECK_ORDER)]))
        runs = _as_dict(payload.get("runs"))
        run_rows = []
        for name in _ordered_keys(runs, _RUN_ORDER):
            entry = _as_dict(runs.get(name))
            run_rows.append([
                _code(name), _code(entry.get("commit")), _Cell("status", entry.get("status")),
                _code(entry.get("exit_code")), _code(entry.get("duration_ms")),
                _Cell("text", entry.get("reason")), _Cell("log", entry.get("log_path"))])
        sections.append(_Section(
            "Runs", ("Run", "Commit", "Status", "Exit Code", "Duration (ms)", "Reason", "Log Path"),
            run_rows))
        cov = payload.get("coverage")
        if isinstance(cov, dict):
            sections.append(_kv("Coverage", "Category", [
                (key, _codes(cov.get(key))) for key in ("checked", "skipped", "unchecked")]))
    else:
        result_rows = []
        for entry in _as_list(payload.get("results")):
            item = _as_dict(entry)
            matched = item.get("matched")
            result_rows.append([
                _code(item.get("commit")), _code(item.get("expected")),
                _Cell("status", item.get("status")),
                _Cell("status", None if matched is None else _format_scalar(matched)),
                _code(item.get("exit_code")), _code(item.get("duration_ms")),
                _Cell("text", item.get("reason")), _Cell("log", item.get("log_path"))])
        sections.append(_Section(
            "Results", ("Commit", "Expected", "Status", "Matched", "Exit Code", "Duration (ms)",
                        "Reason", "Log Path"), result_rows))

    return _Report(
        kind=kind, result=str(payload["result"]),
        policy=payload.get("policy") if kind == "accept" else None,
        mode=payload.get("mode"), reason_code=payload.get("reason_code"),
        exit_code=payload.get("exit_code"), scope=payload.get("scope"),
        version=payload.get("acceptbox_version"), message=payload.get("message"),
        warnings=_warnings_list(payload), sections=sections)


# --- Markdown ---


def _md_value(cell: _Cell) -> str:
    if cell.kind == "codes":
        return _md_list_codes(cell.value)
    if cell.kind == "details":
        return _md_details(cell.value)
    if cell.kind == "label":
        return str(cell.value)  # подписи задаёт сам рендерер — экранировать нечего
    if cell.kind == "text":
        return "—" if cell.value is None else _md_text(cell.value)
    return _md_code_or_dash(cell.value)  # code, status, log


def render_markdown(payload: dict) -> str:
    """Сформировать Markdown-отчёт из словаря accept.json или calibrate.json (format 1)."""
    report = _build_report(payload)
    title = f"# AcceptBox: {_md_text(report.result)}"
    if report.policy is not None:
        title += f" [policy={_md_text(report.policy)}]"
    if report.reason_code:
        title += f" ({_md_text(report.reason_code)})"
    lines = [title, ""]

    # Сводка вердикта наверху (L31)
    verdict_rows = [["Result", _md_code_span(report.result)]]
    if report.kind == "accept":
        verdict_rows.append(["Policy", _md_code_or_dash(report.policy)])
    verdict_rows += [
        ["Mode", _md_code_or_dash(report.mode)],
        ["Reason code", _md_code_or_dash(report.reason_code)],
        ["Exit code", _md_code_or_dash(report.exit_code)],
        ["Scope", _md_code_or_dash(report.scope)],
        ["AcceptBox", _md_code_or_dash(report.version)],
        ["Message", "—" if report.message is None else _md_text(report.message)],
    ]
    lines += _md_table(["Field", "Value"], verdict_rows) + [""]

    # Предупреждения — всегда заметно наверху, никогда не сворачиваются (L31)
    if report.warnings:
        lines += ["## Warnings", ""]
        for index, warning in enumerate(report.warnings):
            if index:
                lines.append(">")
            lines.append(f"> **WARNING:** {_md_text(warning)}")
        lines.append("")

    for section in report.sections:
        lines += [f"## {section.title}", ""]
        lines += _md_table(list(section.headers),
                           [[_md_value(cell) for cell in row] for row in section.rows])
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


_DASH_HTML = '<span class="muted">—</span>'


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _html_code_or_dash(value: object) -> str:
    return _DASH_HTML if value is None else f"<code>{_e(value)}</code>"


def _html_list_codes(items: list) -> str:
    if not items:
        return _DASH_HTML
    return ", ".join(f"<code>{_e(item)}</code>" for item in items)


def _status_badge(status: object) -> str:
    if status is None:
        return _DASH_HTML
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


def _html_details(items: list[tuple[str, object]]) -> str:
    if not items:
        return _DASH_HTML
    parts: list[str] = []
    for key, val in items:
        if isinstance(val, list):
            rendered = _html_list_codes(val) if val else '<span class="muted">(none)</span>'
        else:
            rendered = _html_code_or_dash(None if val is None else _format_scalar(val))
        parts.append(f"<li><strong>{_e(key)}</strong>: {rendered}</li>")
    return f'<ul class="detail-list">{"".join(parts)}</ul>'


def _log_href(log_path: object, log_base: str) -> str | None:
    """Относительная ссылка на лог прогона (runs/...) или None, если путь не похож на него.

    Ссылка не читает файл: страница остаётся чистой функцией от отчёта.
    """
    if not isinstance(log_path, str) or not log_path or "\\" in log_path or ":" in log_path \
            or log_path.startswith("/") or any(ord(char) < 32 for char in log_path) \
            or ".." in log_path.split("/"):
        return None
    return quote(posixpath.join(log_base, log_path) if log_base else log_path, safe="/")


def _html_value(cell: _Cell, log_base: str) -> str:
    if cell.kind == "codes":
        return _html_list_codes(cell.value)
    if cell.kind == "details":
        return _html_details(cell.value)
    if cell.kind == "status":
        return _status_badge(cell.value)
    if cell.kind == "text":
        return _DASH_HTML if cell.value is None else _e(cell.value)
    if cell.kind == "log":
        href = _log_href(cell.value, log_base)
        if href is not None:
            return f'<a href="{_e(href)}"><code>{_e(cell.value)}</code></a>'
    return _html_code_or_dash(cell.value)  # code и лог без ссылки


def _html_section(section: _Section, log_base: str) -> str:
    if section.kv:
        body = "".join(
            f'<tr><td class="kv-key">{_e(row[0].value)}</td>'
            f"<td>{_html_value(row[1], log_base)}</td></tr>"
            for row in section.rows)
        table = f"<table><tbody>{body}</tbody></table>"
    else:
        head = "".join(f"<th>{_e(header)}</th>" for header in section.headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{_html_value(cell, log_base)}</td>" for cell in row) + "</tr>"
            for row in section.rows)
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return (f'<section class="card"><h2>{_e(section.title)}</h2>'
            f'<div class="table-wrap">{table}</div></section>')


def render_html(payload: dict, *, log_base: str = "") -> str:
    """Сформировать автономный HTML5-отчёт (без внешних ресурсов) из accept.json / calibrate.json.

    ``log_base`` — путь от места сохранения страницы до каталога отчёта: ссылки на логи
    прогонов строятся относительно него (пусто — страница лежит рядом с отчётом).
    """
    report = _build_report(payload)
    title_text = f"AcceptBox: {report.result}"
    badges: list[str] = []
    if report.policy is not None:
        title_text += f" [policy={report.policy}]"
        badges.append(f'<span class="badge">[policy={_e(report.policy)}]</span>')
    if report.mode is not None:
        badges.append(f'<span class="badge">mode={_e(report.mode)}</span>')
    if report.reason_code:
        title_text += f" ({report.reason_code})"
        badges.append(f'<span class="badge">{_e(report.reason_code)}</span>')
    meta = "".join(
        f"<span><strong>{label}:</strong> <code>{_e(value)}</code></span>"
        for label, value in (("Exit code", report.exit_code), ("Scope", report.scope),
                             ("AcceptBox", report.version))
        if value is not None)
    message = ("" if report.message is None
               else f'<p class="banner-message">{_e(report.message)}</p>')

    # Баннер вердикта и предупреждения — наверху, не сворачиваются (L31)
    parts = [f'<header class="banner {_banner_class(report.result)}">'
             f'<h1>{_e(report.result)} {" ".join(badges)}</h1>'
             f'<div class="banner-meta">{meta}</div>{message}</header>']
    if report.warnings:
        items = "".join(f"<li>{_e(warning)}</li>" for warning in report.warnings)
        parts.append(f'<section class="warnings" aria-label="Warnings">'
                     f"<h2>Warnings ({len(report.warnings)})</h2><ul>{items}</ul></section>")
    parts += [_html_section(section, log_base) for section in report.sections]

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
        + "\n".join(parts)
        + "\n</main>\n"
        "</body>\n"
        "</html>\n"
    )
