from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date,
                              date_input, money)
from aflatoon.models import PurchaseBatch, Supplier, SUPPLIER_TYPES

bp = Blueprint("batches", __name__, url_prefix="/batches")


@bp.route("/")
@login_required
def index():
    batches = PurchaseBatch.query.order_by(PurchaseBatch.id.desc()).all()
    return render_template(
        "table.html",
        title="Purchase Batches",
        new_url=url_for("batches.create"),
        headers=["Batch ID", "Date", "Supplier", "Qty", "Total Cost",
                 "Paid", "Outstanding", "Processed"],
        rows=[{
            "id": b.id,
            "cells": [b.batch_id, b.purchase_date.strftime("%d %b %Y"),
                      b.supplier.name if b.supplier else b.supplier_type or "-",
                      b.qty_purchased, money(b.total_cost), money(b.paid_amount),
                      money(b.outstanding), f"{b.processed_qty}/{b.qty_purchased}"],
        } for b in batches],
        actions=[{"label": "Edit", "endpoint": "batches.edit"},
                 {"label": "Delete", "endpoint": "batches.delete", "delete": True}],
        total=len(batches),
    )


def _fields():
    suppliers = db.session.query(Supplier.name).order_by(Supplier.name).all()
    return [
        {"name": "purchase_date", "label": "Purchase Date", "type": "date", "required": True},
        {"name": "supplier_sel", "label": "Supplier", "type": "select",
         "choices": [(s[0], s[0]) for s in suppliers], "optional": True},
        {"name": "supplier_type", "label": "Supplier Type", "type": "select",
         "choices": SUPPLIER_TYPES},
        {"name": "invoice_ref", "label": "Invoice / Reference", "type": "text"},
        {"name": "qty_purchased", "label": "Quantity Purchased", "type": "number", "step": "1"},
        {"name": "total_cost", "label": "Total Purchase Cost (₹)", "type": "number", "step": "0.01"},
        {"name": "paid_amount", "label": "Paid Amount (₹)", "type": "number", "step": "0.01"},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(batch, form):
    s = form.get("supplier_sel")
    if s:
        sup = Supplier.query.filter_by(name=s).first()
        batch.supplier_id = sup.id if sup else None
    batch.supplier_type = form.get("supplier_type") or "Other Vendor"
    batch.invoice_ref = form.get("invoice_ref")
    batch.qty_purchased = int(to_float(form.get("qty_purchased")))
    batch.total_cost = to_float(form.get("total_cost"))
    batch.paid_amount = to_float(form.get("paid_amount"))
    batch.notes = form.get("notes")
    return batch


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        batch = PurchaseBatch()
        batch.purchase_date = to_date(request.form.get("purchase_date")) or _date.today()
        batch.batch_id = next_id(db.session.query(PurchaseBatch.batch_id), "PUR", 5)
        try:
            _resolve(batch, request.form)
            db.session.add(batch)
            db.session.commit()
            flash(f"Batch {batch.batch_id} created.", "success")
            return redirect(url_for("batches.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving batch: {e}", "danger")
    return render_template("form.html", form_title="Add Purchase Batch",
                           fields=_fields(), values={},
                           cancel_url=url_for("batches.index"))


@bp.route("/<int:batch_id>/edit", methods=["GET", "POST"])
@login_required
def edit(batch_id):
    batch = PurchaseBatch.query.get_or_404(batch_id)
    if request.method == "POST":
        try:
            _resolve(batch, request.form)
            db.session.commit()
            flash(f"Batch {batch.batch_id} updated.", "success")
            return redirect(url_for("batches.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving batch: {e}", "danger")
    values = {
        "purchase_date": date_input(batch.purchase_date),
        "supplier_sel": batch.supplier.name if batch.supplier else "",
        "supplier_type": batch.supplier_type,
        "invoice_ref": batch.invoice_ref or "",
        "qty_purchased": batch.qty_purchased or "",
        "total_cost": batch.total_cost or "",
        "paid_amount": batch.paid_amount or "",
        "notes": batch.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {batch.batch_id}",
                           fields=_fields(), values=values,
                           cancel_url=url_for("batches.index"))


@bp.route("/<int:batch_id>/delete", methods=["POST"])
@login_required
def delete(batch_id):
    batch = PurchaseBatch.query.get_or_404(batch_id)
    db.session.delete(batch)
    db.session.commit()
    flash(f"Batch {batch.batch_id} deleted.", "info")
    return redirect(url_for("batches.index"))