"""Restoring a backup: the happy paths, every refusal (which must leave the data untouched) and the safety copy."""
import io
import sqlite3
import zipfile
from datetime import datetime

import pytest

from support import expense_form, set_settings, text
from taxhelper import backup as bk, db

PDF = b"%PDF-1.4 first invoice\n" + bytes(range(256))


def add_entry(client, number, base, document=None):
    data = {**expense_form(base=base, counterparty="Vendor"), "number": number}
    if document:
        data["document"] = (io.BytesIO(document), "invoice.pdf")
    client.post("/entries/new/expense", data=data, content_type="multipart/form-data")


def numbers():
    con = db.connect()
    try:
        return [r[0] for r in con.execute("SELECT number FROM entries ORDER BY id")]
    finally:
        con.close()


def document_names():
    return sorted(p.name for p in db.documents_dir().iterdir())


def stored_document(number):
    con = db.connect()
    try:
        return con.execute("SELECT document FROM entries WHERE number = ?", (number,)).fetchone()[0]
    finally:
        con.close()


def restore(client, archive, name="backup.zip", **fields):
    data = {"archive": (io.BytesIO(archive), name)} if archive is not None else {}
    return client.post("/backup/restore", data={**data, **fields}, content_type="multipart/form-data",
                       follow_redirects=True)


def zip_of(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    return buf.getvalue()


@pytest.fixture()
def saved(client):
    """The state that gets backed up: entries A-1 (with a PDF) and A-2; returns the zip and the stored PDF name."""
    add_entry(client, "A-1", "100", PDF)
    add_entry(client, "A-2", "200")
    return client.get("/backup").data


def changed_since_backup(client):
    """Data that exists only after the backup: an entry with its own document, and A-1's document removed."""
    add_entry(client, "B-1", "300", b"%PDF-1.4 later invoice")
    con = db.connect()
    first = con.execute("SELECT id FROM entries WHERE number = 'A-1'").fetchone()[0]
    con.close()
    client.post(f"/entries/{first}/delete")  # removes the row and its document, like the user would


def test_restore_returns_the_data_of_the_backup(client, saved):
    original_pdf = stored_document("A-1")
    changed_since_backup(client)
    assert numbers() == ["A-2", "B-1"]

    page = text(restore(client, saved))

    assert "Restored from backup.zip" in page
    assert numbers() == ["A-1", "A-2"]
    assert document_names() == [original_pdf]  # the later document is gone, the backed-up one is back
    assert client.get("/documents/" + original_pdf).data == PDF


def test_the_replaced_data_is_kept_in_a_safety_copy(client, saved):
    changed_since_backup(client)
    restore(client, saved)

    [copy] = (db.data_dir() / bk.SAFETY_DIR).glob("*.zip")
    with zipfile.ZipFile(copy) as z:
        z.extract("taxhelper.db", copy.parent / "old")
        assert any(n.startswith("documents/") for n in z.namelist())
    old = sqlite3.connect(copy.parent / "old" / "taxhelper.db")
    assert [r[0] for r in old.execute("SELECT number FROM entries ORDER BY id")] == ["A-2", "B-1"]
    old.close()


def test_only_the_newest_safety_copies_are_kept(client, saved):
    for second in range(5):
        con = db.connect()
        bk.restore_backup(con, io.BytesIO(saved), datetime(2026, 11, 4, 12, 0, second))
        con.close()
    kept = sorted(p.name for p in (db.data_dir() / bk.SAFETY_DIR).glob("*.zip"))
    assert kept == [f"{bk.SAFETY_PREFIX}20261104-12000{s}.zip" for s in (2, 3, 4)]


def test_restore_from_the_backup_folder(client, tmp_path):
    folder = tmp_path / "cloud"
    set_settings(backup_dir=str(folder))
    add_entry(client, "A-1", "100", PDF)
    con = db.connect()
    assert bk.run_backup(con, datetime(2026, 11, 4, 12, 0), force=True)[0] == "ok"
    con.close()
    add_entry(client, "B-1", "300")

    page = text(client.get("/settings"))
    assert 'value="taxhelper-backup-20261104-1200.zip"' in page and "2026-11-04 12:00" in page

    after = text(client.post("/backup/restore", data={"name": "taxhelper-backup-20261104-1200.zip"},
                             follow_redirects=True))
    assert "Restored from taxhelper-backup-20261104-1200.zip" in after
    assert numbers() == ["A-1"]


def test_a_backup_from_an_older_version_is_brought_up_to_date(client, saved):
    with zipfile.ZipFile(io.BytesIO(saved)) as z:
        snapshot = z.read("taxhelper.db")
    path = db.data_dir() / "old.db"
    path.write_bytes(snapshot)
    old = sqlite3.connect(path)
    old.execute("PRAGMA user_version = 3")
    old.execute("DELETE FROM categories WHERE code = 'office_rent'")
    old.commit()
    old.close()
    members = {"taxhelper.db": path.read_bytes()}

    restore(client, zip_of(members))

    con = db.connect()
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute("SELECT COUNT(*) FROM categories WHERE code = 'office_rent'").fetchone()[0] == 1
    assert [r[0] for r in con.execute("SELECT number FROM entries ORDER BY id")] == ["A-1", "A-2"]
    con.close()


def good_database(client):
    return zipfile.ZipFile(io.BytesIO(client.get("/backup").data)).read("taxhelper.db")


def newer_database(client):
    path = db.data_dir() / "newer.db"
    path.write_bytes(good_database(client))
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    return path.read_bytes()


def foreign_database(_client):
    path = db.data_dir() / "foreign.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE other (x)")
    con.commit()
    con.close()
    return path.read_bytes()


