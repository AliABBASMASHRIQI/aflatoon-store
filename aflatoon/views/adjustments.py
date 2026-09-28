from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date as _date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_int, to_date,
                              date_input, to_bool)
from aflatoon.models import (StockAdjustment, Item, ADJUSTMENT_TYPES,
                             ITEM_STATUSES, OFF_RAIL_STATUSES)

bp = Blueprint("adjustments", __name__, url_prefix="/adjustments")

# An adjustment exists to correct the stock, so it has to actually correct
# it. The type decides what happens to the piece it is filed against.
ADJUSTMENT_TO_STATUS = {
    "Damaged": "Damaged",
    "Lost": "Lost",
    "Found": "Ready for Sale",
    "Correction": None,          # no status change: physical count note only
    "Physical Count": None,
}


def apply_adjustment(adj, item):
    """Move the item to the status this adjustment type implies.

    Returns (changed, message). This is what makes an adjustment more than
    a log line: filing "Damaged" against a piece takes it off the rail, so
    it stops inflating stock value and stops being counted as stock to
    re-buy. Previously nothing read qty_change or adjustment_type at all,
    so both mechanisms for recording a loss were inert.
    """
    new_status = ADJUSTMENT_TO_STATUS.get(adj.adjustment_type)
    if not new_status:
        return False, None
    if item is None:
        return False, None
    if not adj.is_processed:
        # an unprocessed adjustment is only a note: the owner has not
        # confirmed it, so nothing about the item should change
        return False, ("Saved as a note only. Tick Processed? to apply it "
                       "to the item.")
    if item.current_status == "Sold":
        return False, (f"{item.item_id} is already sold, so a "
                       f"{adj.adjustment_type.lower()} report cannot change it.")
    if item.current_status == new_status:
        return False, None
    item.current_status = new_status
    if new_status in ("Damaged", "Lost"):
        item.date_sold = None
    return True, f"{item.item_id} marked {new_status}."


@bp.route("/")
@login_required
def index():
    try:
        paginate = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        paginate = 1
    adjustments = StockAdjustment.query.order_by(
        StockAdjustment.id.desc()).paginate(page=paginate, per_page=100,
                                            error_out=False)
    rows = [{
        "id": a.id,
        "cells": [a.adjustment_date.strftime("%d %b %Y"), a.adjustment_id,
                  a.item.item_id if a.item else "?",
                  a.adjustment_type or "-", str(a.qty_change),
                  a.reason or "-", a.approved_by or "-",
                  "Yes" if a.is_processed else "No"],
    } for a in adjustments.items]
    return render_template(
        "table.html",
        title="Stock Adjustments",
        subtitle="Recording a loss takes the piece off the rail, so stock "
                 "value drops and restock stops asking for it again.",
        new_url=url_for("adjustments.create"),
        headers=["Date", "ID", "Item", "Type", "Qty Change", "Reason",
                 "Approved By", "Processed?"],
        rows=rows,
        actions=[{"label": "Edit", "endpoint": "adjustments.edit", "arg": "adj_id"},
                 {"label": "Delete", "endpoint": "adjustments.delete", "arg": "adj_id",
                  "delete": True,
                  "confirm": "Delete this adjustment? The item status it set "
                             "will not be reverted."}],
        pagination=adjustments,
        total=adjustments.total,
    )


def _pickable_items(limit=400):
    """Items offered in the picker, cheapest to fix first.

    Rendering every item as an <option> meant a store with a few thousand
    pieces produced tens of thousands of them and the page stopped being
    usable. On-rail pieces come first because that is what gets adjusted.
    """
    on_rail = [s for s in ITEM_STATUSES if s not in OFF_RAIL_STATUSES]
    rows = (db.session.query(Item.item_id, Item.description, Item.current_status)
            .filter(Item.current_status.in_(on_rail))
            .order_by(Item.item_id.desc()).limit(limit).all())
    if len(rows) < limit:
        seen = {r[0] for r in rows}
        more = (db.session.query(Item.item_id, Item.description, Item.current_status)
                .order_by(Item.item_id.desc()).limit(limit).all())
        rows += [r for r in more if r[0] not in seen]
    return rows


def _field_spec():
    items = _pickable_items()
    return [
        {"name": "adjustment_date", "label": "Adjustment Date", "type": "date", "required": True},
        {"name": "item_sel", "label": "Item (ID - Description)", "type": "select",
         "choices": [(i, f"{i} - {(d or 'untagged')[:40]} [{st}]") for i, d, st in items]},
        {"name": "adjustment_type", "label": "Adjustment Type", "type": "select",
         "choices": ADJUSTMENT_TYPES,
         "hint": "Damaged or Lost takes the piece off the rail and out of "
                 "stock value. Physical Count is a note only."},
        {"name": "qty_change", "label": "Qty Change", "type": "number", "step": "1",
         "hint": "For your records. One row is one piece, so -1 is the usual "
                 "answer for a loss."},
        {"name": "reason", "label": "Reason", "type": "textarea"},
        {"name": "approved_by", "label": "Approved By", "type": "text"},
        {"name": "is_processed", "label": "Processed?", "type": "select",
         "choices": ["No", "Yes"],
         "hint": "Tick this to actually apply the change to the item. Untick "
                 "keeps it as a note."},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(adj, form):
    adj.adjustment_date = to_date(form.get("adjustment_date")) or _date.today()
    item = None
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
            # look the item up by id, not through the relationship: adj.item
            # would trigger an autoflush that makes the pending row visible
            # to the query, and the returned object is not the one whose
            # status change gets committed reliably
            item = db.session.get(Item, adj.item_id)
            changed, note = apply_adjustment(adj, item)
            db.session.commit()
            flash(note or "Adjustment recorded.",
                  "success" if changed or not note else "info")
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
            changed, note = apply_adjustment(adj, db.session.get(Item, adj.item_id))
            db.session.commit()
            flash(note or "Adjustment updated.",
                  "success" if changed or not note else "info")
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