"""Отпечаток дерева EBT из объектов Git."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .git import (GitError, base_env, clone_no_checkout, ensure_commit, run_git,
                  strip_local_env_vars)

_SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


class EbtPathMissing(ValueError):
    """В коммите нет каталога ebt/ (для доверенной подготовки — inputs-untrusted)."""


def _hash_tree(repo: Path, commit: str, env: dict[str, str]) -> str:
    """SHA-256 дерева ``<commit>:ebt`` из объектов Git: метаданные + содержимое блобов."""
    try:
        kind = run_git(repo, env, "cat-file", "-t", f"{commit}^{{commit}}").strip()
    except GitError as error:
        raise ValueError(f"--ebt-sha должен указывать на коммит: {error}") from error
    if kind != b"commit":
        raise ValueError("--ebt-sha должен указывать на коммит")

    top = run_git(repo, env, "ls-tree", "-z", commit, "--", "ebt")
    entries = [entry for entry in top.split(b"\0") if entry]
    if not entries:
        raise EbtPathMissing("путь ebt/ отсутствует в коммите")
    entry_kind = entries[0].split(b"\t", 1)[0].split()[1]
    if entry_kind != b"tree":
        raise EbtPathMissing("путь ebt/ в коммите не является каталогом")

    listing = run_git(repo, env, "ls-tree", "--full-tree", "-r", "-t", "-z", f"{commit}:ebt")
    digest = hashlib.sha256()
    for entry in listing.split(b"\0")[:-1]:
        metadata, path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        digest.update(mode + b" " + kind + b" " + path + b"\0")
        if kind == b"blob":
            blob = run_git(repo, env, "cat-file", "blob", object_id.decode("ascii"))
            digest.update(hashlib.sha256(blob).digest())
        elif kind == b"commit":
            digest.update(hashlib.sha256(object_id).digest())
    return digest.hexdigest()


def tree_sha256_in_repo(repo: Path, commit: str, env: dict[str, str]) -> str:
    """SHA-256 дерева ebt/ в уже клонированном/вытянутом репозитории.

    Позволяет git-изоляции использовать готовый клон без повторного клонирования.
    """
    return _hash_tree(Path(repo), commit, strip_local_env_vars(env))


def tree_sha256(repository: str, commit: str) -> str:
    """SHA-256 дерева ebt/ из объектов Git по полному SHA коммита."""
    if not _SHA_RE.fullmatch(commit):
        raise ValueError("--ebt-sha должен содержать полный SHA коммита Git")
    if repository.startswith("-"):
        raise ValueError("репозиторий не может начинаться с '-'")
    commit = commit.lower()

    env = base_env()
    local_repo = Path(repository).expanduser()
    if local_repo.is_dir():
        return tree_sha256_in_repo(local_repo, commit, env)
    with TemporaryDirectory(prefix="acceptbox-pin-") as temp:
        clone = Path(temp) / "repo"
        clone_no_checkout(repository, clone, env)
        if not ensure_commit(clone, env, repository, commit):
            raise ValueError(f"коммит {commit} не найден в репозитории")
        return tree_sha256_in_repo(clone, commit, env)
