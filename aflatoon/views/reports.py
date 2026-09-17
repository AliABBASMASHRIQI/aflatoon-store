from flask import Blueprint, render_template

from aflatoon.helpers import login_required, money, pct, num, datefmt
from aflatoon.services import (stock_summary_rows, sales_analysis_data,
                               target_projection_rows, projection_summary,
                               restock_intelligence_rows, profit_cash_rows,
                               major_restock_rows, data_quality_checks)
from aflatoon.models import CATEGORIES

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _fmt_coverage(c):
    return "-" if c is None else f"{num(c)} days"


@bp.route("/stock")
@login_required
def stock():
    rows = stock_summary_rows()
    headers = ["Item ID", "Category", "Item Type", "Brand", "Size", "Status",
               "Cost", "Listed", "Ready", "Days", "Sold", "Sales",
               "Qty", "Stock Value", "Velocity", "Coverage", "Health"]
    return render_template("report.html",
                           title="Stock Summary",
                           subtitle="Per-item inventory view with health flags",
                           headers=headers,
                           rows=[{
                               "cells": [r["item"].item_id, r["category"] or "-",
                                         r["item"].item_type or "-",
                                         r["item"].brand_name or r["item"].brand_type or "-",
                                         r["item"].size or "-", r["item"].current_status or "-",
                                         money(r["item"].allocated_cost),
                                         money(r["item"].listed_price),
                                         datefmt(r["item"].date_ready),
                                         num(r["days_in_stock"]) if r["days_in_stock"] is not None else "-",
                                         num(r["units_sold"]), money(r["sales_value"]),
                                         num(r["current_qty"]), money(r["stock_value"]),
                                         f"{r['velocity']:.3f}", _fmt_coverage(r["coverage"]),
                                         r["health"]],
                           } for r in rows],
                           kpis=[{"label": "Items tracked", "value": num(len(rows))}],
                           )


@bp.route("/sales-analysis")
@login_required
def sales_analysis():
    d = sales_analysis_data()
    cat_rows = [{
        "cells": [c["category"], num(c["units"]), money(c["sales"]),
                  money(c["gp"]), pct(c["margin"]), pct(c["share"])],
    } for c in d["categories"]]
    matrix_headers = ["Month"] + CATEGORIES
    matrix_rows = [{"cells": [m["month"].strftime("%b %Y")] +
                    [money(m[cat]) for cat in CATEGORIES]} for m in d["matrix"]]
    return render_template("sales_analysis.html",
                           d=d,
                           cat_headers=["Category", "Units", "Sales", "Gross Profit",
                                        "Margin", "Sales Share"],
                           cat_rows=cat_rows,
                           m_headers=matrix_headers,
                           m_rows=matrix_rows)


@bp.route("/target")
@login_required
def target():
    rows = target_projection_rows()
    summary = projection_summary()
    return render_template("report.html",
                           title="Target & Projection",
                           subtitle="Monthly sales targets, run-rate and month-end forecast",
                           kpis=[{"label": "Next Month Forecast",
                                  "value": money(summary["next_month_forecast"])},
                                 {"label": "12-Month Forecast",
                                  "value": money(summary["twelve_month_total"])}],
                           headers=["Month", "Target", "Actual", "Achievement",
                                    "Remaining", "Days Left", "Required/Day",
                                    "Run Rate", "Forecast", "Status"],
                           rows=[{
                               "cells": [r["month"].strftime("%b %Y"),
                                         money(r["target"]), money(r["actual"]),
                                         pct(r["achievement"]), money(r["remaining"]),
                                         num(r["days_in_month"] - r["days_elapsed"]),
                                         money(r["required_daily"]),
                                         money(r["run_rate"]), money(r["forecast"]),
                                         r["status"]],
                           } for r in rows])


@bp.route("/restock")
@login_required
def restock():
    rows = restock_intelligence_rows()
    return render_template("report.html",
                           title="Restock Intelligence",
                           subtitle="Category-level restock priority and suggested fund",
                           headers=["Category", "Ready Units", "Sold 90D", "Sales 90D",
                                    "GP 90D", "Coverage", "Priority Score",
                                    "Priority", "Suggested Fund"],
                           rows=[{
                               "cells": [r["category"], num(r["sale_ready_units"]),
                                         num(r["units_sold_90d"]), money(r["sales_90d"]),
                                         money(r["gp_90d"]), _fmt_coverage(r["coverage"]),
                                         f"{r['score']:.1f}", r["priority"],
                                         money(r["suggested_fund"])],
                           } for r in rows])


@bp.route("/profit")
@login_required
def profit():
    rows = profit_cash_rows()
    return render_template("report.html",
                           title="Profit & Cash",
                           subtitle="Monthly profit & loss with reserve allocation",
                           headers=["Month", "Net Sales", "COGS", "Gross Profit",
                                    "Expenses", "Operating Profit", "EMI Paid",
                                    "Restock Reserve", "Major Reserve",
                                    "Withdrawals", "Owner Cash / Savings"],
                           rows=[{
                               "cells": [r["month"].strftime("%b %Y"),
                                         money(r["net_sales"]), money(r["cogs"]),
                                         money(r["gross_profit"]), money(r["expenses"]),
                                         money(r["op_profit"]), money(r["emi_paid"]),
                                         money(r["restock_reserve"]),
                                         money(r["major_reserve"]),
                                         money(r["withdrawals"]),
                                         money(r["owner_cash_savings"])],
                           } for r in rows])


@bp.route("/major-restock")
@login_required
def major_restock():
    rows = major_restock_rows()
    return render_template("report.html",
                           title="Major Restock Reserve",
                           subtitle="Cumulative reserve vs spending for big purchases",
                           headers=["Month", "Reserve Target", "Actual Reserved",
                                    "Spent", "Available Reserve", "Status"],
                           rows=[{
                               "cells": [r["month"].strftime("%b %Y"),
                                         money(r["reserve_target"]),
                                         money(r["actual_reserved"]),
                                         money(r["spent"]), money(r["available"]),
                                         r["status"]],
                           } for r in rows])


@bp.route("/data-quality")
@login_required
def data_quality():
    checks = data_quality_checks()
    return render_template(
        "table.html",
        title="Data Quality",
        headers=["Severity", "Check", "Count"],
        rows=[{"id": i, "cells": [c["severity"], c["label"], num(c["count"])]}
              for i, c in enumerate(checks)],
        total=sum(c["count"] for c in checks),
    )