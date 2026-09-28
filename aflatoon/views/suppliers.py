from flask import Blueprint, render_template, request, redirect, url_for, flash

from aflatoon.extensions import db
from aflatoon.helpers import login_required, money
from aflatoon.models import Supplier, SUPPLIER_TYPES

bp = Blueprint("suppliers", __name__, url_prefix="/suppliers")


@bp.route("/")
@login_required
def index():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    return render_template(
        "table.html",
        title="Suppliers",
        new_url=url_for("suppliers.create"),
        headers=["Name", "Type", "Contact", "Phone", "Pay Terms",
                 "Total Purchased", "Outstanding", "Active"],
        rows=[{
            "id": s.id,
            "cells": [s.name, s.supplier_type, s.contact_name or "-", s.phone or "-",
                      s.payment_terms or "-", money(s.total_purchased),
                      money(s.outstanding), "Yes" if s.is_active else "No"],
        } for s in suppliers],
        actions=[{"label": "Edit", "endpoint": "suppliers.edit", "arg": "sid"},
                 {"label": "Delete", "endpoint": "suppliers.delete", "arg": "sid",
                  "delete": True,
                  "confirm": "Delete this supplier? Purchase lots and items "
                             "keep their costs but lose the vendor name, and "
                             "anything still owed disappears from the "
                             "outstanding list."}],
        total=len(suppliers),
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Supplier name is required.", "danger")
        elif Supplier.query.filter_by(name=name).first():
            flash("A supplier with that name already exists.", "danger")
        else:
            s = Supplier()
            _resolve(s, request.form)
            try:
                db.session.add(s)
                db.session.commit()
                flash(f"Supplier {s.name} added.", "success")
                return redirect(url_for("suppliers.index"))
            except Exception as e:
                db.session.rollback()
                flash(f"Error: {e}", "danger")
    return render_template("form.html", form_title="Add Supplier", fields=_fields(),
                           values={}, cancel_url=url_for("suppliers.index"))


@bp.route("/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def edit(sid):
    s = Supplier.query.get_or_404(sid)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            # an empty string satisfies NOT NULL, which left a nameless
            # vendor rendering as a blank row and a blank dropdown option
            flash("Supplier name cannot be blank.", "danger")
            return redirect(url_for("suppliers.edit", sid=sid))
        clash = Supplier.query.filter(Supplier.name == name,
                                      Supplier.id != s.id).first()
        if clash:
            # duplicate names made the name-based lookup ambiguous
            flash(f"A different supplier is already called {name}.", "danger")
            return redirect(url_for("suppliers.edit", sid=sid))
        _resolve(s, request.form)
        try:
            db.session.commit()
            flash(f"Supplier {s.name} updated.", "success")
            return redirect(url_for("suppliers.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "name": s.name,
        "supplier_type": s.supplier_type,
        "contact_name": s.contact_name or "",
        "phone": s.phone or "",
        "email": s.email or "",
        "address": s.address or "",
        "payment_terms": s.payment_terms or "",
        "is_active": "Yes" if s.is_active else "No",
        "notes": s.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {s.name}",
                           fields=_fields(), values=values,
                           cancel_url=url_for("suppliers.index"))


@bp.route("/<int:sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    s = Supplier.query.get_or_404(sid)
    name = s.name
    n_batches, n_items = len(s.batches), len(s.items)
    owed = float(s.outstanding or 0)
    db.session.delete(s)
    db.session.commit()
    # the FKs are nullable, so nothing crashes - but the lots lose their
    # vendor and the money owed leaves the outstanding list silently
    msg = f"Supplier {name} deleted."
    if n_batches or n_items:
        msg += (f" {n_batches} purchase lot(s) and {n_items} item(s) kept "
                f"their costs but no longer show a vendor.")
    if owed:
        msg += f" You had {money(owed)} outstanding with them - check your records."
    flash(msg, "info")
    return redirect(url_for("suppliers.index"))


def _fields():
    return [
        {"name": "name", "label": "Supplier Name", "type": "text", "required": True},
        {"name": "supplier_type", "label": "Supplier Type", "type": "select",
         "choices": SUPPLIER_TYPES},
        {"name": "contact_name", "label": "Contact Person", "type": "text"},
        {"name": "phone", "label": "Phone", "type": "text"},
        {"name": "email", "label": "Email", "type": "text"},
        {"name": "address", "label": "Address", "type": "textarea"},
        {"name": "payment_terms", "label": "Payment Terms", "type": "text",
         "placeholder": "e.g. Net 30, Cash on delivery"},
        {"name": "is_active", "label": "Active?", "type": "select",
         "choices": ["Yes", "No"]},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(s, form):
    s.name = (form.get("name") or "").strip()
    s.supplier_type = form.get("supplier_type") or "Other Vendor"
    s.contact_name = form.get("contact_name")
    s.phone = form.get("phone")
    s.email = form.get("email")
    s.address = form.get("address")
    s.payment_terms = form.get("payment_terms")
    s.is_active = (form.get("is_active") == "Yes")
    s.notes = form.get("notes")
    return s