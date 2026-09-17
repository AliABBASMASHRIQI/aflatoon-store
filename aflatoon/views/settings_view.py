from flask import Blueprint, render_template, request, redirect, url_for, flash

from aflatoon.extensions import db
from aflatoon.helpers import login_required, to_float, money
from aflatoon.models import Settings
from aflatoon.services import get_settings

bp = Blueprint("settings_view", __name__, url_prefix="/settings")


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    s = get_settings()
    if request.method == "POST":
        try:
            resolve(s, request.form)
            db.session.commit()
            flash("Settings saved.", "success")
            return redirect(url_for("settings_view.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {e}", "danger")
    return render_template("settings.html", s=s, money=money)


def resolve(s: Settings, form):
    s.store_name = form.get("store_name") or "Aflatoon Studio"
    s.store_type = form.get("store_type") or ""
    s.currency = form.get("currency") or "INR"
    s.planning_sales_bad = to_float(form.get("planning_sales_bad"))
    s.planning_sales_normal = to_float(form.get("planning_sales_normal"))
    s.planning_sales_good = to_float(form.get("planning_sales_good"))
    s.initial_stock_estimate = int(to_float(form.get("initial_stock_estimate")))
    s.est_new_items_per_month = int(to_float(form.get("est_new_items_per_month")))
    s.restock_pct = to_float(form.get("restock_pct")) / 100
    s.major_restock_pct = to_float(form.get("major_restock_pct")) / 100
    s.owner_cash_target_pct = to_float(form.get("owner_cash_target_pct")) / 100
    s.critical_coverage_days = int(to_float(form.get("critical_coverage_days"), 14))
    s.low_coverage_days = int(to_float(form.get("low_coverage_days"), 30))
    s.slow_moving_days = int(to_float(form.get("slow_moving_days"), 60))
    s.dead_stock_days = int(to_float(form.get("dead_stock_days"), 90))
    s.emi_alert_days = int(to_float(form.get("emi_alert_days"), 5))
    s.starting_cash = to_float(form.get("starting_cash"))
    s.starting_bank_upi = to_float(form.get("starting_bank_upi"))
    new_user = (form.get("admin_user") or "").strip()
    new_pass = form.get("admin_password")
    if new_user:
        s.admin_user = new_user
    if new_pass:
        s.set_password(new_pass)
    return s