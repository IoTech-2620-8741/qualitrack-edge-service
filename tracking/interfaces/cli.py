"""Command-line interface for the cloud synchronization of the Tracking context.

Usage::

    flask --app app cloud sync
    flask --app app cloud devices
"""
import click
from flask import current_app
from flask.cli import AppGroup

from shared.infrastructure.cloud_session import CloudRequestError
from tracking.application.errors import CloudSynchronizationDisabled

cloud_cli = AppGroup("cloud", help="Synchronize the Edge with the QualiTrack cloud.")


def _registry_service():
    return current_app.extensions["device_registry_service"]


@cloud_cli.command("sync")
def synchronize() -> None:
    """Read now the nodes the cloud registers for the laboratory."""
    try:
        devices = _registry_service().synchronize_registry()
    except CloudSynchronizationDisabled as error:
        raise click.ClickException("Cloud synchronization is disabled (CLOUD_SYNC_ENABLED=false)") from error
    except CloudRequestError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Device registry: {len(devices)} device(s) from the cloud")


@cloud_cli.command("devices")
def list_monitored_devices() -> None:
    """List the local copy of the cloud registry."""
    devices = _registry_service().monitored_devices()
    if not devices:
        click.echo("No devices read from the cloud yet")
        return
    for device in devices:
        environment = "-" if device.environment_id is None else device.environment_id
        click.echo(
            f"{device.device_id:<20} {device.device_type.value:<22} "
            f"laboratory {device.laboratory_id:<5} environment {environment!s:<5} "
            f"cloud id {device.cloud_device_id:<6} {device.status.value}"
        )
