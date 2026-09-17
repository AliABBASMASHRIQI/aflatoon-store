from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from aflatoon.services import get_settings

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authed"):
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        s = get_settings()
        if username == (s.admin_user or "admin") and s.check_password(password):
            session["authed"] = True
            session["username"] = username
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard.index"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))