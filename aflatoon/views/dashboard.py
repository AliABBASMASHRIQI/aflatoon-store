from flask import Blueprint, render_template

from aflatoon.helpers import login_required
from aflatoon.services import dashboard_data, projection_summary

bp = Blueprint("dashboard", __name__, url_prefix="/")


@bp.route("/")
@login_required
def index():
    data = dashboard_data()
    summary = projection_summary()
    return render_template("dashboard.html", d=data, summary=summary)

# convenience alias: /dashboard
@bp.route("/dashboard")
@login_required
def dashboard_redirect():
    return index()