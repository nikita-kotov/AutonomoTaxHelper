"""Documents, backups and the language switch."""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_file,
                   send_from_directory, url_for)

from ... import backup as bk
from ... import db, i18n, launcher
from ...i18n import t
from ..helpers import safe_next
from ..infra import svc

bp = Blueprint("system", __name__)


INLINE_TYPES = {"pdf", "png", "jpg", "jpeg", "webp", "gif"}  # shown in the browser; everything else downloads


@bp.get("/documents/<path:name>")
def document(name):
    """An attached file. Only PDFs and images open in the browser: other types download, so a file
    from an untrusted counterparty is never rendered on the app's own origin."""
    inline = name.rsplit(".", 1)[-1].lower() in INLINE_TYPES
    response = send_from_directory(db.documents_dir(), name, as_attachment=not inline)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/quit")
def quit_app():
    """Stop the application (the Exit button). Only available when it was started by the launcher; a request
    that a browser marks as coming from another site is refused, so a web page cannot switch the app off."""
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)
    if not launcher.request_stop(current_app):
        abort(404)
    return render_template("quit.html")


@bp.post("/lang")
def switch_language():
    """Persist the interface language ('' = follow the browser) and go back."""
    code = request.form.get("lang", "")
    if code in i18n.available() or code == "":
        svc().settings.update(language=code)
    return redirect(safe_next(request.form.get("next", ""), url_for("dashboard.index")))


@bp.get("/backup")
def backup():
    """Download a zip with a consistent database snapshot and all attached documents."""
    buf = io.BytesIO()
    bk.build_backup_zip(svc().con, buf)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=bk.backup_name(datetime.now()))


@bp.post("/backup/now")
def backup_now():
    """Run the folder backup immediately, ignoring the minimum interval."""
    status, message = bk.run_backup(svc().con, force=True)
    flash(message, "ok" if status == "ok" else "error")
    return redirect(url_for("settings.page"))


RESTORE_MAX_UPLOAD = 4 * 1024 ** 3  # a backup zip holds every attached document, so it can be far larger than a form post


@bp.post("/backup/restore")
def restore():
    """Replace the data with a backup: an uploaded zip, or one of the zips in the backup folder."""
    request.max_content_length = RESTORE_MAX_UPLOAD
    upload = request.files.get("archive")
    chosen = request.form.get("name", "")
    s = svc()
    try:
        if upload and upload.filename:
            source, label = upload.stream, upload.filename
        elif chosen:
            folder = Path(s.settings.load().backup_dir.strip())
            path = next((p for p in bk.list_backups(folder) if p.name == chosen), None)
            if path is None:
                raise bk.RestoreError("restore.not_in_folder", name=chosen)
            source, label = path, path.name
        else:
            raise bk.RestoreError("restore.nothing_chosen")
        saved = bk.restore_backup(s.con, source)
    except bk.RestoreError as exc:
        flash(exc.message(), "error")
    else:
        flash(t("restore.done", source=label, saved=saved), "ok")
    return redirect(url_for("settings.page"))
