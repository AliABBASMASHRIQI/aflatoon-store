from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, to_float, to_date, to_bool,
                              date_input, money, pct)
from aflatoon.models import Sale, Item, PAYMENT_METHODS

bp = Blueprint("sales", __name__, url_prefix="/sales")


@bp.route("/")
@login_required
def index():
    from aflatoon.services import dashboard_data
    d = dashboard_data()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    q = db.session.query(Sale)
    if date_from:
        q = q.filter(Sale.sale_date >= to_date(date_from))
    if date_to:
        q = q.filter(Sale.sale_date <= to_date(date_to))
    sales = q.order_by(Sale.id.desc()).limit(500).all()
    return render_template(
        "table.html",
        title="Sales Entry",
        new_url=url_for("sales.create"),
        headers=["Date", "Bill ID", "Item", "Qty", "Value", "Profit",
                 "Payment", "Return"],
        rows=[{
            "id": s.id,
            "cells": [s.sale_date.strftime("%d %b %Y"), s.bill_id or "-",
                      f"{s.item.item_id} - {s.item.description[:25]}" if s.item else "?",
                      s.qty, money(s.final_value),
                      money(s.gross_profit) if s.gross_profit is not None else "-",
                      s.payment_method, "Yes" if s.is_returned else "No"],
        } for s in sales],
        actions=[{"label": "Edit", "endpoint": "sales.edit"},
                 {"label": "Delete", "endpoint": "sales.delete", "delete": True}],
        total=len(sales),
        month_sales=d["month_sales"],
        month_gp=d["gp_mtd"],
    )


def _field_spec():
    items = db.session.query(Item.item_id, Item.description,
                             Item.current_status).order_by(Item.item_id).all()
    return [
        {"name": "sale_date", "label": "Sale Date", "type": "date", "required": True},
        {"name": "bill_id", "label": "Bill ID", "type": "text"},
        {"name": "item_sel", "label": "Item (ID - Description)", "type": "select",
         "choices": [(i, f"{i} - {d} [{st}]") for i, d, st in items]},
        {"name": "qty", "label": "Qty", "type": "number", "step": "1"},
        {"name": "listed_price", "label": "Listed Price (₹)", "type": "number", "step": "0.01"},
        {"name": "discount", "label": "Discount (₹)", "type": "number", "step": "0.01"},
        {"name": "payment_method", "label": "Payment Method", "type": "select",
         "choices": PAYMENT_METHODS},
        {"name": "is_returned", "label": "Return?", "type": "select", "choices": ["No", "Yes"]},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(sale, form):
    sale.sale_date = to_date(form.get("sale_date")) or _date.today()
    sale.bill_id = form.get("bill_id")
    if form.get("item_sel"):
        item = Item.query.filter_by(item_id=form.get("item_sel")).first()
        sale.item_id = item.id if item else None
    sale.qty = int(to_float(form.get("qty"), 1))
    sale.listed_price = to_float(form.get("listed_price"))
    sale.discount = to_float(form.get("discount"))
    sale.payment_method = form.get("payment_method") or "Cash"
    sale.is_returned = (form.get("is_returned") == "Yes")
    sale.notes = form.get("notes")
    if sale.item_id is None:
        raise ValueError("Please pick a valid item.")
    if sale.item_id and not sale.is_returned:
        sale.item.current_status = "Sold"
        sale.item.date_sold = sale.sale_date
    return sale


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        sale = Sale()
        try:
            _resolve(sale, request.form)
            db.session.add(sale)
            db.session.commit()
            flash("Sale recorded.", "success")
            return redirect(url_for("sales.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"sale_date": _date.today().isoformat(), "qty": 1, "is_returned": "No"}
    return render_template("form.html", form_title="Record Sale",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("sales.index"))


@bp.route("/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required
def edit(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    if request.method == "POST":
        old_item = sale.item
        try:
            _resolve(sale, request.form)
            db.session.commit()
            flash("Sale updated.", "success")
            return redirect(url_for("sales.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {
        "sale_date": date_input(sale.sale_date),
        "bill_id": sale.bill_id or "",
        "item_sel": sale.item.item_id if sale.item else "",
        "qty": sale.qty,
        "listed_price": sale.listed_price or "",
        "discount": sale.discount or "",
        "payment_method": sale.payment_method,
        "is_returned": "Yes" if sale.is_returned else "No",
        "notes": sale.notes or "",
    }
    return render_template("form.html", form_title="Edit Sale",
                           fields=_field_spec(), values=values,
                           cancel_url=url_for("sales.index"))


@bp.route("/<int:sale_id>/delete", methods=["POST"])
@login_required
def delete(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    item = sale.item
    db.session.delete(sale)
    db.session.commit()
    flash("Sale deleted.", "info")
    return redirect(url_for("sales.index"))