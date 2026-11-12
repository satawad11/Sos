from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import env

_kw = {"connect_args": {"check_same_thread": False}} if env.database_url.startswith("sqlite") else {"pool_pre_ping": True}
engine = create_engine(env.database_url, **_kw)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns() -> None:
    """Tiny forward-only migration: add columns that exist in the models but not in an already-created table.
    (create_all never alters existing tables.) Only nullable/default-safe columns are supported."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as cx:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                if not col.nullable and col.default is None and col.server_default is None:
                    raise RuntimeError(f"cannot auto-add NOT NULL column {table.name}.{col.name}")
                typ = col.type.compile(dialect=engine.dialect)
                dflt = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    # give existing rows the model default (otherwise they would read back as NULL)
                    lit = col.type.literal_processor(dialect=engine.dialect)
                    if lit is not None:
                        dflt = f" DEFAULT {lit(col.default.arg)}"
                cx.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {typ}{dflt}'))


# ---------------- schema migrations (Alembic, scripts in app/migrations) ----------------
BASELINE = "0001"


def _alembic_cfg(connection):
    import os
    from alembic.config import Config
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def migrate() -> str:
    """Bring the database to the newest schema. Returns what was done: created | adopted | upgraded.

    Two layers, on every start:
      1. create_all() + ensure_columns()  — new tables and new nullable/defaulted columns appear by themselves, as before
      2. Alembic (app/migrations)         — everything those cannot do: indexes on existing tables, data backfills,
                                            type changes, renames. Scripts must be re-runnable (migrations/util.py).
    A database from before Alembic is marked as the baseline first, then upgraded."""
    from alembic import command
    from sqlalchemy import inspect
    insp = inspect(engine)
    has_ver, has_tables = insp.has_table("alembic_version"), insp.has_table("sos_users")
    Base.metadata.create_all(engine)
    ensure_columns()
    did = "upgraded"
    if not has_ver:
        did = "adopted" if has_tables else "created"
        with engine.begin() as cx:
            command.stamp(_alembic_cfg(cx), BASELINE if has_tables else "head")
    with engine.begin() as cx:
        command.upgrade(_alembic_cfg(cx), "head")
    return did
