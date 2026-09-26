from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from datetime import date as _date
import json
import re

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, next_id, to_float, to_date, to_bool,
                              date_input, money)
from aflatoon.models import (Item, PurchaseBatch, Supplier,
                             CATEGORIES, ITEM_TYPES, BRAND_TYPES, ITEM_STATUSES,
                             SUPPLIER_TYPES)

bp = Blueprint("items", __name__, url_prefix="/items")

BULK_ROWS = 20          # rows rendered per block
BULK_MAX_ROWS = 300     # hard cap so a stray POST cannot create 100k rows


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
        {"name": "brand_type", "label": "Brand Type", "type": "select", "choices": BRAND_TYPES},
        {"name": "brand_name", "label": "Brand Name", "type": "text"},
        {"name": "description", "label": "Description", "type": "textarea"},
        {"name": "size", "label": "Size", "type": "text"},
        {"name": "colour", "label": "Colour", "type": "text"},
        {"name": "purchase_date", "label": "Purchase Date", "type": "date"},
        {"name": "allocated_cost", "label": "Allocated Cost (₹)", "type": "number", "step": "0.01"},
        {"name": "listed_price", "label": "Listed Price (₹)", "type": "number", "step": "0.01"},
        {"name": "mrp", "label": "MRP (₹)", "type": "number", "step": "0.01"},
        {"name": "current_status", "label": "Current Status", "type": "select", "choices": ITEM_STATUSES},
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
    obj.brand_type = form.get("brand_type") or "Other"
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
    return obj


@bp.route("/")
@login_required
def index():
    paginate = int(request.args.get("page", 1))
    q = request.args.get("q", "").strip()
    query = Item.query.order_by(Item.id.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Item.description.ilike(like), Item.item_id.ilike(like),
            Item.category.ilike(like), Item.brand_name.ilike(like)))
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
                 {"label": "Delete", "endpoint": "items.delete", "arg": "item_id", "delete": True}],
        pagination=items,
        search_field="q",
        search_placeholder="Search ID, category, description...",
        total=items.total,
    )


@bp.route("/bulk", methods=["GET", "POST"])
@login_required
def bulk():
    """Enter a whole purchase lot in one go.

    A thrift lot is 100-200 pieces of broadly similar stock, so the defaults
    are set once (type, brand, category, cost) and the grid only needs the
    things that actually vary: size, colour and price.
    """
    batches = (db.session.query(PurchaseBatch)
               .order_by(PurchaseBatch.purchase_date.desc()).all())
    suppliers = [s[0] for s in db.session.query(Supplier.name).order_by(Supplier.name)]
    batch_json = json.dumps([
        {"id": b.id, "batch_id": b.batch_id,
         "cost_per_item": round(float(b.cost_per_item or 0), 2),
         "qty": b.qty_purchased or 0,
         "processed": b.processed_qty}
        for b in batches])

    ctx = dict(batches=batches, batch_json=batch_json, rows=BULK_ROWS,
               suppliers=suppliers, suppliers_json=json.dumps(suppliers),
               CATEGORIES=CATEGORIES, ITEM_TYPES=ITEM_TYPES,
               BRAND_TYPES=BRAND_TYPES, ITEM_STATUSES=ITEM_STATUSES)

    if request.method == "POST":
        batch_id = None
        bsel = request.form.get("purchase_batch_id_sel")
        if bsel:
            batch = db.session.get(PurchaseBatch, int(bsel)) if bsel.isdigit() else None
            batch_id = batch.id if batch else None

        item_type = request.form.get("item_type") or "Clothing"
        brand_type = request.form.get("brand_type") or "Unbranded"
        default_cat = request.form.get("default_category") or ""
        purchase_date = to_date(request.form.get("purchase_date"))
        supplier_id = None
        ssel = request.form.get("supplier_sel")
        if ssel:
            sup = Supplier.query.filter_by(name=ssel).first()
            supplier_id = sup.id if sup else None

        try:
            row_count = min(int(request.form.get("row_count") or 0), BULK_MAX_ROWS)
        except (TypeError, ValueError):
            row_count = 0

        nxt = _next_item_number()
        first_code = f"AFL-{nxt:05d}"
        created, skipped = 0, 0
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

            item = Item()
            item.item_id = f"AFL-{nxt:05d}"
            nxt += 1
            item.purchase_batch_id = batch_id
            item.supplier_id = supplier_id
            item.item_type = item_type
            item.category = category or None
            item.subcategory = cell("subcategory") or None
            item.brand_type = brand_type
            item.brand_name = None
            item.description = desc or None
            item.size = size or None
            item.colour = colour or None
            item.purchase_date = purchase_date
            item.allocated_cost = to_float(cost_raw)
            item.listed_price = to_float(listed_raw)
            item.mrp = to_float(mrp_raw)
            item.current_status = request.form.get("current_status") or "Ready for Sale"
            db.session.add(item)
            created += 1

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Could not save the batch of items: {e}", "danger")
            return render_template("bulk_items.html", values=request.form, **ctx)

        if created:
            last = f"AFL-{nxt - 1:05d}"
            msg = f"Created {created} item{'s' if created != 1 else ''}."
            if first_code == last:
                msg += f" First code {first_code}."
            else:
                msg += f" Codes {first_code} to {last}."
            if skipped:
                msg += f" {skipped} blank row{'s' if skipped != 1 else ''} skipped."
            flash(msg, "success")
            return redirect(url_for("items.index"))
        flash("Nothing to save - every row was blank.", "warning")
        return redirect(url_for("items.bulk"))

    return render_template("bulk_items.html", values={
        "item_type": "Clothing", "brand_type": "Unbranded",
        "current_status": "Ready for Sale",
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
    db.session.delete(item)
    db.session.commit()
    flash(f"Item {item.item_id} deleted.", "info")
    return redirect(url_for("items.index"))