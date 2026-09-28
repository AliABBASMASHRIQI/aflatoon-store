# -*- coding: utf-8 -*-
"""Aflatoon Studio — reporting/business services (dashboard, reports, budgets).

This module provides every pure-computation function used by the view layer.
All queries go through SQLAlchemy against the aplatoon models; all helpers come
from ``aflatoon.helpers``.

NOTICE: this file was rebuilt after an accidental overwrite (the original
business logic was lost). It reconstructs the full view/service contract
discovered in the intact views/templates/models. Review the return shapes and
threshold names in the monthly budget / dashboard / cash ledgers / reports
sections carefully, then commit.

CONTRACT  (kept in sync with aflatoon/views/*.py):
  get_settings()                     -> Settings (single row, auto-created)
  dashboard_data()                   -> dict for dashboard.html  (see d.*)
  projection_summary()               -> dict {next_month_forecast, twelve_month_total}
  budget_data()                      -> list[dict] for budgets index  (see budget.html)
  budget_category_block(month)       -> dict for budget sidebar block   (see budget.html)
  cash_ledger_rows()                 -> dict {starting_cash, starting_bank, rows:[...]}
  stock_summary_rows()               -> list[dict] for reports/stock
  sales_analysis_data()              -> dict {categories, matrix} for sales_analysis
  target_projection_rows()           -> list[dict] for reports/target
  projection_summary()               -> dict {next_month_forecast, twelve_month_total}
  restock_intelligence_rows()        -> list[dict] for reports/restock
  profit_cash_rows()                 -> list[dict] for reports/profit
  major_restock_rows()               -> list[dict] for reports/major-restock
  data_quality_checks()              -> list[dict] for reports/data_quality
"""

from datetime import date, timedelta
from decimal import Decimal
import json

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from aflatoon.extensions import db
from aflatoon.helpers import (month_start, month_end, month_series, num,
                              money, money2, pct, datefmt, to_float, to_int,
                              to_date, next_id)
from aflatoon.models import (Settings, PurchaseBatch, Supplier, Item, Sale,
                             MonthlyBudget, BUDGET_CATEGORY_FIELDS, CATEGORIES,
                             ITEM_STATUSES, SUPPLIER_TYPES, PAYMENT_METHODS,
                             ITEM_TYPES, EXPENSE_CATEGORIES, EXPENSE_NATURES,
                             ADJUSTMENT_TYPES, CashTxn, Expense, EmiTracker,
                             OFF_RAIL_STATUSES)

# ---------------------------------------------------------------------------
# SQL mirrors of the Sale python properties.
# A python @property cannot be handed to sum(), so every aggregate that used
# Sale.final_value / cost_total / gross_profit needs the equivalent SQL here.
# Anything touching SALE_COST or SALE_GP must also .join(Item).
# ---------------------------------------------------------------------------
SALE_QTY = func.coalesce(Sale.qty, 0)
SALE_NET = (SALE_QTY * func.coalesce(Sale.listed_price, 0)
            - func.coalesce(Sale.discount, 0))
SALE_COST = SALE_QTY * func.coalesce(Item.allocated_cost, 0)
SALE_GP = SALE_NET - SALE_COST

# ----------------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------------


def get_settings() -> Settings:
    """Return the single Settings row, creating + seeding it lazily if needed."""
    s = Settings.query.first()
    if s is None:
        s = Settings()
        s.store_name = "Aflatoon Studio"
        s.store_type = "Women + Men, thrift-led mixed retail"
        s.currency = "INR"
        s.planning_sales_bad = Decimal("50000")
        s.planning_sales_normal = Decimal("100000")
        s.planning_sales_good = Decimal("150000")
        s.initial_stock_estimate = 0
        s.est_new_items_per_month = 175
        s.restock_pct = Decimal("0.10")
        s.major_restock_pct = Decimal("0.20")
        s.owner_cash_target_pct = Decimal("0.10")
        s.critical_coverage_days = 14
        s.low_coverage_days = 30
        s.slow_moving_days = 60
        s.dead_stock_days = 90
        s.emi_alert_days = 5
        s.starting_cash = Decimal("0")
        s.starting_bank_upi = Decimal("0")
        s.admin_user = "admin"
        db.session.add(s)
        db.session.commit()
    return s


# ----------------------------------------------------------------------------
# dashboard
# ----------------------------------------------------------------------------

