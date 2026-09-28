from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from datetime import date as _date
import json
import re

from sqlalchemy import func

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date, to_bool,
                              date_input, money)
from aflatoon.models import (Item, PurchaseBatch, Supplier,
                             CATEGORIES, ITEM_TYPES, BRAND_TYPES, ITEM_STATUSES,
                             SUPPLIER_TYPES)

bp = Blueprint("items", __name__, url_prefix="/items")

BULK_ROWS = 20          # rows rendered per block in the "Rows" tab
BULK_GROUPS = 8         # group blocks rendered in the "Groups" tab
BULK_MAX_ROWS = 300     # hard cap so a stray POST cannot create 100k rows


def _block_cost(cost_raw, batch_cost_per_item):
    """A typed cost wins; a blank one falls back to the lot average.

    Never negative: a stray minus sign would otherwise give the piece a
    negative cost, which inflates every profit figure downstream.
    """
    if cost_raw:
        return max(to_float(cost_raw), 0)
    return max(round(float(batch_cost_per_item or 0), 2), 0)


def _next_item_number() -> int:
    """Next numeric suffix for an AFL-##### code."""
    nums = []
    for (value,) in db.session.query(Item.item_id).all():
        if not value:
            continue
        m = re.match(r"AFL-(\d+)$", str(value))
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _fields():
    batches = db.session.query(PurchaseBatch.batch_id).order_by(PurchaseBatch.id.desc()).all()
    suppliers = db.session.query(Supplier.name).order_by(Supplier.name).all()
    return [
        {"name": "purchase_batch_id_sel", "label": "Purchase Batch", "type": "select",
         "choices": [(b[0], b[0]) for b in batches], "optional": True},
        {"name": "item_type", "label": "Item Type", "type": "select", "choices": ITEM_TYPES},
        {"name": "category", "label": "Category", "type": "select", "choices": CATEGORIES},
        {"name": "subcategory", "label": "Subcategory", "type": "text"},
        {"name": "supplier_sel", "label": "Supplier", "type": "select",
         "choices": [(s[0], s[0]) for s in suppliers], "optional": True},
        {"name": "brand_type", "label": "Brand Type", "type": "select", "choices": BRAND_TYPES, "default": "Unbranded"},
        {"name": "brand_name", "label": "Brand Name", "type": "text"},
        {"name": "description", "label": "Description", "type": "textarea"},
        {"name": "size", "label": "Size", "type": "text"},
        {"name": "colour", "label": "Colour", "type": "text"},
        {"name": "purchase_date", "label": "Purchase Date", "type": "date"},
        {"name": "allocated_cost", "label": "Allocated Cost (₹)", "type": "number", "step": "0.01"},
        {"name": "listed_price", "label": "Listed Price (₹)", "type": "number", "step": "0.01"},
        {"name": "mrp", "label": "MRP (₹)", "type": "number", "step": "0.01"},
        {"name": "current_status", "label": "Current Status", "type": "select",
         "choices": ITEM_STATUSES, "default": "Ready for Sale",
         "hint": "Damaged or Lost takes the piece off the rail, so it stops "
                 "counting towards stock value and restock."},
        {"name": "date_ready", "label": "Date Ready", "type": "date"},
        {"name": "date_sold", "label": "Date Sold", "type": "date"},
        {"name": "notes", "label": "Notes", "type": "textarea"},
    ]


def _resolve(obj, form):
    batch_id = None
    b = form.get("purchase_batch_id_sel")
    if b:
        batch = PurchaseBatch.query.filter_by(batch_id=b).first()
        batch_id = batch.id if batch else None
    supplier_id = None
    s = form.get("supplier_sel")
    if s:
        sup = Supplier.query.filter_by(name=s).first()
        supplier_id = sup.id if sup else None

    obj.purchase_batch_id = batch_id
    obj.supplier_id = supplier_id
    obj.item_type = form.get("item_type") or "Clothing"
    obj.category = form.get("category")
    obj.subcategory = form.get("subcategory")
    obj.brand_type = form.get("brand_type") or BRAND_TYPES[0]
    obj.brand_name = form.get("brand_name")
    obj.description = form.get("description")
    obj.size = form.get("size")
    obj.colour = form.get("colour")
    obj.purchase_date = to_date(form.get("purchase_date"))
    obj.allocated_cost = to_float(form.get("allocated_cost"))
    obj.listed_price = to_float(form.get("listed_price"))
    obj.mrp = to_float(form.get("mrp"))
    obj.current_status = form.get("current_status") or "Unprocessed"
    obj.date_ready = to_date(form.get("date_ready"))
    obj.date_sold = to_date(form.get("date_sold"))
    obj.notes = form.get("notes")

    if obj.current_status == "Sold" and not obj.date_sold:
        from datetime import date
        obj.date_sold = date.today()
    elif obj.current_status != "Sold" and obj.date_sold:
        # put back on the rail: keeping the old sale date made "Days Held"
        # age from a day it was never sold on, forever
        obj.date_sold = None
    return obj


