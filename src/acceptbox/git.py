"""Изоляция Git: временный клон, fetch по полному SHA, detached-worktree, ancestry, diff.

Правила изоляции (L17): каждый вызов git получает ``--no-replace-objects``;
клонирование, fetch и создание worktree дополнительно получают
``-c core.hooksPath=/dev/null``; каждый diff получает
``-c diff.renames=false --no-renames -z``; из окружения везде удаляются
переменные из ``git rev-parse --local-env-vars``.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from pathlib import Path

GIT_TIMEOUT_SEC = 600.0

# Резерв на случай, когда `git rev-parse --local-env-vars` недоступен (git не
# установлен): список стабилен для всех актуальных версий git.
_FALLBACK_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
    "GIT_SHALLOW_FILE", "GIT_COMMON_DIR",
)


# URL (scheme://) или scp-подобный адрес host:path — не локальный путь.
_REMOTE_SOURCE_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|[^/]+:")


def is_remote_source(value: str) -> bool:
    """True для URL и scp-адреса репозитория, False для локального пути."""
    return _REMOTE_SOURCE_RE.match(value) is not None


class GitError(Exception):
    """Сбой git-инфраструктуры: git недоступен, репозиторий недостижим, таймаут."""


@functools.lru_cache(maxsize=1)
def local_env_var_names() -> tuple[str, ...]:
    """Имена переменных git-изоляции; работает и вне git-репозитория."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"],
            capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _FALLBACK_LOCAL_ENV_VARS
    if result.returncode == 0:
        names = tuple(result.stdout.decode("ascii", "replace").split())
        if names:
            return names
    return _FALLBACK_LOCAL_ENV_VARS


def strip_local_env_vars(env: dict[str, str]) -> dict[str, str]:
    """Копия окружения без переменных git-изоляции (защита от GIT_DIR и т.п.)."""
    clean = dict(env)
    for name in local_env_var_names():
        clean.pop(name, None)
    return clean