def _month_bounds(ref: date) -> tuple:
    """(first day of ref's month, first day of the NEXT month).

    The end is EXCLUSIVE so it can be used with `<` in filters. The old
    _month_start_end returned an inclusive last-day while callers compared
    with `<`, which silently dropped the final day of every month.
    """
    return month_start(ref), month_end(ref) + timedelta(days=1)


def _sum(q) -> float:
    """Execute a (coalesce-sum) query and return as float."""
    v = q.scalar()
    return float(v or 0)


def dashboard_data() -> dict:
    """Everything the dashboard.html page shows (see d.* context)."""
    now = date.today()
    m_start, m_end = _month_bounds(now)
    per = db.func.coalesce

    today_sales = _sum(
        db.session.query(per(func.sum(SALE_NET), 0))
        .filter(Sale.sale_date == now, Sale.is_returned.is_(False)))
    month_sales = _sum(
        db.session.query(per(func.sum(SALE_NET), 0))
        .filter(Sale.sale_date >= m_start, Sale.sale_date < m_end,
                Sale.is_returned.is_(False)))
    gp_mtd = _sum(
        db.session.query(per(func.sum(SALE_GP), 0))
        .join(Item, Sale.item_id == Item.id)
        .filter(Sale.sale_date >= m_start, Sale.sale_date < m_end,
                Sale.is_returned.is_(False)))
    exp_mtd = _sum(
        db.session.query(per(func.sum(Expense.amount), 0))
        .filter(Expense.expense_date >= m_start, Expense.expense_date < m_end))

    s = get_settings()
    target = float(s.planning_sales_normal or 0)
    achievement = (month_sales / target) if target else 0
    days_in_month = (m_end - m_start).days or 1
    elapsed = (now - m_start).days + 1
    run_rate = month_sales / elapsed if elapsed else 0
    forecast = run_rate * days_in_month

    bank_in = _sum(
        db.session.query(per(func.sum(CashTxn.bank_upi_in), 0))
        .filter(CashTxn.date >= m_start, CashTxn.date < m_end))
    bank_out = _sum(
        db.session.query(per(func.sum(CashTxn.bank_upi_out), 0))
        .filter(CashTxn.date >= m_start, CashTxn.date < m_end))
    owner_cash_savings_mtd = bank_in - bank_out
    pos = cash_position()

    # Only loans that are actually due (or overdue) inside the alert window.
    # Once a paid loan rolls forward it is unpaid again, so counting every
    # unpaid loan would show all of them forever.
    alert_days = int(s.emi_alert_days or 5)
    emi_due = EmiTracker.query.filter(
        EmiTracker.is_paid.is_(False),
        EmiTracker.next_due_date.isnot(None),
        EmiTracker.next_due_date <= now + timedelta(days=alert_days)).count()
    dead_stock = 0
    slow_stock = 0
    for r in stock_summary_rows():
        if r.get("health") == "DEAD":
            dead_stock += 1
        elif r.get("health") == "SLOW":
            slow_stock += 1

    over_budget = 0
    for b in MonthlyBudget.query.all():
        if (b.actual or 0) > (b.total_budget or 0):
            over_budget += 1

    quality_issues = data_quality_checks_count()

    return {
        "today": now,
        "today_sales": today_sales,
        "month_sales": month_sales,
        "target": target,
        "achievement": achievement,
        "forecast": forecast,
        "gp_mtd": gp_mtd,
        "expenses_mtd": exp_mtd,
        "owner_cash_savings_mtd": owner_cash_savings_mtd,
        "target_gap": max(target - month_sales, 0),
        "restock_pct": float(s.restock_pct or 0),
        "major_restock_pct": float(s.major_restock_pct or 0),
        "owner_cash_target_pct": float(s.owner_cash_target_pct or 0),
        "emi_due": emi_due,
        "dead_stock": dead_stock,
        "slow_stock": slow_stock,
        "over_budget": over_budget,
        "quality_issues": quality_issues,
        "cash_in_hand": pos["cash"],
        "bank_balance": pos["bank"],
        "liquid_total": pos["total"],
        "opening_done": bool(s.opening_done),
    }


