"""Clients and suppliers: the data that new entries start from."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ...i18n import t
from ..forms import parse_contact
from ..infra import svc

bp = Blueprint("contacts", __name__)


def _kind_arg(value):
    return value if value in ("income", "expense") else None


def _form_page(e, kind, contact_id=None):
    s = svc()
    return render_template("contact_form.html", e=e, kind=kind, contact_id=contact_id, cats=s.categories.all())


def _save(kind, row=None):
    """Validate and store the posted contact; returns the redirect on success, None after flashing errors."""
    s = svc()
    form = request.form.copy()
    form["kind"] = kind  # the kind is fixed by the page, not by the form
    result = parse_contact(form)
    category = result.values["category_id"]
    if category is not None and s.categories.get(category) is None:
        result.errors.append(t("err.category"))
    if not result.errors:
        try:
            if row is None:
                s.contacts.add(result.values)
                flash(t("contacts.added"), "ok")
            else:
                s.contacts.update(row["id"], result.values)
                flash(t("contacts.updated"), "ok")
            return redirect(url_for("contacts.index", kind=kind))
        except sqlite3.IntegrityError:
            result.errors.append(t("contacts.err_exists"))
    for message in result.errors:
        flash(message, "error")
    return None


@bp.get("/contacts")
def index():
    s = svc()
    kind = _kind_arg(request.args.get("kind"))
    return render_template("contacts.html", rows=s.contacts.all(kind), usage=s.contacts.usage(), kind=kind,
                           cats={c["id"]: c for c in s.categories.all()})


@bp.route("/contacts/new/<kind>", methods=["GET", "POST"])
def new(kind):
    if _kind_arg(kind) is None:
        abort(404)
    if request.method == "POST":
        done = _save(kind)
        if done:
            return done
        return _form_page(request.form, kind)
    return _form_page({"country": "ES"}, kind)


@bp.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
def edit(contact_id):
    row = svc().contacts.get(contact_id)
    if row is None:
        abort(404)
    if request.method == "POST":
        done = _save(row["kind"], row)
        if done:
            return done
        return _form_page(request.form, row["kind"], contact_id)
    return _form_page(dict(row), row["kind"], contact_id)


@bp.post("/contacts/<int:contact_id>/delete")
def delete(contact_id):
    row = svc().contacts.get(contact_id)
    if row is None:
        abort(404)
    svc().contacts.delete(contact_id)
    flash(t("contacts.deleted"), "ok")
    return redirect(url_for("contacts.index", kind=row["kind"]))


@bp.post("/contacts/from-entries")
def from_entries():
    """Add a contact for every name already used in the entries."""
    added = svc().contacts.create_from_ledger()
    flash(t("contacts.from_entries_done", n=added), "ok")
    return redirect(url_for("contacts.index"))
