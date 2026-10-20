"""
ICICI Direct Breeze authentication for OpenAlgo.

Login flow (matches ``blueprints/brlogin.py``'s ``icici`` branch, which
reads ``?apisession=...`` from the redirect and calls
``authenticate_broker(api_session)``):

1. The browser is sent to ICICI's Breeze login page (apiissue/login) with
   the app key and registered redirect URL.
2. ICICI redirects back to ``/icici/callback?apisession=<token>``.
3. ``authenticate_broker`` validates the session token through the
   customerdetails exchange (plain requests call — the shared httpx client
   rejected this endpoint's ``content=`` kwarg, so the requests transport
   is deliberate) and stores the derived credentials as JSON:

       {"api_session": ..., "user_id": ..., "session_key": ...,
        "session_token": <base64 user:session_key>}

   The SDK reuses api_session for ``generate_session``; the REST layer
   signs requests with the base64 session token.
"""

import base64
import json
import os

import requests

from broker.icici.api.baseurl import CUSTOMER_DETAILS_URL, LOGIN_URL
from utils.logging import get_logger


logger = get_logger(__name__)


def get_login_url() -> str:
    """Breeze interactive login URL (app key + registered redirect)."""

    api_key = os.getenv("BROKER_API_KEY", "").strip()

    redirect_url = os.getenv("REDIRECT_URL", "").strip()

    return (
        f"{LOGIN_URL}?api_key={api_key}&redirect_url={redirect_url}"
    )


def validate_api_session(api_session: str) -> dict:
    """
    Exchange the login api_session for customer details.

    Returns the parsed Breeze response dict. Raises RuntimeError with a
    user-actionable message on any failure.
    """

    if not api_session:
        raise RuntimeError(
            "ICICI api_session is missing — re-login from the broker page"
        )

    api_key = os.getenv("BROKER_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "BROKER_API_KEY is not configured — add the ICICI Breeze app "
            "key to .env and restart"
        )

    body = json.dumps(
        {"SessionToken": str(api_session).strip(), "AppKey": api_key},
        separators=(",", ":"),
    )

    try:
        response = requests.get(
            CUSTOMER_DETAILS_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=20,
        )

        response.raise_for_status()

    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(
            f"ICICI session exchange failed (HTTP "
            f"{exc.response.status_code if exc.response is not None else '?'}): "
            "the api_session token may have expired — re-login"
        ) from exc

    except requests.exceptions.RequestException as exc:
        raise RuntimeError(
            f"ICICI session exchange could not reach the broker: {exc}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "ICICI session exchange returned a non-JSON response"
        ) from exc

    if not isinstance(payload, dict) or payload.get("Status") != 200:
        message = (
            str(payload.get("Error") or "session rejected")
            if isinstance(payload, dict)
            else str(payload)
        )

        raise RuntimeError(f"ICICI login failed: {message}")

    success = payload.get("Success") or {}

    if not isinstance(success, dict):
        success = {}

    return success


def authenticate_broker(api_session):
    """
    Validate the ICICI api_session and return the stored auth payload.

    Returns (auth_token, error) per the OpenAlgo auth contract.
    """

    try:
        success = validate_api_session(api_session)

    except RuntimeError as exc:
        logger.error("ICICI authenticate_broker failed: %s", exc)

        return None, str(exc)

    except Exception as exc:
        logger.exception("ICICI authenticate_broker crashed")

        return None, f"ICICI login failed unexpectedly: {exc}"

    session_token_b64 = str(success.get("session_token") or "").strip()

    user_id = ""
    session_key = ""

    if session_token_b64:
        # The returned session_token is base64("user_id:session_key").
        try:
            decoded = base64.b64decode(
                session_token_b64.encode("ascii")
            ).decode("ascii")

            user_id, session_key = decoded.split(":", 1)

        except Exception:
            logger.warning(
                "ICICI session token decode failed — falling back to the "
                "raw api_session for REST signing"
            )

            session_token_b64 = ""

    stored = {
        "api_session": str(api_session).strip(),
        "user_id": str(user_id or success.get("user_id") or "").strip(),
        "session_key": str(
            session_key or success.get("session_key") or ""
        ).strip(),
    }

    if session_token_b64:
        stored["session_token"] = session_token_b64

    logger.info(
        "ICICI login ok (user_id_present=%s)", bool(stored["user_id"])
    )

    return json.dumps(stored), None
