from __future__ import annotations

"""
ICICI Direct Breeze client factory for OpenAlgo.

Authentication model
---------------------

OpenAlgo stores the ICICI API session returned by the ICICI login flow.

The official Breeze SDK expects:

    BreezeConnect(api_key=...)
    breeze.generate_session(
        api_secret=...,
        session_token=api_session,
    )

The SDK then:

    1. stores the supplied API session
    2. calls CustomerDetails
    3. obtains the authenticated session information
    4. initializes ApificationBreeze
    5. allows REST APIs such as get_funds()
    6. allows websocket APIs through ws_connect()

This module owns the BreezeConnect lifecycle so that funds,
portfolio and websocket code use the same authentication mechanism.
"""
import json
import os
import threading
from typing import Any

from breeze_connect import BreezeConnect

from utils.logging import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------

_clients: dict[str, BreezeConnect] = {}

_clients_lock = threading.RLock()


# ---------------------------------------------------------------------
# Environment credentials
# ---------------------------------------------------------------------

def _get_credentials() -> tuple[str, str]:
    """
    Read ICICI Breeze API credentials.

    OpenAlgo should provide:

        BROKER_API_KEY
        BROKER_API_SECRET
    """

    api_key = os.getenv(
        "BROKER_API_KEY",
        "",
    ).strip()

    api_secret = os.getenv(
        "BROKER_API_SECRET",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "BROKER_API_KEY is not configured"
        )

    if not api_secret:
        raise RuntimeError(
            "BROKER_API_SECRET is not configured"
        )

    return api_key, api_secret


# ---------------------------------------------------------------------
# Authentication decoding
# ---------------------------------------------------------------------

def decode_auth(auth: Any) -> dict:
    """
    Decode OpenAlgo's stored ICICI authentication value.

    Supported formats:

    1. Dictionary

       {
           "api_session": "...",
           "user_id": "...",
           "session_key": "..."
       }

    2. JSON string

       '{"api_session":"..."}'

    3. Plain API session string

       "..."
    """

    if not auth:
        raise RuntimeError(
            "ICICI authentication token is empty"
        )

    if isinstance(auth, dict):
        return dict(auth)

    if isinstance(auth, bytes):
        auth = auth.decode(
            "utf-8",
            errors="ignore",
        )

    auth = str(auth).strip()

    if not auth:
        raise RuntimeError(
            "ICICI authentication token is empty"
        )

    try:
        decoded = json.loads(auth)

        if isinstance(decoded, dict):
            return decoded

    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        pass

    return {
        "api_session": auth
    }


# ---------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------

def _cache_key(data: dict) -> str:
    """
    Build a stable cache key.

    Prefer the authenticated session key when available.
    Fall back to api_session for legacy authentication.
    """

    user_id = str(
        data.get("user_id")
        or ""
    ).strip()

    session_key = str(
        data.get("session_key")
        or ""
    ).strip()

    api_session = str(
        data.get("api_session")
        or data.get("apisession")
        or ""
    ).strip()

    if user_id and session_key:
        return f"{user_id}:{session_key}"

    if api_session:
        return f"api_session:{api_session}"

    raise RuntimeError(
        "ICICI authentication data does not contain "
        "api_session or session_key"
    )


# ---------------------------------------------------------------------
# Create authenticated Breeze client
# ---------------------------------------------------------------------

