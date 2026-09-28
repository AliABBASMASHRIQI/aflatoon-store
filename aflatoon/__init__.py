from flask import Flask
from sqlalchemy import text

from aflatoon.config import Config
from aflatoon.extensions import db
from aflatoon.helpers import money, money2, pct, datefmt, num


# Tables that existed before this release, and the columns each needs.
# create_all() only creates missing TABLES, it never adds a column to a table
# that already exists, so an older database has to be widened in place.
#
# This list is derived from the models rather than written by hand, so a new
# column can never be forgotten here: a column present in the model but
# missing from the database is added automatically on boot. Existing rows are
# left untouched - this only ever runs ADD COLUMN.
MIGRATE_TABLES = ("settings", "cash_txns", "emi_tracker", "suppliers",
                  "purchase_batches", "items", "sales", "expenses",
                  "stock_adjustments", "monthly_budgets")


def _column_specs():
    """(table, column, sqltype) for every column in the migrated tables."""
    from aflatoon import models
    from sqlalchemy import Integer, Numeric, Boolean, Date, DateTime, String, Text
    types = {Integer: "INTEGER", Numeric: "NUMERIC", Boolean: "BOOLEAN",
             Date: "DATE", DateTime: "DATETIME", Text: "TEXT"}
    out = []
    for name in MIGRATE_TABLES:
        table = getattr(models, {
            "settings": "Settings", "cash_txns": "CashTxn",
            "emi_tracker": "EmiTracker", "suppliers": "Supplier",
            "purchase_batches": "PurchaseBatch", "items": "Item",
            "sales": "Sale", "expenses": "Expense",
            "stock_adjustments": "StockAdjustment",
            "monthly_budgets": "MonthlyBudget"}[name], None)
        if table is None:
            continue
        for col in table.__table__.columns:
            t = col.type
            if isinstance(t, Numeric):
                spec = f"NUMERIC({t.precision or 12}, {t.scale or 2})"
            elif isinstance(t, String):
                spec = f"VARCHAR({t.length or 120})"
            else:
                spec = types.get(type(t), "TEXT")
            out.append((name, col.name, spec))
    return out


def _existing_columns(table):
    """Column names for a table, on SQLite or Postgres."""
    if db.engine.dialect.name == "postgresql":
        rows = db.session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"), {"t": table}).fetchall()
        return {r[0] for r in rows}
    rows = db.session.execute(
        text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def run_migrations(app):
    """Add any column the models expect but the database is missing."""
    for table, column, coltype in _column_specs():
        try:
            have = _existing_columns(table)
        except Exception:
            db.session.rollback()
            continue
        if not have or column in have:
            continue                      # table absent, or nothing to do
        try:
            db.session.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
            db.session.commit()
        except Exception:
            db.session.rollback()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    # Jinja filters
    app.jinja_env.filters["money"] = money
    app.jinja_env.filters["money2"] = money2
    app.jinja_env.filters["pct"] = pct
    app.jinja_env.filters["datefmt"] = datefmt
    app.jinja_env.filters["num"] = num

    from aflatoon.views import auth, core, dashboard, items, batches, suppliers, \
        sales, expenses, cash, adjustments, emi, budgets, reports, settings_view

    for bp in (auth.bp, core.bp, dashboard.bp, items.bp, batches.bp,
               suppliers.bp, sales.bp, expenses.bp, cash.bp, adjustments.bp,
               emi.bp, budgets.bp, reports.bp, settings_view.bp):
        app.register_blueprint(bp)

    @app.context_processor
    def inject_settings():
        from aflatoon.services import get_settings
        from aflatoon.models import Settings
        s = Settings.query.first()
        return {"app_settings": s or get_settings()}

    # create tables + seed settings on first boot.
    # Wrapped so that a database that is briefly unreachable fails the
    # individual request instead of stopping the whole app from booting.
    with app.app_context():
        try:
            db.create_all()
            run_migrations(app)
            from aflatoon.services import get_settings
            s = get_settings()
            if not s.admin_pass_hash:
                s.set_password(app.config["ADMIN_PASSWORD"])
                db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Database bootstrap failed - continuing without tables")

    return app