"""Command-line interface for the cloud synchronization of the Tracking context.

``cloud sync`` runs now what the background thread runs every interval: it
reads the device registry and sends the pending readings and actions.

Usage::

    flask --app app cloud sync
    flask --app app cloud devices
    flask --app app cloud pending
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
    """Read the device registry and send the pending readings and actions now."""
    try:
        devices = _registry_service().synchronize_registry()
        click.echo(f"Device registry: {len(devices)} device(s) from the cloud")
        summaries = current_app.extensions["telemetry_synchronization_service"].synchronize_pending()
    except CloudSynchronizationDisabled as error:
        raise click.ClickException("Cloud synchronization is disabled (CLOUD_SYNC_ENABLED=false)") from error
    except CloudRequestError as error:
        raise click.ClickException(str(error)) from error
    for label, key in (("Readings", "readings"), ("Actuation events", "actuation_events")):
        summary = summaries[key]
        click.echo(
            f"{label}: {summary.synced} synced, {summary.rejected} rejected, "
            f"{summary.waiting} waiting for their node in the cloud registry"
        )


@cloud_cli.command("pending")
def show_pending() -> None:
    """Count the readings and actions stored by synchronization status (TS60)."""
    counts = current_app.extensions["telemetry_service"].pending_counts()
    for label, key in (("Readings", "readings"), ("Actuation events", "actuation_events")):
        by_status = ", ".join(f"{status.value} {total}" for status, total in counts[key].items())
        click.echo(f"{label}: {by_status}")


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