def _create_client(
    data: dict,
) -> BreezeConnect:
    """
    Create an authenticated BreezeConnect instance.

    IMPORTANT:

    The official Breeze SDK requires generate_session().
    Do not manually populate private SDK fields.

    generate_session() is intentionally performed only when
    constructing a new client.
    """

    api_key, api_secret = _get_credentials()

    api_session = (
        data.get("api_session")
        or data.get("apisession")
        or data.get("session_token")
    )

    if not api_session:
        raise RuntimeError(
            "ICICI api_session is missing from authentication data. "
            "Reconnect ICICI Direct."
        )

    api_session = str(
        api_session
    ).strip()

    if not api_session:
        raise RuntimeError(
            "ICICI api_session is empty. "
            "Reconnect ICICI Direct."
        )

    logger.info(
        "Creating ICICI Breeze client"
    )

    breeze = BreezeConnect(
        api_key=api_key
    )

    try:

        logger.info(
            "Generating ICICI Breeze session"
        )
        breeze.generate_session(
            api_secret=api_secret,
            session_token=api_session,
        )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze session generation failed"
        )

        raise RuntimeError(
            f"Unable to initialize ICICI Breeze session: {exc}"
        ) from exc

    if not getattr(
        breeze,
        "api_handler",
        None,
    ):
        raise RuntimeError(
            "ICICI Breeze API handler was not initialized"
        )

    user_id = getattr(
        breeze,
        "user_id",
        None,
    )

    session_key = getattr(
        breeze,
        "session_key",
        None,
    )

    if not user_id:
        raise RuntimeError(
            "ICICI Breeze user_id was not initialized"
        )

    if not session_key:
        raise RuntimeError(
            "ICICI Breeze session_key was not initialized"
        )

    logger.info(
        "ICICI Breeze client initialized successfully "
        "(user_id_present=%s, session_key_present=%s)",
        bool(user_id),
        bool(session_key),
    )

    return breeze

def get_breeze_websocket_credentials(
    auth_token,
) -> tuple[str, str]:
    """
    Return the authenticated Breeze websocket credentials.

    Returns:

        (user_id, session_key)

    The values come directly from the authenticated
    BreezeConnect instance created by generate_session().
    """

    breeze = get_breeze_client(
        auth_token
    )

    user_id = getattr(
        breeze,
        "user_id",
        None,
    )

    session_key = getattr(
        breeze,
        "session_key",
        None,
    )

    if not user_id:

        raise RuntimeError(
            "ICICI Breeze user_id is unavailable"
        )

    if not session_key:

        raise RuntimeError(
            "ICICI Breeze session_key is unavailable"
        )

    return (
        str(user_id).strip(),
        str(session_key).strip(),
    )


# ---------------------------------------------------------------------
# Public client factory
# ---------------------------------------------------------------------

def get_breeze_client(
    auth_token,
) -> BreezeConnect:
    """
    Return a cached authenticated BreezeConnect client.

    OpenAlgo's auth_token is the source of truth.

    A new Breeze client is created only when the authentication
    session is not already cached.
    """

    data = decode_auth(
        auth_token
    )

    key = _cache_key(
        data
    )

    with _clients_lock:

        existing = _clients.get(
            key
        )

        if existing is not None:

            # Make sure the SDK is still usable.
            if getattr(
                existing,
                "api_handler",
                None,
            ) is not None:

                return existing

            # Broken/stale client.
            _clients.pop(
                key,
                None,
            )

        client = _create_client(
            data
        )

        _clients[key] = client

        return client


# ---------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------

def clear_breeze_clients() -> None:
    """
    Disconnect and clear all cached Breeze clients.

    Call this when:

    - ICICI user logs out
    - ICICI authentication is refreshed
    - session expires
    - application restarts
    """

    with _clients_lock:

        clients = list(
            _clients.values()
        )

        _clients.clear()

    for client in clients:

        try:

            client.ws_disconnect()

        except Exception:

            # ws_disconnect may fail if the websocket was never
            # established. This must not prevent cache cleanup.
            pass


def clear_breeze_client(
    auth_token,
) -> None:
    """
    Clear one cached Breeze client.
    """

    try:

        data = decode_auth(
            auth_token
        )

        key = _cache_key(
            data
        )

    except Exception:

        return

    with _clients_lock:

        client = _clients.pop(
            key,
            None,
        )

    if client is not None:

        try:

            client.ws_disconnect()

        except Exception:

            pass


# ---------------------------------------------------------------------
# Compatibility aliases
# ---------------------------------------------------------------------

clear_client_cache = clear_breeze_clients
get_client = get_breeze_client
