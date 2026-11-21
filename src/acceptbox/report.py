"""Атомарный захват ``--out`` и запись отчётов accept.json / calibrate.json (format 1).

Прерванный прогон не оставляет ни ложного вердикта, ни мусора: отчёт пишется
в конце через ``<name>.tmp`` и ``os.replace``, ``.tmp`` и ``.acceptbox.lock``
убираются при выходе.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .verdict import RunOutcome

LOCK_NAME = ".acceptbox.lock"
TMP_NAMES = ("accept.json.tmp", "calibrate.json.tmp")


class OutNotEmptyError(Exception):
    """``--out`` существует, не пуст или захвачен другим процессом: код 2, без отчёта."""


def utc_now_iso() -> str:
    """Метка времени ISO-8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


class OutDirLock:
    """Атомарный захват пустого ``--out``: ``os.mkdir`` + ``os.open(.acceptbox.lock, O_EXCL)``.

    Гонка двух процессов за несуществующий ``--out``: оба доходят до O_EXCL,
    побеждает один; проигравший получает OutNotEmptyError и не трогает каталог
    победителя. После захвата блокировки каталог проверяется ещё раз: в нём
    должен быть только ``.acceptbox.lock``.
    """

    def __init__(self, out_path: Path):
        self.out_path = Path(out_path)
        self.lock_path = self.out_path / LOCK_NAME
        self.created_dir = False

    def __enter__(self) -> "OutDirLock":
        try:
            os.mkdir(self.out_path)
            self.created_dir = True
        except FileExistsError:
            if self.out_path.is_symlink() or not self.out_path.is_dir():
                raise OutNotEmptyError(
                    f"--out существует и не является каталогом: {self.out_path}") from None
            entries = os.listdir(self.out_path)
            if entries:
                raise OutNotEmptyError(
                    f"--out не пуст: {', '.join(sorted(entries)[:10])}") from None
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise OutNotEmptyError(
                f"--out занят другим процессом или содержит {LOCK_NAME}: {self.out_path}") from None
        else:
            os.close(descriptor)
        try:
            leftovers = [name for name in os.listdir(self.out_path) if name != LOCK_NAME]
        except OSError:
            self._release_lock()
            raise
        if leftovers:
            self._release_lock()
            raise OutNotEmptyError(f"--out не пуст: {', '.join(sorted(leftovers)[:10])}")
        return self

    def __exit__(self, *exc_info: object) -> None:
        for name in TMP_NAMES:
            try:
                (self.out_path / name).unlink()
            except FileNotFoundError:
                pass
        self._release_lock()

    def _release_lock(self) -> None:
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(out_path: Path, name: str, payload: dict) -> Path:
    """Атомарная запись JSON: ``<name>.tmp`` → flush+fsync → ``os.replace``."""
    final_path = Path(out_path) / name
    tmp_path = Path(out_path) / (name + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)
    return final_path


def skipped_run(commit: str | None, reason: str) -> dict:
    """Запись непрогнанного коммита в runs отчёта."""
    return {"commit": commit, "status": "skipped", "exit_code": None, "reason": reason,
            "duration_ms": None, "log_path": None, "artifacts": None}


def completed_run(commit: str, outcome: RunOutcome, *, log_path: str | None,
                  artifacts: dict | None = None) -> dict:
    """Запись выполненного прогона в runs/результаты отчёта."""
    return {"commit": commit, "status": outcome.status, "exit_code": outcome.exit_code,
            "reason": outcome.reason, "duration_ms": outcome.duration_ms,
            "log_path": log_path, "artifacts": artifacts}
