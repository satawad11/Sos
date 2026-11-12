"""Alembic environment. Run through app.db.migrate() (which hands over a live connection), or from the command line:
    cd backend && SOS_DATABASE_URL=... alembic -c alembic.ini upgrade head
"""
from alembic import context
from app.db import Base, engine
from app import models  # noqa: F401  (registers the tables on Base.metadata)

target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


conn = context.config.attributes.get("connection")
if conn is not None:
    run(conn)
else:
    with engine.begin() as c:
        run(c)
