"""Authenticated HTTP session with the QualiTrack Platform.

Edge Gateways authenticate using their external device identifier
and an API key issued by the Platform IAM.

The API key is sent through the X-API-Key header.
No operator account, JWT or sign-in request is required.
"""

import logging
from typing import Any, Protocol

import requests

LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10
_API_KEY_HEADER = "X-API-Key"


class CloudRequestError(Exception):
    """Base exception for cloud communication failures."""


class CloudUnavailableError(CloudRequestError):
    """The cloud is unreachable or returned a server error."""


class CloudRejectedError(CloudRequestError):
    """The cloud rejected an authenticated request."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code


class HttpClient(Protocol):
    """HTTP client contract used by the cloud session."""

    def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        ...


class CloudSession:
    """Manage authenticated requests from Edge to Platform.

    Each request uses the API key provisioned for the Gateway.
    Credentials are never exchanged for an additional token.
    """

    def __init__(
        self,
        base_url: str,
        device_id: str,
        api_key: str,
        http_client: HttpClient | None = None,
    ):
        if not device_id or not device_id.strip():
            raise ValueError("Gateway device ID is required")

        if not api_key or not api_key.strip():
            raise ValueError("Gateway API key is required")

        self._base_url = base_url.rstrip("/")
        self._device_id = device_id.strip()
        self._api_key = api_key.strip()
        self._http = http_client or requests.Session()

    @property
    def device_id(self) -> str:
        """Return the Gateway external identifier."""
        return self._device_id

    def request(
        self,
        method: str,
        path: str,
        json: Any = None,
    ) -> requests.Response:
        """Send an API-key-authenticated cloud request.

        Args:
            method: HTTP method.
            path: Platform API resource path.
            json: Optional JSON request body.

        Returns:
            Cloud HTTP response.

        Raises:
            CloudUnavailableError: On network or server failures.
            CloudRejectedError: On invalid credentials or
                insufficient Gateway permissions.
        """
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en",
            _API_KEY_HEADER: self._api_key,
        }

        try:
            response = self._http.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise CloudUnavailableError(
                f"The cloud is not reachable: {error.__class__.__name__}"
            ) from error

        if response.status_code >= 500:
            raise CloudUnavailableError(
                f"The cloud answered {response.status_code}"
            )

        if response.status_code == 401:
            raise CloudRejectedError(
                "Invalid Edge Gateway credentials",
                401,
            )

        if response.status_code == 403:
            raise CloudRejectedError(
                "Edge Gateway is not authorized for this resource",
                403,
            )

        return response