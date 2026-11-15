"""Shared database infrastructure for the QualiTrack Edge Service.

Provides a single :class:`peewee.SqliteDatabase` instance (``db``) shared by
the bounded contexts' ORM mappings, so they use the same database.
"""
from pathlib import Path

from peewee import PeeweeException, SqliteDatabase

# Shared SQLite database instance used by all bounded-context ORM models.
db = SqliteDatabase(None, timeout=30, pragmas={"busy_timeout": 30_000})


def configure_database(database_path: str) -> None:
    """Configure the shared database before the application is created."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db.init(str(path))


def database_available() -> bool:
    """Whether the SQLite database answers a read of its schema.

    The query reads the file, so a database that cannot be opened, read or
    parsed answers ``False``. It never writes.
    """
    try:
        db.execute_sql("SELECT 1 FROM sqlite_master LIMIT 1")
    except PeeweeException:
        return False
    return True


def init_db() -> None:
    """Open the database connection and create all required tables.

    Imports the ORM models at call time to avoid import cycles, then creates
    their tables if absent. Calling this function repeatedly is safe.
    """
    if db.is_closed():
        db.connect()
    from iam.infrastructure.models import Device
    from sync.infrastructure.models import MonitoredDevice
    from tracking.infrastructure.models import ActuationEvent, EnvironmentalProfile, TelemetryReading

    db.create_tables([Device, MonitoredDevice, EnvironmentalProfile, TelemetryReading, ActuationEvent], safe=True)