def projection_summary() -> dict:
    """Next-month + 12-month forecast (dashboard panel + reports/target)."""
    s = get_settings()
    normal = float(s.planning_sales_normal or 0)
    good = float(s.planning_sales_good or 0)
    next_month_forecast = max(normal, good * 0.85)
    twelve_month_total = normal * 12
    return {
        "next_month_forecast": next_month_forecast,
        "twelve_month_total": twelve_month_total,
    }


# ----------------------------------------------------------------------------
# budgets
# ----------------------------------------------------------------------------

def budget_data() -> list:
    """Rows for the budgets index (one per monthly budget).

    Returns raw numbers - the view layer applies money formatting.
    """
    out = []
    for b in MonthlyBudget.query.order_by(MonthlyBudget.month.desc()).all():
        total = float(b.total_budget or 0)
        actual = float(b.actual or 0)
        out.append({
            "id": b.id,
            "month": b.month,
            "budgets": [float(getattr(b, f) or 0) for f, _ in BUDGET_CATEGORY_FIELDS],
            "total": total,
            "actual": actual,
            "remaining": total - actual,
            "status": "OVER BUDGET" if actual > total else "OK",
        })
    return out


def budget_category_block(month: date) -> dict:
    """Budget block for a single month (sidebar block)."""
    start = month_start(month)
    b = MonthlyBudget.query.filter_by(month=start).first()
    if not b:
        return {
            "month": start,
            "rows": [],
            "total_budget": 0,
            "total_actual": 0,
            "total_remaining": 0,
            "status": "No budget set",
        }
    rows = []
    for fname, label in BUDGET_CATEGORY_FIELDS:
        bval = float(getattr(b, fname) or 0)
        rows.append({"label": label, "budgeted": bval})
    total_budget = float(b.total_budget or 0)
    actual = float(b.actual or 0)
    return {
        "id": b.id,
        "month": start,
        "rows": rows,
        "total_budget": total_budget,
        "total_actual": actual,
        "total_remaining": total_budget - actual,
        "status": "OVER BUDGET" if actual > total_budget else "OK",
    }


# ----------------------------------------------------------------------------
# cash
# ----------------------------------------------------------------------------

def cash_ledger_rows() -> dict:
    """Running cash + bank/UPI ledger with starting balances."""
    s = get_settings()
    starting_cash = float(s.starting_cash or 0)
    starting_bank = float(s.starting_bank_upi or 0)
    txn_list = CashTxn.query.order_by(CashTxn.date, CashTxn.id).all()
    rows = []
    cash = starting_cash
    bank = starting_bank
    for t in txn_list:
        cash += float(t.cash_in or 0) - float(t.cash_out or 0)
        bank += float(t.bank_upi_in or 0) - float(t.bank_upi_out or 0)
        rows.append({
            "id": t.id,
            "date": t.date,
            "txn_id": t.txn_id,
            "type": t.txn_type or "",
            "description": t.description or "",
            "cash_in": float(t.cash_in or 0),
            "cash_out": float(t.cash_out or 0),
            "bank_in": float(t.bank_upi_in or 0),
            "bank_out": float(t.bank_upi_out or 0),
            "running_cash": cash,
            "running_bank": bank,
            "running_total": cash + bank,
        })
    return {"starting_cash": starting_cash, "starting_bank": starting_bank,
            "rows": rows}


# ----------------------------------------------------------------------------
# cash auto-posting
#
# The single most important rule in this app: a sale, an expense, an EMI
# payment or a supplier payment is recorded ONCE, in its own screen, and
# the cash ledger updates itself. Entering the same money twice - once in
# the real screen and once again by hand in Cash - is exactly how the books
# drift, so the Cash screen is reserved for the handful of movements that
# have no other home (owner money in/out, transfers, refunds, other income).
# ----------------------------------------------------------------------------

def cash_position() -> dict:
    """Cash on hand / bank today: opening balances + everything since."""
    s = get_settings()
    cash = float(s.starting_cash or 0)
    bank = float(s.starting_bank_upi or 0)
    agg = db.session.query(
        func.coalesce(func.sum(CashTxn.cash_in), 0),
        func.coalesce(func.sum(CashTxn.cash_out), 0),
        func.coalesce(func.sum(CashTxn.bank_upi_in), 0),
        func.coalesce(func.sum(CashTxn.bank_upi_out), 0)).one()
    cash += float(agg[0] or 0) - float(agg[1] or 0)
    bank += float(agg[2] or 0) - float(agg[3] or 0)
    return {"cash": cash, "bank": bank, "total": cash + bank,
            "starting_cash": float(s.starting_cash or 0),
            "starting_bank": float(s.starting_bank_upi or 0)}