REFUSALS = {
    "not a zip": (lambda c: b"this is not a zip", "This file is not a zip archive."),
    "no database": (lambda c: zip_of({"readme.txt": "x"}), "The archive has no taxhelper.db"),
    "damaged database": (lambda c: zip_of({"taxhelper.db": b"not sqlite" * 100}), "The database in the backup is damaged."),
    "foreign database": (lambda c: zip_of({"taxhelper.db": foreign_database(c)}), "not a database of this app"),
    "newer database": (lambda c: zip_of({"taxhelper.db": newer_database(c)}), "made by a newer version"),
    "path outside documents": (lambda c: zip_of({"taxhelper.db": good_database(c), "../evil.txt": "x"}),
                               "unexpected file: ../evil.txt"),
    "nested path": (lambda c: zip_of({"taxhelper.db": good_database(c), "documents/../../evil.txt": "x"}),
                    "unexpected file"),
    "drive path": (lambda c: zip_of({"taxhelper.db": good_database(c), "documents/C:evil.txt": "x"}),
                   "unexpected file"),
}


@pytest.mark.parametrize("case", REFUSALS)
def test_an_unusable_backup_is_refused_and_nothing_changes(client, saved, case):
    build, message = REFUSALS[case]
    archive = build(client)
    before_numbers, before_documents = numbers(), document_names()

    page = text(restore(client, archive))

    assert message in page
    assert numbers() == before_numbers and document_names() == before_documents
    assert not (db.data_dir() / bk.SAFETY_DIR).exists()
    assert not list(db.data_dir().glob("restore-*"))  # no staging folder is left behind
    assert not (db.data_dir().parent / "evil.txt").exists()


def test_nothing_chosen_and_an_unknown_name_change_nothing(client, saved):
    assert "Choose a backup file" in text(restore(client, None))
    set_settings(backup_dir=str(db.data_dir() / "no-backups-here"))
    page = text(client.post("/backup/restore", data={"name": "../../taxhelper.db"}, follow_redirects=True))
    assert "There is no backup" in page
    assert numbers() == ["A-1", "A-2"]


def test_a_failure_while_replacing_the_database_leaves_the_old_data(client, saved, monkeypatch):
    changed_since_backup(client)
    before_numbers = numbers()
    before_documents = document_names()

    def fail(con, snapshot):
        raise bk.RestoreError("restore.failed", error="disk full")

    monkeypatch.setattr(bk, "_replace_database", fail)
    page = text(restore(client, saved))

    assert "the data is unchanged: disk full" in page
    assert numbers() == before_numbers
    assert set(before_documents) <= set(document_names())  # nothing of the current data was deleted


def test_a_restore_waits_for_a_running_backup(client, saved):
    assert bk._RUNNING.acquire(blocking=False)
    try:
        page = text(restore(client, saved))
    finally:
        bk._RUNNING.release()
    assert "A backup is being written right now" in page
    assert numbers() == ["A-1", "A-2"]


def test_the_page_offers_restore_and_asks_for_confirmation(client):
    page = text(client.get("/settings"))
    assert 'action="/backup/restore"' in page and 'type="file" name="archive"' in page
    assert "onsubmit='return confirm(" in page
