import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from support import set_settings
from taxhelper import backup as bk, db
from taxhelper.settings import SettingsRepository


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("TAXHELPER_DATA", str(data))
    con = db.connect()
    db.init_db(con)
    (db.data_dir() / "documents" / "abc_invoice.pdf").write_bytes(b"%PDF-1.4")
    con.execute("INSERT INTO entries (kind,date,base_cents) VALUES ('income','2026-01-05',12345)")
    con.commit()
    yield con, tmp_path / "cloud"
    con.close()


def configure(con, folder):
    SettingsRepository(con).update(backup_dir=str(folder))
    con.commit()


def test_disabled_without_folder(env):
    con, _ = env
    assert bk.run_backup(con)[0] == "disabled"


def test_backup_contains_consistent_db_and_documents(env):
    con, cloud = env
    configure(con, cloud)
    now = datetime(2026, 11, 4, 12, 0)
    status, _ = bk.run_backup(con, now)
    assert status == "ok"
    files = bk.list_backups(cloud)
    assert [p.name for p in files] == ["taxhelper-backup-20261104-1200.zip"]
    assert not list(cloud.glob("*.partial"))
    with zipfile.ZipFile(files[0]) as z:
        assert set(z.namelist()) == {"taxhelper.db", "documents/abc_invoice.pdf"}
        z.extract("taxhelper.db", cloud / "restored")
    restored = sqlite3.connect(cloud / "restored" / "taxhelper.db")
    assert restored.execute("SELECT base_cents FROM entries").fetchone()[0] == 12345
    restored.close()
    assert SettingsRepository(con).load().last_backup_at == "2026-11-04 12:00"


def test_respects_min_interval_and_force(env):
    con, cloud = env
    configure(con, cloud)
    t0 = datetime(2026, 11, 4, 9, 0)
    assert bk.run_backup(con, t0)[0] == "ok"
    assert bk.run_backup(con, t0 + timedelta(hours=2))[0] == "skipped"
    assert bk.run_backup(con, t0 + timedelta(hours=2), force=True)[0] == "ok"
    # the forced copy (t0+2h) is now the newest: +5h later is still too soon, +7h is due
    assert bk.run_backup(con, t0 + timedelta(hours=7))[0] == "skipped"
    assert bk.run_backup(con, t0 + timedelta(hours=9))[0] == "ok"
    assert len(bk.list_backups(cloud)) == 3


def test_prune_keeps_recent_and_first_of_each_month(tmp_path):
    folder = tmp_path / "b"
    folder.mkdir()
    stamps = [datetime(2026, 1, 3) + timedelta(days=d) for d in range(0, 120)]  # Jan..early May
    for s in stamps:
        (folder / bk.backup_name(s)).write_bytes(b"x")
    (folder / "notes.txt").write_text("not a backup")
    removed = bk.prune(folder, keep_recent=5)
    names = [p.name for p in bk.list_backups(folder)]
    assert (folder / "notes.txt").exists()  # foreign files untouched
    assert "taxhelper-backup-20260103-0000.zip" in names  # first of January
    assert any(n.startswith("taxhelper-backup-202602") for n in names)  # first of February
    assert names[-1] == bk.backup_name(stamps[-1])
    # kept: newest 5 (Apr 28 - May 2) + first of Jan 3, Feb 1, Mar 1, Apr 1 (May 1 is already among the newest)
    assert len(names) == 9 and removed == 111


def test_error_is_reported_not_raised(env, tmp_path):
    con, _ = env
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    configure(con, blocker / "sub")  # parent is a file -> cannot create folder
    status, msg = bk.run_backup(con)
    assert status == "error" and msg
    assert SettingsRepository(con).load().last_backup_error


def test_app_backs_up_after_write_and_validates_folder(fresh_app, tmp_path):
    set_settings(language="ru")  # the assertions below check Russian texts
    client = fresh_app.test_client()
    cloud = tmp_path / "cloud"
    base = {"language": "ru", "home_total_m2": "0", "home_affected_pct": "0", "supplies_factor_pct": "30",
            "difficult_pct_pct": "5", "difficult_cap_eur": "2000"}
    r = client.post("/settings", data={**base, "backup_dir": str(cloud)}, follow_redirects=True)
    assert "Настройки сохранены" in r.get_data(as_text=True)
    assert len(bk.list_backups(cloud)) == 1  # first backup right after saving the folder
    assert "Последний успешный бэкап" in client.get("/settings").get_data(as_text=True)
    blocker = tmp_path / "f.txt"
    blocker.write_text("x")
    bad = client.post("/settings", data={**base, "backup_dir": str(blocker / "x")})
    assert "недоступна для записи" in bad.get_data(as_text=True)
    now_r = client.post("/backup/now", follow_redirects=True)
    assert "Бэкап сохранён" in now_r.get_data(as_text=True)


