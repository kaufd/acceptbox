"""Прогон одного коммита в режиме command: argv после --, без shell (L19)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .git import strip_local_env_vars
from .verdict import RunOutcome

LOG_NAME = "command.log"


def run_commit(worktree: Path, commit_sha: str, run_dir: Path, env: dict[str, str],
               timeout: float, *, cmd_argv: list[str]) -> RunOutcome:
    """Выполнить команду в detached-worktree коммита и классифицировать исход.

    Код 0 → green; код 1 → red; любой другой код, завершение по сигналу,
    ошибка запуска (OSError) или таймаут → error.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    clean_env = strip_local_env_vars(env)
    exit_code: int | None = None
    stdout = stderr = b""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(cmd_argv), cwd=str(worktree), env=clean_env,
            timeout=timeout, shell=False, capture_output=True,
        )
    except subprocess.TimeoutExpired as expired:
        stdout, stderr = expired.stdout or b"", expired.stderr or b""
        status, reason = "error", f"команда превысила таймаут {timeout:g} с"
    except OSError as os_error:
        status, reason = "error", f"не удалось запустить команду: {os_error}"
    else:
        stdout, stderr = completed.stdout, completed.stderr
        exit_code = completed.returncode
        if completed.returncode == 0:
            status, reason = "green", "команда завершилась с кодом 0"
        elif completed.returncode == 1:
            status, reason = "red", "команда завершилась с кодом 1"
        elif completed.returncode < 0:
            status, reason = "error", f"команда завершена сигналом {-completed.returncode}"
        else:
            status, reason = "error", f"команда завершилась с кодом {completed.returncode}"
    duration_ms = int((time.monotonic() - started) * 1000)
    (run_dir / LOG_NAME).write_bytes(stdout + stderr)
    return RunOutcome(status=status, exit_code=exit_code, reason=reason,
                      duration_ms=duration_ms, log_path=LOG_NAME, artifacts=None)
