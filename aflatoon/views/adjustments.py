from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_int, to_date,
                              date_input, to_bool)
from aflatoon.models import StockAdjustment, Item, ADJUSTMENT_TYPES

bp = Blueprint("adjustments", __name__, url_prefix="/adjustments")


@bp.route("/")
@login_required
def index():
    rows = StockAdjustment.query.order_by(StockAdjustment.id.desc()).limit(300).all()
    return render_template(
        "table.html",
        title="Stock Adjustments",
        new_url=url_for("adjustments.create"),
        headers=["Date", "ID", "Item", "Type", "Qty Change", "Reason",
                 "Approved By", "Processed?"],
        rows=[{
            "id": a.id,
            "cells": [a.adjustment_date.strftime("%d %b %Y"), a.adjustment_id,
                      a.item.item_id if a.item else "?",
                      a.adjustment_type or "-", str(a.qty_change),
                      a.reason or "-", a.approved_by or "-",
                      "Yes" if a.is_processed else "No"],
        } for a in rows],
        actions=[{"label": "Edit", "endpoint": "adjustments.edit"},
                 {"label": "Delete", "endpoint": "adjustments.delete", "delete": True}],
        total=len(rows),
    )


def _field_spec():
    items = db.session.query(Item.item_id, Item.description).order_by(Item.item_id).all()
    return [
        {"name": "adjustment_date", "label": "Adjustment Date", "type": "date", "required": True},
        {"name": "item_sel", "label": "Item (ID - Description)", "type": "select",
         "choices": [(i, f"{i} - {d}") for i, d in items]},
        {"name": "adjustment_type", "label": "Adjustment Type", "type": "select",
         "choices": ADJUSTMENT_TYPES},
        {"name": "qty_change", "label": "Qty Change (negative = reduce)", "type": "number", "step": "1"},
        {"name": "reason", "label": "Reason", "type": "textarea"},
        {"name": "approved_by", "label": "Approved By", "type": "text"},
        {"name": "is_processed", "label": "Processed?", "type": "select",
         "choices": ["No", "Yes"]},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(adj, form):
    adj.adjustment_date = to_date(form.get("adjustment_date")) or _date.today()
    if form.get("item_sel"):
        item = Item.query.filter_by(item_id=form.get("item_sel")).first()
        adj.item_id = item.id if item else None
    adj.adjustment_type = form.get("adjustment_type")
    adj.qty_change = to_int(form.get("qty_change"))
    adj.reason = form.get("reason")
    adj.approved_by = form.get("approved_by")
    adj.is_processed = (form.get("is_processed") == "Yes")
    adj.notes = form.get("notes")
    if adj.item_id is None:
        raise ValueError("Please pick a valid item.")
    return adj


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        adj = StockAdjustment()
        adj.adjustment_id = next_id(db.session.query(StockAdjustment.adjustment_id), "ADJ", 5)
        try:
            _resolve(adj, request.form)
            db.session.add(adj)
            db.session.commit()
            flash("Adjustment recorded.", "success")
            return redirect(url_for("adjustments.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"adjustment_date": _date.today().isoformat(), "is_processed": "No"}
    return render_template("form.html", form_title="Add Stock Adjustment",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("adjustments.index"))


@bp.route("/<int:adj_id>/edit", methods=["GET", "POST"])
@login_required
def edit(adj_id):
    adj = StockAdjustment.query.get_or_404(adj_id)
    if request.method == "POST":
        try:
            _resolve(adj, request.form)
            db.session.commit()
            flash("Adjustment updated.", "success")
            return redirect(url_for("adjustments.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "adjustment_date": date_input(adj.adjustment_date),
        "item_sel": adj.item.item_id if adj.item else "",
        "adjustment_type": adj.adjustment_type,
        "qty_change": adj.qty_change,
        "reason": adj.reason or "",
        "approved_by": adj.approved_by or "",
        "is_processed": "Yes" if adj.is_processed else "No",
        "notes": adj.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {adj.adjustment_id}",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("adjustments.index"))


@bp.route("/<int:adj_id>/delete", methods=["POST"])
@login_required
def delete(adj_id):
    adj = StockAdjustment.query.get_or_404(adj_id)
    db.session.delete(adj)
    db.session.commit()
    flash("Adjustment deleted.", "info")
    return redirect(url_for("adjustments.index"))