def test_foreign_files_in_the_backup_folder_do_not_stop_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    junk = ["taxhelper-backup-old.zip", "taxhelper-backup-copy (1).zip", "taxhelper-backup-20269999-9999.zip", "notes.txt"]
    for name in junk:
        (cloud / name).write_text("x")
    leftover = cloud / "taxhelper-backup-20260101-0000.zip.partial"
    leftover.write_text("half-written")
    two_hours_ago = time.time() - 7_200
    os.utime(leftover, (two_hours_ago, two_hours_ago))  # an interrupted run from long ago
    SettingsRepository(con).update(backup_dir=str(cloud))
    status, _ = bk.run_backup(con, datetime(2026, 11, 4, 12, 0))
    con.close()
    assert status == "ok"
    assert [p.name for p in bk.list_backups(cloud)] == ["taxhelper-backup-20261104-1200.zip"]
    assert not list(cloud.glob("*.partial"))
    assert all((cloud / name).exists() for name in junk)  # nothing foreign is touched


def test_failed_backup_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    cloud = tmp_path / "cloud"
    SettingsRepository(con).update(backup_dir=str(cloud))

    def boom(_con, target):
        Path(target).write_text("half")
        raise OSError("disk full")

    monkeypatch.setattr(bk, "build_backup_zip", boom)
    status, message = bk.run_backup(con, datetime(2026, 11, 4, 12, 0))
    assert status == "error" and "disk full" in message
    assert not list(cloud.glob("*.partial")) and not bk.list_backups(cloud)
    assert "disk full" in SettingsRepository(con).load().last_backup_error
    con.close()


def test_backup_name_parsing():
    assert bk.parse_backup_name("taxhelper-backup-20261104-1200.zip") == datetime(2026, 11, 4, 12, 0)
    assert all(bk.parse_backup_name(n) is None for n in
               ("taxhelper-backup-old.zip", "taxhelper-backup-20261104-1200.zip.partial", "x.zip", "taxhelper-backup-20261301-0000.zip"))


@pytest.fixture()
def backup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    yield con, tmp_path
    con.close()


def test_a_failing_target_is_retried_only_every_few_minutes(backup_env):
    con, tmp = backup_env
    blocker = tmp / "file.txt"
    blocker.write_text("x")
    SettingsRepository(con).update(backup_dir=str(blocker / "sub"))  # cannot be created
    t0 = datetime(2026, 11, 4, 12, 0)
    assert bk.run_backup(con, t0)[0] == "error"
    assert bk.run_backup(con, t0.replace(minute=1))[0] == "skipped"     # next save a minute later: no new attempt
    assert bk.run_backup(con, t0.replace(minute=14))[0] == "skipped"
    assert bk.run_backup(con, t0.replace(minute=16))[0] == "error"      # after 15 minutes it tries again
    assert bk.run_backup(con, t0.replace(minute=17), force=True)[0] == "error"  # the button always tries


def test_a_successful_backup_is_not_repeated_within_the_interval(backup_env):
    con, tmp = backup_env
    SettingsRepository(con).update(backup_dir=str(tmp / "cloud"))
    t0 = datetime(2026, 11, 4, 12, 0)
    assert bk.run_backup(con, t0)[0] == "ok"
    assert bk.run_backup(con, t0.replace(minute=30))[0] == "skipped"
    assert bk.run_backup(con, t0.replace(hour=19))[0] == "ok"


def test_a_second_backup_waits_while_one_is_running(backup_env):
    con, tmp = backup_env
    SettingsRepository(con).update(backup_dir=str(tmp / "cloud"))
    assert bk._RUNNING.acquire(blocking=False)
    try:
        assert bk.run_backup(con, datetime(2026, 11, 4, 12, 0))[0] == "skipped"
        assert not (tmp / "cloud").exists()
    finally:
        bk._RUNNING.release()


def test_a_partial_file_of_a_running_backup_is_left_alone(backup_env):
    con, tmp = backup_env
    cloud = tmp / "cloud"
    cloud.mkdir()
    fresh = cloud / "taxhelper-backup-20261104-1100.zip.partial"
    fresh.write_text("being written by another request")
    SettingsRepository(con).update(backup_dir=str(cloud))
    assert bk.run_backup(con, datetime(2026, 11, 4, 12, 0))[0] == "ok"
    assert fresh.exists()


def test_concurrent_backups_do_not_corrupt_each_other(backup_env):
    con, tmp = backup_env
    cloud = tmp / "cloud"
    SettingsRepository(con).update(backup_dir=str(cloud))
    results = []

    def run():
        c = db.connect()
        try:
            results.append(bk.run_backup(c, datetime(2026, 11, 4, 12, 0), force=True)[0])
        finally:
            c.close()

    threads = [threading.Thread(target=run) for _ in range(4)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert "ok" in results and set(results) <= {"ok", "skipped"}
    assert len(bk.list_backups(cloud)) == 1 and not list(cloud.glob("*.partial"))