@bp.route("/")
@login_required
def index():
    try:
        paginate = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        paginate = 1                      # /items/?page=abc must not be a 500
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    query = Item.query.order_by(Item.id.desc())
    if q:
        # strip LIKE wildcards, else a search for "50%" matches everything
        safe = q.replace("%", "").replace("_", "").strip()
        if safe:
            like = f"%{safe}%"
            query = query.filter(db.or_(
                Item.description.ilike(like), Item.item_id.ilike(like),
                Item.category.ilike(like), Item.brand_name.ilike(like)))
    if status:
        query = query.filter(Item.current_status == status)

    # counts for the status filter, so it is obvious what is in each bucket
    by_status = dict(db.session.query(
        Item.current_status, func.count(Item.id)
    ).group_by(Item.current_status).all())

    items = query.paginate(page=paginate, per_page=50, error_out=False)

    return render_template(
        "table.html",
        title="Items",
        subtitle="Every piece of stock is one row. For big lots use bulk entry.",
        new_url=url_for("items.create"),
        headers=["ID", "Category", "Description", "Size", "Listed Price", "Status"],
        rows=[{
            "id": it.id,
            "cells": [it.item_id, it.category or "-", it.description or "-",
                      it.size or "-", money(it.listed_price), it.current_status],
        } for it in items.items],
        actions=[{"label": "Edit", "endpoint": "items.edit", "arg": "item_id"},
                 {"label": "Delete", "endpoint": "items.delete", "arg": "item_id",
                  "delete": True,
                  "confirm": "Delete this item? Any sales recorded against it "
                             "will be deleted too, and the cash balance goes "
                             "back up. This cannot be undone."}],
        pagination=items,
        search_field="q",
        search_placeholder="Search ID, category, description...",
        status=by_status,
        status_choices=ITEM_STATUSES,
        total=items.total,
    )


