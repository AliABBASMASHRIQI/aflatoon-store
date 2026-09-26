from flask import Flask

from aflatoon.config import Config
from aflatoon.extensions import db
from aflatoon.helpers import money, money2, pct, datefmt, num


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

    from aflatoon.views import auth, dashboard, items, batches, suppliers, sales, \
        expenses, cash, adjustments, emi, budgets, reports, settings_view

    for bp in (auth.bp, dashboard.bp, items.bp, batches.bp, suppliers.bp,
               sales.bp, expenses.bp, cash.bp, adjustments.bp, emi.bp,
               budgets.bp, reports.bp, settings_view.bp):
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
            from aflatoon.services import get_settings
            s = get_settings()
            if not s.admin_pass_hash:
                s.set_password(app.config["ADMIN_PASSWORD"])
                db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Database bootstrap failed - continuing without tables")

    return app