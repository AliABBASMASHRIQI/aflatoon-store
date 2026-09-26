from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date,
                              date_input, money)
from aflatoon.models import CashTxn, TXN_TYPES
from aflatoon.services import cash_ledger_rows

bp = Blueprint("cash", __name__, url_prefix="/cash")


@bp.route("/")
@login_required
def index():
    led = cash_ledger_rows()
    rows = [{
        "id": r["id"],
        "cells": [r["date"].strftime("%d %b %Y"), r["txn_id"], r["type"],
                  r["description"] or "-",
                  money(r["cash_in"]) if r["cash_in"] else "-",
                  money(r["cash_out"]) if r["cash_out"] else "-",
                  money(r["bank_in"]) if r["bank_in"] else "-",
                  money(r["bank_out"]) if r["bank_out"] else "-",
                  money(r["running_cash"]), money(r["running_bank"]),
                  money(r["running_total"])],
    } for r in led["rows"]]
    last = led["rows"][-1] if led["rows"] else None
    return render_template(
        "cash.html",
        rows=rows,
        starting_cash=led["starting_cash"],
        starting_bank=led["starting_bank"],
        closing_cash=last["running_cash"] if last else led["starting_cash"],
        closing_bank=last["running_bank"] if last else led["starting_bank"],
        closing_total=(last["running_total"] if last
                       else led["starting_cash"] + led["starting_bank"]),
        new_url=url_for("cash.create"),
    )


def _field_spec():
    return [
        {"name": "date", "label": "Date", "type": "date", "required": True},
        {"name": "txn_type", "label": "Transaction Type", "type": "select", "choices": TXN_TYPES, "default": "Expense"},
        {"name": "description", "label": "Description", "type": "text"},
        {"name": "cash_in", "label": "Cash In (₹)", "type": "number", "step": "0.01"},
        {"name": "cash_out", "label": "Cash Out (₹)", "type": "number", "step": "0.01"},
        {"name": "bank_upi_in", "label": "Bank/UPI In (₹)", "type": "number", "step": "0.01"},
        {"name": "bank_upi_out", "label": "Bank/UPI Out (₹)", "type": "number", "step": "0.01"},
        {"name": "reference", "label": "Reference", "type": "text"},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(txn, form):
    txn.date = to_date(form.get("date")) or _date.today()
    txn.txn_type = form.get("txn_type") or "Expense"
    txn.description = form.get("description")
    txn.cash_in = to_float(form.get("cash_in"))
    txn.cash_out = to_float(form.get("cash_out"))
    txn.bank_upi_in = to_float(form.get("bank_upi_in"))
    txn.bank_upi_out = to_float(form.get("bank_upi_out"))
    txn.reference = form.get("reference")
    txn.notes = form.get("notes")
    return txn


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        txn = CashTxn()
        txn.txn_id = next_id(db.session.query(CashTxn.txn_id), "TXN", 5)
        try:
            _resolve(txn, request.form)
            db.session.add(txn)
            db.session.commit()
            flash("Transaction recorded.", "success")
            return redirect(url_for("cash.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"date": _date.today().isoformat()}
    return render_template("form.html", form_title="Add Cash / Bank Transaction",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("cash.index"))


@bp.route("/<int:txn_id>/edit", methods=["GET", "POST"])
@login_required
def edit(txn_id):
    txn = CashTxn.query.get_or_404(txn_id)
    if request.method == "POST":
        try:
            _resolve(txn, request.form)
            db.session.commit()
            flash("Transaction updated.", "success")
            return redirect(url_for("cash.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "date": date_input(txn.date),
        "txn_type": txn.txn_type,
        "description": txn.description or "",
        "cash_in": txn.cash_in or "",
        "cash_out": txn.cash_out or "",
        "bank_upi_in": txn.bank_upi_in or "",
        "bank_upi_out": txn.bank_upi_out or "",
        "reference": txn.reference or "",
        "notes": txn.notes or "",
    }
    return render_template("form.html", form_title="Edit Transaction",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("cash.index"))


@bp.route("/<int:txn_id>/delete", methods=["POST"])
@login_required
def delete(txn_id):
    txn = CashTxn.query.get_or_404(txn_id)
    db.session.delete(txn)
    db.session.commit()
    flash("Transaction deleted.", "info")
    return redirect(url_for("cash.index"))