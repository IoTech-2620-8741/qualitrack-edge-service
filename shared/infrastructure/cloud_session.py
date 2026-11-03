"""Authenticated HTTP session with the QualiTrack Cloud REST API.

The Edge signs in with the account of an operator of the laboratory (report
5.6) and sends the JWT of that account as a Bearer token. The session signs in
again once when the cloud answers ``401`` (for example, after the token
expires). The bounded contexts reach the cloud through their own gateways,
which use this session.
"""
import logging
import threading
from typing import Any, Protocol

import requests

LOGGER = logging.getLogger(__name__)
_SIGN_IN_PATH = "/api/v1/authentication/sign-in"
_TIMEOUT_SECONDS = 10


class CloudRequestError(Exception):
    """The cloud could not complete a request of the Edge."""


class CloudUnavailableError(CloudRequestError):
    """The cloud did not answer: network error, timeout or server error."""


class CloudRejectedError(CloudRequestError):
    """The cloud refused the request of the Edge."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpClient(Protocol):
    """The part of :class:`requests.Session` the cloud session uses."""

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        ...


class CloudSession:
    """Signs in the operator account of the Edge and sends authenticated requests."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        http_client: HttpClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._http = http_client or requests.Session()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._laboratory_id: int | None = None

    def laboratory_id(self) -> int:
        """Return the laboratory of the operator account, signing in when needed."""
        with self._lock:
            if self._token is None:
                self._sign_in()
            return self._laboratory_id

    def request(self, method: str, path: str, json: Any = None) -> requests.Response:
        """Send an authenticated request and return the answer of the cloud.

        Raises:
            CloudUnavailableError: The cloud did not answer or failed (``5xx``).
            CloudRejectedError: The cloud rejected the credentials of the
                operator (``401`` after signing in again) or the access of the
                account (``403``).
        """
        with self._lock:
            if self._token is None:
                self._sign_in()
            token = self._token
        response = self._send(method, path, json, token)
        if response.status_code == 401:
            with self._lock:
                if self._token == token:
                    self._sign_in()
                token = self._token
            response = self._send(method, path, json, token)
        if response.status_code == 401:
            raise CloudRejectedError("The cloud rejected the token of the operator account", 401)
        if response.status_code == 403:
            raise CloudRejectedError(_forbidden_message(response), 403)
        return response

    def _sign_in(self) -> None:
        """Sign in the operator account; the caller holds the lock."""
        self._token = None
        response = self._send(
            "POST",
            _SIGN_IN_PATH,
            {"username": self._username, "password": self._password},
            None,
        )
        if response.status_code in (400, 401, 404):
            raise CloudRejectedError("The cloud rejected the operator credentials", response.status_code)
        if response.status_code != 200:
            raise CloudRejectedError(
                f"The cloud answered {response.status_code} to the operator sign-in",
                response.status_code,
            )
        account = response.json()
        if account.get("passwordChangeRequired"):
            raise CloudRejectedError(
                "The operator account must change its password in QualiTrack before the Edge can use it"
            )
        if account.get("laboratoryId") is None:
            raise CloudRejectedError("The operator account does not belong to a laboratory")
        self._token = account["token"]
        self._laboratory_id = int(account["laboratoryId"])
        LOGGER.info("Signed in to the cloud as %s (laboratory %s)", self._username, self._laboratory_id)

    def _send(self, method: str, path: str, json: Any, token: str | None) -> requests.Response:
        headers = {"Accept": "application/json", "Accept-Language": "en"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = self._http.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise CloudUnavailableError(f"The cloud is not reachable: {error.__class__.__name__}") from error
        if response.status_code >= 500:
            raise CloudUnavailableError(f"The cloud answered {response.status_code}")
        return response


def _forbidden_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and body.get("code") == "ONBOARDING_REQUIRED":
        return (
            "The laboratory of the operator account is not ready "
            f"(next step in QualiTrack: {body.get('nextStep')})"
        )
    return "The operator account cannot access this resource of the cloud"
