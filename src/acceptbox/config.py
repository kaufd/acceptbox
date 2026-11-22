"""Проектный конфиг AcceptBox (.acceptbox.toml), чтение строго из коммита base (L23).

Схема формата 1 (секционная, fail-closed):
- Верхний уровень: ``format = 1`` (обязателен), ``mode``, ``timeout``,
  ``protect``, ``env``, таблицы ``[command]`` и ``[witnessbox]``.
- ``[command]``: ``tests`` (непустой список pathspec), ``argv`` (непустой список строк).
- ``[witnessbox]``: ``ebt_repo`` (строка), ``ebt_ref`` (строка).

Неизвестные ключи, пер-задачные поля (``inv``, ``ebt_sha``, ``approved_tree``,
``base``, ``repro``, ``head``, ``repo``, ``out``), симлинки, невалидные типы и
относительный локальный путь ``ebt_repo`` отклоняются с ConfigError.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .git import is_remote_source, run_git
from .inputs import (InputError, parse_env_entry, validate_pathspec,
                     validate_ref, validate_repo_source, validate_timeout)

CONFIG_FILENAME = ".acceptbox.toml"
CONFIG_FORMAT = 1

_TOP_KEYS = frozenset({"format", "mode", "timeout", "protect", "env",
                       "command", "witnessbox"})
_COMMAND_KEYS = frozenset({"tests", "argv"})
_WITNESSBOX_KEYS = frozenset({"ebt_repo", "ebt_ref"})
_VALID_MODES = frozenset({"command", "witnessbox"})


class ConfigError(ValueError):
    """Некорректный конфиг (.acceptbox.toml или --config) → config-invalid (код 2).

    ``commit``, ``path`` и ``sha256`` заполняются загрузчиком для отчёта.
    """

    commit: str | None = None
    path: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class ProjectConfig:
    """Разобранный и провалидированный конфиг из коммита base или внешнего --config."""

    source_commit: str | None
    sha256: str
    source_path: str = CONFIG_FILENAME
    mode: str | None = None
    timeout: float | None = None
    protect: tuple[str, ...] = ()
    env: tuple[tuple[str, str | None], ...] = ()
    command_tests: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()
    witnessbox_ebt_repo: str | None = None
    witnessbox_ebt_ref: str | None = None


def _reject_unknown_keys(table: dict, allowed: frozenset[str], section: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(
            f"{CONFIG_FILENAME} ({section}): unknown keys: {', '.join(unknown)}")


def _require_str_list(value: object, *, label: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{label} must be a list of strings")
    if not allow_empty and not value:
        raise ConfigError(f"{label} cannot be an empty list")
    return value


def parse_config(raw_bytes: bytes, *, commit_sha: str | None,
                 source_path: str = CONFIG_FILENAME) -> ProjectConfig:
    """Разобрать и провалидировать содержимое .acceptbox.toml (fail-closed)."""
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError(f"{CONFIG_FILENAME}: invalid UTF-8 encoding: {error}") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{CONFIG_FILENAME}: TOML parse error: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_FILENAME}: root must be a TOML table")

    _reject_unknown_keys(data, _TOP_KEYS, "top level")

    fmt = data.get("format")
    if type(fmt) is not int or fmt != CONFIG_FORMAT:
        raise ConfigError(
            f"{CONFIG_FILENAME}: required key format = {CONFIG_FORMAT} (got: {fmt!r})")

    mode: str | None = None
    if "mode" in data:
        raw_mode = data["mode"]
        if not isinstance(raw_mode, str) or raw_mode not in _VALID_MODES:
            raise ConfigError(
                f"{CONFIG_FILENAME}: mode must be 'command' or 'witnessbox'")
        mode = raw_mode

    timeout: float | None = None
    if "timeout" in data:
        raw_timeout = data["timeout"]
        if type(raw_timeout) not in (int, float):
            raise ConfigError(f"{CONFIG_FILENAME}: timeout must be a number of seconds")
        try:
            timeout = validate_timeout(raw_timeout, flag=f"{CONFIG_FILENAME}: timeout")
        except InputError as error:
            raise ConfigError(str(error)) from error

    protect: list[str] = []
    if "protect" in data:
        raw_protect = _require_str_list(data["protect"], label=f"{CONFIG_FILENAME}: protect")
        try:
            protect = [validate_pathspec(item, flag=f"{CONFIG_FILENAME}: protect")
                       for item in raw_protect]
        except InputError as error:
            raise ConfigError(str(error)) from error

    env_entries: list[tuple[str, str | None]] = []
    if "env" in data:
        raw_env = _require_str_list(data["env"], label=f"{CONFIG_FILENAME}: env")
        try:
            env_entries = [parse_env_entry(item, flag=f"{CONFIG_FILENAME}: env")
                           for item in raw_env]
        except InputError as error:
            raise ConfigError(str(error)) from error

    command_tests: list[str] = []
    command_argv: list[str] = []
    if "command" in data:
        cmd_table = data["command"]
        if not isinstance(cmd_table, dict):
            raise ConfigError(f"{CONFIG_FILENAME}: section [command] must be a table")
        _reject_unknown_keys(cmd_table, _COMMAND_KEYS, "[command]")
        if "tests" in cmd_table:
            raw_tests = _require_str_list(
                cmd_table["tests"], label=f"{CONFIG_FILENAME}: [command].tests",
                allow_empty=False,
            )
            try:
                command_tests = [
                    validate_pathspec(item, flag=f"{CONFIG_FILENAME}: [command].tests")
                    for item in raw_tests
                ]
            except InputError as error:
                raise ConfigError(str(error)) from error
        if "argv" in cmd_table:
            raw_argv = _require_str_list(
                cmd_table["argv"], label=f"{CONFIG_FILENAME}: [command].argv",
                allow_empty=False,
            )
            if not raw_argv[0].strip():
                raise ConfigError(
                    f"{CONFIG_FILENAME}: [command].argv[0] cannot be empty")
            if any("\x00" in item for item in raw_argv):
                raise ConfigError(
                    f"{CONFIG_FILENAME}: [command].argv contains invalid NUL byte")
            command_argv = list(raw_argv)

    wb_ebt_repo: str | None = None
    wb_ebt_ref: str | None = None
    if "witnessbox" in data:
        wb_table = data["witnessbox"]
        if not isinstance(wb_table, dict):
            raise ConfigError(f"{CONFIG_FILENAME}: section [witnessbox] must be a table")
        _reject_unknown_keys(wb_table, _WITNESSBOX_KEYS, "[witnessbox]")
        if "ebt_repo" in wb_table:
            raw_ebt_repo = wb_table["ebt_repo"]
            if not isinstance(raw_ebt_repo, str):
                raise ConfigError(
                    f"{CONFIG_FILENAME}: [witnessbox].ebt_repo must be a string")
            try:
                wb_ebt_repo = validate_repo_source(
                    raw_ebt_repo, flag=f"{CONFIG_FILENAME}: [witnessbox].ebt_repo")
            except InputError as error:
                raise ConfigError(str(error)) from error
            # Относительный путь зависел бы от каталога запуска acceptbox, а не от --repo.
            if not is_remote_source(wb_ebt_repo) and not os.path.isabs(wb_ebt_repo):
                raise ConfigError(
                    f"{CONFIG_FILENAME}: [witnessbox].ebt_repo must be a URL or "
                    f"an absolute path (got: {raw_ebt_repo!r})")
        if "ebt_ref" in wb_table:
            raw_ebt_ref = wb_table["ebt_ref"]
            if not isinstance(raw_ebt_ref, str):
                raise ConfigError(
                    f"{CONFIG_FILENAME}: [witnessbox].ebt_ref must be a string")
            try:
                wb_ebt_ref = validate_ref(
                    raw_ebt_ref, flag=f"{CONFIG_FILENAME}: [witnessbox].ebt_ref")
            except InputError as error:
                raise ConfigError(str(error)) from error

    return ProjectConfig(
        source_commit=commit_sha,
        sha256=sha256,
        source_path=source_path,
        mode=mode,
        timeout=timeout,
        protect=tuple(protect),
        env=tuple(env_entries),
        command_tests=tuple(command_tests),
        command_argv=tuple(command_argv),
        witnessbox_ebt_repo=wb_ebt_repo,
        witnessbox_ebt_ref=wb_ebt_ref,
    )


def load_config_from_file(config_path: Path) -> ProjectConfig:
    """Прочитать внешний конфиг ``--config <path>`` с диска (fail-closed, L30).

    Отклоняет отсутствующий путь, симлинки, не-обычные файлы и ошибки схемы → ConfigError.
    O_NOFOLLOW и fstat по открытому дескриптору: без гонки между проверкой и чтением;
    O_NONBLOCK — чтобы FIFO не подвесил открытие.
    """
    path_str = os.path.abspath(os.path.expanduser(str(config_path)))

    def reject(message: str) -> ConfigError:
        error = ConfigError(f"--config {path_str}: {message}")
        error.path = path_str
        return error

    try:
        descriptor = os.open(path_str, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as missing:
        raise reject("file not found") from missing
    except OSError as io_error:
        if os.path.islink(path_str):
            raise reject("symlinks are not allowed") from io_error
        raise reject(f"failed to open file: {io_error}") from io_error
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise reject("must be a regular file")
        try:
            raw = handle.read()
        except OSError as io_error:
            raise reject(f"failed to read file: {io_error}") from io_error
    try:
        return parse_config(raw, commit_sha=None, source_path=path_str)
    except ConfigError as error:
        error.path = path_str
        error.sha256 = hashlib.sha256(raw).hexdigest()
        raise


def load_config_from_commit(repo: Path, env: dict[str, str],
                            commit_sha: str) -> ProjectConfig | None:
    """Прочитать .acceptbox.toml строго из объектов Git коммита ``commit_sha``.

    Возвращает None, если файл отсутствует в коммите. Симлинки, каталоги,
    сабмодули, ошибки разбора TOML и нарушения схемы → ConfigError.
    """
    listing = run_git(repo, env, "ls-tree", "-z", commit_sha, "--", CONFIG_FILENAME)
    entries = [entry for entry in listing.split(b"\0") if entry]
    if not entries:
        return None
    metadata, _path = entries[0].split(b"\t", 1)
    mode, kind, object_id = metadata.split()
    if kind != b"blob" or mode not in (b"100644", b"100755"):
        mode_text = mode.decode("ascii", "replace")
        kind_text = kind.decode("ascii", "replace")
        error = ConfigError(
            f"{CONFIG_FILENAME} in commit {commit_sha} must be a regular file "
            f"(got: mode={mode_text}, kind={kind_text})"
        )
        error.commit = commit_sha
        raise error
    raw = run_git(repo, env, "cat-file", "blob", object_id.decode("ascii"))
    try:
        return parse_config(raw, commit_sha=commit_sha)
    except ConfigError as error:
        error.commit = commit_sha
        error.sha256 = hashlib.sha256(raw).hexdigest()
        raise
