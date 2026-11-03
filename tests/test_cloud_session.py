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


def signed_in(token="token-1", laboratory_id=7, password_change_required=False):
    return FakeResponse(200, {
        "id": 3,
        "username": "edge.operator@lab.pe",
        "token": token,
        "roles": ["ROLE_OPERATOR"],
        "laboratoryId": laboratory_id,
        "passwordChangeRequired": password_change_required,
    })


class FakeHttpClient:
    """Answers each request with the next queued response and records the calls."""

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
        return CloudSession("https://cloud.example/", "edge.operator@lab.pe", "local-secret", self.http)

    def test_signs_in_once_and_sends_the_bearer_token(self):
        session = self.session(signed_in(), FakeResponse(200, []), FakeResponse(200, []))

        self.assertEqual(session.laboratory_id(), 7)
        session.request("GET", "/api/v1/laboratories/7/equipments")
        session.request("GET", "/api/v1/laboratories/7/equipments")

        method, url, sign_in = self.http.calls[0]
        self.assertEqual((method, url), ("POST", "https://cloud.example/api/v1/authentication/sign-in"))
        self.assertEqual(sign_in["json"], {"username": "edge.operator@lab.pe", "password": "local-secret"})
        self.assertNotIn("Authorization", sign_in["headers"])
        self.assertEqual(len(self.http.calls), 3)
        for _, url, call in self.http.calls[1:]:
            self.assertEqual(url, "https://cloud.example/api/v1/laboratories/7/equipments")
            self.assertEqual(call["headers"]["Authorization"], "Bearer token-1")
            self.assertGreater(call["timeout"], 0)

    def test_signs_in_again_once_when_the_token_expires(self):
        session = self.session(
            signed_in("old-token"), FakeResponse(401), signed_in("new-token"), FakeResponse(200, [])
        )

        response = session.request("GET", "/api/v1/laboratories/7/equipments")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.http.calls[3][2]["headers"]["Authorization"], "Bearer new-token")

    def test_rejects_a_token_the_cloud_refuses_after_signing_in_again(self):
        session = self.session(signed_in(), FakeResponse(401), signed_in(), FakeResponse(401))

        with self.assertRaises(CloudRejectedError) as raised:
            session.request("GET", "/api/v1/laboratories/7/equipments")
        self.assertEqual(raised.exception.status_code, 401)

    def test_reports_rejected_or_unusable_operator_accounts(self):
        for response, message in (
            (FakeResponse(401), "rejected the operator credentials"),
            (signed_in(password_change_required=True), "must change its password"),
            (signed_in(laboratory_id=None), "does not belong to a laboratory"),
        ):
            with self.subTest(message=message):
                session = self.session(response)
                with self.assertRaisesRegex(CloudRejectedError, message):
                    session.laboratory_id()

    def test_reports_a_laboratory_that_is_not_ready(self):
        session = self.session(
            signed_in(),
            FakeResponse(403, {"code": "ONBOARDING_REQUIRED", "nextStep": "SUBSCRIPTION"}),
        )

        with self.assertRaisesRegex(CloudRejectedError, "next step in QualiTrack: SUBSCRIPTION"):
            session.request("GET", "/api/v1/laboratories/7/equipments")

    def test_reports_an_unreachable_or_failing_cloud(self):
        for responses in (
            (requests.ConnectionError("offline"),),
            (requests.Timeout("slow"),),
            (signed_in(), FakeResponse(503)),
        ):
            with self.subTest(responses=responses):
                session = self.session(*responses)
                with self.assertRaises(CloudUnavailableError):
                    session.request("GET", "/api/v1/laboratories/7/equipments")

    def test_returns_other_client_errors_to_the_gateway(self):
        session = self.session(signed_in(), FakeResponse(400, {"message": "Invalid reading"}))

        self.assertEqual(session.request("POST", "/api/v1/any", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
