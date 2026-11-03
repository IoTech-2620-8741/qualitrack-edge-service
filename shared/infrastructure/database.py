"""Shared database infrastructure for the Smart Band Edge Service.

Provides a single :class:`peewee.SqliteDatabase` instance (``db``) shared by
the bounded contexts' ORM mappings, so they use the same database.

The :func:`init_db` function is called during application startup to open the
configured database and create any missing tables with Peewee's
``safe=True`` option.

Note:
    The ``db`` object is configured before :func:`init_db` connects it.
    Peewee manages connections using thread-local storage; the Flask
    application closes the active connection during teardown.
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
    their tables if absent.

    Calling this function repeatedly is safe when tables already exist.

    Side effects:
        Opens the configured SQLite connection, creates its database file
        when needed, and creates the ``devices`` and ``health_records``
        tables when absent. The connection remains open until Flask teardown.
    """
