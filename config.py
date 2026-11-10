"""Environment-specific application configuration."""

import os
from pathlib import Path
from typing import TypedDict

from dotenv import dotenv_values


class AppConfig(TypedDict):
    APP_ENV: str
    DEBUG: bool
    DATABASE_PATH: str
    SEED_TEST_DEVICE: bool
    TEST_DEVICE_ID: str
    TEST_DEVICE_API_KEY: str | None
    HOST: str
    PORT: int
    CLOUD_SYNC_ENABLED: bool
    CLOUD_API_URL: str | None
    CLOUD_DEVICE_ID: str | None
    CLOUD_API_KEY: str | None
    CLOUD_SYNC_INTERVAL_SECONDS: int | None
    CLOUD_ENVIRONMENT_IDS: tuple[int, ...]
    SYNCED_RETENTION_DAYS: int
    REJECTED_RETENTION_DAYS: int


def _as_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")

def load_config(project_root: Path) -> AppConfig:
    """Load the selected dotenv file, then apply process environment overrides."""
    environment_name = os.getenv("APP_ENV", "development").strip().lower()
    if environment_name not in {"development", "production"}:
        raise ValueError("APP_ENV must be 'development' or 'production'")

    values = {
        **{
            key: value
            for key, value in dotenv_values(project_root / f".env.{environment_name}").items()
            if value is not None
        },
        **os.environ,
    }

    configured_environment = _required(values, "APP_ENV")
    if configured_environment.strip().lower() != environment_name:
        raise ValueError(
            f"APP_ENV in .env.{environment_name} must be '{environment_name}'"
        )

    debug = _as_bool("DEBUG", _required(values, "DEBUG"))
    seed_test_device = _as_bool("SEED_TEST_DEVICE", _required(values, "SEED_TEST_DEVICE"))
    if environment_name == "production" and debug:
        raise ValueError("DEBUG must be false in production")
    if environment_name == "production" and seed_test_device:
        raise ValueError("SEED_TEST_DEVICE must be false in production")
    database_path = _required(values, "DATABASE_PATH")
    if not database_path.strip():
        raise ValueError("DATABASE_PATH cannot be empty")
    database = Path(database_path).expanduser()
    if not database.is_absolute():
        database = project_root / database

    try:
        port = int(_required(values, "PORT"))
    except ValueError as error:
        raise ValueError("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")

    api_key = values.get("TEST_DEVICE_API_KEY", "").strip() or None
    if seed_test_device and not api_key:
        raise ValueError("TEST_DEVICE_API_KEY is required when seeding is enabled")
    device_id = _required(values, "TEST_DEVICE_ID").strip()
    host = _required(values, "HOST").strip()
    if seed_test_device and not device_id:
        raise ValueError("TEST_DEVICE_ID cannot be empty when seeding is enabled")
    if not host:
        raise ValueError("HOST cannot be empty")

    cloud = _cloud_settings(values, environment_name)
    synced_retention_days = _retention_days(values, "SYNCED_RETENTION_DAYS", 7)
    rejected_retention_days = _retention_days(values, "REJECTED_RETENTION_DAYS", 90)

    return {
        "APP_ENV": environment_name,
        "DEBUG": debug,
        "DATABASE_PATH": str(database),
        "SEED_TEST_DEVICE": seed_test_device,
        "TEST_DEVICE_ID": device_id,
        "TEST_DEVICE_API_KEY": api_key,
        "HOST": host,
        "PORT": port,
        "SYNCED_RETENTION_DAYS": synced_retention_days,
        "REJECTED_RETENTION_DAYS": rejected_retention_days,
        **cloud,
    }


def _cloud_settings(values: dict[str, str], environment_name: str) -> dict:
    """Validate Edge Gateway cloud connection settings.

    The Edge authenticates using its external device identifier
    and the API key issued by the QualiTrack Platform IAM.

    Args:
        values: Environment configuration values.
        environment_name: Selected application environment.

    Returns:
        Validated cloud configuration.

    Raises:
        ValueError: If a required cloud setting is missing
            or invalid.
    """
    enabled = _as_bool(
        "CLOUD_SYNC_ENABLED",
        _required(values, "CLOUD_SYNC_ENABLED"),
    )

    if not enabled:
        return {
            "CLOUD_SYNC_ENABLED": False,
            "CLOUD_API_URL": None,
            "CLOUD_DEVICE_ID": None,
            "CLOUD_API_KEY": None,
            "CLOUD_SYNC_INTERVAL_SECONDS": None,
            "CLOUD_ENVIRONMENT_IDS": (),
        }

    url = _required(values, "CLOUD_API_URL").strip().rstrip("/")

    if not url.startswith(("http://", "https://")):
        raise ValueError(
            "CLOUD_API_URL must be an http or https URL"
        )

    device_id = _required(values, "CLOUD_DEVICE_ID").strip()
    api_key = _required(values, "CLOUD_API_KEY").strip()

    if not device_id:
        raise ValueError("CLOUD_DEVICE_ID cannot be empty")

    if not api_key:
        raise ValueError("CLOUD_API_KEY cannot be empty")

    try:
        interval = int(
            _required(values, "CLOUD_SYNC_INTERVAL_SECONDS")
        )
    except ValueError as error:
        raise ValueError(
            "CLOUD_SYNC_INTERVAL_SECONDS must be an integer"
        ) from error

    if interval < 1:
        raise ValueError(
            "CLOUD_SYNC_INTERVAL_SECONDS must be at least 1"
        )

    return {
        "CLOUD_SYNC_ENABLED": True,
        "CLOUD_API_URL": url,
        "CLOUD_DEVICE_ID": device_id,
        "CLOUD_API_KEY": api_key,
        "CLOUD_SYNC_INTERVAL_SECONDS": interval,
        "CLOUD_ENVIRONMENT_IDS": _environment_ids(values.get("CLOUD_ENVIRONMENT_IDS")),
    }


def _environment_ids(setting: str | None) -> tuple[int, ...]:
    """Return the cloud environments this station serves, empty for the whole laboratory (ADR-0013).

    The setting is a comma-separated list of positive integers, such as ``3`` or ``3,5``.
    """
    if setting is None or not setting.strip():
        return ()
    identifiers = []
    for part in setting.split(","):
        try:
            identifier = int(part.strip())
        except ValueError as error:
            raise ValueError("CLOUD_ENVIRONMENT_IDS must be a comma-separated list of integers") from error
        if identifier < 1:
            raise ValueError("CLOUD_ENVIRONMENT_IDS must contain identifiers from 1")
        if identifier not in identifiers:
            identifiers.append(identifier)
    return tuple(identifiers)

def _retention_days(values: dict[str, str], name: str, default: int) -> int:
    """Return the days the Edge keeps a synchronized or rejected record, ``default`` when unset."""
    setting = values.get(name)
    if setting is None or not setting.strip():
        return default
    try:
        days = int(setting)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if days < 1:
        raise ValueError(f"{name} must be at least 1")
    return days


def _required(values: dict[str, str], name: str) -> str:
    """Return a configured value or report the missing environment setting."""
    try:
        return values[name]
    except KeyError as error:
        raise ValueError(f"{name} must be set in the selected dotenv file or environment") from error
