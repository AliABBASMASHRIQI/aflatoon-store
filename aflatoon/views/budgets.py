from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, to_float, to_date, date_input,
                              money, first_of_month, end_of_month)
from aflatoon.models import MonthlyBudget, BUDGET_CATEGORY_FIELDS
from aflatoon.services import budget_data, budget_category_block

bp = Blueprint("budgets", __name__, url_prefix="/budgets")


@bp.route("/")
@login_required
def index():
    from dateutil.relativedelta import relativedelta

    today = _date.today()
    view_month = to_date(request.args.get("month")) if request.args.get("month") else today
    view_month = first_of_month(view_month)
    block = budget_category_block(view_month)
    raw = budget_data()

    headers = ["Month"] + [label for _f, label in BUDGET_CATEGORY_FIELDS] + \
              ["Total", "Actual", "Remaining", "Status", ""]
    rows = []
    for r in raw:
        cells = [r["month"].strftime("%b %Y")]
        cells += [money(v) if v else "-" for v in r["budgets"]]
        cells += [money(r["total"]), money(r["actual"]),
                  money(r["remaining"]), r["status"], ""]
        rows.append({"id": r["id"], "cells": cells,
                     "status": r["status"], "month": r["month"]})

    return render_template(
        "budget.html",
        rows=rows,
        block=block,
        headers=headers,
        cat_fields=BUDGET_CATEGORY_FIELDS,
        view_month=view_month,
        today=today,
        prev_month=first_of_month(view_month - relativedelta(months=1)),
        next_month=first_of_month(view_month + relativedelta(months=1)),
        money=money,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        month = to_date(request.form.get("month"))
        if not month:
            flash("Choose a month.", "danger")
        else:
            month = first_of_month(month)
            if MonthlyBudget.query.filter_by(month=month).first():
                flash("A budget already exists for that month.", "warning")
            else:
                b = MonthlyBudget(month=month)
                _resolve(b, request.form)
                try:
                    db.session.add(b)
                    db.session.commit()
                    flash(f"Budget for {month.strftime('%b %Y')} created.", "success")
                    return redirect(url_for("budgets.index"))
                except Exception as e:
                    db.session.rollback()
                    flash(f"Error: {e}", "danger")
    values = {"month": first_of_month(_date.today()).isoformat()}
    return render_template("form.html", form_title="Add Monthly Budget",
                           fields=budget_field_spec(), values=values,
                           cancel_url=url_for("budgets.index"))


@bp.route("/<int:bid>/edit", methods=["GET", "POST"])
@login_required
def edit(bid):
    b = MonthlyBudget.query.get_or_404(bid)
    if request.method == "POST":
        try:
            _resolve(b, request.form)
            db.session.commit()
            flash("Budget updated.", "success")
            return redirect(url_for("budgets.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"month": date_input(b.month)}
    for fname, _label in BUDGET_CATEGORY_FIELDS:
        values[fname] = getattr(b, fname) or ""
    return render_template("form.html",
                           form_title=f"Edit Budget {b.month.strftime('%b %Y')}",
                           fields=budget_field_spec(), values=values,
                           cancel_url=url_for("budgets.index"))


@bp.route("/<int:bid>/delete", methods=["POST"])
@login_required
def delete(bid):
    b = MonthlyBudget.query.get_or_404(bid)
    db.session.delete(b)
    db.session.commit()
    flash("Budget deleted.", "info")
    return redirect(url_for("budgets.index"))


def budget_field_spec():
    fields = [{"name": "month", "label": "Month", "type": "date", "required": True}]
    for fname, label in BUDGET_CATEGORY_FIELDS:
        fields.append({"name": fname, "label": f"{label} (₹)",
                       "type": "number", "step": "0.01"})
    return fields


def _resolve(b, form):
    for fname, _label in BUDGET_CATEGORY_FIELDS:
        setattr(b, fname, to_float(form.get(fname)))
    return b