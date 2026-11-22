#!/usr/bin/env python3
"""Verify accept.json (including strict-refs guard) and render PR/status summaries.

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMENT_MARKER = "<!-- acceptbox-verdict -->"
MAX_LOG_LINES = 40


def _read_tail(path: Path, max_lines: int = MAX_LOG_LINES) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    return "\n".join(lines[-max_lines:])


def evaluate_report(report_path: Path | None, *, raw_exit_code: int,
                    strict_refs: bool = True, fallback_error: str = "") -> dict:
    """Validate accept.json independently and compute final status, description, markdown."""
    data = None
    if report_path is not None and report_path.is_file():
        try:
            parsed = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and parsed.get("format") == 1:
                data = parsed
        except (OSError, UnicodeError, json.JSONDecodeError):
            data = None

    if data is None:
        msg = fallback_error.strip() or "accept.json missing or invalid"
        md = (
            f"{COMMENT_MARKER}\n"
            f"### AcceptBox: `ERROR`\n\n"
            f"- **Exit code:** `{raw_exit_code}`\n"
            f"- **Details:** {msg}\n"
        )
        return {
            "state": "error",
            "exit_code": 2,
            "result": "ERROR",
            "reason_code": "missing-report",
            "description": f"ERROR · {msg}"[:140],
            "markdown": md,
        }

    result = str(data.get("result") or "ERROR")
    reason_code = data.get("reason_code")
    message = str(data.get("message") or "")
    report_exit = data.get("exit_code")
    mode = data.get("mode", "unknown")
    policy = data.get("policy", "bugfix")
    repo_block = data.get("repo") or {}
    checks = data.get("checks") or {}
    runs = data.get("runs") or {}

    # Fail-closed guard: exit code must match report; strict_refs forbids local fallback.
    override_error: str | None = None
    if type(report_exit) is not int or report_exit != raw_exit_code:
        override_error = f"exit code mismatch (process={raw_exit_code}, report={report_exit!r})"
    elif strict_refs and result == "ACCEPTED":
        base_kind = (checks.get("base_ref") or {}).get("ref_source_kind")
        ebt_kind = (checks.get("ebt_trust") or {}).get("ref_source_kind")
        if base_kind == "local" or ebt_kind == "local":
            override_error = "strict-refs guard: accepted with weak ref_source_kind='local'"
        elif (checks.get("base_ref") or {}).get("status") != "ok" and mode == "command":
            override_error = "strict-refs guard: checks.base_ref is not 'ok'"

    if override_error is not None:
        state = "error"
        final_exit = 2
        result = "ERROR"
        reason_code = "ci-guard"
        message = override_error
    elif result == "ACCEPTED" and raw_exit_code == 0:
        state = "success"
        final_exit = 0
    elif result == "REJECTED" and raw_exit_code == 1:
        state = "failure"
        final_exit = 1
    else:
        state = "error"
        final_exit = 2

    parts = [result]
    if reason_code:
        parts.append(str(reason_code))
    if message:
        parts.append(message)
    description = " · ".join(parts).replace("\n", " ")[:140]

    icon = {"success": "✅", "failure": "❌", "error": "⚠️"}[state]
    base_sha = repo_block.get("base") or "—"
    repro_sha = repo_block.get("repro") or "—"
    head_sha = repo_block.get("head") or "—"
    base_ref_kind = (checks.get("base_ref") or {}).get("ref_source_kind") or "skipped"
    ebt_ref_kind = (checks.get("ebt_trust") or {}).get("ref_source_kind")

    lines = [
        COMMENT_MARKER,
        f"### {icon} AcceptBox: `{result}`" + (f" (`{reason_code}`)" if reason_code else ""),
        "",
        f"- **Mode / Policy:** `{mode}` / `{policy}`",
        f"- **Commits:** `base={base_sha}` · `repro={repro_sha}` · `head={head_sha}`",
        f"- **Trusted ref source:** `base_ref={base_ref_kind}`"
        + (f" · `ebt_ref={ebt_ref_kind}`" if ebt_ref_kind else ""),
        f"- **Message:** {message}",
    ]

    if state != "success" and report_path is not None:
        out_dir = report_path.parent
        for run_name in ("base", "repro", "head"):
            run_info = runs.get(run_name)
            if not isinstance(run_info, dict):
                continue
            status = run_info.get("status")
            log_rel = run_info.get("log_path")
            if status in ("red", "red-other", "error", "invalid-report") and isinstance(log_rel, str):
                tail = _read_tail(out_dir / log_rel)
                if tail:
                    lines += [
                        "",
                        f"<details><summary>Log tail: <code>{run_name}</code> ({status})</summary>",
                        "",
                        "```text",
                        tail,
                        "```",
                        "</details>",
                    ]

    return {
        "state": state,
        "exit_code": final_exit,
        "result": result,
        "reason_code": reason_code,
        "description": description,
        "markdown": "\n".join(lines) + "\n",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate accept.json and render CI summary")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--raw-exit-code", type=int, required=True)
    parser.add_argument("--strict-refs", action="store_true", default=False)
    parser.add_argument("--fallback-error", default="")
    parser.add_argument("--summary-out", type=Path, required=True)
    parser.add_argument("--meta-out", type=Path, required=True)
    args = parser.parse_args(argv)

    evaluated = evaluate_report(
        args.report,
        raw_exit_code=args.raw_exit_code,
        strict_refs=args.strict_refs,
        fallback_error=args.fallback_error,
    )
    args.summary_out.write_text(evaluated["markdown"], encoding="utf-8")
    meta = {k: v for k, v in evaluated.items() if k != "markdown"}
    args.meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
