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


# Bumped whenever a column is added or changed. A database whose stored
# version matches this skips the whole column-widening pass, which matters
# on serverless: the check is ~143 round trips, microseconds against a
# local SQLite file but seconds over the network on every cold start.
SCHEMA_VERSION = 2


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


def _schema_version() -> int:
    """Version recorded in the settings table, or 0 if not yet stamped."""
    try:
        from aflatoon.models import Settings
        row = db.session.query(Settings.schema_version).first()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        db.session.rollback()
        return 0


def run_migrations(app):
    """Add any column the models expect but the database is missing.

    Skipped entirely once the database records the current SCHEMA_VERSION,
    which is the whole point: on a serverless platform this runs on every
    cold start, and a Postgres round trip per column is far too slow to do
    on the critical path of a request.
    """
    if _schema_version() >= SCHEMA_VERSION:
        return
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
    try:
        from aflatoon.models import Settings
        row = Settings.query.first()
        if row is None:
            # A brand new database has no settings row to stamp yet - it is
            # created by get_settings() further down, after this runs. Create
            # it here so the version is recorded on the very first boot,
            # otherwise every cold start would re-run the whole check.
            from aflatoon.services import get_settings
            row = get_settings()
        row.schema_version = SCHEMA_VERSION
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
        # On a serverless platform the bundle is read-only, so the SQLite
        # fallback cannot be created at all. Failing loudly here is the
        # difference between "DATABASE_URL is not set, see the log" and an
        # app that boots, serves a login page and then 500s everywhere
        # behind it while quietly keeping no records.
        if app.config.get("IS_SERVERLESS") and not app.config.get(
                "DATABASE_URL", "").startswith("postgres"):
            raise RuntimeError(
                "DATABASE_URL is not set to a Postgres URL. On Vercel this "
                "app cannot use a SQLite file (the bundle is read-only), so "
                "it will not start. Set DATABASE_URL in the project's "
                "environment variables.")

        try:
            db.create_all()
            run_migrations(app)
            from aflatoon.services import get_settings
            s = get_settings()
            if not s.admin_pass_hash:
                s.set_password(app.config["ADMIN_PASSWORD"])
            db.session.commit()
            if app.config.get("SEED_DEMO_DATA"):
                from aflatoon.demo_seed import seed_if_empty
                if seed_if_empty():
                    s.demo_seeded = True
                    db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Database bootstrap failed - continuing without tables")

    return app