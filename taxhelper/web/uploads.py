"""Storing and removing attached documents (invoices, justificantes)."""
from __future__ import annotations

import uuid

from werkzeug.utils import secure_filename

from .. import db
from ..i18n import UserError

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "webp", "gif", "txt", "csv"}


def save_upload(file_storage) -> str:
    """Store an uploaded document; returns the stored file name, or '' when nothing was uploaded."""
    if not file_storage or not file_storage.filename:
        return ""
    # The extension comes from the original name: secure_filename() drops non-ASCII letters,
    # so «счёт.pdf» would otherwise lose its extension and be rejected.
    stem, dot, ext = file_storage.filename.rpartition(".")
    ext = ext.lower() if dot else ""
    if ext not in ALLOWED_EXT:
        raise UserError("err.file_type", ext=ext, allowed=", ".join(sorted(ALLOWED_EXT)))
    stored = f"{uuid.uuid4().hex[:8]}_{secure_filename(stem) or 'document'}.{ext}"
    file_storage.save(db.documents_dir() / stored)
    return stored


def delete_document(name: str) -> None:
    """Remove a stored document (only files inside the documents folder)."""
    if not name:
        return
    path = db.documents_dir() / name
    if path.is_file() and path.resolve().parent == db.documents_dir().resolve():
        path.unlink()
