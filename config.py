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

    return {
        "APP_ENV": environment_name,
        "DEBUG": debug,
        "DATABASE_PATH": str(database),
        "SEED_TEST_DEVICE": seed_test_device,
        "TEST_DEVICE_ID": device_id,
        "TEST_DEVICE_API_KEY": api_key,
        "HOST": host,
        "PORT": port,
    }

def _required(values: dict[str, str], name: str) -> str:
    """Return a configured value or report the missing environment setting."""
    try:
        return values[name]
    except KeyError as error:
        raise ValueError(f"{name} must be set in the selected dotenv file or environment") from error
