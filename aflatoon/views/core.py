"""Static guidance page.

Deliberately plain English and deliberately inside the app: the person using
this every day should never need to ask anyone how it works. Everything here
mirrors the daily/weekly routine the rest of the app expects.
"""

from flask import Blueprint, render_template

from aflatoon.helpers import login_required

bp = Blueprint("core", __name__)


@bp.route("/help")
@login_required
def help_page():
    return render_template("help.html")
