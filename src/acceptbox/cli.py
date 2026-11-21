"""Командная строка AcceptBox: --version, --installation, pin, accept, calibrate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

from . import __version__
from .command import run_commit as run_command_commit
from .config import (CONFIG_FILENAME, ConfigError, ProjectConfig,
                     load_config_from_commit)
from .git import (GitError, add_worktree, base_env, changed_paths, clone_no_checkout,
                  ensure_commit, fetch_ref, is_ancestor, probe_git,
                  resolve_repo_head, rev_parse_tree)
from .inputs import (InputError, build_runtime_env, parse_env_entry, parse_invariants,
                     validate_commit_sha, validate_pathspec, validate_ref,
                     validate_repo_source, validate_sha256, validate_timeout)
from .pin import EbtPathMissing, tree_sha256, tree_sha256_in_repo
from .report import (OutDirLock, OutNotEmptyError, atomic_write_json, completed_run,
                     skipped_run, utc_now_iso)
from .verdict import (RunOutcome, Verdict, ancestry_verdict, coverage,
                      evaluate_runs, inputs_untrusted, no_invariants,
                      protected_paths_verdict, repro_tests_verdict, run_verdict)
from .verdict import error as error_verdict
from .witnessbox import (WITNESSBOX_SUPPORTED_TEXT, check_witnessbox_installation,
                         read_findings_inv, run_commit as run_witnessbox_commit)

DEFAULT_TIMEOUT_SEC = 600.0
BASE_SKIP_REASON = "в режиме command базовый коммит не прогоняется"
NOT_RUN_REASON = "прогон не выполнялся"


class _EbtUntrusted(Exception):
    """Нарушение доверия к ebt/ (L10) → inputs-untrusted, код 1."""


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _config_summary(project_config: ProjectConfig | None) -> dict | None:
    if project_config is None:
        return None
    return {
        "path": CONFIG_FILENAME,
        "commit": project_config.source_commit,
        "sha256": project_config.sha256,
    }


def _remove_empty_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _reject_mode_mixing(args: argparse.Namespace, mode: str,
                        cmd_argv: list[str] | None) -> None:
    """Флаги чужого режима — ошибка CLI (код 2)."""
    if mode == "command":
        for flag, value in (("--inv", args.inv), ("--ebt-repo", args.ebt_repo),
                             ("--ebt-ref", args.ebt_ref), ("--ebt-sha", args.ebt_sha),
                             ("--approved-tree", args.approved_tree)):
            if value is not None:
                raise InputError(f"{flag} не используется в режиме command")
    else:
        if args.tests:
            raise InputError("--tests не используется в режиме witnessbox")
        if cmd_argv is not None:
            raise InputError("разделитель -- (команда) не используется в режиме witnessbox")


def _reject_cli_cross_mode_mixing(args: argparse.Namespace,
                                  cmd_argv: list[str] | None) -> None:
    """Явное смешивание command- и witnessbox-флагов на CLI, даже когда --mode опущен."""
    has_command_flags = bool(args.tests) or cmd_argv is not None
    has_witnessbox_flags = any(
        value is not None
        for value in (args.inv, args.ebt_repo, args.ebt_ref,
                      args.ebt_sha, args.approved_tree)
    )
    if has_command_flags and has_witnessbox_flags:
        raise InputError("нельзя смешивать флаги режимов command и witnessbox")


def _prepare_ebt(temp: Path, git_env: dict[str, str], ebt_repo: str, ebt_ref: str,
                ebt_sha: str, approved_tree: str) -> tuple[Path, str, str]:
    """Доверенные входы EBT (L10): клон, явный fetch --ebt-ref и --ebt-sha,
    проверка предка, сверка дерева и чистый checkout ebt/ из --ebt-sha.

    Возвращает (ebt_dir, actual_tree_sha256, spec_sha256). Кидает _EbtUntrusted
    (→ inputs-untrusted, код 1) и GitError (→ gate-error, код 2).
    """
    ebt_clone = temp / "ebt-repo"
    clone_no_checkout(ebt_repo, ebt_clone, git_env)
    ref_commit = fetch_ref(ebt_clone, git_env, ebt_repo, ebt_ref)
    if ref_commit is None:
        raise _EbtUntrusted(f"ссылка --ebt-ref {ebt_ref!r} не найдена в --ebt-repo")
    if not ensure_commit(ebt_clone, git_env, ebt_repo, ebt_sha):
        raise _EbtUntrusted(f"коммит --ebt-sha {ebt_sha} не найден в --ebt-repo")
    if not is_ancestor(ebt_clone, git_env, ebt_sha, ref_commit):
        raise _EbtUntrusted(f"--ebt-sha {ebt_sha} не является предком --ebt-ref")
    try:
        actual_tree = tree_sha256_in_repo(ebt_clone, ebt_sha, git_env)
    except EbtPathMissing as missing:
        raise _EbtUntrusted(str(missing)) from missing
    if actual_tree != approved_tree:
        raise _EbtUntrusted(
            f"хеш дерева ebt/ ({actual_tree}) не совпадает с --approved-tree ({approved_tree})")
    ebt_worktree = temp / "ebt-worktree"
    add_worktree(ebt_clone, git_env, ebt_sha, ebt_worktree)
    ebt_dir = ebt_worktree / "ebt"
    spec_path = ebt_dir / "spec.toml"
    if not spec_path.is_file():
        raise _EbtUntrusted(f"ebt/spec.toml отсутствует в --ebt-sha {ebt_sha}")
    return ebt_dir, actual_tree, hashlib.sha256(spec_path.read_bytes()).hexdigest()


def _relative_artifacts(run_name: str, outcome: RunOutcome) -> dict | None:
    """Пути артефактов прогона относительно --out (spec_sha256 — не путь)."""
    if not outcome.artifacts:
        return outcome.artifacts
    result: dict = {}
    for key, value in outcome.artifacts.items():
        if isinstance(value, dict) and isinstance(value.get("path"), str):
            entry = dict(value)
            entry["path"] = f"runs/{run_name}/{value['path']}"
            result[key] = entry
        else:
            result[key] = value
    return result


def _installation() -> int:
    """Диагностика окружения: код 0, если git и acceptbox доступны, независимо от witnessbox."""
    git_block = probe_git()
    witnessbox = check_witnessbox_installation()
    payload = {
        "format": 1,
        "report_format": 1,
        "distribution": "acceptbox",
        "version": __version__,
        "git": git_block,
        "witnessbox": {
            "path": witnessbox.path,
            "version": witnessbox.version,
            "supported": WITNESSBOX_SUPPORTED_TEXT,
            "status": witnessbox.status,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if git_block["status"] == "ok" else 2


def _cmd_pin(args: argparse.Namespace) -> int:
    try:
        repo = validate_repo_source(args.ebt_repo, flag="--ebt-repo")
        sha = validate_commit_sha(args.ebt_sha, flag="--ebt-sha")
        print(tree_sha256(repo, sha))
    except (GitError, ValueError) as failure:
        print(f"acceptbox: {failure}", file=sys.stderr)
        return 2
    return 0


def _cmd_accept(args: argparse.Namespace, cmd_argv: list[str] | None) -> int:
    """Приёмка цепочки base → repro → head; стадии 1–10 с поддержкой L22–L24."""
    started_at = utc_now_iso()
    run_id = str(uuid.uuid4())
    git_env = base_env()

    # Стадия 1: валидация синтаксиса CLI и немедленный резолв HEAD при опущенном --head (L22).
    try:
        repo = validate_repo_source(args.repo, flag="--repo")
        base = validate_commit_sha(args.base, flag="--base")
        repro = validate_commit_sha(args.repro, flag="--repro")
        if args.head is not None:
            head = validate_commit_sha(args.head, flag="--head")
        else:
            try:
                resolved_head = resolve_repo_head(repo, git_env)
            except GitError as head_error:
                raise InputError(
                    f"не удалось разрешить HEAD из --repo: {head_error}") from head_error
            head = validate_commit_sha(resolved_head, flag="--head")

        auto_out = args.out is None
        out_path: Path | None = None
        if not auto_out:
            if not args.out.strip():
                raise InputError("--out: укажите пустой каталог для отчёта")
            out_path = Path(args.out)

        cli_timeout = (validate_timeout(args.timeout)
                       if args.timeout is not None else None)
        cli_protect = [validate_pathspec(item, flag="--protect")
                       for item in args.protect or []]
        cli_env_entries = [parse_env_entry(item) for item in args.env or []]
        cli_tests = [validate_pathspec(item, flag="--tests")
                     for item in args.tests or []]
        if cmd_argv is not None and not cmd_argv:
            raise InputError("после -- обязательна непустая команда")
        cli_inv_list = (parse_invariants(args.inv)
                        if args.inv is not None else None)
        cli_ebt_repo = (validate_repo_source(args.ebt_repo, flag="--ebt-repo")
                        if args.ebt_repo is not None else None)
        cli_ebt_ref = (validate_ref(args.ebt_ref, flag="--ebt-ref")
                       if args.ebt_ref is not None else None)
        cli_ebt_sha = (validate_commit_sha(args.ebt_sha, flag="--ebt-sha")
                       if args.ebt_sha is not None else None)
        cli_approved_tree = (validate_sha256(args.approved_tree, flag="--approved-tree")
                             if args.approved_tree is not None else None)

        if args.mode is not None:
            _reject_mode_mixing(args, args.mode, cmd_argv)
        else:
            _reject_cli_cross_mode_mixing(args, cmd_argv)
    except InputError as invalid:
        print(f"acceptbox: {invalid}", file=sys.stderr)
        return 2

    # Общее состояние отчёта, наполняется стадиями 3–10 (дополняется после чтения .acceptbox.toml).
    mode = args.mode or "command"
    scope = "command-only" if mode == "command" else "ebt-only"
    timeout = cli_timeout if cli_timeout is not None else DEFAULT_TIMEOUT_SEC
    protect = list(cli_protect)
    env_keys = sorted({key for key, _ in cli_env_entries})
    cmd: list[str] | None = list(cmd_argv) if cmd_argv is not None else None
    tests: list[str] | None = cli_tests if cli_tests else None
    inv_list: list[str] | None = cli_inv_list
    ebt_repo: str | None = cli_ebt_repo
    ebt_ref: str | None = cli_ebt_ref
    ebt_sha: str | None = cli_ebt_sha
    approved_tree: str | None = cli_approved_tree
    project_config: ProjectConfig | None = None

    witnessbox_block = None
    wb_info: dict | None = None
    wb_bin = "witnessbox"
    head_tree = None
    actual_tree = spec_sha = None
    findings_inv: list[str] = []
    runs = {"base": skipped_run(base, BASE_SKIP_REASON if mode == "command" else NOT_RUN_REASON),
            "repro": skipped_run(repro, NOT_RUN_REASON),
            "head": skipped_run(head, NOT_RUN_REASON)}
    checks = {"ebt_trust": {"status": "skipped" if mode == "command" else "not-run"},
              "ancestry": {"status": "not-run"},
              "protected_paths": {"status": "not-run"},
              "repro_tests": {"status": "skipped" if mode == "witnessbox" else "not-run"}}

    def finish(verdict: Verdict, coverage_override: dict | None = None) -> int:
        assert out_path is not None
        finished_at = utc_now_iso()
        inputs_block: dict = {
            "timeout_sec": timeout,
            "protect": protect,
            "env_keys": env_keys,
            "config": _config_summary(project_config),
        }
        if mode == "command":
            inputs_block["command"] = cmd
            inputs_block["tests"] = tests
        else:
            inputs_block["inv"] = inv_list
            inputs_block["ebt"] = {
                "repo": ebt_repo, "ref": ebt_ref, "sha": ebt_sha,
                "approved_tree_sha256": approved_tree,
                "actual_tree_sha256": actual_tree, "spec_sha256": spec_sha,
            }
        payload = {
            "format": 1,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "acceptbox_version": __version__,
            "mode": mode,
            "scope": scope,
            "witnessbox": witnessbox_block,
            "repo": {"source": repo, "base": base, "repro": repro, "head": head,
                     "head_tree": head_tree},
            "inputs": inputs_block,
            "checks": checks,
            "runs": runs,
            "result": verdict.result,
            "exit_code": verdict.exit_code,
            "reason_code": verdict.reason_code,
            "message": verdict.message,
            "coverage": coverage_override or coverage(mode, inv_list or [], findings_inv),
        }
        report_path = atomic_write_json(out_path, "accept.json", payload)
        stream = sys.stdout if verdict.exit_code == 0 else sys.stderr
        print(f"acceptbox: {verdict.result}"
              + (f" ({verdict.reason_code})" if verdict.reason_code else "")
              + f": {verdict.message} (отчёт: {report_path})", file=stream)
        return verdict.exit_code

    # Стадия 2: атомарный захват --out (или создание нового пустого каталога в TMPDIR, L22).
    if auto_out:
        try:
            out_path = Path(mkdtemp(prefix="acceptbox-accept-"))
        except OSError as io_failure:
            print(f"acceptbox: сбой окружения: {io_failure}", file=sys.stderr)
            return 2
    assert out_path is not None
    lock = OutDirLock(out_path)
    remove_unused_out = False
    try:
        try:
            with lock:
                # Стадия 3a: git.
                if probe_git()["status"] != "ok":
                    return finish(error_verdict("gate-error", "git недоступен или сломан"))

                try:
                    with TemporaryDirectory(prefix="acceptbox-run-") as temp_name:
                        temp = Path(temp_name)
                        # Стадия 5a: клон --repo и чтение .acceptbox.toml строго из base (L23).
                        clone = temp / "repo"
                        clone_no_checkout(repo, clone, git_env)
                        if not ensure_commit(clone, git_env, repo, base):
                            return finish(error_verdict(
                                "gate-error", f"коммит {base} отсутствует в --repo"))
                        try:
                            project_config = load_config_from_commit(clone, git_env, base)
                        except ConfigError as cfg_error:
                            return finish(error_verdict("config-invalid", str(cfg_error)))

                        # Слияние CLI + .acceptbox.toml + дефолты L22.
                        mode = (args.mode
                                or (project_config.mode
                                    if project_config and project_config.mode else None)
                                or "command")
                        scope = "command-only" if mode == "command" else "ebt-only"
                        runs["base"] = skipped_run(
                            base, BASE_SKIP_REASON if mode == "command" else NOT_RUN_REASON)
                        checks["ebt_trust"] = {
                            "status": "skipped" if mode == "command" else "not-run"}
                        checks["repro_tests"] = {
                            "status": "skipped" if mode == "witnessbox" else "not-run"}

                        timeout = (cli_timeout if cli_timeout is not None
                                   else (project_config.timeout
                                         if project_config and project_config.timeout is not None
                                         else DEFAULT_TIMEOUT_SEC))
                        protect = _dedupe_preserve_order(
                            [*(project_config.protect if project_config else ()),
                             *cli_protect])
                        merged_env_entries = [
                            *(project_config.env if project_config else ()),
                            *cli_env_entries,
                        ]

                        try:
                            _reject_mode_mixing(args, mode, cmd_argv)
                            if mode == "command":
                                tests = (cli_tests if cli_tests
                                         else (list(project_config.command_tests)
                                               if project_config and project_config.command_tests
                                               else None))
                                cmd = (list(cmd_argv) if cmd_argv is not None
                                       else (list(project_config.command_argv)
                                             if project_config and project_config.command_argv
                                             else None))
                                if not tests:
                                    raise InputError(
                                        "в режиме command обязателен --tests <pathspec> "
                                        "(или [command].tests в .acceptbox.toml)")
                                if not cmd:
                                    raise InputError(
                                        "в режиме command обязательна команда после -- "
                                        "(или [command].argv в .acceptbox.toml)")
                            else:
                                if cli_inv_list is None:
                                    raise InputError("в режиме witnessbox обязателен флаг --inv")
                                inv_list = cli_inv_list
                                ebt_repo = (cli_ebt_repo
                                            or (project_config.witnessbox_ebt_repo
                                                if project_config else None)
                                            or repo)
                                ebt_ref = (cli_ebt_ref
                                           or (project_config.witnessbox_ebt_ref
                                               if project_config else None))
                                if ebt_ref is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен --ebt-ref "
                                        "(или [witnessbox].ebt_ref в .acceptbox.toml)")
                                if cli_ebt_sha is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен флаг --ebt-sha")
                                if cli_approved_tree is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен флаг --approved-tree")
                                ebt_sha = cli_ebt_sha
                                approved_tree = cli_approved_tree
                        except InputError as invalid:
                            remove_unused_out = auto_out or lock.created_dir
                            print(f"acceptbox: {invalid}", file=sys.stderr)
                            return 2

                        env, env_keys = build_runtime_env(
                            os.environ, merged_env_entries,
                            base_sha=base if mode == "witnessbox" else None,
                            inv_csv=",".join(inv_list) if mode == "witnessbox" else None,
                        )

                        # Стадия 3b: witnessbox --installation (только для witnessbox).
                        if mode == "witnessbox":
                            witnessbox_status = check_witnessbox_installation(env=env)
                            if witnessbox_status.info is not None:
                                wb_info = witnessbox_status.info
                                wb_bin = witnessbox_status.path
                                witnessbox_block = {
                                    "version": witnessbox_status.info["version"],
                                    "gate_sha256": witnessbox_status.info["gate_sha256"],
                                    "checker_sha256": witnessbox_status.info["checker_sha256"],
                                }
                            if witnessbox_status.status == "missing":
                                return finish(error_verdict("witnessbox-missing",
                                                            witnessbox_status.message))
                            if witnessbox_status.status == "incompatible":
                                return finish(error_verdict("witnessbox-incompatible",
                                                            witnessbox_status.message))
                            if witnessbox_status.status == "error":
                                return finish(error_verdict("gate-error",
                                                            witnessbox_status.message))

                        # Стадия 4: пустой --inv → no-invariants (код 1, прогоны не запускаются).
                        if mode == "witnessbox" and not inv_list:
                            checks["ebt_trust"] = {"status": "skipped", "reason": "no-invariants"}
                            return finish(no_invariants(),
                                          {"checked": [], "skipped": [],
                                           "unchecked": ["no-invariants"]})

                        # Стадия 5b: наличие repro и head, SHA дерева head.
                        for sha in (repro, head):
                            if not ensure_commit(clone, git_env, repo, sha):
                                return finish(error_verdict(
                                    "gate-error", f"коммит {sha} отсутствует в --repo"))
                        head_tree = rev_parse_tree(clone, git_env, head)

                        # Стадия 6: доверенные входы EBT (L10) → inputs-untrusted.
                        ebt_dir = None
                        if mode == "witnessbox":
                            try:
                                ebt_dir, actual_tree, spec_sha = _prepare_ebt(
                                    temp, git_env, ebt_repo, ebt_ref, ebt_sha, approved_tree)
                            except _EbtUntrusted as untrusted:
                                checks["ebt_trust"] = {
                                    "status": "failed", "reason": str(untrusted)}
                                return finish(inputs_untrusted(str(untrusted)))
                            checks["ebt_trust"] = {
                                "status": "ok", "actual_tree_sha256": actual_tree,
                                "spec_sha256": spec_sha}

                        # Стадия 7: ancestry → прогоны не запускаются при отказе.
                        ancestry = {
                            "base_ancestor_of_repro": is_ancestor(clone, git_env, base, repro),
                            "repro_ancestor_of_head": is_ancestor(clone, git_env, repro, head),
                        }
                        verdict = ancestry_verdict(**ancestry)
                        checks["ancestry"] = {"status": "ok" if verdict is None else "failed",
                                               **ancestry}
                        if verdict is not None:
                            return finish(verdict)

                        # Стадия 8: защищённые пути на base..repro и base..head.
                        # .acceptbox.toml защищается автоматически всегда (L23).
                        protected_specs = _dedupe_preserve_order(
                            list(protect)
                            + [CONFIG_FILENAME]
                            + (["ebt", "tool/ebt_check"] if mode == "witnessbox" else [])
                        )
                        changed_base_repro = changed_paths(clone, git_env, base, repro,
                                                           protected_specs)
                        changed_base_head = changed_paths(clone, git_env, base, head,
                                                          protected_specs)
                        verdict = protected_paths_verdict(
                            changed_base_repro=changed_base_repro,
                            changed_base_head=changed_base_head)
                        checks["protected_paths"] = {
                            "status": "ok" if verdict is None else "failed",
                            "specs": protected_specs,
                            "changed_base_repro": changed_base_repro,
                            "changed_base_head": changed_base_head,
                        }
                        if verdict is not None:
                            return finish(verdict)

                        # Стадия 9 (только command): репро-тесты.
                        if mode == "command":
                            repro_changed = changed_paths(clone, git_env, base, repro)
                            tests_changed = changed_paths(clone, git_env, base, repro, tests)
                            head_changed = changed_paths(clone, git_env, repro, head)
                            modified = sorted(set(repro_changed) & set(head_changed))
                            verdict = repro_tests_verdict(
                                repro_changed=repro_changed, tests_changed=tests_changed,
                                modified_after_repro=modified)
                            checks["repro_tests"] = {
                                "status": "ok" if verdict is None else "failed",
                                "changed_base_repro": repro_changed,
                                "outside_tests": sorted(
                                    set(repro_changed) - set(tests_changed)),
                                "modified_after_repro": modified,
                            }
                            if verdict is not None:
                                return finish(verdict)

                        # Стадия 10: последовательные fail-fast прогоны.
                        executed: list[tuple[str, RunOutcome]] = []
                        order = ("base", "repro", "head") if mode == "witnessbox" \
                            else ("repro", "head")
                        shas = {"base": base, "repro": repro, "head": head}
                        for name in order:
                            worktree = temp / f"worktree-{name}"
                            add_worktree(clone, git_env, shas[name], worktree)
                            run_dir = out_path / "runs" / name
                            if mode == "command":
                                outcome = run_command_commit(worktree, shas[name], run_dir,
                                                             env, timeout, cmd_argv=cmd)
                            else:
                                outcome = run_witnessbox_commit(
                                    worktree, shas[name], run_dir, env, timeout,
                                    ebt_dir=ebt_dir, approved_spec_sha256=spec_sha,
                                    expected_invariants=inv_list, installation_info=wb_info,
                                    witnessbox_bin=wb_bin)
                            runs[name] = completed_run(
                                shas[name], outcome,
                                log_path=f"runs/{name}/{outcome.log_path}",
                                artifacts=_relative_artifacts(name, outcome))
                            executed.append((name, outcome))
                            if outcome.status == "red":
                                findings_inv.extend(read_findings_inv(run_dir))
                            step = run_verdict(name, outcome)
                            if step is not None:
                                return finish(step)
                        return finish(evaluate_runs(executed))
                except GitError as git_failure:
                    return finish(error_verdict("gate-error", f"сбой git: {git_failure}"))
                except EbtPathMissing as missing:
                    return finish(inputs_untrusted(str(missing)))
        except OutNotEmptyError as occupied:
            print(f"acceptbox: {occupied}", file=sys.stderr)
            return 2
        except OSError as io_failure:
            remove_unused_out = auto_out
            print(f"acceptbox: сбой окружения: {io_failure}", file=sys.stderr)
            return 2
    finally:
        if remove_unused_out:
            _remove_empty_dir(out_path)
    return 2


def _cmd_calibrate(args: argparse.Namespace, cmd_argv: list[str] | None) -> int:
    """Калибровка чувствительности (L18): calibrate.json, никогда accept.json.

    Проверки ancestry и base..repro диффа не выполняются. При переданном
    ``--base`` может читать ``.acceptbox.toml`` строго из базового коммита (L23).
    """
    started_at = utc_now_iso()
    run_id = str(uuid.uuid4())
    git_env = base_env()

    # Стадия 1: валидация CLI (код 2).
    try:
        repo = validate_repo_source(args.repo, flag="--repo")
        auto_out = args.out is None
        out_path: Path | None = None
        if not auto_out:
            if not args.out.strip():
                raise InputError("--out: укажите пустой каталог для отчёта")
            out_path = Path(args.out)
        cli_timeout = (validate_timeout(args.timeout)
                       if args.timeout is not None else None)
        cli_env_entries = [parse_env_entry(item) for item in args.env or []]
        base = validate_commit_sha(args.base, flag="--base") if args.base else None
        if not args.green and not args.red:
            raise InputError("укажите хотя бы один --green <sha> или --red <sha>")
        greens = [validate_commit_sha(sha, flag="--green") for sha in args.green or []]
        reds = [validate_commit_sha(sha, flag="--red") for sha in args.red or []]
        cli_tests = [validate_pathspec(item, flag="--tests")
                     for item in args.tests or []]
        if cmd_argv is not None and not cmd_argv:
            raise InputError("после -- обязательна непустая команда")
        cli_inv_list = (parse_invariants(args.inv)
                        if args.inv is not None else None)
        cli_ebt_repo = (validate_repo_source(args.ebt_repo, flag="--ebt-repo")
                        if args.ebt_repo is not None else None)
        cli_ebt_ref = (validate_ref(args.ebt_ref, flag="--ebt-ref")
                       if args.ebt_ref is not None else None)
        cli_ebt_sha = (validate_commit_sha(args.ebt_sha, flag="--ebt-sha")
                       if args.ebt_sha is not None else None)
        cli_approved_tree = (validate_sha256(args.approved_tree, flag="--approved-tree")
                             if args.approved_tree is not None else None)

        if args.mode is not None:
            _reject_mode_mixing(args, args.mode, cmd_argv)
        else:
            _reject_cli_cross_mode_mixing(args, cmd_argv)

        # Без --base конфиг .acceptbox.toml не читается (L23) — проверяем полноту CLI сразу.
        if base is None:
            resolved_mode = args.mode or "command"
            _reject_mode_mixing(args, resolved_mode, cmd_argv)
            if resolved_mode == "command":
                if not cmd_argv:
                    raise InputError("в режиме command после -- обязательна непустая команда")
            else:
                if cli_inv_list is None:
                    raise InputError("в режиме witnessbox обязателен флаг --inv")
                if cli_ebt_ref is None:
                    raise InputError("в режиме witnessbox обязателен флаг --ebt-ref")
                if cli_ebt_sha is None:
                    raise InputError("в режиме witnessbox обязателен флаг --ebt-sha")
                if cli_approved_tree is None:
                    raise InputError("в режиме witnessbox обязателен флаг --approved-tree")
    except InputError as invalid:
        print(f"acceptbox: {invalid}", file=sys.stderr)
        return 2

    mode = args.mode or "command"
    scope = "command-only" if mode == "command" else "ebt-only"
    timeout = cli_timeout if cli_timeout is not None else DEFAULT_TIMEOUT_SEC
    env_keys = sorted({key for key, _ in cli_env_entries})
    cmd: list[str] | None = list(cmd_argv) if cmd_argv is not None else None
    tests: list[str] | None = cli_tests if cli_tests else []
    inv_list: list[str] | None = cli_inv_list
    ebt_repo: str | None = cli_ebt_repo
    ebt_ref: str | None = cli_ebt_ref
    ebt_sha: str | None = cli_ebt_sha
    approved_tree: str | None = cli_approved_tree
    project_config: ProjectConfig | None = None

    witnessbox_block = None
    wb_info: dict | None = None
    wb_bin = "witnessbox"
    actual_tree = spec_sha = None
    results: list[dict] = []

    def finish(result: str, exit_code: int, reason_code: str | None, message: str) -> int:
        assert out_path is not None
        finished_at = utc_now_iso()
        inputs_block: dict = {
            "timeout_sec": timeout,
            "env_keys": env_keys,
            "config": _config_summary(project_config),
        }
        if mode == "command":
            inputs_block["command"] = cmd
            inputs_block["tests"] = tests
        else:
            inputs_block["inv"] = inv_list
            inputs_block["ebt"] = {
                "repo": ebt_repo, "ref": ebt_ref, "sha": ebt_sha,
                "approved_tree_sha256": approved_tree,
                "actual_tree_sha256": actual_tree, "spec_sha256": spec_sha,
            }
        payload = {
            "format": 1,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "acceptbox_version": __version__,
            "mode": mode,
            "scope": scope,
            "witnessbox": witnessbox_block,
            "repo": {"source": repo, "base": base},
            "inputs": inputs_block,
            "results": results,
            "result": result,
            "exit_code": exit_code,
            "reason_code": reason_code,
            "message": message,
        }
        report_path = atomic_write_json(out_path, "calibrate.json", payload)
        stream = sys.stdout if exit_code == 0 else sys.stderr
        print(f"acceptbox: {result}"
              + (f" ({reason_code})" if reason_code else "")
              + f": {message} (отчёт: {report_path})", file=stream)
        return exit_code

    if auto_out:
        try:
            out_path = Path(mkdtemp(prefix="acceptbox-calibrate-"))
        except OSError as io_failure:
            print(f"acceptbox: сбой окружения: {io_failure}", file=sys.stderr)
            return 2
    assert out_path is not None
    lock = OutDirLock(out_path)
    remove_unused_out = False
    try:
        try:
            with lock:
                if probe_git()["status"] != "ok":
                    return finish("ERROR", 2, "gate-error", "git недоступен или сломан")

                try:
                    with TemporaryDirectory(prefix="acceptbox-calibrate-") as temp_name:
                        temp = Path(temp_name)
                        clone = temp / "repo"
                        clone_no_checkout(repo, clone, git_env)
                        if base is not None:
                            if not ensure_commit(clone, git_env, repo, base):
                                return finish("ERROR", 2, "gate-error",
                                              f"коммит --base {base} отсутствует в --repo")
                            try:
                                project_config = load_config_from_commit(clone, git_env, base)
                            except ConfigError as cfg_error:
                                return finish("ERROR", 2, "config-invalid", str(cfg_error))

                        mode = (args.mode
                                or (project_config.mode
                                    if project_config and project_config.mode else None)
                                or "command")
                        scope = "command-only" if mode == "command" else "ebt-only"
                        timeout = (cli_timeout if cli_timeout is not None
                                   else (project_config.timeout
                                         if project_config and project_config.timeout is not None
                                         else DEFAULT_TIMEOUT_SEC))
                        merged_env_entries = [
                            *(project_config.env if project_config else ()),
                            *cli_env_entries,
                        ]

                        try:
                            _reject_mode_mixing(args, mode, cmd_argv)
                            if mode == "command":
                                tests = (cli_tests if cli_tests
                                         else (list(project_config.command_tests)
                                               if project_config and project_config.command_tests
                                               else []))
                                cmd = (list(cmd_argv) if cmd_argv is not None
                                       else (list(project_config.command_argv)
                                             if project_config and project_config.command_argv
                                             else None))
                                if not cmd:
                                    raise InputError(
                                        "в режиме command обязательна команда после -- "
                                        "(или [command].argv в .acceptbox.toml при --base)")
                            else:
                                if cli_inv_list is None:
                                    raise InputError("в режиме witnessbox обязателен флаг --inv")
                                inv_list = cli_inv_list
                                ebt_repo = (cli_ebt_repo
                                            or (project_config.witnessbox_ebt_repo
                                                if project_config else None)
                                            or repo)
                                ebt_ref = (cli_ebt_ref
                                           or (project_config.witnessbox_ebt_ref
                                               if project_config else None))
                                if ebt_ref is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен --ebt-ref "
                                        "(или [witnessbox].ebt_ref в .acceptbox.toml при --base)")
                                if cli_ebt_sha is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен флаг --ebt-sha")
                                if cli_approved_tree is None:
                                    raise InputError(
                                        "в режиме witnessbox обязателен флаг --approved-tree")
                                ebt_sha = cli_ebt_sha
                                approved_tree = cli_approved_tree
                        except InputError as invalid:
                            remove_unused_out = auto_out or lock.created_dir
                            print(f"acceptbox: {invalid}", file=sys.stderr)
                            return 2

                        env, env_keys = build_runtime_env(
                            os.environ, merged_env_entries,
                            base_sha=base if mode == "witnessbox" else None,
                            inv_csv=",".join(inv_list) if mode == "witnessbox" else None,
                        )

                        if mode == "witnessbox":
                            witnessbox_status = check_witnessbox_installation(env=env)
                            if witnessbox_status.info is not None:
                                wb_info = witnessbox_status.info
                                wb_bin = witnessbox_status.path
                                witnessbox_block = {
                                    "version": witnessbox_status.info["version"],
                                    "gate_sha256": witnessbox_status.info["gate_sha256"],
                                    "checker_sha256": witnessbox_status.info["checker_sha256"],
                                }
                            if witnessbox_status.status == "missing":
                                return finish("ERROR", 2, "witnessbox-missing",
                                              witnessbox_status.message)
                            if witnessbox_status.status == "incompatible":
                                return finish("ERROR", 2, "witnessbox-incompatible",
                                              witnessbox_status.message)
                            if witnessbox_status.status == "error":
                                return finish("ERROR", 2, "gate-error",
                                              witnessbox_status.message)
                            if not inv_list:
                                return finish("FAILED", 1, "no-invariants",
                                              "--inv не содержит ни одного инварианта")

                        ebt_dir = None
                        if mode == "witnessbox":
                            ebt_dir, actual_tree, spec_sha = _prepare_ebt(
                                temp, git_env, ebt_repo, ebt_ref, ebt_sha, approved_tree)
                        targets = ([("green", sha) for sha in greens]
                                   + [("red", sha) for sha in reds])
                        for index, (kind, sha) in enumerate(targets, start=1):
                            if not ensure_commit(clone, git_env, repo, sha):
                                return finish("ERROR", 2, "gate-error",
                                              f"коммит {sha} отсутствует в --repo")
                            worktree = temp / f"worktree-{index}"
                            add_worktree(clone, git_env, sha, worktree)
                            run_dir = out_path / "runs" / f"{kind}-{index}"
                            if mode == "command":
                                outcome = run_command_commit(worktree, sha, run_dir,
                                                             env, timeout, cmd_argv=cmd)
                            else:
                                outcome = run_witnessbox_commit(
                                    worktree, sha, run_dir, env, timeout,
                                    ebt_dir=ebt_dir, approved_spec_sha256=spec_sha,
                                    expected_invariants=inv_list,
                                    installation_info=wb_info, witnessbox_bin=wb_bin)
                            run_name = f"{kind}-{index}"
                            results.append({
                                "commit": sha,
                                "expected": kind,
                                "status": outcome.status,
                                "matched": outcome.status == kind,
                                "exit_code": outcome.exit_code,
                                "reason": outcome.reason,
                                "duration_ms": outcome.duration_ms,
                                "log_path": f"runs/{run_name}/{outcome.log_path}",
                                "artifacts": _relative_artifacts(run_name, outcome),
                            })
                except GitError as git_failure:
                    return finish("ERROR", 2, "gate-error", f"сбой git: {git_failure}")
                except _EbtUntrusted as untrusted:
                    return finish("FAILED", 1, "inputs-untrusted", str(untrusted))
                except EbtPathMissing as missing:
                    return finish("FAILED", 1, "inputs-untrusted", str(missing))

                if any(item["status"] == "error" for item in results):
                    return finish("ERROR", 2, "gate-error",
                                  "хотя бы один прогон завершился ошибкой")
                mismatches = [item for item in results if not item["matched"]]
                if mismatches:
                    detail = ", ".join(
                        f"{item['commit']} ({item['expected']} → {item['status']})"
                        for item in mismatches)
                    return finish("FAILED", 1, "mismatch", f"исход не совпал: {detail}")
                return finish("CALIBRATED", 0, None, "все коммиты дали ожидаемые исходы")
        except OutNotEmptyError as occupied:
            print(f"acceptbox: {occupied}", file=sys.stderr)
            return 2
        except OSError as io_failure:
            remove_unused_out = auto_out
            print(f"acceptbox: сбой окружения: {io_failure}", file=sys.stderr)
            return 2
    finally:
        if remove_unused_out:
            _remove_empty_dir(out_path)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acceptbox",
        description="Независимая приёмка коммитов: base → repro → head "
                    "(багфиксы и утверждённые инварианты).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--installation", action="store_true",
                        help="проверить окружение (git, witnessbox) и выйти")
    commands = parser.add_subparsers(dest="command")

    pin = commands.add_parser(
        "pin", help="вычислить SHA-256 дерева ebt/ из Git-коммита")
    pin.add_argument("--ebt-repo", required=True, help="путь или URL репозитория с ebt/")
    pin.add_argument("--ebt-sha", required=True, help="полный SHA коммита Git")

    accept = commands.add_parser(
        "accept",
        help="приёмка цепочки base → repro → head (багфиксы и утверждённые инварианты)",
    )
    _add_accept_flags(accept)

    calibrate = commands.add_parser(
        "calibrate", help="калибровка чувствительности прогонщиков (L18)")
    _add_calibrate_flags(calibrate)
    return parser


def _add_accept_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--mode", default=None, choices=("command", "witnessbox"),
        help="режим приёмки (по умолчанию из .acceptbox.toml или command)")
    subparser.add_argument(
        "--repo", default=".",
        help="путь или URL проверяемого репозитория (по умолчанию .)")
    subparser.add_argument("--base", required=True, help="полный SHA базового коммита")
    subparser.add_argument("--repro", required=True, help="полный SHA репро-коммита")
    subparser.add_argument(
        "--head", default=None,
        help="полный SHA головного коммита (по умолчанию HEAD из --repo)")
    subparser.add_argument(
        "--out", default=None,
        help="пустой каталог для отчёта accept.json (по умолчанию создаётся в TMPDIR)")
    subparser.add_argument(
        "--timeout", default=None,
        help="таймаут одного прогона в секундах (по умолчанию из .acceptbox.toml или 600)")
    subparser.add_argument("--protect", action="append",
                            help="pathspec защищённых путей (повторяемый; суммируется с конфигом)")
    subparser.add_argument("--env", action="append",
                            help="переменная окружения KEY[=VAL] (повторяемый)")
    subparser.add_argument("--tests", action="append",
                            help="pathspec репро-теста (повторяемый, только command)")
    subparser.add_argument("--inv", help="инварианты через запятую (только witnessbox)")
    subparser.add_argument(
        "--ebt-repo",
        help="репозиторий EBT (только witnessbox; по умолчанию из .acceptbox.toml или --repo)")
    subparser.add_argument(
        "--ebt-ref",
        help="ветка основной ветки EBT (только witnessbox; можно задать в .acceptbox.toml)")
    subparser.add_argument("--ebt-sha", help="полный SHA коммита EBT (только witnessbox)")
    subparser.add_argument("--approved-tree",
                            help="утверждённый SHA-256 дерева ebt/ (только witnessbox)")


def _add_calibrate_flags(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--mode", default=None, choices=("command", "witnessbox"),
        help="режим калибровки (по умолчанию из .acceptbox.toml при --base или command)")
    subparser.add_argument(
        "--repo", default=".",
        help="путь или URL проверяемого репозитория (по умолчанию .)")
    subparser.add_argument(
        "--out", default=None,
        help="пустой каталог для отчёта calibrate.json (по умолчанию создаётся в TMPDIR)")
    subparser.add_argument("--green", action="append",
                           help="коммит, ожидающий green (повторяемый)")
    subparser.add_argument("--red", action="append",
                           help="коммит, ожидающий red (повторяемый)")
    subparser.add_argument(
        "--base",
        help="полный SHA базового коммита (BASE в witnessbox и источник .acceptbox.toml)")
    subparser.add_argument(
        "--timeout", default=None,
        help="таймаут одного прогона в секундах (по умолчанию из .acceptbox.toml или 600)")
    subparser.add_argument("--env", action="append",
                            help="переменная окружения KEY[=VAL] (повторяемый)")
    subparser.add_argument("--tests", action="append",
                            help="pathspec репро-теста (повторяемый, только command)")
    subparser.add_argument("--inv", help="инварианты через запятую (только witnessbox)")
    subparser.add_argument(
        "--ebt-repo",
        help="репозиторий EBT (только witnessbox; по умолчанию из .acceptbox.toml или --repo)")
    subparser.add_argument(
        "--ebt-ref",
        help="ветка основной ветки EBT (только witnessbox; можно задать в .acceptbox.toml)")
    subparser.add_argument("--ebt-sha", help="полный SHA коммита EBT (только witnessbox)")
    subparser.add_argument("--approved-tree",
                            help="утверждённый SHA-256 дерева ebt/ (только witnessbox)")


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Команда после -- не разбирается argparse: её флаги принадлежат ей, не acceptbox.
    cmd_argv: list[str] | None = None
    if "--" in raw:
        separator = raw.index("--")
        cmd_argv = raw[separator + 1:]
        raw = raw[:separator]
    parser = _build_parser()
    args = parser.parse_args(raw)

    if args.installation:
        if args.command or cmd_argv is not None:
            parser.error("--installation используется отдельно от команд")
        return _installation()
    if args.command == "pin":
        if cmd_argv is not None:
            parser.error("pin не принимает аргументы после --")
        return _cmd_pin(args)
    if args.command == "accept":
        return _cmd_accept(args, cmd_argv)
    if args.command == "calibrate":
        return _cmd_calibrate(args, cmd_argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
