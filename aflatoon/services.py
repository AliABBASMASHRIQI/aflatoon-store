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

from aflatoon.extensions import db
from aflatoon.helpers import (month_start, month_end, month_series, num,
                              money, money2, pct, datefmt, to_float, to_int,
                              to_date, next_id)
from aflatoon.models import (Settings, PurchaseBatch, Supplier, Item, Sale,
                             MonthlyBudget, BUDGET_CATEGORY_FIELDS,
                             BUDGET_CATEGORY_FIELDS_LABELS, CATEGORIES,
                             ITEM_STATUSES, SUPPLIER_TYPES, PAYMENT_METHODS,
                             ITEM_TYPES, EXPENSE_CATEGORIES, EXPENSE_NATURES,
                             ADJUSTMENT_TYPES, ITEM_STATUSES1, Sale,
                             CashTxn, Expense, EmiTracker, MonthlyBudget,
                             Settings)

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
        s.restock_pct = Decimal("0")
        s.major_restock_pct = Decimal("0")
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

def _month_start_end(ref: date) -> tuple:
    return month_start(ref), month_end(ref)


def _sum(q) -> float:
    """Execute a (coalesce-sum) query and return as float."""
    v = q.scalar()
    return float(v or 0)


def dashboard_data() -> dict:
    """Everything the dashboard.html page shows (see d.* context)."""
    now = date.today()
    m_start, m_end = _month_start_end(now)
    per = db.func.coalesce

    today_sales = _sum(
        db.session.query(per(db.func.sum(Sale.final_value), 0))
        .filter(Sale.sale_date == now, Sale.is_returned.is_(False)))
    month_sales = _sum(
        db.session.query(per(db.func.sum(Sale.final_value), 0))
        .filter(Sale.sale_date >= m_start, Sale.sale_date < m_end,
                Sale.is_returned.is_(False)))
    gp_mtd = _sum(
        db.session.query(per(db.func.sum(Sale.gross_profit), 0))
        .filter(Sale.sale_date >= m_start, Sale.sale_date < m_end,
                Sale.is_returned.is_(False)))
    exp_mtd = _sum(
        db.session.query(per(db.func.sum(Expense.amount), 0))
        .filter(Expense.expense_date >= m_start, Expense.expense_date < m_end))

    s = get_settings()
    target = float(s.planning_sales_normal or 0)
    achievement = (month_sales / target) if target else 0
    days_in_month = (m_end - m_start).days or 1
    elapsed = (now - m_start).days + 1
    run_rate = month_sales / elapsed if elapsed else 0
    forecast = run_rate * days_in_month

    bank_in = _sum(
        db.session.query(per(db.func.sum(CashTxn.bank_upi_in), 0))
        .filter(CashTxn.date >= m_start, CashTxn.date < m_end))
    bank_out = _sum(
        db.session.query(per(db.func.sum(CashTxn.bank_upi_out), 0))
        .filter(CashTxn.date >= m_start, CashTxn.date < m_end))
    owner_cash_savings_mtd = bank_in - bank_out

    emi_due = EmiTracker.query.filter(
        EmiTracker.is_paid.is_(False),
        EmiTracker.next_due_date.isnot(None)).count()
    critical_stock = 0
    low_stock = 0
    for r in stock_summary_rows():
        if r.get("health") in ("CRITICAL",):
            critical_stock += 1
        elif r.get("health") in ("LOW",):
            low_stock += 1

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
        "critical_stock": critical_stock,
        "low_stock": low_stock,
        "over_budget": over_budget,
        "quality_issues": quality_issues,
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
    """Rows for the budgets index (one per monthly budget)."""
    out = []
    for b in MonthlyBudget.query.order_by(MonthlyBudget.month.desc()).all():
        cells = [b.month.strftime("%b %Y")]
        for fname, _label in BUDGET_CATEGORY_FIELDS:
            cells.append(float(getattr(b, fname) or 0))
        total = float(b.total_budget or 0)
        actual = float(b.actual or 0)
        cells += [total, actual, total - actual,
                  "OVER BUDGET" if actual > total else "OK"]
        out.append({
            "id": b.id,
            "cells": cells,
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
    """Per-item inventory summary with velocity / coverage / health."""

    from aflatoon.models import Item as _Item
    rows = []
    for i in _Item.query.order_by(_Item.item_id).all():
        ready_qty = max((i.current_qty or 0), 0)
        sold = [s for s in i.sales if not s.is_returned]
        units_sold = sum((s.qty or 0) for s in sold)
        sales_value = sum(float(s.final_value or 0) for s in sold)
        days_in_stock = None
        if i.purchase_date:
            days_in_stock = (date.today() - i.purchase_date).days
        days_sold = (i.date_sold - i.purchase_date).days if i.date_sold and i.purchase_date else None
        velocity = (units_sold / days_in_stock) if days_in_stock else 0
        current_qty = ready_qty - sum((s.qty or 0) for s in sold)
        stock_value = current_qty * float(i.allocated_cost or 0)
        coverage = None
        if velocity and current_qty is not None:
            coverage = current_qty / velocity
        health = "CRITICAL"
        if current_qty <= 0:
            health = "OUT OF STOCK"
        elif coverage is not None:
            s = get_settings()
            if coverage <= float(s.critical_coverage_days or 14):
                health = "CRITICAL"
            elif coverage <= float(s.low_coverage_days or 30):
                health = "LOW"
            elif coverage <= float(s.slow_moving_days or 60):
                health = "SLOW"
            else:
                health = "OK"
        rows.append({
            "item": i,
            "category": i.category or "",
            "days_in_stock": days_in_stock,
            "units_sold": units_sold,
            "sales_value": sales_value,
            "current_qty": current_qty,
            "stock_value": stock_value,
            "velocity": velocity,
            "coverage": coverage,
            "health": health,
        })
    return rows


# ----------------------------------------------------------------------------
# reports — sales analysis
# ----------------------------------------------------------------------------

def sales_analysis_data() -> dict:
    """Category + monthly matrix of sales / gross profit."""
    from aflatoon.models import Sale as _Sale, Item as _Item
    categories = []
    cat_totals = {}
    for sale in _Sale.query.options(db.jointload(_Sale.item)).all():
        if sale.is_returned:
            continue
        cat = (sale.item.category if sale.item else None) or "Other Clothing"
        if cat not in cat_totals:
            cat_totals[cat] = {"units": 0, "sales": 0.0, "gp": 0.0}
        cat_totals[cat]["units"] += sale.qty or 0
        cat_totals[cat]["sales"] += float(sale.final_value or 0)
        cat_totals[cat]["gp"] += float(sale.gross_profit or 0)
    total_sales = sum(c["sales"] for c in cat_totals.values()) or 1
    for cat, t in sorted(cat_totals.items()):
        categories.append({
            "category": cat,
            "units": t["units"],
            "sales": t["sales"],
            "gp": t["gp"],
            "margin": (t["gp"] / t["sales"]) if t["sales"] else 0,
            "share": (t["sales"] / total_sales) if total_sales else 0,
        })

    # month x category matrix (last 12 months with sales)
    months = month_series(date.today(), back=12, fwd=0)[-12:]
    matrix = []
    for m in months:
        m_start = month_start(m)
        m_end = month_end(m) + timedelta(days=1)
        row = {"month": m_start, "cats": {}}
        for cat in cat_totals.keys():
            v = _sum(
                db.session.query(db.func.coalesce(db.func.sum(_Sale.final_value), 0))
                .join(_Item)
                .filter(_Sale.sale_date >= m_start, _Sale.sale_date < m_end,
                        _Sale.is_returned.is_(False),
                        _Item.category == cat))
            row["cats"][cat] = v
        matrix.append(row)
    return {"categories": categories, "matrix": matrix}


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
            db.session.query(db.func.coalesce(db.func.sum(Sale.final_value), 0))
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
    """Category-level restock priority and suggested fund."""
    s = get_settings()
    normal_target = float(s.planning_sales_normal or 0)
    today = date.today()
    cutoff90 = today - timedelta(days=90)
    out = []
    from aflatoon.models import Item as _Item
    by_cat = {}
    for i in _Item.query.all():
        c = i.category or "Other Clothing"
        by_cat.setdefault(c, []).append(i)
    for cat, items in by_cat.items():
        ready = sum(max((i.current_qty or 0) - (i.qty_sold or 0), 0) for i in items)
        sold_90 = sum(i.qty_sold or 0 for i in items
                      if i.date_sold and i.date_sold >= cutoff90)
        sales_90 = sum(float(i.sales_value or 0) for i in items
                       if i.date_sold and i.date_sold >= cutoff90)
        gp_90 = sum(float(i.gross_profit or 0) for i in items
                    if i.date_sold and i.date_sold >= cutoff90)
        coverage = None
        if sold_90:
            daily = sold_90 / 90.0
            coverage = (ready / daily) if daily else None
        score = 0.0
        if coverage is not None:
            if coverage <= float(s.critical_coverage_days or 14):
                score = 5.0
            elif coverage <= float(s.low_coverage_days or 30):
                score = 3.0
            elif coverage <= float(s.slow_moving_days or 60):
                score = 1.0
        priority = ("CRITICAL" if score >= 5 else
                    "HIGH" if score >= 3 else
                    "MEDIUM" if score >= 1 else "OK")
        suggested_fund = 0.0
        if score >= 3:
            daily_target = (normal_target / 30.0) if normal_target else 0
            suggested_fund = daily_target * max(
                (float(s.critical_coverage_days or 14) - (coverage or 0)), 0)
        out.append({
            "category": cat,
            "sale_ready_units": ready,
            "units_sold_90d": sold_90,
            "sales_90d": sales_90,
            "gp_90d": gp_90,
            "coverage": coverage,
            "score": score,
            "priority": priority,
            "suggested_fund": suggested_fund,
        })
    out.sort(key=lambda r: r["score"], reverse=True)
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
            db.session.query(db.func.coalesce(db.func.sum(Sale.final_value), 0))
            .filter(Sale.sale_date >= m_start, Sale.sale_date <= m_end,
                    Sale.is_returned.is_(False)))
        cogs = _sum(
            db.session.query(db.func.coalesce(db.func.sum(Sale.cost_total), 0))
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
            db.session.query(db.func.coalesce(db.func.sum(Sale.gross_profit), 0))
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

def _quality_checks() -> list:
    checks = []
    from aflatoon.models import (Item as _Item, Sale as _Sale,
                                 PurchaseBatch as _Batch, Expense as _Exp)
    # items missing category
    n = _Item.query.filter(db.or_(_Item.category.is_(None),
                                  _Item.category == "")).count()
    if n:
        checks.append({"severity": "High", "label": "Items with no category",
                       "count": n})
    # items without allocated cost
    n = _Item.query.filter(db.or_(_Item.allocated_cost.is_(None),
                                  _Item.allocated_cost == 0)).count()
    if n:
        checks.append({"severity": "Medium", "label": "Items without allocated cost",
                       "count": n})
    # items without listed price
    n = _Item.query.filter(db.or_(_Item.listed_price.is_(None),
                                  _Item.listed_price == 0)).count()
    if n:
        checks.append({"severity": "Medium", "label": "Items without listed price",
                       "count": n})
    # sales without item link / zero value
    n = _Sale.query.filter(db.or_(_Sale.item_id.is_(None),
                                  _Sale.final_value.is_(None))).count()
    if n:
        checks.append({"severity": "High", "label": "Sales missing item/value",
                       "count": n})
    # returned sales that still count as sold
    n = _Sale.query.filter(_Sale.is_returned.is_(True),
                           _Sale.is_returned.isnot(None)).count()
    if n:
        checks.append({"severity": "Low", "label": "Returned sales on record",
                       "count": n})
    # batches without total cost
    n = _Batch.query.filter(db.or_(_Batch.total_cost.is_(None),
                                   _Batch.total_cost == 0)).count()
    if n:
        checks.append({"severity": "Medium", "label": "Batches without total cost",
                       "count": n})
    # expenses without category
    n = _Exp.query.filter(db.or_(_Exp.category.is_(None), _Exp.category == "")).count()
    if n:
        checks.append({"severity": "Low", "label": "Expenses without category",
                       "count": n})
    return checks


def data_quality_checks_count() -> int:
    return sum(c["count"] for c in _quality_checks())


def data_quality_checks() -> list:
    return _quality_checks()
