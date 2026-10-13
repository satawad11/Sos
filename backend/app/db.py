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
                cx.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {typ}'))