def post_cash(source_type: str, source_id, txn_date: date, txn_type: str,
              description: str, amount, payment_method: str,
              direction: str, reference: str = None) -> "CashTxn | None":
    """Write one cash-ledger line for a business record.

    direction is "in" or "out". The payment method decides the bucket:
    Cash goes to the drawer, UPI/Card to the bank. Credit/Outstanding and
    Other move no money, so nothing is written. A zero amount writes
    nothing. Nothing here commits - the caller commits once, so the source
    record and its cash line can never disagree.
    """
    amount = float(amount or 0)
    if amount <= 0:
        return None
    if payment_method == "Cash":
        in_key, out_key = "cash_in", "cash_out"
    elif payment_method in ("UPI", "Card"):
        in_key, out_key = "bank_upi_in", "bank_upi_out"
    else:
        return None

    txn = CashTxn()
    txn.date = txn_date or date.today()
    txn.txn_id = next_id(db.session.query(CashTxn.txn_id), "TXN", 5)
    txn.txn_type = txn_type
    txn.description = description
    setattr(txn, in_key if direction == "in" else out_key, amount)
    txn.reference = reference
    txn.source_type = source_type
    txn.source_id = source_id
    db.session.add(txn)
    return txn


def unpost_cash(source_type: str, source_id) -> None:
    """Drop the cash lines generated for a record (all of them).

    Used when a record is edited or deleted so the ledger follows it
    instead of keeping a stale amount. Installment payments against one
    purchase batch share a source, so this always removes the full set.
    """
    if source_id is None:
        return
    CashTxn.query.filter(CashTxn.source_type == source_type,
                         CashTxn.source_id == source_id).delete(
        synchronize_session=False)


def record_cash_for(source_type: str, source_id, txn_date, txn_type,
                    description, amount, payment_method, direction,
                    reference: str = None):
    """Replace whatever was posted before with the current values."""
    unpost_cash(source_type, source_id)
    return post_cash(source_type, source_id, txn_date, txn_type, description,
                     amount, payment_method, direction, reference)


def save_reconcile(counted_cash, counted_bank) -> dict:
    """Store a physical cash count and return it with the differences.

    A non-zero difference is the point of the whole exercise: it is where
    a missing record, a wrong amount or a hand in the till shows up.
    """
    pos = cash_position()
    counted_cash = to_float(counted_cash)
    counted_bank = to_float(counted_bank)
    entry = {
        "date": date.today().isoformat(),
        "counted_cash": counted_cash,
        "counted_bank": counted_bank,
        "system_cash": round(pos["cash"], 2),
        "system_bank": round(pos["bank"], 2),
        "diff_cash": round(counted_cash - pos["cash"], 2),
        "diff_bank": round(counted_bank - pos["bank"], 2),
    }
    s = get_settings()
    s.last_reconcile = json.dumps(entry)
    db.session.commit()
    return entry


def last_reconcile() -> dict:
    """The stored cash count, or an empty dict if never counted."""
    raw = get_settings().last_reconcile
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


# ----------------------------------------------------------------------------
# reports — stock summary
# ----------------------------------------------------------------------------

def _coverage_flag(health: str) -> str:
    return {
        "CRITICAL": "CRITICAL",
        "LOW": "LOW",
        "SLOW": "Slow",
        "OK": "OK",
    }.get(health, health or "")


