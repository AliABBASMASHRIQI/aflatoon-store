from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date,
                              date_input, money)
from aflatoon.models import PurchaseBatch, Supplier, SUPPLIER_TYPES, PAYMENT_METHODS
from aflatoon.services import post_cash, unpost_cash

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
        actions=[{"label": "Edit", "endpoint": "batches.edit", "arg": "batch_id"},
                 {"label": "Pay", "endpoint": "batches.pay", "arg": "batch_id",
                  "confirm": "Record a payment against this lot?"},
                 {"label": "Delete", "endpoint": "batches.delete", "arg": "batch_id", "delete": True}],
        total=len(batches),
        empty_message=("No purchase lots yet. Use \u201cAdd New\u201d each time you "
                       "buy stock, then bulk-add the pieces from it."),
    )


def _fields(include_paid=True):
    suppliers = db.session.query(Supplier.name).order_by(Supplier.name).all()
    fields = [
        {"name": "purchase_date", "label": "Purchase Date", "type": "date", "required": True},
        {"name": "supplier_sel", "label": "Supplier", "type": "select",
         "choices": [(s[0], s[0]) for s in suppliers], "optional": True},
        {"name": "supplier_type", "label": "Supplier Type", "type": "select",
         "choices": SUPPLIER_TYPES},
        {"name": "invoice_ref", "label": "Invoice / Reference", "type": "text"},
        {"name": "qty_purchased", "label": "Quantity Purchased", "type": "number", "step": "1"},
        {"name": "total_cost", "label": "Total Purchase Cost (₹,1)", "type": "number", "step": "0.01"},
    ]
    if include_paid:
        # Paying a lot is recorded here on the day it happens. Later
        # instalments go through the Pay button so each payment keeps its
        # own date, which is what the cash ledger needs.
        fields += [
            {"name": "paid_amount", "label": "Paid Amount (₹,1)", "type": "number", "step": "0.01"},
            {"name": "payment_method", "label": "Paid Using", "type": "select",
             "choices": ["Cash", "UPI", "Card", "Credit/Outstanding"],
             "default": "Cash"},
        ]
    fields.append({"name": "notes", "label": "Notes", "type": "textarea"})
    return fields


def _resolve(batch, form, include_paid=True):
    s = form.get("supplier_sel")
    if s:
        sup = Supplier.query.filter_by(name=s).first()
        batch.supplier_id = sup.id if sup else None
    batch.supplier_type = form.get("supplier_type") or "Other Vendor"
    batch.invoice_ref = form.get("invoice_ref")
    batch.qty_purchased = int(to_float(form.get("qty_purchased")))
    batch.total_cost = to_float(form.get("total_cost"))
    if include_paid:
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
            db.session.flush()          # batch.id needed to link the cash line
            method = request.form.get("payment_method") or "Cash"
            post_cash("batch", batch.id, batch.purchase_date, "Purchase",
                      f"{batch.batch_id} "
                      f"{batch.supplier.name if batch.supplier else ''}".strip(),
                      batch.paid_amount, method, "out",
                      reference=batch.invoice_ref)
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
            # paid_amount is not editable here on purpose: changing it
            # silently would rewrite history in the cash ledger without a
            # date. Use the Pay button, or correct the ledger line itself.
            _resolve(batch, request.form, include_paid=False)
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
        "notes": batch.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {batch.batch_id}",
                           form_subtitle=f"Paid so far {money(batch.paid_amount)} of "
                                    f"{money(batch.total_cost)}. To record a "
                                    f"payment use the Pay button on the list.",
                           fields=_fields(include_paid=False), values=values,
                           cancel_url=url_for("batches.index"))


def _pay_fields():
    return [
        {"name": "pay_date", "label": "Payment Date", "type": "date", "required": True},
        {"name": "pay_amount", "label": "Amount Paid (₹,1)", "type": "number",
         "step": "0.01", "required": True},
        {"name": "payment_method", "label": "Paid Using", "type": "select",
         "choices": ["Cash", "UPI", "Card"], "default": "UPI"},
    ]


@bp.route("/<int:batch_id>/pay", methods=["GET", "POST"])
@login_required
def pay(batch_id):
    batch = PurchaseBatch.query.get_or_404(batch_id)
    if request.method == "POST":
        amount = to_float(request.form.get("pay_amount"))
        if amount <= 0:
            flash("Enter an amount greater than zero.", "danger")
        else:
            pay_date = to_date(request.form.get("pay_date")) or _date.today()
            method = request.form.get("payment_method") or "UPI"
            batch.paid_amount = float(batch.paid_amount or 0) + amount
            # Each payment is its own dated ledger line, so the running
            # balance is right on the day the money actually moved.
            post_cash("batch", batch.id, pay_date, "Purchase",
                      f"{batch.batch_id} payment"
                      f"{' - ' + batch.supplier.name if batch.supplier else ''}",
                      amount, method, "out", reference=batch.invoice_ref)
            db.session.commit()
            flash(f"Payment of {money(amount)} recorded. "
                  f"Outstanding now {money(batch.outstanding)}.", "success")
            return redirect(url_for("batches.index"))
    return render_template(
        "form.html",
        form_title=f"Pay {batch.batch_id}",
        form_subtitle=f"Total {money(batch.total_cost)} · paid {money(batch.paid_amount)}"
                 f" · outstanding {money(batch.outstanding)}",
        fields=_pay_fields(),
        values={"pay_date": _date.today().isoformat()},
        cancel_url=url_for("batches.index"))


@bp.route("/<int:batch_id>/delete", methods=["POST"])
@login_required
def delete(batch_id):
    batch = PurchaseBatch.query.get_or_404(batch_id)
    unpost_cash("batch", batch.id)     # drops the lot payment + any instalments
    db.session.delete(batch)
    db.session.commit()
    flash(f"Batch {batch.batch_id} deleted.", "info")
    return redirect(url_for("batches.index"))