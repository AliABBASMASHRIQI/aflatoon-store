from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, to_float, to_date, to_bool,
                              date_input, money, pct)
from aflatoon.models import Sale, Item, PAYMENT_METHODS
from aflatoon.services import post_cash, unpost_cash

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
                      f"{s.item.item_id} - {(s.item.description or 'no description')[:25]}"
                      if s.item else "?",
                      s.qty, money(s.final_value),
                      money(s.gross_profit) if s.gross_profit is not None else "-",
                      s.payment_method, "Yes" if s.is_returned else "No"],
        } for s in sales],
        actions=[{"label": "Edit", "endpoint": "sales.edit", "arg": "sale_id"},
                 {"label": "Delete", "endpoint": "sales.delete", "arg": "sale_id",
                  "delete": True,
                  "confirm": "Delete this sale? The piece goes back on the rail "
                             "and the cash balance drops by what it fetched."}],
        total=len(sales),
        month_sales=d["month_sales"],
        month_gp=d["gp_mtd"],
    )


SALE_PICKER_LIMIT = 500


def _pickable_items(limit=SALE_PICKER_LIMIT):
    """Sellable pieces, newest first.

    Only "Ready for Sale" pieces can be picked. Listing every item meant a
    store with a few thousand pieces rendered thousands of <option>s and the
    sale form became unusably slow, and it offered already-sold pieces too.
    The rest of the stock is reachable by editing the piece's status first.
    """
    return (db.session.query(Item.item_id, Item.description,
                             Item.listed_price, Item.allocated_cost)
            .filter(Item.current_status == "Ready for Sale")
            .order_by(Item.item_id.desc()).limit(limit).all())


def _item_maps(limit=SALE_PICKER_LIMIT):
    """(listed price, allocated cost) keyed by item code, for the form JS."""
    rows = _pickable_items(limit)
    prices = {}
    costs = {}
    for code, _desc, price, cost in rows:
        prices[code] = float(price or 0)
        costs[code] = float(cost or 0)
    return prices, costs


def _field_spec():
    items = _pickable_items()
    return [
        {"name": "sale_date", "label": "Sale Date", "type": "date", "required": True},
        {"name": "bill_id", "label": "Bill ID", "type": "text"},
        {"name": "item_sel", "label": "Item (ID - Description)", "type": "select",
         "choices": [(i, f"{i} - {(d or 'untagged')[:40]}") for i, d, _p, _c in items]},
        {"name": "listed_price", "label": "Listed Price (₹)", "type": "number", "step": "0.01",
         "hint": "Fills in from the item when you pick it."},
        {"name": "discount", "label": "Discount (₹)", "type": "number", "step": "0.01",
         "hint": "Or type the final price you agreed below."},
        {"name": "final_price", "label": "Final Price (₹)", "type": "number", "step": "0.01",
         "hint": "If a customer haggles, type what they paid here and the "
                 "discount is worked out for you."},
        {"name": "qty", "label": "Qty", "type": "number", "step": "1", "min": "1"},
        {"name": "payment_method", "label": "Payment Method", "type": "select",
         "choices": PAYMENT_METHODS},
        {"name": "is_returned", "label": "Return?", "type": "select", "choices": ["No", "Yes"]},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(sale, form):
    sale.sale_date = to_date(form.get("sale_date")) or _date.today()
    sale.bill_id = form.get("bill_id")
    item = None
    if form.get("item_sel"):
        item = Item.query.filter_by(item_id=form.get("item_sel")).first()
    if item is None:
        raise ValueError("Please pick a valid item.")
    # assign the relationship, not just the id: a new Sale has no .item yet,
    # so setting only item_id would leave sale.item as None below.
    sale.item = item
    sale.qty = max(int(to_float(form.get("qty"), 1)), 1)
    sale.listed_price = to_float(form.get("listed_price"))
    sale.discount = to_float(form.get("discount"))

    # Haggling: type what the customer actually paid and the discount is
    # worked out, so he never has to do the subtraction to record a deal.
    final_raw = (form.get("final_price") or "").strip()
    if final_raw:
        final = to_float(final_raw)
        if sale.listed_price:
            sale.discount = max(sale.listed_price - final, 0)
        else:
            # no listed price typed: treat the agreed figure as the price
            sale.listed_price = final
            sale.discount = 0

    if form.get("payment_method"):
        sale.payment_method = form.get("payment_method")
    else:
        sale.payment_method = "Cash"
    sale.is_returned = (form.get("is_returned") == "Yes")
    sale.notes = form.get("notes")
    if not sale.is_returned:
        sale.item.current_status = "Sold"
        sale.item.date_sold = sale.sale_date
    elif sale.item.current_status == "Sold":
        # un-marking a return puts the piece back on the rail
        sale.item.current_status = "Ready for Sale"
        sale.item.date_sold = None
    return sale


def _sync_cash(sale):
    """Keep the cash ledger in step with a sale.

    A real sale brings money in. A returned sale brings nothing in, so its
    ledger line is removed instead. Credit/Outstanding sales post nothing
    at all - post_cash skips them.
    """
    unpost_cash("sale", sale.id)
    if sale.is_returned:
        return
    label = sale.item.item_id if sale.item else (sale.bill_id or "sale")
    post_cash("sale", sale.id, sale.sale_date, "Sale",
              f"Sale {sale.bill_id or ''} - {label}".strip(" -"),
              sale.final_value, sale.payment_method, "in")


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        sale = Sale()
        try:
            _resolve(sale, request.form)
            db.session.add(sale)
            db.session.flush()          # sale.id needed to link the cash line
            _sync_cash(sale)
            db.session.commit()
            flash("Sale recorded.", "success")
            return redirect(url_for("sales.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    values = {"sale_date": _date.today().isoformat(), "qty": 1, "is_returned": "No"}
    prices, costs = _item_maps()
    return render_template("form.html", form_title="Record Sale",
                           fields=_field_spec(), values=values,
                           item_prices=prices, item_costs=costs,
                           cancel_url=url_for("sales.index"))


@bp.route("/<int:sale_id>/edit", methods=["GET", "POST"])
@login_required
def edit(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    old_item = sale.item
    if request.method == "POST":
        try:
            _resolve(sale, request.form)
            # changing which piece was sold frees the previous one
            if old_item and old_item.id != sale.item_id and not sale.is_returned:
                old_item.current_status = "Ready for Sale"
                old_item.date_sold = None
            _sync_cash(sale)
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
    prices, costs = _item_maps()
    if sale.item:
        # the piece being sold may no longer be "Ready for Sale" (e.g. after
        # it was marked returned), but its price must still show
        prices.setdefault(sale.item.item_id, float(sale.item.listed_price or 0))
        costs.setdefault(sale.item.item_id, float(sale.item.allocated_cost or 0))
    return render_template("form.html", form_title="Edit Sale",
                           fields=_field_spec(), values=values,
                           item_prices=prices, item_costs=costs,
                           cancel_url=url_for("sales.index"))


@bp.route("/<int:sale_id>/delete", methods=["POST"])
@login_required
def delete(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    item = sale.item
    was_returned = sale.is_returned
    unpost_cash("sale", sale.id)
    db.session.delete(sale)
    # a deleted sale means the piece is back on the rail
    if item and not was_returned and item.current_status == "Sold":
        remaining = Sale.query.filter(
            Sale.item_id == item.id,
            Sale.id != sale_id,
            Sale.is_returned.is_(False)).count()
        if remaining == 0:
            item.current_status = "Ready for Sale"
            item.date_sold = None
    db.session.commit()
    flash("Sale deleted.", "info")
    return redirect(url_for("sales.index"))