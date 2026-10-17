"""
Turn Claude API failures into short, friendly messages (no raw exception text).

Duck-typed on purpose: it looks at `status_code`, `body` / `type` and `message`
rather than importing anthropic classes, so it also works for fakes and for
exceptions that langchain wraps (the __cause__/__context__ chain is walked).
Full technical details are logged by the caller.
"""

CREDITS = "credits"
AUTH = "auth"
RATE_LIMIT = "rate_limit"
OVERLOADED = "overloaded"
NETWORK = "network"
UNKNOWN = "unknown"

MESSAGES = {
    CREDITS: ("⚠️ Claude API credits ran out. Top up at console.anthropic.com → Billing "
              "(tip: turn on auto-reload). Prices below, no AI analysis this run."),
    AUTH: "⚠️ Claude API key was rejected. Check the ANTHROPIC_API_KEY secret in GitHub.",
    RATE_LIMIT: "⚠️ Claude API rate limit hit. The next run should work.",
    OVERLOADED: "⚠️ Claude is temporarily overloaded or down. The next run should work.",
    NETWORK: "⚠️ Couldn't reach the Claude API (network/timeout). The next run should work.",
    UNKNOWN: "⚠️ AI analysis failed unexpectedly. Details are in the GitHub Actions log.",
}

# Problems that will not fix themselves → the workflow run should be marked failed.
FATAL_CODES = (CREDITS, AUTH)


def _body_error(exc) -> dict:
    body = getattr(exc, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    return err if isinstance(err, dict) else {}


def _message_text(exc) -> str:
    err = _body_error(exc)
    parts = [str(err.get("message") or ""), str(getattr(exc, "message", "") or ""), str(exc)]
    return " ".join(parts).lower()


def _classify_one(exc) -> str:
    status = getattr(exc, "status_code", None)
    etype = getattr(exc, "type", None) or _body_error(exc).get("type")
    text = _message_text(exc)

    # Anthropic reports exhausted credits as a 400 invalid_request_error whose message says
    # "Your credit balance is too low ..."; newer API versions also have a billing_error type.
    if etype == "billing_error" or status == 402 or "credit balance" in text:
        return CREDITS
    if status in (401, 403) or etype in ("authentication_error", "permission_error"):
        return AUTH
    if status == 429 or etype == "rate_limit_error":
        return RATE_LIMIT
    if (isinstance(status, int) and status >= 500) or etype in ("overloaded_error", "api_error", "timeout_error"):
        return OVERLOADED
    # anthropic.APIConnectionError / APITimeoutError, httpx and builtin network errors
    names = {c.__name__ for c in type(exc).__mro__}
    if names & {"APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutException",
                "ConnectionError", "TimeoutError"}:
        return NETWORK
    return UNKNOWN


def classify_llm_error(exc: BaseException) -> tuple:
    """Return (code, friendly_message). Walks __cause__/__context__ for wrapped errors."""
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = _classify_one(cur)
        if code != UNKNOWN:
            return code, MESSAGES[code]
        cur = cur.__cause__ or cur.__context__
    return UNKNOWN, MESSAGES[UNKNOWN]