def base_env() -> dict[str, str]:
    """Окружение git-операций acceptbox: os.environ без local-env-vars."""
    env = strip_local_env_vars(dict(os.environ))
    # Репозиторий может требовать авторизацию: без подсказок git падает, а не висит.
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def run_git(repo: Path | None, env: dict[str, str], *args: str,
            timeout: float = GIT_TIMEOUT_SEC) -> bytes:
    """Вызов git с ``--no-replace-objects``; любая ошибка или таймаут — GitError."""
    argv = ["git", "--no-replace-objects"]
    if repo is not None:
        argv += ["-C", str(repo)]
    argv += list(args)
    try:
        result = subprocess.run(argv, env=env, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise GitError(f"git {args[0]}: timed out after {timeout:g}s") from error
    except OSError as error:
        raise GitError(f"failed to start git: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        message = f"git {' '.join(args[:2])}: exit code {result.returncode}"
        raise GitError(f"{message}: {detail[-1]}" if detail else message)
    return result.stdout


def probe_git() -> dict:
    """Диагностика git для ``acceptbox --installation``."""
    executable = shutil.which("git")
    if executable is None:
        return {"path": None, "version": None, "status": "missing"}
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"path": executable, "version": None, "status": "error"}
    if result.returncode != 0:
        return {"path": executable, "version": None, "status": "error"}
    match = re.search(r"git version\s+(\S+)", result.stdout.decode("utf-8", "replace"))
    if match is None:
        return {"path": executable, "version": None, "status": "error"}
    return {"path": executable, "version": match.group(1), "status": "ok"}


def clone_no_checkout(source: str, destination: Path, env: dict[str, str]) -> None:
    """Временный клон без checkout; локальные пути клонируются с ``--no-local``.

    ``--no-local`` исключает hardlink'и на объекты репозитория пользователя и
    гарантирует полноту передачи через transport (все объявленные ссылки).
    """
    args = ["-c", "core.hooksPath=/dev/null", "clone", "--quiet", "--no-checkout"]
    if os.path.exists(source):
        args.append("--no-local")
    args += ["--", source, str(destination)]
    run_git(None, env, *args)


def commit_present(repo: Path, env: dict[str, str], sha: str) -> bool:
    """True, только если sha разрешается в коммит; любое несоответствие — False."""
    try:
        kind = run_git(repo, env, "cat-file", "-t", f"{sha}^{{commit}}")
    except GitError:
        return False
    return kind.strip() == b"commit"


def _fetch(repo: Path, env: dict[str, str], source: str, *refspecs: str) -> None:
    # "--": источник (в т.ч. URL origin из .git/config) не может стать опцией fetch.
    # Локальный относительный путь (например ".") приводим к абсолютному, иначе
    # git -C <temp_clone> трактовал бы его относительно временного клона.
    fetch_source = source if is_remote_source(source) else os.path.abspath(source)
    run_git(repo, env, "-c", "core.hooksPath=/dev/null", "fetch", "--no-tags", "--",
            fetch_source, *refspecs)


def fetch_all_refs(repo: Path, env: dict[str, str], source: str) -> None:
    """Вытянуть все ссылки источника в отдельное пространство имён.

    Гарантирует доступность коммитов с не-дефолтных веток и PR-ссылок как в
    локальных репозиториях (без ``uploadpack.allowAnySHA1InWant``), так и на серверах.
    """
    _fetch(repo, env, source, "+refs/*:refs/acceptbox-fetch/*")


def ensure_commit(repo: Path, env: dict[str, str], source: str, sha: str) -> bool:
    """Гарантировать наличие коммита в клоне: cat-file → fetch по SHA → fetch всех ссылок.

    True — коммит доступен; False — источник достижим, но коммита нет;
    GitError — недостижимый источник или сбой сети (gate-error).
    """
    if commit_present(repo, env, sha):
        return True
    try:
        _fetch(repo, env, source, sha)
    except GitError:
        fetch_all_refs(repo, env, source)
    return commit_present(repo, env, sha)


def resolve_ref(repo: Path, env: dict[str, str], *candidates: str) -> str | None:
    """Полный SHA коммита первого подходящего кандидата, иначе None."""
    for candidate in candidates:
        try:
            output = run_git(repo, env, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        except GitError:
            continue
        return output.strip().decode("ascii")
    return None


def resolve_repo_head(source: str, env: dict[str, str]) -> str:
    """Разрешить HEAD репозитория ``source`` в полный SHA коммита (L22).

    Для локального каталога использует ``rev-parse --verify HEAD^{commit}``,
    для URL — ``ls-remote -- <source> HEAD``. Любой сбой — GitError.
    """
    local_path = Path(source).expanduser()
    if local_path.is_dir():
        output = run_git(local_path, env, "rev-parse", "--verify", "HEAD^{commit}")
        sha = output.strip().decode("ascii", "replace")
        if not sha:
            raise GitError(f"failed to resolve HEAD in {source}")
        return sha
    output = run_git(None, env, "-c", "core.hooksPath=/dev/null",
                     "ls-remote", "--", source, "HEAD")
    for line in output.decode("ascii", "replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1] == "HEAD" and parts[0]:
            return parts[0]
    raise GitError(f"ref HEAD not found in repository {source}")


def trusted_ref_source(source: str, env: dict[str, str]) -> tuple[str, str]:
    """Откуда брать доверенную ветку (L29): ``(источник для fetch, kind)``.

    URL — как есть (``remote``); локальный путь — его remote ``origin``
    (``origin``), ведь локальные ветки может переписать любой, кто пишет в
    checkout; без ``origin`` — сам путь (``local``, слабая проверка).
    """
    if is_remote_source(source):
        return source, "remote"
    argv = ["git", "--no-replace-objects", "-C", source, "remote", "get-url", "origin"]
    try:
        result = subprocess.run(argv, env=env, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise GitError(f"git remote get-url origin: {error}") from error
    # Код 2 — remote origin не настроен; любой другой сбой (safe.directory и т.п.)
    # не должен молча ослаблять проверку до локальной ветки.
    if result.returncode == 2:
        return os.path.abspath(source), "local"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        message = f"git remote get-url origin: exit code {result.returncode}"
        raise GitError(f"{message}: {detail[-1]}" if detail else message)
    url = result.stdout.strip().decode("utf-8", "replace")
    if not url or url.startswith("-") or any(ord(char) < 32 for char in url):
        raise GitError(f"invalid remote origin URL in {source}: {url!r}")
    if not is_remote_source(url) and not os.path.isabs(url):
        url = os.path.normpath(os.path.join(os.path.abspath(source), url))
    return url, "origin"


def fetch_ref(repo: Path, env: dict[str, str], source: str, ref: str) -> str | None:
    """Вытянуть ссылку EBT и вернуть SHA коммита, на который она указывает.

    Короткое имя разрешается явно: сначала ``refs/heads/<ref>``, затем
    ``refs/tags/<ref>`` (DWIM git fetch предпочёл бы одноимённый тег ветке).
    При неудаче — все ссылки источника в том же порядке.
    None — ссылка не найдена (источник достижим); GitError — сбой источника/сети.
    """
    if ref.startswith("refs/"):
        candidates = (ref,)
    else:
        candidates = (f"refs/heads/{ref}", f"refs/tags/{ref}")
    for candidate in candidates:
        try:
            _fetch(repo, env, source, f"+{candidate}:refs/acceptbox-ebt/ref")
        except GitError:
            continue
        return resolve_ref(repo, env, "refs/acceptbox-ebt/ref")
    fetch_all_refs(repo, env, source)
    mapped = tuple(f"refs/acceptbox-fetch/{name[len('refs/'):]}" for name in candidates)
    return resolve_ref(repo, env, *mapped)


def add_worktree(repo: Path, env: dict[str, str], sha: str, path: Path) -> None:
    """Detached-worktree для прогона: checkout пользователя не переключается, хуки отключены."""
    run_git(repo, env, "-c", "core.hooksPath=/dev/null", "worktree", "add", "--quiet",
            "--detach", str(path), sha)


def rev_parse_tree(repo: Path, env: dict[str, str], sha: str) -> str:
    """SHA дерева коммита (L13): связывает проверенное дерево с доставленным коммитом."""
    return run_git(repo, env, "rev-parse", "--verify", f"{sha}^{{tree}}").strip().decode("ascii")


def is_ancestor(repo: Path, env: dict[str, str], ancestor: str, descendant: str) -> bool:
    """``git merge-base --is-ancestor``: True/False; любой иной исход — GitError."""
    argv = ["git", "--no-replace-objects", "-C", str(repo),
            "merge-base", "--is-ancestor", ancestor, descendant]
    try:
        result = subprocess.run(argv, env=env, capture_output=True,
                                timeout=GIT_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired as error:
        raise GitError("git merge-base: timed out") from error
    except OSError as error:
        raise GitError(f"failed to start git: {error}") from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
    message = f"git merge-base --is-ancestor: exit code {result.returncode}"
    raise GitError(f"{message}: {detail[-1]}" if detail else message)


def merge_base(repo: Path, env: dict[str, str], commit_a: str, commit_b: str) -> str | None:
    """``git merge-base`` двух коммитов: полный SHA или None, если общего предка нет."""
    argv = ["git", "--no-replace-objects", "-C", str(repo),
            "merge-base", commit_a, commit_b]
    try:
        result = subprocess.run(argv, env=env, capture_output=True,
                                timeout=GIT_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired as error:
        raise GitError("git merge-base: timed out") from error
    except OSError as error:
        raise GitError(f"failed to start git: {error}") from error
    if result.returncode == 0:
        sha = result.stdout.strip().decode("ascii", "replace").lower()
        return sha or None
    if result.returncode == 1:
        return None
    detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
    message = f"git merge-base: exit code {result.returncode}"
    raise GitError(f"{message}: {detail[-1]}" if detail else message)


def first_parent_roles(repo: Path, env: dict[str, str], base: str,
                       head: str) -> list[tuple[str, list[str]]]:
    """First-parent коммиты ``base..head`` по порядку и значения их trailer
    ``AcceptBox-Role`` — только из блока trailers (разбор git), одним вызовом."""
    output = run_git(repo, env, "log", "--no-show-signature", "--first-parent", "--reverse",
                     "--format=%H %(trailers:key=AcceptBox-Role,valueonly,unfold,separator=%x2C)",
                     f"{base}..{head}")
    commits: list[tuple[str, list[str]]] = []
    for line in output.decode("utf-8", "replace").splitlines():
        sha, _, roles = line.strip().partition(" ")
        if sha:
            commits.append((sha.lower(), [role.strip().lower() for role in roles.split(",")
                                          if role.strip()]))
    return commits


def changed_paths(repo: Path, env: dict[str, str], older: str, newer: str,
                  pathspecs: list[str] | None = None, *,
                  diff_filter: str | None = None) -> list[str]:
    """Пути, изменённые между деревьями двух коммитов; ``--no-renames``:
    переименование распадается в удаление старого пути и добавление нового.

    ``diff_filter`` ограничивает типы изменений (``git diff --diff-filter``):
    ``"a"`` — все изменения, кроме добавлений; ``"A"`` — только добавления.
    """
    args = ["-c", "diff.renames=false", "diff", "--name-only", "--no-renames"]
    if diff_filter:
        args.append(f"--diff-filter={diff_filter}")
    args += ["-z", older, newer]
    if pathspecs:
        args += ["--", *pathspecs]
    output = run_git(repo, env, *args)
    return [path for path in output.decode("utf-8", "replace").split("\0") if path]
