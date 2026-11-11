"""Helpers that make a migration safe to run twice (and safe on a database that ensure_columns() already touched)."""
from alembic import op
import sqlalchemy as sa


def _insp():
    return sa.inspect(op.get_bind())


def has_table(name: str) -> bool:
    return _insp().has_table(name)


def has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _insp().get_columns(table)}


def has_index(table: str, name: str) -> bool:
    return name in {i["name"] for i in _insp().get_indexes(table)}


def create_index(name: str, table: str, columns: list[str], unique: bool = False) -> None:
    if has_table(table) and not has_index(table, name):
        op.create_index(name, table, columns, unique=unique)


def drop_index(name: str, table: str) -> None:
    if has_table(table) and has_index(table, name):
        op.drop_index(name, table_name=table)


def add_column(table: str, column: sa.Column) -> None:
    if has_table(table) and not has_column(table, column.name):
        op.add_column(table, column)
