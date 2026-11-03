"""Command-line interface for the IAM bounded context.

Registers the ESP32 nodes of the facility in the Edge and manages their API
keys. These commands run on the local station; they are not exposed over
HTTP. Usage::

    flask --app app devices register ENV-001 ENVIRONMENTAL_DEVICE
    flask --app app devices list
    flask --app app devices rotate-key ENV-001
    flask --app app devices revoke ENV-001
"""
import click
from flask import current_app
from flask.cli import AppGroup

from iam.domain.enums import DeviceType

devices_cli = AppGroup("devices", help="Register the ESP32 nodes and manage their API keys.")


def _service():
    return current_app.extensions["iam_authentication_service"]


@devices_cli.command("register")
@click.argument("device_id")
@click.argument("device_type", type=click.Choice([member.value for member in DeviceType]))
def register_device(device_id: str, device_type: str) -> None:
    """Register DEVICE_ID, the identifier the cloud registers for the node."""
    try:
        issued = _service().register_device(device_id, DeviceType(device_type))
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Registered {issued.device.device_id} ({issued.device.device_type.value})")
    click.echo(f"API key (shown once): {issued.api_key}")


@devices_cli.command("rotate-key")
@click.argument("device_id")
def rotate_api_key(device_id: str) -> None:
    """Give DEVICE_ID a new API key; the previous one stops working."""
    try:
        issued = _service().rotate_api_key(device_id)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"New API key for {issued.device.device_id} (shown once): {issued.api_key}")


@devices_cli.command("revoke")
@click.argument("device_id")
def revoke_device(device_id: str) -> None:
    """Revoke DEVICE_ID; it can no longer sign in."""
    try:
        device = _service().revoke_device(device_id)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"Revoked {device.device_id}")


@devices_cli.command("list")
def list_devices() -> None:
    """List the registered nodes with their type and state."""
    devices = _service().list_devices()
    if not devices:
        click.echo("No devices registered")
        return
    for device in devices:
        state = "active" if device.revoked_at is None else "revoked"
        click.echo(f"{device.device_id:<20} {device.device_type.value:<22} {state}")
