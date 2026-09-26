from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date, timedelta

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date,
                              date_input, money)
from aflatoon.models import EmiTracker

bp = Blueprint("emi", __name__, url_prefix="/emi")


@bp.route("/")
@login_required
def index():
    emis = EmiTracker.query.order_by(EmiTracker.next_due_date).all()
    return render_template(
        "table.html",
        title="EMI Tracker",
        new_url=url_for("emi.create"),
        headers=["Loan ID", "Name", "Lender", "EMI", "Due Day",
                 "Next Due Date", "Outstanding", "Remaining", "Alert"],
        rows=[{
            "id": e.id,
            "cells": [e.loan_id, e.loan_name or "-", e.lender or "-",
                      money(e.emi_amount), e.due_day or "-",
                      e.next_due_date.strftime("%d %b %Y") if e.next_due_date else "-",
                      money(e.outstanding_principal), e.remaining_emis or "-",
                      e.alert],
        } for e in emis],
        actions=[{"label": "Edit", "endpoint": "emi.edit", "arg": "emi_id"},
                 {"label": "Mark Paid", "endpoint": "emi.mark_paid", "arg": "emi_id",
                  "confirm": "Mark this EMI as paid?"},
                 {"label": "Delete", "endpoint": "emi.delete", "arg": "emi_id", "delete": True}],
        total=len(emis),
    )


def _fields():
    return [
        {"name": "loan_name", "label": "Loan Name", "type": "text", "required": True},
        {"name": "lender", "label": "Lender", "type": "text"},
        {"name": "emi_amount", "label": "EMI Amount (₹)", "type": "number",
         "step": "0.01", "required": True},
        {"name": "due_day", "label": "Due Day (date of month)", "type": "number", "step": "1"},
        {"name": "next_due_date", "label": "Next Due Date", "type": "date"},
        {"name": "outstanding_principal", "label": "Outstanding Principal (₹)",
         "type": "number", "step": "0.01"},
        {"name": "remaining_emis", "label": "Remaining EMIs", "type": "number", "step": "1"},
        {"name": "is_paid", "label": "Paid?", "type": "select", "choices": ["No", "Yes"]},
        {"name": "paid_date", "label": "Paid Date", "type": "date"},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(emi, form):
    emi.loan_name = form.get("loan_name")
    emi.lender = form.get("lender")
    emi.emi_amount = to_float(form.get("emi_amount"))
    emi.due_day = int(to_float(form.get("due_day"))) or None
    emi.next_due_date = to_date(form.get("next_due_date"))
    emi.outstanding_principal = to_float(form.get("outstanding_principal"))
    emi.remaining_emis = int(to_float(form.get("remaining_emis"))) or None
    emi.is_paid = (form.get("is_paid") == "Yes")
    emi.paid_date = to_date(form.get("paid_date"))
    emi.notes = form.get("notes")
    return emi


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        emi = EmiTracker()
        emi.loan_id = next_id(db.session.query(EmiTracker.loan_id), "LOAN", 2)
        try:
            _resolve(emi, request.form)
            db.session.add(emi)
            db.session.commit()
            flash("EMI record added.", "success")
            return redirect(url_for("emi.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    return render_template("form.html", form_title="Add EMI / Loan",
                           fields=_fields(), values={"is_paid": "No"},
                           cancel_url=url_for("emi.index"))


@bp.route("/<int:emi_id>/edit", methods=["GET", "POST"])
@login_required
def edit(emi_id):
    emi = EmiTracker.query.get_or_404(emi_id)
    if request.method == "POST":
        try:
            _resolve(emi, request.form)
            db.session.commit()
            flash("EMI record updated.", "success")
            return redirect(url_for("emi.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "loan_name": emi.loan_name or "",
        "lender": emi.lender or "",
        "emi_amount": emi.emi_amount or "",
        "due_day": emi.due_day or "",
        "next_due_date": date_input(emi.next_due_date),
        "outstanding_principal": emi.outstanding_principal or "",
        "remaining_emis": emi.remaining_emis or "",
        "is_paid": "Yes" if emi.is_paid else "No",
        "paid_date": date_input(emi.paid_date),
        "notes": emi.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {emi.loan_id}",
                           fields=_fields(), values=values,
                           cancel_url=url_for("emi.index"))


@bp.route("/<int:emi_id>/mark-paid", methods=["POST"])
@login_required
def mark_paid(emi_id):
    emi = EmiTracker.query.get_or_404(emi_id)
    emi.is_paid = True
    emi.paid_date = emi.paid_date or _date.today()
    if emi.remaining_emis is not None and emi.remaining_emis > 0:
        emi.remaining_emis -= 1
    if emi.next_due_date:
        emi.next_due_date = emi.next_due_date + timedelta(days=30)
    db.session.commit()
    flash(f"{emi.loan_id} marked as paid.", "success")
    return redirect(url_for("emi.index"))


@bp.route("/<int:emi_id>/delete", methods=["POST"])
@login_required
def delete(emi_id):
    emi = EmiTracker.query.get_or_404(emi_id)
    db.session.delete(emi)
    db.session.commit()
    flash("EMI record deleted.", "info")
    return redirect(url_for("emi.index"))