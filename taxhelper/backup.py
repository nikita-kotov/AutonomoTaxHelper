"""Backups: zip of a consistent SQLite snapshot plus all attached documents.

Auto-backup writes into a user-chosen folder (any folder; one that a sync
service such as Google Drive, OneDrive or Dropbox uploads also protects against losing the computer). The live database is never placed in
the synced folder: syncing a SQLite file while it is being written can corrupt it.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from . import db
from .i18n import t
from .settings import SettingsRepository

PREFIX = "taxhelper-backup-"
STAMP = "%Y%m%d-%H%M"
MIN_INTERVAL = timedelta(hours=6)  # automatic backups are at most this frequent
RETRY_INTERVAL = timedelta(minutes=15)  # an attempt (even a failed one) is not repeated sooner
ATTEMPT_STAMP = "%Y-%m-%d %H:%M:%S"
PARTIAL_MAX_AGE_SECONDS = 3600  # an older .partial is a leftover of an interrupted run
KEEP_RECENT = 30  # newest N backups are kept; plus the first backup of every month
_RUNNING = threading.Lock()  # one backup at a time within the process


def backup_name(now: datetime) -> str:
    return f"{PREFIX}{now.strftime(STAMP)}.zip"


def build_backup_zip(con: sqlite3.Connection, target) -> None:
    """Write the zip (db snapshot + documents) to a path or file-like object."""
    tmp = Path(tempfile.mkdtemp(prefix="taxhelper-snap-"))
    try:
        snap = tmp / "taxhelper.db"
        dest = sqlite3.connect(snap)
        try:
            con.backup(dest)
        finally:
            dest.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "taxhelper.db")
            for p in sorted(db.documents_dir().iterdir()):
                if p.is_file():
                    z.write(p, f"documents/{p.name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_NAME = re.compile(re.escape(PREFIX) + r"(\d{8}-\d{4})\.zip")


def parse_backup_name(name: str) -> Optional[datetime]:
    """Timestamp encoded in a backup file name, or None if the name is not one of ours."""
    m = _NAME.fullmatch(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), STAMP)
    except ValueError:
        return None


def list_backups(folder: Path) -> List[Path]:
    """Our backup files in the folder, oldest first. Anything else in the folder is ignored."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and parse_backup_name(p.name))


def backup_time(path: Path) -> datetime:
    return parse_backup_name(path.name)


def prune(folder: Path, keep_recent: int = KEEP_RECENT) -> int:
    """Delete old backups; keep the newest N and the first one of each month."""
    files = list_backups(folder)
    keep = set(files[-keep_recent:])
    seen_months = set()
    for p in files:
        month = p.name[len(PREFIX):len(PREFIX) + 6]  # YYYYMM
        if month not in seen_months:
            seen_months.add(month)
            keep.add(p)
    removed = 0
    for p in files:
        if p not in keep:
            p.unlink()
            removed += 1
    return removed


def run_backup(con: sqlite3.Connection, now: datetime = None, force: bool = False) -> Tuple[str, str]:
    """Create a backup in the configured folder if one is due.

    Returns (status, message) where status is one of
    'disabled', 'skipped', 'ok', 'error'. The outcome is stored in settings
    (last_backup_at / last_backup_error) so the UI can show it.
    """
    now = now or datetime.now()
    settings = SettingsRepository(con)
    current = settings.load()
    folder_text = current.backup_dir.strip()
    if not folder_text:
        return "disabled", t("backup.not_set")
    if not _RUNNING.acquire(blocking=False):  # another request is already backing up
        return "skipped", t("backup.fresh")
    try:
        if not force and _tried_recently(current.last_backup_attempt, now):
            return "skipped", t("backup.fresh")  # a failing target is retried only every few minutes
        settings.update(last_backup_attempt=now.strftime(ATTEMPT_STAMP))
        return _write_backup(con, settings, Path(folder_text), now, force)
    finally:
        _RUNNING.release()


def _tried_recently(last_attempt: str, now: datetime) -> bool:
    try:
        return now - datetime.strptime(last_attempt, ATTEMPT_STAMP) < RETRY_INTERVAL
    except ValueError:
        return False


def _write_backup(con: sqlite3.Connection, settings: SettingsRepository, folder: Path, now: datetime,
                  force: bool) -> Tuple[str, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        existing = list_backups(folder)
        if not force and existing and now - backup_time(existing[-1]) < MIN_INTERVAL:
            return "skipped", t("backup.fresh")
        final = folder / backup_name(now)
        partial = folder / (final.name + ".partial")
        _remove_stale_partials(folder)
        try:
            build_backup_zip(con, partial)
            os.replace(partial, final)  # cloud client never sees a half-written .zip
        finally:
            partial.unlink(missing_ok=True)
        prune(folder)
    except (OSError, ValueError, sqlite3.Error) as exc:
        settings.update(last_backup_error=f"{now:%Y-%m-%d %H:%M} — {exc}")
        return "error", str(exc)
    settings.update(last_backup_at=now.strftime("%Y-%m-%d %H:%M"), last_backup_error="")
    return "ok", t("backup.saved", path=final)


def _remove_stale_partials(folder: Path) -> None:
    """Delete leftovers of interrupted runs; a recent .partial may belong to a backup still being written."""
    cutoff = time.time() - PARTIAL_MAX_AGE_SECONDS
    for stale in folder.glob(PREFIX + "*.partial"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink(missing_ok=True)
        except OSError:
            pass
