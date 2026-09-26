from flask import Blueprint, render_template, redirect, url_for

from aflatoon.helpers import login_required
from aflatoon.services import dashboard_data, projection_summary

bp = Blueprint("dashboard", __name__, url_prefix="/")


@bp.route("/")
@login_required
def index():
    data = dashboard_data()
    summary = projection_summary()
    return render_template("dashboard.html", d=data, summary=summary)


@bp.route("/dashboard")
@login_required
def dashboard_redirect():
    return redirect(url_for("dashboard.index"))