def stock_summary_rows() -> list:
    """Per-item inventory summary.

    An Item row IS one physical garment (PurchaseBatch.processed_qty counts
    rows, models.py), so there is no running quantity to track - an item is
    either on the rail or sold. Per-item *coverage* is therefore meaningless
    (one unit / tiny velocity = "0 days"), so health is driven by how long
    the piece has been sitting unsold. Category-level coverage - the number
    that actually means something - lives in restock_intelligence_rows().
    """
    today = date.today()
    s = get_settings()
    slow_days = float(s.slow_moving_days or 60)
    dead_days = float(s.dead_stock_days or 90)

    rows = []
    for i in Item.query.options(joinedload(Item.sales)).order_by(Item.item_id).all():
        sold = [x for x in i.sales if not x.is_returned]
        units_sold = sum((x.qty or 0) for x in sold)
        sales_value = sum(float(x.final_value or 0) for x in sold)
        gp = sum(float(x.gross_profit or 0) for x in sold)
        is_sold = i.current_status == "Sold"
        # Damaged / Lost / Returned / Reserved are all off the rail. Only
        # "Sold" used to be excluded, so a written-off piece kept its full
        # cost in stock value and was counted as ready stock to re-buy.
        in_stock = 1 if i.is_on_rail else 0

        if is_sold and i.date_sold:
            days_held = (i.date_sold - i.purchase_date).days if i.purchase_date else None
        elif i.purchase_date:
            days_held = (today - i.purchase_date).days
        else:
            days_held = None

        if is_sold:
            health = "SOLD"
        elif i.current_status in ("Damaged", "Lost"):
            # aged by the write-off date when there is one, so the report
            # shows how long the money was tied up before it was written off
            health = "WRITTEN OFF"
        elif not i.is_on_rail:
            health = "OFF RAIL"
        elif days_held is None:
            health = "UNKNOWN"
        elif days_held >= dead_days:
            health = "DEAD"
        elif days_held >= slow_days:
            health = "SLOW"
        else:
            health = "OK"

        rows.append({
            "item": i,
            "category": i.category or "",
            "days_held": days_held,
            "units_sold": units_sold,
            "sales_value": sales_value,
            "gross_profit": gp,
            "in_stock": in_stock,
            "is_sold": is_sold,
            "stock_value": in_stock * float(i.allocated_cost or 0),
            "written_off": i.is_written_off,
            "written_off_value": (0 if in_stock
                                  else float(i.allocated_cost or 0)),
            "health": health,
        })
    return rows


# ----------------------------------------------------------------------------
# reports — sales analysis
# ----------------------------------------------------------------------------

def sales_analysis_data() -> dict:
    """Category + monthly matrix of sales / gross profit."""
    categories = []
    cat_totals = {}
    for sale in Sale.query.options(joinedload(Sale.item)).all():
        if sale.is_returned:
            continue
        cat = (sale.item.category if sale.item else None) or "Other Clothing"
        if cat not in cat_totals:
            cat_totals[cat] = {"units": 0, "sales": 0.0, "gp": 0.0}
        cat_totals[cat]["units"] += sale.qty or 0
        cat_totals[cat]["sales"] += float(sale.final_value or 0)
        cat_totals[cat]["gp"] += float(sale.gross_profit or 0)
    total_sales = sum(c["sales"] for c in cat_totals.values()) or 1
    for cat, t in sorted(cat_totals.items(), key=lambda kv: -kv[1]["sales"]):
        categories.append({
            "category": cat,
            "units": t["units"],
            "sales": t["sales"],
            "gp": t["gp"],
            "margin": (t["gp"] / t["sales"]) if t["sales"] else 0,
            "share": (t["sales"] / total_sales) if total_sales else 0,
        })

    # month x category matrix - one grouped query instead of 12 * len(categories)
    months = month_series(date.today(), back=12, fwd=0)[-12:]
    matrix = []
    if cat_totals:
        first = month_start(months[0])
        last = month_end(months[-1]) + timedelta(days=1)
        grid = {}
        q = (db.session.query(Item.category, Sale.sale_date, func.sum(SALE_NET))
             .join(Sale, Sale.item_id == Item.id)
             .filter(Sale.sale_date >= first, Sale.sale_date < last,
                     Sale.is_returned.is_(False))
             .group_by(Item.category, Sale.sale_date))
        for cat, sdate, total in q:
            key = (cat or "Other Clothing", month_start(sdate))
            grid[key] = grid.get(key, 0.0) + float(total or 0)
        for m in months:
            ms = month_start(m)
            matrix.append({
                "month": ms,
                "cats": {c: grid.get((c, ms), 0.0) for c in cat_totals},
            })
    else:
        for m in months:
            matrix.append({"month": month_start(m), "cats": {}})

    return {"categories": categories, "matrix": matrix,
            "cat_names": list(cat_totals.keys()),
            "this_month": current_month_sales()}


