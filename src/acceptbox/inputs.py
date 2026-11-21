"""Валидация входов CLI и построение чистого окружения прогона."""

from __future__ import annotations

import math
import re

from .git import local_env_var_names


class InputError(ValueError):
    """Ошибка входов CLI: код 2, stderr, без accept.json."""


_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

# Минимальный allowlist наследуемого окружения прогона (L17).
ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG",
                "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "TZ")


def validate_commit_sha(value: str, flag: str) -> str:
    """Полный SHA коммита Git (40 или 64 hex), нормализованный в нижний регистр."""
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise InputError(f"{flag} должен быть полным SHA коммита Git (40 или 64 hex-символа)")
    return value.lower()


def validate_sha256(value: str, flag: str) -> str:
    """SHA-256 (64 hex), нормализованный в нижний регистр."""
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise InputError(f"{flag} должен содержать SHA-256 (64 hex-символа)")
    return value.lower()


def validate_timeout(value: str | int | float, flag: str = "--timeout") -> float:
    """Положительное конечное число секунд."""
    if isinstance(value, bool):
        raise InputError(f"{flag} должен быть положительным числом секунд")
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise InputError(f"{flag} должен быть положительным числом секунд") from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise InputError(f"{flag} должен быть конечным положительным числом секунд")
    return timeout


def validate_pathspec(value: str, flag: str) -> str:
    """Непустой pathspec без отрицающей магии (``:!``, ``:^``, ``:(exclude)``)."""
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{flag}: pathspec пуст")
    if value.startswith(":"):
        magic = value[1:]
        if magic.startswith("("):
            end = magic.find(")")
            if end < 0:
                raise InputError(f"{flag}: незакрытый pathspec magic")
            keywords = [item.strip().lower() for item in magic[1:end].split(",")]
            if any(item.startswith(("!", "^")) or item.lstrip("!^") == "exclude"
                   for item in keywords):
                raise InputError(f"{flag}: pathspec с отрицающей магией (exclude) не допускается")
        else:
            # Короткая магия pathspec (/, !, ^) идёт до первого символа пути или ':'.
            short = []
            for ch in magic:
                if ch in ("/", "!", "^"):
                    short.append(ch)
                else:
                    break
            if "!" in short or "^" in short:
                raise InputError(f"{flag}: pathspec с отрицающей магией (!/^) не допускается")
    return value


def validate_repo_source(value: str, flag: str) -> str:
    """Путь или URL репозитория без управляющих символов и ведущего дефиса."""
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{flag}: укажите путь или URL репозитория")
    if value.startswith("-"):
        raise InputError(f"{flag}: значение не может начинаться с '-'")
    if "\x00" in value or any(ord(char) < 32 for char in value):
        raise InputError(f"{flag}: недопустимые управляющие символы")
    return value


def validate_ref(value: str, flag: str) -> str:
    """Имя ветки или полной ссылки без refspec-синтаксиса и недопустимых символов."""
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{flag}: укажите ветку или ссылку")
    if value.startswith(("-", ":")):
        raise InputError(f"{flag}: значение не может начинаться с '-' или ':'")
    if "\x00" in value or any(char.isspace() or ord(char) < 32 for char in value):
        raise InputError(f"{flag}: недопустимые символы в ссылке")
    for fragment in ("..", "@{", ":", "*", "?", "[", "//", "/."):
        if fragment in value:
            raise InputError(f"{flag}: недопустимые символы в ссылке")
    if value.endswith(("/", ".", ".lock")):
        raise InputError(f"{flag}: недопустимое имя ссылки")
    return value


def validate_env_key(key: str, flag: str = "--env") -> None:
    """Запрещённые имена: BASE (задаётся автоматически), EBT_*, git-изоляция."""
    if key == "BASE":
        raise InputError(f"{flag}: BASE задаётся автоматически из --base")
    if key.startswith("EBT_"):
        raise InputError(f"{flag}: переменные EBT_* запрещены")
    if key in local_env_var_names():
        raise InputError(f"{flag}: переменные git-изоляции ({key}) запрещены")


def parse_env_entry(entry: str, flag: str = "--env") -> tuple[str, str | None]:
    """``KEY=VAL`` — задать значение; ``KEY`` — взять значение из окружения вызывающего.

    Возвращает (имя, значение или None для проброса из окружения).
    """
    if not isinstance(entry, str) or not entry:
        raise InputError(f"{flag}: пустая запись")
    if "\x00" in entry:
        raise InputError(f"{flag}: недопустимые управляющие символы")
    key, separator, value = entry.partition("=")
    if not key:
        raise InputError(f"{flag}: имя переменной пустое")
    if any(char.isspace() or ord(char) < 32 for char in key):
        raise InputError(f"{flag}: некорректное имя переменной {key!r}")
    validate_env_key(key, flag)
    return key, (value if separator else None)


def parse_invariants(value: str | None, flag: str = "--inv") -> list[str]:
    """Разбор ``--inv``: инварианты через запятую; только запятые/пробелы → пустой список."""
    items = [item.strip() for item in (value or "").split(",")]
    return [item for item in items if item]


def build_runtime_env(environ: dict[str, str], env_entries: list[tuple[str, str | None]], *,
                      base_sha: str | None = None,
                      inv_csv: str | None = None) -> tuple[dict[str, str], list[str]]:
    """Чистое окружение прогона: allowlist + ``--env``; в режиме witnessbox
    принудительно задаются ``BASE`` и ``EBT_INV`` (L17).

    ``inv_csv is not None`` означает режим witnessbox. Возвращает
    (окружение, отсортированные имена переданных ``--env`` без значений).
    """
    env = {name: environ[name] for name in ENV_ALLOWLIST if name in environ}
    env_keys: list[str] = []
    for key, value in env_entries:
        env_keys.append(key)
        if value is None:
            if key in environ:
                env[key] = environ[key]
        else:
            env[key] = value
    # Оборонительно: наследуемые EBT_* и переменные git-изоляции отбрасываются всегда.
    for name in local_env_var_names():
        env.pop(name, None)
    for name in [name for name in env if name.startswith("EBT_")]:
        env.pop(name)
    if inv_csv is not None:
        if base_sha is not None:
            env["BASE"] = base_sha
        env["EBT_INV"] = inv_csv
    return env, sorted(set(env_keys))
