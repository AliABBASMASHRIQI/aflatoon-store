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
        actions=[{"label": "Edit", "endpoint": "suppliers.edit"},
                 {"label": "Delete", "endpoint": "suppliers.delete", "delete": True}],
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
    db.session.delete(s)
    db.session.commit()
    flash(f"Supplier {name} deleted.", "info")
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