def current_month_sales() -> dict:
    """This month's sales, gross profit and units.

    Without this the page leads with all-time totals, which read as zero (or
    as a lifetime number) on the first day of use and tell you nothing about
    how this month is going.
    """
    now = date.today()
    m_start, m_end = _month_bounds(now)
    filt = [Sale.sale_date >= m_start, Sale.sale_date < m_end,
            Sale.is_returned.is_(False)]
    sales = _sum(
        db.session.query(func.coalesce(func.sum(SALE_NET), 0))
        .filter(*filt))
    units = _sum(
        db.session.query(func.coalesce(func.sum(SALE_QTY), 0)).filter(*filt))
    gp = _sum(
        db.session.query(func.coalesce(func.sum(SALE_GP), 0))
        .join(Item, Sale.item_id == Item.id).filter(*filt))
    return {"month": m_start, "sales": sales, "units": units, "gp": gp,
            "margin": (gp / sales) if sales else 0}


# ----------------------------------------------------------------------------
# reports — target / projection
# ----------------------------------------------------------------------------

def target_projection_rows() -> list:
    """Month-by-month target / projection rows for reports/target."""
    s = get_settings()
    target = float(s.planning_sales_normal or 0)
    today = date.today()
    out = []
    for m in month_series(today, back=12, fwd=0)[-12:]:
        m_start = month_start(m)
        m_end = month_end(m)
        days_in_month = (m_end - m_start).days + 1
        days_elapsed = max((today - m_start).days + 1, 0)
        if m_start > today:
            days_elapsed = 0
        days_remaining = max(days_in_month - days_elapsed, 0)
        actual = _sum(
            db.session.query(func.coalesce(func.sum(SALE_NET), 0))
            .filter(Sale.sale_date >= m_start, Sale.sale_date <= m_end,
                    Sale.is_returned.is_(False)))
        achievement = (actual / target) if target else 0
        remaining = max(target - actual, 0)
        required_daily = (remaining / days_remaining) if days_remaining else 0
        run_rate = (actual / days_elapsed) if days_elapsed else 0
        forecast = run_rate * days_in_month if run_rate else 0
        status = "ON TRACK" if achievement >= 0.9 else (
            "NEAR" if achievement >= 0.7 else "BEHIND")
        out.append({
            "month": m_start,
            "target": target,
            "actual": actual,
            "achievement": achievement,
            "remaining": remaining,
            "days_in_month": days_in_month,
            "days_elapsed": days_elapsed,
            "required_daily": required_daily,
            "run_rate": run_rate,
            "forecast": forecast,
            "status": status,
        })
    return out


# ----------------------------------------------------------------------------
# reports — restock intelligence
# ----------------------------------------------------------------------------

