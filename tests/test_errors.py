"""Claude API error classification — real anthropic exception classes, no network."""

import unittest

import anthropic
import httpx2

from bot.errors import MESSAGES, classify_llm_error

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status, message="m", etype="invalid_request_error"):
    body = {"type": "error", "error": {"type": etype, "message": message}}
    return cls(message, response=httpx2.Response(status, request=REQ), body=body)


class ClassifyTests(unittest.TestCase):
    def code(self, exc):
        code, msg = classify_llm_error(exc)
        self.assertEqual(msg, MESSAGES[code])
        return code

    def test_credits_400_message(self):
        exc = status_error(anthropic.BadRequestError, 400,
                           "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing.")
        self.assertEqual(self.code(exc), "credits")

    def test_credits_billing_error_type(self):
        exc = status_error(anthropic.BadRequestError, 400, "payment needed", etype="billing_error")
        self.assertEqual(self.code(exc), "credits")

    def test_other_400_is_unknown(self):
        self.assertEqual(self.code(status_error(anthropic.BadRequestError, 400, "bad schema")), "unknown")

    def test_auth(self):
        self.assertEqual(self.code(status_error(anthropic.AuthenticationError, 401, etype="authentication_error")), "auth")
        self.assertEqual(self.code(status_error(anthropic.PermissionDeniedError, 403, etype="permission_error")), "auth")

    def test_rate_limit(self):
        self.assertEqual(self.code(status_error(anthropic.RateLimitError, 429, etype="rate_limit_error")), "rate_limit")

    def test_overloaded_and_5xx(self):
        self.assertEqual(self.code(status_error(anthropic.OverloadedError, 529, etype="overloaded_error")), "overloaded")
        self.assertEqual(self.code(status_error(anthropic.InternalServerError, 500, etype="api_error")), "overloaded")

    def test_network(self):
        self.assertEqual(self.code(anthropic.APIConnectionError(request=REQ)), "network")
        self.assertEqual(self.code(anthropic.APITimeoutError(REQ)), "network")

    def test_wrapped_exception(self):
        inner = status_error(anthropic.BadRequestError, 400, "Your credit balance is too low")
        try:
            try:
                raise inner
            except Exception as e:
                raise RuntimeError("chain failed") from e
        except RuntimeError as outer:
            self.assertEqual(self.code(outer), "credits")

    def test_wrapped_via_context(self):
        try:
            try:
                raise anthropic.RateLimitError("x", response=httpx2.Response(429, request=REQ), body=None)
            except Exception:
                raise ValueError("oops")
        except ValueError as outer:
            self.assertEqual(self.code(outer), "rate_limit")

    def test_duck_typed_fake(self):
        class Fake(Exception):
            status_code = 401
        self.assertEqual(self.code(Fake("secret details")), "auth")

    def test_unknown_hides_raw_text(self):
        code, msg = classify_llm_error(RuntimeError("secret-host:27017 boom"))
        self.assertEqual(code, "unknown")
        self.assertNotIn("secret", msg)


if __name__ == "__main__":
    unittest.main()
