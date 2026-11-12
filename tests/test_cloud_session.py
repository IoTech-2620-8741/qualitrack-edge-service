
import unittest

import requests

from shared.infrastructure.cloud_session import (
    CloudRejectedError,
    CloudSession,
    CloudUnavailableError,
)


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("No JSON body")
        return self._body


class FakeHttpClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


class CloudSessionTestCase(unittest.TestCase):
    def session(self, *responses):
        self.http = FakeHttpClient(*responses)

        return CloudSession(
            "https://cloud.example/",
            "EDGE-001",
            "gateway-secret",
            self.http,
        )

    def test_sends_api_key_on_every_request_without_sign_in(self):
        session = self.session(
            FakeResponse(200, []),
            FakeResponse(200, []),
        )

        path = "/api/v1/edge-gateways/EDGE-001/iot-devices"

        self.assertEqual(session.device_id, "EDGE-001")
        self.assertEqual(session.request("GET", path).status_code, 200)
        self.assertEqual(session.request("GET", path).status_code, 200)

        self.assertEqual(len(self.http.calls), 2)

        for method, url, kwargs in self.http.calls:
            self.assertEqual(
                (method, url),
                ("GET", "https://cloud.example" + path),
            )
            self.assertEqual(
                kwargs["headers"]["X-API-Key"],
                "gateway-secret",
            )
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertGreater(kwargs["timeout"], 0)

    def test_rejects_invalid_or_forbidden_gateway_credentials(self):
        for status, message in (
            (401, "Invalid Edge Gateway credentials"),
            (403, "Edge Gateway is not authorized"),
        ):
            with self.subTest(status=status):
                session = self.session(FakeResponse(status))

                with self.assertRaisesRegex(
                    CloudRejectedError, message
                ) as raised:
                    session.request("GET", "/api/v1/any")

                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(len(self.http.calls), 1)

    def test_reports_an_unreachable_or_failing_cloud(self):
        for response in (
            requests.ConnectionError("offline"),
            requests.Timeout("slow"),
            FakeResponse(503),
        ):
            with self.subTest(response=response):
                session = self.session(response)

                with self.assertRaises(CloudUnavailableError):
                    session.request("GET", "/api/v1/any")

    def test_returns_other_client_errors_to_the_gateway(self):
        session = self.session(
            FakeResponse(400, {"message": "Invalid reading"})
        )

        response = session.request(
            "POST",
            "/api/v1/any",
            json={},
        )

        self.assertEqual(response.status_code, 400)

    def test_requires_gateway_credentials(self):
        with self.assertRaisesRegex(ValueError, "device ID"):
            CloudSession(
                "https://cloud.example",
                " ",
                "gateway-secret",
            )

        with self.assertRaisesRegex(ValueError, "API key"):
            CloudSession(
                "https://cloud.example",
                "EDGE-001",
                " ",
            )


if __name__ == "__main__":
    unittest.main()
