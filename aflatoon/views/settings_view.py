from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import date

from aflatoon.extensions import db
from aflatoon.helpers import (login_required, to_float, to_date, date_input,
                              money)
from aflatoon.models import Settings, PurchaseBatch, EmiTracker
from aflatoon.services import get_settings, cash_position

bp = Blueprint("settings_view", __name__, url_prefix="/settings")


@bp.route("/opening", methods=["GET", "POST"])
@login_required
def opening():
    """Where the books start.

    Nothing from before today can be imported - the year of trading was
    never written down anywhere. So the honest way to start is to record
    what is true right now, and let the app keep it true from here.
    """
    s = get_settings()
    if request.method == "POST":
        s.starting_cash = to_float(request.form.get("starting_cash"))
        s.starting_bank_upi = to_float(request.form.get("starting_bank_upi"))
        s.opening_payables = to_float(request.form.get("opening_payables"))
        s.opening_date = to_date(request.form.get("opening_date")) or date.today()
        s.opening_done = True
        db.session.commit()
        flash("Opening position saved. Your cash balance is now correct.", "success")
        return redirect(url_for("dashboard.index"))
    return render_template(
        "opening.html", s=s, money=money,
        supplier_owing=sum(float(b.outstanding or 0)
                           for b in PurchaseBatch.query.all()),
        loans=EmiTracker.query.order_by(EmiTracker.next_due_date).all(),
        loans_due=sum(1 for l in EmiTracker.query.all()
                      if l.days_to_due is not None and l.days_to_due <= 5),
        today=date.today().isoformat(),
        position=cash_position())


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
    """Apply the settings form.

    Only fields actually present in the submitted form are written. An earlier
    version rewrote every column from the form, so saving the page silently
    reset the thresholds, alert days and opening balances to 0.
    """
    def has(name):
        return name in form

    def txt(name, default=None):
        return (form.get(name) or "").strip() if has(name) else default

    def num(name, cast, default):
        if not has(name):
            return default
        raw = (form.get(name) or "").strip()
        return cast(raw) if raw else default

    s.store_name = txt("store_name", s.store_name) or "Aflatoon Studio"
    s.store_type = txt("store_type", s.store_type)
    s.currency = txt("currency", s.currency) or "INR"

    # the form sends percentages as whole numbers (10 = 10%)
    for field in ("restock_pct", "major_restock_pct", "owner_cash_target_pct"):
        if has(field):
            raw = (form.get(field) or "").strip()
            setattr(s, field, to_float(raw) / 100.0 if raw else 0)

    for field in ("planning_sales_bad", "planning_sales_normal",
                  "planning_sales_good", "starting_cash", "starting_bank_upi"):
        val = num(field, to_float, getattr(s, field))
        if val is not None:
            setattr(s, field, val)

    for field in ("initial_stock_estimate", "est_new_items_per_month",
                  "critical_coverage_days", "low_coverage_days",
                  "slow_moving_days", "dead_stock_days", "emi_alert_days"):
        val = num(field, lambda v: int(to_float(v)), getattr(s, field))
        if val is not None:
            setattr(s, field, val)

    if has("admin_user"):
        new_user = (form.get("admin_user") or "").strip()
        if new_user:
            s.admin_user = new_user
    new_pass = form.get("admin_password") if has("admin_password") else None
    if new_pass:
        s.set_password(new_pass)
    return s