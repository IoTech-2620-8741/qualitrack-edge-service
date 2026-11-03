"""Shared database infrastructure for the QualiTrack Edge Service.

Provides a single :class:`peewee.SqliteDatabase` instance (``db``) shared by
the bounded contexts' ORM mappings, so they use the same database.
"""
from pathlib import Path

from peewee import SqliteDatabase

# Shared SQLite database instance used by all bounded-context ORM models.
db = SqliteDatabase(None, timeout=30, pragmas={"busy_timeout": 30_000})


def configure_database(database_path: str) -> None:
    """Configure the shared database before the application is created."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db.init(str(path))

def init_db() -> None:
    """Open the database connection and create all required tables.

    Imports the ORM models at call time to avoid import cycles, then creates
    their tables if absent. Calling this function repeatedly is safe.
    """
    if db.is_closed():
        db.connect()
    from iam.infrastructure.models import Device

    db.create_tables([Device], safe=True)