"""Attached documents: accepted names and types, how they are served, replacing and removing them."""

import io

import pytest

from taxhelper import db
from tests.support import category_id, expense_form, text


def attach(client, filename, content):
    client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")), "document": (io.BytesIO(content), filename)},
        content_type="multipart/form-data")
    con = db.connect()
    try:
        row = con.execute("SELECT document FROM entries ORDER BY id DESC").fetchone()
        return row[0] if row else None  # None: the upload was refused and nothing was saved
    finally:
        con.close()


def test_cyrillic_file_name_is_accepted(client):
    r = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")),
        "document": (io.BytesIO(b"%PDF-1.4"), "счёт.pdf")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Entry added." in text(r)
    con = db.connect()
    name = con.execute("SELECT document FROM entries").fetchone()["document"]
    con.close()
    assert name.endswith("_document.pdf") and (db.data_dir() / "documents" / name).is_file()


@pytest.mark.parametrize("filename", ["evil.exe", "noextension", "счёт", ".pdf.exe"])
def test_bad_extensions_are_still_rejected(client, filename):
    r = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")), "document": (io.BytesIO(b"x"), filename)},
        content_type="multipart/form-data")
    assert "is not allowed" in text(r)


def test_only_pdfs_and_images_open_in_the_browser(client):
    pdf = client.get("/documents/" + attach(client, "a.pdf", b"%PDF-1.4"))
    assert pdf.status_code == 200 and "attachment" not in pdf.headers.get("Content-Disposition", "")
    png = client.get("/documents/" + attach(client, "a.PNG", b"\x89PNG"))
    assert "attachment" not in png.headers.get("Content-Disposition", "")
    note = client.get("/documents/" + attach(client, "notes.txt", b"<script>alert(1)</script>"))
    assert "attachment" in note.headers["Content-Disposition"]
    for response in (pdf, png, note):
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_markup_files_cannot_be_attached(client):
    for name in ("a.xml", "a.html", "a.svg", "a.xhtml"):
        assert attach(client, name, b"<x/>") is None  # rejected: nothing was stored


def test_saving_with_a_deleted_category_keeps_the_old_invoice(client):
    doc = attach(client, "invoice.pdf", b"%PDF-1.4")
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()[0]
    con.close()
    path = db.documents_dir() / doc
    assert path.is_file()
    r = client.post(f"/entries/{eid}/edit", data={**expense_form(category_id="9999"),
                                                  "document": (io.BytesIO(b"%PDF-new"), "new.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and "Choose an expense category." in text(r)
    assert path.is_file()  # the original invoice survived
    con = db.connect()
    assert con.execute("SELECT document FROM entries WHERE id=?", (eid,)).fetchone()[0] == doc
    con.close()
    assert sorted(p.name for p in db.documents_dir().iterdir()) == [doc]  # and no orphan upload was left behind


def test_a_replaced_document_is_removed_only_after_the_save(client):
    old = attach(client, "old.pdf", b"%PDF-old")
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()[0]
    con.close()
    client.post(f"/entries/{eid}/edit", data={**expense_form(), "document": (io.BytesIO(b"%PDF-new"), "new.pdf")},
                content_type="multipart/form-data")
    con = db.connect()
    new = con.execute("SELECT document FROM entries WHERE id=?", (eid,)).fetchone()[0]
    con.close()
    assert new != old and (db.documents_dir() / new).is_file() and not (db.documents_dir() / old).exists()
