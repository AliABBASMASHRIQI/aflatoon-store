from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date,
                              date_input, money)
from aflatoon.models import Expense, EXPENSE_CATEGORIES, EXPENSE_NATURES, PAYMENT_METHODS
from aflatoon.services import post_cash, unpost_cash

bp = Blueprint("expenses", __name__, url_prefix="/expenses")


@bp.route("/")
@login_required
def index():
    month = request.args.get("month", "")
    q = db.session.query(Expense)
    if month:
        from aflatoon.helpers import first_of_month
        start = first_of_month(to_date(month) or _date.today())
        from aflatoon.helpers import next_month
        q = q.filter(Expense.expense_date >= start,
                     Expense.expense_date < next_month(start))
    expenses = q.order_by(Expense.id.desc()).limit(500).all()
    total = float(sum((e.amount or 0) for e in expenses))
    return render_template(
        "table.html",
        title="Expense Entry",
        new_url=url_for("expenses.create"),
        headers=["Date", "ID", "Category", "Nature", "Description",
                 "Amount", "Payment", "Paid?"],
        rows=[{
            "id": e.id,
            "cells": [e.expense_date.strftime("%d %b %Y"), e.expense_id,
                      e.category or "-", e.nature or "-",
                      e.description or "-", money(e.amount),
                      e.payment_method, "Yes" if e.is_paid else "No"],
        } for e in expenses],
        actions=[{"label": "Edit", "endpoint": "expenses.edit", "arg": "exp_id"},
                 {"label": "Delete", "endpoint": "expenses.delete", "arg": "exp_id",
                  "delete": True,
                  "confirm": "Delete this expense? The cash balance goes back "
                             "up by the same amount."}],
        total=len(expenses),
        extra_total=total,
    )


def _field_spec():
    return [
        {"name": "expense_date", "label": "Expense Date", "type": "date", "required": True},
        {"name": "category", "label": "Category", "type": "select",
         "choices": EXPENSE_CATEGORIES},
        {"name": "nature", "label": "Nature", "type": "select", "choices": EXPENSE_NATURES, "default": "Variable"},
        {"name": "description", "label": "Description", "type": "textarea"},
        {"name": "amount", "label": "Amount (₹)", "type": "number", "step": "0.01", "required": True},
        {"name": "payment_method", "label": "Payment Method", "type": "select",
         "choices": PAYMENT_METHODS},
        {"name": "is_paid", "label": "Paid?", "type": "select", "choices": ["Yes", "No"]},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(exp, form):
    exp.expense_date = to_date(form.get("expense_date")) or _date.today()
    exp.category = form.get("category")
    exp.nature = form.get("nature") or "Variable"
    exp.description = form.get("description")
    exp.amount = to_float(form.get("amount"))
    exp.payment_method = form.get("payment_method") or "Cash"
    exp.is_paid = (form.get("is_paid") == "Yes")
    exp.notes = form.get("notes")
    return exp


def _sync_cash(exp):
    """Paid expenses leave the till; unpaid ones have not left yet.

    An expense left as Paid? = No is money still owed, so it posts nothing.
    Ticking Paid later posts the cash line then.
    """
    unpost_cash("expense", exp.id)
    if not exp.is_paid:
        return
    label = exp.category or "expense"
    detail = (exp.description or "").strip()
    post_cash("expense", exp.id, exp.expense_date, "Expense",
              f"{label} - {detail}".strip(" -") if detail else label,
              exp.amount, exp.payment_method, "out")


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        exp = Expense()
        exp.expense_id = next_id(db.session.query(Expense.expense_id), "EXP", 5)
        try:
            _resolve(exp, request.form)
            db.session.add(exp)
            db.session.flush()          # exp.id needed to link the cash line
            _sync_cash(exp)
            db.session.commit()
            flash("Expense recorded.", "success")
            return redirect(url_for("expenses.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"expense_date": _date.today().isoformat(), "is_paid": "Yes"}
    return render_template("form.html", form_title="Add Expense",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("expenses.index"))


@bp.route("/<int:exp_id>/edit", methods=["GET", "POST"])
@login_required
def edit(exp_id):
    exp = Expense.query.get_or_404(exp_id)
    if request.method == "POST":
        try:
            _resolve(exp, request.form)
            _sync_cash(exp)
            db.session.commit()
            flash("Expense updated.", "success")
            return redirect(url_for("expenses.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "expense_date": date_input(exp.expense_date),
        "category": exp.category,
        "nature": exp.nature,
        "description": exp.description or "",
        "amount": exp.amount or "",
        "payment_method": exp.payment_method,
        "is_paid": "Yes" if exp.is_paid else "No",
        "notes": exp.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {exp.expense_id}",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("expenses.index"))


@bp.route("/<int:exp_id>/delete", methods=["POST"])
@login_required
def delete(exp_id):
    exp = Expense.query.get_or_404(exp_id)
    unpost_cash("expense", exp.id)
    db.session.delete(exp)
    db.session.commit()
    flash("Expense deleted.", "info")
    return redirect(url_for("expenses.index"))