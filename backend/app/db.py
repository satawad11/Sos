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
