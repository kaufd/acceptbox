"""Отпечаток дерева EBT из объектов Git."""

import hashlib
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def _git(cwd: Path, env: dict[str, str], *args: str) -> bytes:
    return subprocess.run(
        ["git", "--no-replace-objects", "-C", str(cwd), *args],
        env=env,
        capture_output=True,
        check=True,
    ).stdout


def _hash_tree(repo: Path, commit: str, env: dict[str, str]) -> str:
    if _git(repo, env, "cat-file", "-t", commit).strip() != b"commit":
        raise ValueError("--ebt-sha должен указывать на коммит")

    entries = _git(repo, env, "ls-tree", "--full-tree", "-r", "-t", "-z", f"{commit}:ebt")
    digest = hashlib.sha256()
    for entry in entries.split(b"\0")[:-1]:
        metadata, path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        digest.update(mode + b" " + kind + b" " + path + b"\0")
        if kind == b"blob":
            blob = _git(repo, env, "cat-file", "blob", object_id.decode("ascii"))
            digest.update(hashlib.sha256(blob).digest())
        elif kind == b"commit":
            digest.update(hashlib.sha256(object_id).digest())
    return digest.hexdigest()


def tree_sha256(repository: str, commit: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
        raise ValueError("--ebt-sha должен содержать полный SHA коммита Git")
    commit = commit.lower()

    cwd = Path.cwd()
    local_variables = _git(cwd, dict(os.environ), "rev-parse", "--local-env-vars")
    env = os.environ.copy()
    for name in local_variables.decode("ascii").splitlines():
        env.pop(name, None)

    local_repo = Path(repository).expanduser()
    if local_repo.is_dir():
        return _hash_tree(local_repo, commit, env)
    with TemporaryDirectory(prefix="acceptbox-pin-") as temp:
        clone = Path(temp) / "repo"
        _git(cwd, env, "clone", "--quiet", "--no-checkout", "--", repository, str(clone))
        return _hash_tree(clone, commit, env)
