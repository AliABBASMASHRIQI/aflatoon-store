import os

from sqlalchemy.pool import NullPool

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _is_postgres(url: str) -> bool:
    return url.startswith("postgres")


# Sessions are plain http on localhost, where a Secure cookie would simply
# never be sent back and nobody could log in.
_LOCAL = os.environ.get("VERCEL") is None and not _is_postgres(
    os.environ.get("DATABASE_URL") or "")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    DATABASE_URL = os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        DATABASE_URL = "sqlite:///" + os.path.join(BASE_DIR, "aflatoon.db")

    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # NullPool on Postgres, because this is a serverless deployment: each
    # function instance handles one request at a time and must not hold a
    # connection open between them, or Neon's free compute never releases
    # itself and a warm instance can end up talking to a suspended database.
    # A short connect timeout keeps a suspending database from turning a
    # request into a long hang. pre_ping survives a connection that dropped
    # while the database was asleep.
    if _is_postgres(DATABASE_URL):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "poolclass": NullPool,
            "pool_pre_ping": True,
            "connect_args": {"connect_timeout": 10},
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "aflatoon2026")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # over HTTPS the login cookie must never travel in the clear. Off
    # locally, because a Secure cookie is dropped on plain http and he
    # would be unable to log in on the laptop.
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE", "0" if _LOCAL else "1")

    # True on Vercel: the templates can say so, and the app refuses to fall
    # back to a SQLite file that cannot exist there.
    IS_SERVERLESS = bool(os.environ.get("VERCEL"))

    # Opt-in sample trading data, used to show the app with something on
    # screen. Only ever seeded into an empty database, and never by default.
    SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA") == "1"