@bp.route("/bulk", methods=["GET", "POST"])
@login_required
def bulk():
    """Enter a whole purchase lot in one go.

    Two ways in, because thrift lots are not uniform:

      * Groups - one line per batch of identical pieces ("10 shirts at Rs60").
        This is what a mixed lot needs: the cost per piece differs group to
        group, which the single batch average cannot express.
      * Rows  - a flat grid for a lot that is broadly the same throughout.

    Both write Item rows in the same AFL-##### sequence, so switching between
    them (or using one after the other for the same lot) cannot collide.
    """
    batches = (db.session.query(PurchaseBatch)
               .order_by(PurchaseBatch.purchase_date.desc()).all())
    suppliers = [s[0] for s in db.session.query(Supplier.name).order_by(Supplier.name)]
    batch_json = json.dumps([
        {"id": b.id, "batch_id": b.batch_id,
         "cost_per_item": round(float(b.cost_per_item or 0), 2),
         "qty": b.qty_purchased or 0,
         "total_cost": round(float(b.total_cost or 0), 2),
         "processed": b.processed_qty,
         # cost already accounted for by pieces entered earlier, so the
         # form can show what is genuinely still left to enter
         "entered_cost": round(sum(float(i.allocated_cost or 0)
                                   for i in b.items), 2)}
        for b in batches])

    ctx = dict(batches=batches, batch_json=batch_json, rows=BULK_ROWS,
               groups=BULK_GROUPS,
               suppliers=suppliers, suppliers_json=json.dumps(suppliers),
               CATEGORIES=CATEGORIES, ITEM_TYPES=ITEM_TYPES,
               BRAND_TYPES=BRAND_TYPES, ITEM_STATUSES=ITEM_STATUSES)

    if request.method == "POST":
        mode = request.form.get("entry_mode") or "rows"

        batch_id = None
        batch_obj = None
        bsel = request.form.get("purchase_batch_id_sel")
        if bsel:
            batch_obj = db.session.get(PurchaseBatch, int(bsel)) if bsel.isdigit() else None
            batch_id = batch_obj.id if batch_obj else None
        lot_cpi = round(float(batch_obj.cost_per_item or 0), 2) if batch_obj else 0.0

        item_type = request.form.get("item_type") or "Clothing"
        brand_type = request.form.get("brand_type") or "Unbranded"
        default_cat = request.form.get("default_category") or ""
        purchase_date = to_date(request.form.get("purchase_date"))
        default_listed = request.form.get("default_listed", "").strip()
        default_mrp = request.form.get("default_mrp", "").strip()
        status = request.form.get("current_status") or "Ready for Sale"
        supplier_id = None
        ssel = request.form.get("supplier_sel")
        if ssel:
            sup = Supplier.query.filter_by(name=ssel).first()
            supplier_id = sup.id if sup else None

        # An exact-code collision can only mean two writers at once (an
        # impatient double-click on Save), since this is the only place
        # codes are minted. Clear the session and look again so the second
        # POST picks the next free number instead of dying on the UNIQUE
        # constraint and losing the whole entry.
        for _attempt in range(5):
            nxt = _next_item_number()
            if not Item.query.filter_by(item_id=f"AFL-{nxt:05d}").first():
                break
            db.session.expire_all()
        first_code = f"AFL-{nxt:05d}"
        created, skipped = 0, 0
        capped = False

        def add_item(category, subcategory, description, size, colour,
                     cost, listed, mrp):
            """Append one item. Returns False if the cap has been reached."""
            nonlocal nxt, created, capped
            if created >= BULK_MAX_ROWS:
                capped = True
                return False
            item = Item()
            item.item_id = f"AFL-{nxt:05d}"
            nxt += 1
            item.purchase_batch_id = batch_id
            item.supplier_id = supplier_id
            item.item_type = item_type
            item.category = category or None
            item.subcategory = subcategory or None
            item.brand_type = brand_type
            item.brand_name = None
            item.description = description or None
            item.size = size or None
            item.colour = colour or None
            item.purchase_date = purchase_date
            item.allocated_cost = to_float(cost)
            item.listed_price = to_float(listed)
            item.mrp = to_float(mrp)
            item.current_status = status
            db.session.add(item)
            created += 1
            return True

        if mode == "groups":
            try:
                group_count = min(int(request.form.get("group_count") or 0),
                                  BULK_GROUPS + 200)
            except (TypeError, ValueError):
                group_count = 0
            for i in range(group_count):
                def gcell(field):
                    return (request.form.get(f"b{i}_{field}") or "").strip()

                category = gcell("category") or default_cat
                qty_raw = gcell("qty")
                qty = max(int(to_float(qty_raw, 0)), 0)
                cost_raw = gcell("cost")
                cost = _block_cost(cost_raw, lot_cpi)
                listed = max(to_float(gcell("listed") or default_listed), 0)
                mrp = max(to_float(gcell("mrp") or default_mrp), 0)

                if qty <= 0 or not category:
                    if any([gcell("size"), gcell("colour"), cost_raw,
                            gcell("listed"), gcell("description")]):
                        skipped += 1
                    continue
                for _ in range(qty):
                    if not add_item(category, gcell("subcategory"),
                                    gcell("description"), gcell("size"),
                                    gcell("colour"), cost, listed, mrp):
                        break
        else:
            try:
                row_count = min(int(request.form.get("row_count") or 0), BULK_MAX_ROWS)
            except (TypeError, ValueError):
                row_count = 0
            for i in range(row_count):
                def cell(field):
                    return (request.form.get(f"r{i}_{field}") or "").strip()

                category = cell("category") or default_cat
                size = cell("size")
                colour = cell("colour")
                cost_raw = cell("cost")
                listed_raw = cell("listed")
                mrp_raw = cell("mrp")
                desc = cell("description")

                if not any([size, colour, cost_raw, listed_raw, mrp_raw, desc]):
                    skipped += 1
                    continue
                # same rule as groups: blank means the lot average
                cost = _block_cost(cost_raw, lot_cpi)
                add_item(category, cell("subcategory"), desc, size, colour,
                         cost, max(to_float(listed_raw or default_listed), 0),
                         max(to_float(mrp_raw or default_mrp), 0))

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            # re-render with what was typed: previously the per-block and
            # per-row tables came back blank, so a failed save of a 300-piece
            # entry meant retyping the whole lot
            flash(f"Could not save the batch of items: {e}", "danger")
            return render_template("bulk_items.html", values=request.form,
                                   post=request.form, **ctx)

        if created:
            last = f"AFL-{nxt - 1:05d}"
            msg = f"Created {created} item{'s' if created != 1 else ''}."
            if first_code == last:
                msg += f" First code {first_code}."
            else:
                msg += f" Codes {first_code} to {last}."
            if skipped:
                msg += f" {skipped} empty {'block' if mode == 'groups' else 'row'}{'s' if skipped != 1 else ''} skipped."
            if capped:
                msg += f" Stopped at the {BULK_MAX_ROWS} item limit."
            if batch_obj:
                # compare the lot total, not this POST alone: entering the
                # last 20 of a 40-piece lot must not say "the lot says 40"
                now_in = batch_obj.processed_qty
                want = batch_obj.qty_purchased or 0
                if now_in < want:
                    msg += (f" Lot {batch_obj.batch_id} now has {now_in} of "
                            f"{want} pieces entered.")
                elif now_in > want:
                    msg += (f" Careful: lot {batch_obj.batch_id} has "
                            f"{now_in} pieces but only {want} were bought.")
            flash(msg, "success")
            return redirect(url_for("items.index"))
        flash("Nothing to save - every row was blank.", "warning")
        return redirect(url_for("items.bulk"))

    return render_template("bulk_items.html", values={
        "item_type": "Clothing", "brand_type": "Unbranded",
        "current_status": "Ready for Sale", "entry_mode": "groups",
        "purchase_date": _date.today().isoformat()}, **ctx)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        desc = request.form.get("description", "").strip()
        if not desc:
            flash("Description is required to create an item.", "danger")
        else:
            item = Item()
            item.item_id = next_id(db.session.query(Item.item_id), "AFL", 5)
            try:
                _resolve(item, request.form)
                db.session.add(item)
                db.session.commit()
                flash(f"Item {item.item_id} created.", "success")
                return redirect(url_for("items.index"))
            except Exception as e:
                db.session.rollback()
                flash(f"Error saving item: {e}", "danger")
    return render_template("form.html", form_title="Add Item",
                           fields=_fields(), values={},
                           cancel_url=url_for("items.index"))


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item = Item.query.get_or_404(item_id)
    if request.method == "POST":
        try:
            _resolve(item, request.form)
            db.session.commit()
            flash(f"Item {item.item_id} updated.", "success")
            return redirect(url_for("items.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving item: {e}", "danger")
    values = {
        "purchase_batch_id_sel": item.purchase_batch.batch_id if item.purchase_batch else "",
        "item_type": item.item_type,
        "category": item.category,
        "subcategory": item.subcategory or "",
        "supplier_sel": item.supplier.name if item.supplier else "",
        "brand_type": item.brand_type,
        "brand_name": item.brand_name or "",
        "description": item.description or "",
        "size": item.size or "",
        "colour": item.colour or "",
        "purchase_date": date_input(item.purchase_date),
        "allocated_cost": item.allocated_cost or "",
        "listed_price": item.listed_price or "",
        "mrp": item.mrp or "",
        "current_status": item.current_status,
        "date_ready": date_input(item.date_ready),
        "date_sold": date_input(item.date_sold),
        "notes": item.notes or "",
    }
    return render_template("form.html", form_title=f"Edit {item.item_id}",
                           fields=_fields(), values=values,
                           cancel_url=url_for("items.index"))


@bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
def delete(item_id):
    item = Item.query.get_or_404(item_id)
    n_sales = len(item.sales)
    code = item.item_id
    # the cash lines have no relationship to the sale, so the ORM cascade
    # cannot reach them: withdraw them explicitly or the ledger keeps
    # counting money from a sale that no longer exists
    from aflatoon.services import unpost_cash
    for s in item.sales:
        unpost_cash("sale", s.id)
    try:
        # the cascade on Item.sales / Item.adjustments removes those rows;
        # without it SQLAlchemy tried to NULL their NOT NULL item_id
        db.session.delete(item)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete {code}: {e}. Edit the status instead if this "
              f"was a mistake.", "danger")
        return redirect(url_for("items.index"))
    msg = f"Item {code} deleted."
    if n_sales:
        msg += (f" {n_sales} sale record{'s' if n_sales != 1 else ''} removed "
                f"with it, and the money they brought in left the cash balance.")
    flash(msg, "info")
    return redirect(url_for("items.index"))