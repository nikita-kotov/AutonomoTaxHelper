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
from typing import IO, List, Optional, Set, Tuple, Union

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


def build_backup_zip(con: sqlite3.Connection, target: Union[Path, IO[bytes]]) -> None:
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
    """The timestamp in the name of one of our backup files."""
    stamp = parse_backup_name(path.name)
    if stamp is None:
        raise ValueError(f"{path.name} is not a backup file name")
    return stamp


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


def run_backup(con: sqlite3.Connection, now: Optional[datetime] = None, force: bool = False) -> Tuple[str, str]:
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


# ----------------------------------------------------------------- restore

DB_NAME = "taxhelper.db"
SAFETY_DIR = "before-restore"  # inside the data folder: a copy of what a restore replaced
SAFETY_PREFIX = "taxhelper-before-restore-"
SAFETY_KEEP = 3
REQUIRED_TABLES = {"settings", "categories", "entries"}


class RestoreError(Exception):
    """A backup that cannot be restored. `key` is the locale key of the message and `params` fill its placeholders."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params

    def message(self) -> str:
        """The error text in the current interface language."""
        return t(self.key, **self.params)


def _is_plain_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\:")


def _unpack(archive: Union[Path, IO[bytes]], staging: Path) -> Set[str]:
    """Check the zip (path or file object) and unpack it into `staging`; returns the names of its documents.

    Only `taxhelper.db` and flat `documents/<name>` members are accepted, so nothing can be written outside the folder.
    """
    try:
        z = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError):
        raise RestoreError("restore.not_zip") from None
    names = set()
    with z:
        if DB_NAME not in z.namelist():
            raise RestoreError("restore.no_database")
        (staging / "documents").mkdir()
        for info in z.infolist():
            if info.is_dir():
                continue
            if info.filename == DB_NAME:
                target = staging / DB_NAME
            else:
                folder, _, name = info.filename.partition("/")
                if folder != "documents" or not _is_plain_name(name):
                    raise RestoreError("restore.unexpected_file", name=info.filename)
                names.add(name)
                target = staging / "documents" / name
            with z.open(info) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
    return names


def _check_database(path: Path) -> None:
    """The unpacked database must be intact, be ours and not come from a newer version of the app."""
    try:
        snap = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            if snap.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("restore.damaged")
            tables = {row[0] for row in snap.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            version = snap.execute("PRAGMA user_version").fetchone()[0]
        finally:
            snap.close()
    except sqlite3.DatabaseError:
        raise RestoreError("restore.damaged") from None
    if not REQUIRED_TABLES <= tables:
        raise RestoreError("restore.not_ours")
    if version > db.SCHEMA_VERSION:
        raise RestoreError("restore.newer")


def _save_current(con: sqlite3.Connection, now: datetime) -> Path:
    """Write the data that a restore is about to replace into `before-restore/`; the newest few are kept."""
    folder = db.data_dir() / SAFETY_DIR
    target = folder / f"{SAFETY_PREFIX}{now:%Y%m%d-%H%M%S}.zip"
    try:
        folder.mkdir(exist_ok=True)
        build_backup_zip(con, target)
        for old in sorted(folder.glob(SAFETY_PREFIX + "*.zip"))[:-SAFETY_KEEP]:
            old.unlink()
    except OSError as exc:
        raise RestoreError("restore.safety_failed", error=exc) from None
    return target


def restore_backup(con: sqlite3.Connection, archive: Union[Path, IO[bytes]], now: Optional[datetime] = None) -> Path:
    """Replace the database and the documents with the contents of a backup zip (a path or a file object).

    The zip is checked and unpacked first, and the current data is saved to `before-restore/`; only then is anything
    replaced. The documents of the backup are put in place before the database, so a failure leaves the old data intact.
    Documents that the backup does not have are removed at the end. Returns the path of the saved copy of the old data;
    raises RestoreError, with nothing changed, when the backup cannot be used.
    """
    now = now or datetime.now()
    if not _RUNNING.acquire(blocking=False):  # not while a backup is being written
        raise RestoreError("restore.busy")
    try:
        staging = Path(tempfile.mkdtemp(prefix="restore-", dir=db.data_dir()))
        try:
            documents = _unpack(archive, staging)
            _check_database(staging / DB_NAME)
            con.commit()
            saved = _save_current(con, now)
            for name in documents:
                os.replace(staging / "documents" / name, db.documents_dir() / name)
            _replace_database(con, staging / DB_NAME)
            _remove_other_documents(documents)
            return saved
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    finally:
        _RUNNING.release()


def _replace_database(con: sqlite3.Connection, snapshot: Path) -> None:
    """Copy the snapshot into the live database in one step (SQLite's backup API), then bring it to the current schema."""
    source = sqlite3.connect(snapshot)
    try:
        source.backup(con)
    except sqlite3.Error as exc:
        raise RestoreError("restore.failed", error=exc) from None
    finally:
        source.close()
    db.init_db(con)


def _remove_other_documents(keep: Set[str]) -> None:
    for path in db.documents_dir().iterdir():
        if path.is_file() and path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass  # an orphan file does no harm; the old data is in before-restore/
