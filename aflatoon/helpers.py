import re
from datetime import date, timedelta
from functools import wraps

from dateutil.relativedelta import relativedelta
from flask import session, redirect, url_for, flash


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return first_of_month(d) + relativedelta(months=1)


def end_of_month(d: date) -> date:
    return next_month(d) - timedelta(days=1)


def month_start_end(d: date):
    return first_of_month(d), next_month(d)


def ym_key(d: date) -> tuple:
    return (d.year, d.month)


# Backwards-compatible names (services.py imports these)
def month_start(d: date) -> date:
    return first_of_month(d)


def month_end(d: date) -> date:
    return end_of_month(d)


def month_series(today: date, back: int = 12, fwd: int = 11):
    start = first_of_month(today) - relativedelta(months=back)
    return [start + relativedelta(months=i) for i in range(back + 1 + fwd)]


def next_id(query, prefix: str, pad: int = 5) -> str:
    """Return the next auto-generated id like PREFIX-00001 based on existing rows."""
    nums = []
    for row in query.all():
        value = row[0] if row else None
        if value:
            m = re.match(re.escape(prefix) + r"-(\d+)$", value)
            if m:
                nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    return "{}-{:0{width}d}".format(prefix, nxt, width=pad)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            flash("Please log in to continue.", "info")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def money(value) -> str:
    from flask import current_app
    cur = "₹"
    try:
        return f"{cur}{value:,.0f}"
    except (TypeError, ValueError):
        return f"{cur}0"


def money2(value) -> str:
    from flask import current_app
    cur = "₹"
    try:
        return f"{cur}{value:,.2f}"
    except (TypeError, ValueError):
        return f"{cur}0.00"


def pct(value) -> str:
    try:
        return f"{value * 100:.1f}%"
    except (TypeError, ValueError):
        return "0%"


def datefmt(value) -> str:
    if not value:
        return ""
    try:
        return value.strftime("%d %b %Y")
    except AttributeError:
        return str(value)


def num(value) -> str:
    try:
        return f"{value:,.0f}"
    except (TypeError, ValueError):
        return "0"


def to_float(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def to_bool(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def date_input(v):
    if not v:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    return str(v)