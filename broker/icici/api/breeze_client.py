# broker/icici/api/breeze_client.py
# Shared transport for the ICICI Direct Breeze API.
#
# Wire contract (mirrors the official Breeze SDK):
#   - Every request carries a JSON body, including GET and DELETE. The exact
#     body string participates in the checksum, so it must be serialized with
#     compact separators and then sent verbatim.
#   - Headers: X-AppKey, X-SessionToken (base64 "user_id:session_key"),
#     X-Timestamp (UTC ISO, millisecond precision) and
#     X-Checksum ("token " + SHA256(timestamp + body + api_secret)).
#   - Responses are {"Success": ..., "Status": 200, "Error": ...}. ICICI
#     answers with HTTP 200 even for business-level failures, so the parsed
#     "Status" field is folded back onto the httpx response's .status - the
#     convention OpenAlgo services rely on to detect failures.

import base64
import json
import os
from datetime import datetime, timezone
from hashlib import sha256

from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_5_8) AppleWebKit/534.50.2 "
    "(KHTML, like Gecko) Version/5.0.6 Safari/533.22.3"
)


def decode_session_token(auth_token):
    """Decode the stored X-SessionToken into (user_id, session_key).

    The auth table stores the base64 "user_id:session_key" string that Breeze
    expects verbatim in the X-SessionToken header. Both halves are needed
    separately - the socket.io live feed authenticates with
    {"user": user_id, "token": session_key}.
    """
    decoded = base64.b64decode(auth_token.encode("ascii")).decode("ascii")
    user_id, _, session_key = decoded.partition(":")
    if not user_id or not session_key:
        raise ValueError("Malformed Breeze session token")
    return user_id, session_key


def encode_session_token(user_id, session_key):
    """Build the X-SessionToken value from its two halves."""
    raw = f"{user_id}:{session_key}".encode("ascii")
    return base64.b64encode(raw).decode("ascii")


def build_headers(body, auth_token):
    """Build the checksummed header set for a Breeze v1 request.

    ``body`` must be the exact compact JSON string that is sent as the request
    body, because SHA256(timestamp + body + secret) is computed over it.
    """
    api_key = os.getenv("BROKER_API_KEY")
    api_secret = os.getenv("BROKER_API_SECRET")
    if not api_key or not api_secret:
        raise Exception(
            "BROKER_API_KEY and BROKER_API_SECRET must be configured for ICICI Direct (Breeze API)"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
    checksum = sha256((timestamp + body + api_secret).encode("utf-8")).hexdigest()

    return {
        "Content-Type": "application/json",
        "X-Checksum": f"token {checksum}",
        "X-Timestamp": timestamp,
        "X-AppKey": api_key,
        "X-SessionToken": auth_token,
        "User-Agent": USER_AGENT,
    }


def is_success(data):
    """True when a parsed Breeze response carries a success payload."""
    return (
        isinstance(data, dict)
        and data.get("Success") is not None
        and (data.get("Status") in (None, 200, "200"))
    )


def extract_error(data):
    """Human-readable error message from a parsed Breeze response."""
    if isinstance(data, dict):
        for key in ("Error", "error", "errorMessage", "message"):
            value = data.get(key)
            if value:
                return str(value)
    return "Unknown Breeze API error"


def request_raw(endpoint, auth, method="GET", payload=""):
    """Perform a Breeze v1 request; returns (httpx response, parsed JSON).

    Sets ``response.status`` to the HTTP status code, then overrides it with
    the body's "Status" field when ICICI reports a business-level failure
    under HTTP 200 - so OpenAlgo services see non-200 exactly when the call
    failed.
    """
    client = get_httpx_client()

    if not payload:
        payload = "{}"
    body = json.dumps(payload, separators=(",", ":")) if not isinstance(payload, str) else payload

    url = endpoint if endpoint.startswith("http") else None
    from broker.icici.api.baseurl import get_url

    url = url or get_url(endpoint)

    redacted_headers = None

    try:
        headers = build_headers(body, auth)
        redacted_headers = {
            k: ("[REDACTED]" if k in ("X-SessionToken", "X-Checksum") else v)
            for k, v in headers.items()
        }
        logger.debug(f"Breeze {method} {endpoint} payload: {body if body != '{}' else '<empty>'}")

        if method == "GET":
            response = client.get(url, headers=headers, content=body)
        elif method == "POST":
            response = client.post(url, headers=headers, content=body)
        else:
            response = client.request(method, url, headers=headers, content=body)

        # Add status attribute for compatibility with the existing codebase
        response.status = response.status_code

        try:
            response_data = json.loads(response.text)
        except json.JSONDecodeError:
            logger.error(f"Breeze {endpoint} returned non-JSON response (HTTP {response.status_code})")
            response_data = {
                "Success": None,
                "Status": response.status_code if response.status_code != 200 else 500,
                "Error": "Invalid JSON response from ICICI Direct",
            }
            response.status = response_data["Status"]
            return response, response_data

        logger.debug(f"Breeze {method} {endpoint} response ({response.status_code}): {response_data}")

        # Breeze signals failures inside HTTP 200 via {"Status": !=200, "Error": ...}
        if isinstance(response_data, dict) and response_data.get("Status") not in (None, 200, "200"):
            try:
                response.status = int(response_data["Status"])
            except (TypeError, ValueError):
                response.status = 400
            logger.error(
                f"Breeze {endpoint} failed with Status {response_data.get('Status')}: "
                f"{extract_error(response_data)}"
            )

        return response, response_data

    except Exception as e:
        logger.exception(f"Error in Breeze API request to {endpoint}: {e}")
        if redacted_headers:
            logger.debug(f"Breeze request headers: {redacted_headers}")
        return None, {"errorType": "ConnectionError", "errorMessage": str(e)}


def get_api_response(endpoint, auth, method="GET", payload=""):
    """Perform a Breeze v1 request and return the parsed JSON."""
    _, response_data = request_raw(endpoint, auth, method, payload)
    return response_data
