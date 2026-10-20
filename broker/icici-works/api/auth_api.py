# broker/icici/api/auth_api.py
# ICICI Direct Breeze API authentication.
#
# Flow: the user opens the Breeze login URL built from the app key, logs in on
# ICICI Direct, and the browser is redirected to the registered redirect URI
# as /icici/callback?apisession=<token>. blueprints/brlogin.py hands that
# apisession value to authenticate_broker(), which exchanges it for the
# long-lived (for the day) session token.
#
# The exchange endpoint is called with a bare ``requests.get`` carrying the
# JSON body via ``data=`` - the same shape the official Breeze SDK uses
# (ApificationBreeze.make_request sends the body with ``data=`` on every verb,
# and the customerdetails exchange is the one call that needs no checksum).
# httpx cannot express this: its convenience verbs do not accept a body
# ("Client.get() got an unexpected keyword argument 'content'").
#
# The value stored in OpenAlgo's auth table is the base64 "user_id:session_key"
# string that every subsequent Breeze request sends verbatim as X-SessionToken;
# the streaming adapter decodes it to authenticate the socket.io feed.

import base64
import json
import os

import requests

from broker.icici.api.baseurl import get_url
from utils.logging import get_logger

logger = get_logger(__name__)


def decode_session_token(auth_token):
    """Decode the stored X-SessionToken into (user_id, session_key).

    Kept here for backwards compatibility; the canonical implementation lives
    in breeze_client (shared with the streaming adapters).
    """
    from broker.icici.api.breeze_client import decode_session_token as _decode

    return _decode(auth_token)


def authenticate_broker(api_session):
    """Exchange the redirect api_session token for a Breeze session token.

    Returns the 2-tuple (auth_token, error_message) expected by the generic
    redirect-broker path in brlogin.py. auth_token is the base64
    "user_id:session_key" X-SessionToken value; it is stored as-is and
    encrypted by upsert_auth.
    """
    if not api_session:
        return None, "No api_session token received from ICICI Direct. Please login again."

    api_session = str(api_session).strip()

    # The session exchange endpoint needs only the app key, which is sent in
    # the body; no checksum is verified on it.
    api_key = os.getenv("BROKER_API_KEY", "").strip()
    if not api_key:
        return None, "BROKER_API_KEY is not configured for ICICI Direct (Breeze API)."

    body = json.dumps(
        {"SessionToken": api_session, "AppKey": api_key}, separators=(",", ":")
    )

    try:
        url = get_url("customerdetails")
        response = requests.get(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        response.raise_for_status()

        data = json.loads(response.text)

        success = data.get("Success")
        if not success:
            error = data.get("Error") or "Invalid ICICI Direct session."
            logger.error(f"Breeze session exchange failed: {error}")
            return None, f"ICICI Direct authentication failed: {error}"

        session_token = success.get("session_token")
        if not session_token:
            return None, "ICICI Direct did not return a session token."

        # Validate the token decodes into user_id + session_key up front so a
        # malformed payload fails at login instead of on the first order.
        try:
            decoded = base64.b64decode(session_token.encode("ascii")).decode("ascii")
            user_id, _, session_key = decoded.partition(":")
            if not user_id.strip() or not session_key.strip():
                raise ValueError("empty credential half")
        except (ValueError, UnicodeDecodeError):
            return None, "ICICI Direct returned a malformed session token."

        logger.info("ICICI Direct Breeze authentication successful")
        return session_token, None

    except requests.HTTPError as e:
        logger.error(f"Breeze authentication HTTP error: {e}")
        return None, f"ICICI Direct authentication failed: HTTP {e.response.status_code if e.response is not None else 'error'}"
    except Exception as e:
        logger.exception(f"Breeze authentication exception: {type(e).__name__}")
        return None, f"An exception occurred during ICICI Direct authentication: {str(e)}"


def close_connection():
    """Logout hook - the Breeze session token simply expires server-side."""
    pass