def restock_intelligence_rows() -> list:
    """Category-level restock priority and suggested fund.

    Coverage is only meaningful once units are aggregated (one garment is one
    unit), so this compares in-stock units per category against 90-day sales
    velocity. Two grouped queries instead of a per-item Python loop.
    """
    s = get_settings()
    normal_target = float(s.planning_sales_normal or 0)
    today = date.today()
    cutoff = today - timedelta(days=90)

    crit = float(s.critical_coverage_days or 14)
    low = float(s.low_coverage_days or 30)
    slow = float(s.slow_moving_days or 60)

    in_stock, avg_cost = {}, {}
    # count only what can actually be sold, or the restock advice tells the
    # owner to re-buy pieces that are damaged, lost, returned or reserved
    on_rail = [s for s in ITEM_STATUSES if s not in OFF_RAIL_STATUSES]
    for cat, cnt, ac in (db.session.query(Item.category, func.count(Item.id),
                                          func.avg(Item.allocated_cost))
                         .filter(Item.current_status.in_(on_rail))
                         .group_by(Item.category)):
        key = cat or "Other Clothing"
        in_stock[key] = in_stock.get(key, 0) + int(cnt or 0)
        if ac:
            avg_cost[key] = float(ac)

    sold90 = {}
    q = (db.session.query(Item.category,
                          func.coalesce(func.sum(Sale.qty), 0),
                          func.coalesce(func.sum(SALE_NET), 0),
                          func.coalesce(func.sum(SALE_GP), 0))
         .join(Sale, Sale.item_id == Item.id)
         .filter(Sale.sale_date >= cutoff, Sale.sale_date <= today,
                 Sale.is_returned.is_(False))
         .group_by(Item.category))
    for cat, units, sales, gp in q:
        key = cat or "Other Clothing"
        e = sold90.setdefault(key, {"units": 0, "sales": 0.0, "gp": 0.0})
        e["units"] += int(units or 0)
        e["sales"] += float(sales or 0)
        e["gp"] += float(gp or 0)

    daily_target = (normal_target / 30.0) if normal_target else 0
    out = []
    for cat in set(in_stock) | set(sold90):
        ready = in_stock.get(cat, 0)
        s90 = sold90.get(cat, {"units": 0, "sales": 0.0, "gp": 0.0})
        daily = s90["units"] / 90.0
        coverage = (ready / daily) if daily > 0 else None

        if coverage is None:
            score = 0.0
        elif coverage <= crit:
            score = 5.0
        elif coverage <= low:
            score = 3.0
        elif coverage <= slow:
            score = 1.0
        else:
            score = 0.0

        priority = ("CRITICAL" if score >= 5 else "HIGH" if score >= 3
                    else "MEDIUM" if score >= 1 else "OK")

        suggested_fund = 0.0
        if score >= 3 and daily_target:
            gap_units = max(daily_target * crit - ready, 0)
            suggested_fund = gap_units * avg_cost.get(cat, 0)

        out.append({
            "category": cat,
            "sale_ready_units": ready,
            "units_sold_90d": s90["units"],
            "sales_90d": s90["sales"],
            "gp_90d": s90["gp"],
            "velocity": daily,
            "coverage": coverage,
            "score": score,
            "priority": priority,
            "avg_cost": avg_cost.get(cat, 0.0),
            "suggested_fund": suggested_fund,
        })
    # What share of the available restock money each category is asking for.
    # Only meaningful across the categories that asked for something.
    total_fund = sum(r["suggested_fund"] for r in out)
    for r in out:
        r["allocation_pct"] = (r["suggested_fund"] / total_fund) if total_fund else 0.0
    out.sort(key=lambda r: (-r["score"], -(r["coverage"] or 9999)))
    return out


# ----------------------------------------------------------------------------
# reports — profit / cash
# ----------------------------------------------------------------------------

def _last_months(n: int) -> list:
    return month_series(date.today(), back=n, fwd=0)[-n:]


def profit_cash_rows() -> list:
    """Monthly P&L with reserve allocation (reports/profit)."""
    today = date.today()
    out = []
    for m in _last_months(12):
        m_start = month_start(m)
        m_end = month_end(m)
        net_sales = _sum(
            db.session.query(func.coalesce(func.sum(SALE_NET), 0))
            .filter(Sale.sale_date >= m_start, Sale.sale_date <= m_end,
                    Sale.is_returned.is_(False)))
        cogs = _sum(
            db.session.query(func.coalesce(func.sum(SALE_COST), 0))
            .join(Item, Sale.item_id == Item.id)
            .filter(Sale.sale_date >= m_start, Sale.sale_date <= m_end,
                    Sale.is_returned.is_(False)))
        gross_profit = net_sales - cogs
        expenses = _sum(
            db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0))
            .filter(Expense.expense_date >= m_start, Expense.expense_date <= m_end))
        s = get_settings()
        emi_paid = _sum(
            db.session.query(db.func.coalesce(db.func.sum(CashTxn.bank_upi_out), 0) +
                             db.func.coalesce(db.func.sum(CashTxn.cash_out), 0))
            .filter(CashTxn.date >= m_start, CashTxn.date <= m_end,
                    CashTxn.txn_type == "EMI"))
        restock_pct = float(s.restock_pct or 0)
        major_pct = float(s.major_restock_pct or 0)
        restock_reserve = gross_profit * restock_pct
        major_reserve = gross_profit * major_pct
        withdrawals = _sum(
            db.session.query(db.func.coalesce(db.func.sum(CashTxn.cash_out), 0))
            .filter(CashTxn.date >= m_start, CashTxn.date <= m_end,
                    CashTxn.txn_type == "Owner Withdrawal"))
        op_profit = gross_profit - expenses - withdrawals
        owner_cash_savings = max(op_profit - restock_reserve - major_reserve, 0)
        out.append({
            "month": m_start,
            "net_sales": net_sales,
            "cogs": cogs,
            "gross_profit": gross_profit,
            "expenses": expenses,
            "op_profit": op_profit,
            "emi_paid": emi_paid,
            "restock_reserve": restock_reserve,
            "major_reserve": major_reserve,
            "withdrawals": withdrawals,
            "owner_cash_savings": owner_cash_savings,
        })
    return out


