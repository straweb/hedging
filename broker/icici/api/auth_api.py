# broker/icici/api/auth_api.py
# ICICI Direct Breeze API authentication.
#
# Flow: the user opens the Breeze login URL built from the app key, logs in on
# ICICI Direct, and the browser is redirected to the registered redirect URI
# as /icici/callback?api_session=<token>. blueprints/brlogin.py hands that
# api_session value to authenticate_broker(), which exchanges it for the
# long-lived (for the day) session token.
#
# The value stored in OpenAlgo's auth table is the base64 "user_id:session_key"
# string that every subsequent Breeze request sends verbatim as X-SessionToken;
# the streaming adapter decodes it to authenticate the socket.io feed.

import json
import os

from broker.icici.api.baseurl import get_url
from broker.icici.api.breeze_client import (
    decode_session_token,
    extract_error,
    is_success,
)
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)


def authenticate_broker(api_session):
    """Exchange the redirect api_session token for a Breeze session token.

    Returns the 2-tuple (auth_token, error_message) expected by the generic
    redirect-broker path in brlogin.py. auth_token is the base64
    "user_id:session_key" X-SessionToken value; it is stored as-is and
    encrypted by upsert_auth.
    """
    import os

    if not api_session:
        return None, "No api_session token received from ICICI Direct. Please login again."

    # The session exchange endpoint needs only the app key, which is sent in
    # the body (and used to build headers); no checksum is verified on it.
    api_key = os.getenv("BROKER_API_KEY")
    if not api_key:
        return None, "BROKER_API_KEY is not configured for ICICI Direct (Breeze API)."

    body = json.dumps(
        {"SessionToken": api_session, "AppKey": api_key}, separators=(",", ":")
    )

    try:
        # The session exchange endpoint needs only the app key, which is sent
        # in the body (and used to build headers); no checksum is verified.
        client = get_httpx_client()
        url = get_url("customerdetails")
        response = client.get(url, headers={"Content-Type": "application/json"}, content=body)
        response.status = response.status_code
        data = json.loads(response.text)

        if not is_success(data):
            error = extract_error(data)
            logger.error(f"Breeze session exchange failed: {error}")
            return None, f"ICICI Direct authentication failed: {error}"

        session_token = data["Success"].get("session_token")
        if not session_token:
            return None, "ICICI Direct did not return a session token."

        # Validate the token decodes into user_id + session_key up front so a
        # malformed payload fails at login instead of on the first order.
        try:
            user_id, _ = decode_session_token(session_token)
        except (ValueError, UnicodeDecodeError):
            return None, "ICICI Direct returned a malformed session token."

        logger.info("ICICI Direct Breeze authentication successful")
        return session_token, None

    except Exception as e:
        logger.exception(f"Breeze authentication exception: {type(e).__name__}")
        return None, f"An exception occurred during ICICI Direct authentication: {str(e)}"
