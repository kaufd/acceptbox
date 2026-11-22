"""Прогон одного коммита в режиме command: argv после --, без shell (L19)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .git import strip_local_env_vars
from .verdict import RunOutcome

LOG_NAME = "command.log"
# Сколько ждать закрытия pipe после SIGKILL группы (потомок мог уйти в свою сессию).
_DRAIN_TIMEOUT_SEC = 5.0


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_process_group(argv: list[str], *, cwd: str, env: dict[str, str],
                      timeout: float) -> subprocess.CompletedProcess:
    """Аналог ``subprocess.run(capture_output=True, timeout=...)`` в отдельной сессии.

    По таймауту и после завершения убивается вся группа процессов: потомки
    (раннеры тестов, серверы) не переживают прогон и не мешают следующему.
    Таймаут — ``subprocess.TimeoutExpired`` с выводом, собранным до SIGKILL.
    """
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=_DRAIN_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                stdout = stderr = b""
            raise subprocess.TimeoutExpired(argv, timeout, output=stdout,
                                            stderr=stderr) from None
    finally:
        _kill_group(process.pid)
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdout, process.stderr):
            stream.close()
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


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
        completed = run_process_group(list(cmd_argv), cwd=str(worktree),
                                      env=clean_env, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        stdout, stderr = expired.stdout or b"", expired.stderr or b""
        status, reason = "error", f"command timed out after {timeout:g}s"
    except OSError as os_error:
        status, reason = "error", f"failed to start command: {os_error}"
    else:
        stdout, stderr = completed.stdout, completed.stderr
        exit_code = completed.returncode
        if completed.returncode == 0:
            status, reason = "green", "command exited with code 0"
        elif completed.returncode == 1:
            status, reason = "red", "command exited with code 1"
        elif completed.returncode < 0:
            status, reason = "error", f"command terminated by signal {-completed.returncode}"
        else:
            status, reason = "error", f"command exited with code {completed.returncode}"
    duration_ms = int((time.monotonic() - started) * 1000)
    (run_dir / LOG_NAME).write_bytes(stdout + stderr)
    return RunOutcome(status=status, exit_code=exit_code, reason=reason,
                      duration_ms=duration_ms, log_path=LOG_NAME, artifacts=None)