# ----------------------------------------------------------------------------
# reports — major restock reserve
# ----------------------------------------------------------------------------

def major_restock_rows() -> list:
    """Cumulative reserve vs spending for big purchases (reports/major-restock)."""
    s = get_settings()
    reserve_pct = float(s.major_restock_pct or 0)
    out = []
    running = 0.0
    for m in _last_months(12):
        m_start = month_start(m)
        m_end = month_end(m)
        gp = _sum(
            db.session.query(func.coalesce(func.sum(SALE_GP), 0))
            .join(Item, Sale.item_id == Item.id)
            .filter(Sale.sale_date >= m_start, Sale.sale_date <= m_end,
                    Sale.is_returned.is_(False)))
        reserve_target = gp * reserve_pct
        spent = _sum(
            db.session.query(db.func.coalesce(db.func.sum(CashTxn.cash_out), 0) +
                             db.func.coalesce(db.func.sum(CashTxn.bank_upi_out), 0))
            .filter(CashTxn.date >= m_start, CashTxn.date <= m_end,
                    CashTxn.txn_type == "Major Reserve Spend"))
        actual_reserved = reserve_target
        available = running + reserve_target - spent
        running = max(available, 0)
        status = "OK" if available >= 0 else "OVERDRAWN"
        out.append({
            "month": m_start,
            "reserve_target": reserve_target,
            "actual_reserved": actual_reserved,
            "spent": spent,
            "available": available,
            "status": status,
        })
    return out


# ----------------------------------------------------------------------------
# reports — data quality
# ----------------------------------------------------------------------------

def _blank(col):
    return or_(col.is_(None), col == "")


def _quality_checks() -> list:
    """Data integrity checks. Each one must be able to fire - an earlier
    version tested Sale.item_id for NULL on a NOT NULL column and a python
    property for NULL, so both silently reported zero forever."""
    checks = []

    def add(severity, label, n, fix=None):
        if n:
            checks.append({"severity": severity, "label": label,
                           "count": n, "fix": fix})

    add("High", "Items with no category",
        Item.query.filter(_blank(Item.category)).count(),
        "reports/restock")
    add("Medium", "Items without allocated cost",
        Item.query.filter(Item.allocated_cost.is_(None),
                          Item.allocated_cost == 0).count())
    add("Medium", "Items without listed price",
        Item.query.filter(Item.listed_price.is_(None),
                          Item.listed_price == 0).count())
    add("Low", "Items not linked to a purchase batch",
        Item.query.filter(Item.purchase_batch_id.is_(None)).count())

    # a sale whose item has no cost silently reports the wrong gross profit
    n = (db.session.query(func.count(Sale.id))
         .join(Item, Sale.item_id == Item.id)
         .filter(Sale.is_returned.is_(False),
                 or_(Item.allocated_cost.is_(None), Item.allocated_cost == 0))
         .scalar())
    add("High", "Sales on items with no cost (profit will be wrong)", int(n or 0))

    # stock says sold but no sale was ever recorded
    n = (db.session.query(func.count(Item.id))
         .outerjoin(Sale, Sale.item_id == Item.id)
         .filter(Item.current_status == "Sold", Sale.id.is_(None))
         .scalar())
    add("High", "Items marked Sold but have no sale record", int(n or 0),
        "items")

    add("Medium", "Batches without total cost",
        PurchaseBatch.query.filter(PurchaseBatch.total_cost.is_(None),
                                   PurchaseBatch.total_cost == 0).count())
    add("Low", "Expenses without category",
        Expense.query.filter(_blank(Expense.category)).count())

    n = int(db.session.query(func.count(Sale.id))
            .filter(Sale.is_returned.is_(True)).scalar() or 0)
    if n:
        checks.append({"severity": "Low",
                       "label": "Returned sales on record (restock these)",
                       "count": n, "fix": "items"})

    order = {"High": 0, "Medium": 1, "Low": 2}
    checks.sort(key=lambda c: (order.get(c["severity"], 9), -c["count"]))
    return checks


def data_quality_checks_count() -> int:
    return sum(c["count"] for c in _quality_checks())


def data_quality_checks() -> list:
    return _quality_checks()
