"""Command-line interface for the cloud synchronization of the Tracking context.

``cloud sync`` runs now what the background thread runs every interval: it
reads the device registry, copies the profile of each node and sends the
pending readings and actions.

Usage::

    flask --app app cloud sync
    flask --app app cloud devices
    flask --app app cloud profiles
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
    """Read the registry and the profiles and send the pending readings and actions now."""
    try:
        registry = _registry_service().synchronize_registry()
        click.echo(f"Device registry: {len(registry.devices)} device(s) of this Edge from the cloud")
        if registry.not_in_cloud:
            click.echo(
                "Registered in this Edge but not in the cloud (check their sensorExternalId): "
                + ", ".join(registry.not_in_cloud)
            )
        profiles = current_app.extensions["environmental_profile_service"].synchronize_profiles()
        click.echo(f"Environmental profiles: {len(profiles)} profile(s) from the cloud")
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


@cloud_cli.command("profiles")
def list_profiles() -> None:
    """List the local copy of the profiles of the nodes."""
    profiles = current_app.extensions["environmental_profile_service"].profiles()
    if not profiles:
        click.echo("No profiles read from the cloud yet")
        return
    for profile in profiles:
        metrics = ", ".join(threshold.metric.value for threshold in profile.thresholds) or "-"
        click.echo(
            f"{profile.device_id:<20} version {profile.version.value:<5} "
            f"thresholds {metrics:<36} rules {len(profile.actuation_rules)}"
        )
