from __future__ import annotations

"""
ICICI Direct Breeze WebSocket adapter -> OpenAlgo unified streaming.

Responsibilities
----------------
- Resolve OpenAlgo symbol/exchange -> ICICI token
- Obtain OpenAlgo authentication token
- Obtain authenticated BreezeConnect client
- Resolve Breeze user_id/session_key
- Create/manage IciciWebSocket
- Subscribe/unsubscribe instruments
- Normalize ICICI ticks
- Publish normalized ticks through BaseBrokerWebSocketAdapter

Authentication/session creation is owned by breeze_client.py.
"""

import logging
from typing import Any, Optional

from database.auth_db import get_auth_token
from database.token_db import get_token

from broker.icici.api.breeze_client import get_breeze_client

from websocket_proxy.base_adapter import (
    BaseBrokerWebSocketAdapter,
)

from .icici_mapping import map_feed_data
from .icici_websocket import IciciWebSocket


logger = logging.getLogger(__name__)


# OpenAlgo numeric modes
#
# 1 = LTP
# 2 = QUOTE
# 3 = DEPTH
_MODE_TO_TOPIC = {
    1: "LTP",
    2: "QUOTE",
    3: "DEPTH",
}


class ICICIAdapter(BaseBrokerWebSocketAdapter):
    """
    ICICI Direct Breeze WebSocket adapter for OpenAlgo.
    """

    def __init__(self):
        super().__init__()

        self.broker_name = "icici"

        self.user_id: Optional[str] = None

        # OpenAlgo authentication token / api_session.
        self.auth_token: Optional[str] = None

        # Optional compatibility credentials.
        self.api_key: Optional[str] = None
        self.api_secret: Optional[str] = None

        # Breeze session key.
        self.session_token: Optional[str] = None

        # Authenticated BreezeConnect instance.
        self.breeze = None

        # Low-level websocket wrapper.
        self.websocket: Optional[IciciWebSocket] = None

        # Backward-compatible alias.
        self.ws_client: Optional[IciciWebSocket] = None

        self.running = False
        self.connected = False

        # token -> subscription information
        self.token_info: dict[int, dict] = {}

    # ==================================================================
    # INITIALIZATION
    # ==================================================================

    def initialize(
        self,
        broker_name=None,
        user_id=None,
        auth_data=None,
        api_key=None,
        auth_token=None,
        api_secret=None,
        session_token=None,
        *args,
        **kwargs,
    ):
        """
        Initialize the ICICI adapter.

        Authentication priority:

            1. auth_data["token"]
            2. auth_data["auth_token"]
            3. auth_data["access_token"]
            4. explicit auth_token
            5. get_auth_token(user_id, bypass_cache=True)
        """

        try:
            if broker_name:
                self.broker_name = broker_name

            self.user_id = user_id

            self.logger.info(
                "Initializing ICICI Direct WebSocket adapter for user %s",
                user_id,
            )

            resolved_auth_token = None

            # ----------------------------------------------------------
            # auth_data dictionary
            # ----------------------------------------------------------

            if isinstance(auth_data, dict):

                resolved_auth_token = (
                    auth_data.get("token")
                    or auth_data.get("auth_token")
                    or auth_data.get("access_token")
                )

                if auth_data.get("api_key") is not None:
                    api_key = auth_data.get("api_key")

                if auth_data.get("api_secret") is not None:
                    api_secret = auth_data.get("api_secret")

                if auth_data.get("session_token") is not None:
                    session_token = auth_data.get(
                        "session_token"
                    )

                if auth_data.get("user_id") is not None:
                    self.user_id = auth_data.get(
                        "user_id"
                    )

            # ----------------------------------------------------------
            # auth_data supplied directly as a string
            # ----------------------------------------------------------

            elif isinstance(auth_data, str):

                resolved_auth_token = auth_data.strip()

            # ----------------------------------------------------------
            # Explicit auth_token
            # ----------------------------------------------------------

            if not resolved_auth_token and auth_token:
                resolved_auth_token = auth_token

            # ----------------------------------------------------------
            # Database fallback
            # ----------------------------------------------------------

            if not resolved_auth_token and self.user_id:

                self.logger.info(
                    "No ICICI auth token supplied in auth_data; "
                    "loading stored auth token for user %s",
                    self.user_id,
                )

                resolved_auth_token = get_auth_token(
                    self.user_id,
                    bypass_cache=True,
                )

            # ----------------------------------------------------------
            # Normalize
            # ----------------------------------------------------------

            if resolved_auth_token is not None:

                resolved_auth_token = str(
                    resolved_auth_token
                ).strip()

            if not resolved_auth_token:

                self.logger.error(
                    "No ICICI Direct auth token found. "
                    "auth_data_type=%s, auth_data_keys=%s, "
                    "user_id_present=%s",
                    type(auth_data).__name__,
                    (
                        list(auth_data.keys())
                        if isinstance(auth_data, dict)
                        else "not-a-dict"
                    ),
                    bool(self.user_id),
                )

                return self._create_error_response(
                    "NO_AUTH_TOKEN",
                    "No ICICI Direct auth token found",
                )

            self.auth_token = resolved_auth_token

            self.api_key = (
                str(api_key).strip()
                if api_key
                else None
            )

            self.api_secret = (
                str(api_secret).strip()
                if api_secret
                else None
            )

            self.session_token = (
                str(session_token).strip()
                if session_token
                else None
            )

            self.logger.info(
                "ICICI Direct auth token resolved "
                "(token_present=%s)",
                bool(self.auth_token),
            )

            return self._create_success_response(
                "ICICI Direct adapter initialized"
            )

        except Exception as exc:

            self.logger.exception(
                "Error initializing ICICI Direct adapter: %s",
                exc,
            )

            return self._create_error_response(
                "INIT_ERROR",
                str(exc),
            )

    # ==================================================================
    # BREEZE CLIENT
    # ==================================================================

    def _get_breeze(self):
        """
        Return the authenticated BreezeConnect client.

        breeze_client.py owns:
            - API session handling
            - BreezeConnect creation
            - generate_session()
            - user_id
            - session_key
        """

        if not self.auth_token:

            raise RuntimeError(
                "ICICI authentication token is missing"
            )

        # Reuse the authenticated Breeze instance whenever possible.
        if self.breeze is not None:
            return self.breeze

        breeze = get_breeze_client(
            self.auth_token
        )

        if breeze is None:

            raise RuntimeError(
                "Unable to initialize ICICI Breeze client"
            )

        self.breeze = breeze

        return breeze

    # ==================================================================
    # BREEZE CREDENTIAL RESOLUTION
    # ==================================================================

    def _resolve_breeze_credentials(self):
        """
        Resolve Breeze user_id and session_key.
        """

        breeze = self._get_breeze()

        resolved_user_id = getattr(
            breeze,
            "user_id",
            None,
        )

        resolved_session_key = getattr(
            breeze,
            "session_key",
            None,
        )

        if not resolved_user_id:

            raise RuntimeError(
                "ICICI Breeze client did not provide user_id"
            )

        if not resolved_session_key:

            raise RuntimeError(
                "ICICI Breeze client did not provide session_key"
            )

        self.user_id = str(
            resolved_user_id
        ).strip()

        self.session_token = str(
            resolved_session_key
        ).strip()

        if not self.user_id:

            raise RuntimeError(
                "ICICI Breeze user_id is empty"
            )

        if not self.session_token:

            raise RuntimeError(
                "ICICI Breeze session_key is empty"
            )

        self.logger.info(
            "ICICI websocket credentials resolved "
            "(user_id_present=%s, session_key_present=%s)",
            bool(self.user_id),
            bool(self.session_token),
        )

        return (
            self.user_id,
            self.session_token,
        )

    # ==================================================================
    # CONNECTION
    # ==================================================================

    def connect(self):

        try:

            if not self.auth_token:

                return self._create_error_response(
                    "NO_AUTH_TOKEN",
                    "No ICICI authentication token available",
                )

            # ----------------------------------------------------------
            # Obtain authenticated Breeze client.
            # ----------------------------------------------------------

            breeze = self._get_breeze()

            # ----------------------------------------------------------
            # Resolve Breeze credentials.
            # ----------------------------------------------------------

            (
                self.user_id,
                self.session_token,
            ) = self._resolve_breeze_credentials()

            # ----------------------------------------------------------
            # Create websocket wrapper.
            #
            # IMPORTANT:
            # Pass the authenticated BreezeConnect instance.
            # ----------------------------------------------------------

            self.websocket = IciciWebSocket(
                breeze=breeze,
                auth_token=self.auth_token,
                api_key=self.api_key,
                api_secret=self.api_secret,
                session_token=self.session_token,
                user_id=self.user_id,
                on_message_callback=self._on_message,
                on_error_callback=self._on_error,
                on_close_callback=self._on_close,
                on_open_callback=self._on_open,
            )

            self.ws_client = self.websocket

            self.logger.info(
                "Starting ICICI Direct websocket"
            )

            result = self.websocket.connect()

            if isinstance(result, dict):

                status = result.get("status")
                success = result.get("success")

                if (
                    status == "error"
                    or success is False
                ):

                    self.connected = False
                    self.running = False

                    raise RuntimeError(
                        result.get(
                            "message",
                            "ICICI websocket connection failed",
                        )
                    )

            if result is False:

                self.connected = False
                self.running = False

                return self._create_error_response(
                    "CONNECT_ERROR",
                    "ICICI websocket failed to start",
                )

            self.running = True

            # The websocket may connect asynchronously.
            self.connected = self.websocket.is_connected()

            self.logger.info(
                "ICICI Direct websocket start requested "
                "(connected=%s)",
                self.connected,
            )

            return self._create_success_response(
                "ICICI Direct WebSocket connecting"
            )

        except Exception as exc:

            self.running = False
            self.connected = False

            self.logger.exception(
                "Error connecting ICICI Direct WebSocket: %s",
                exc,
            )

            return self._create_error_response(
                "CONNECT_ERROR",
                str(exc),
            )

    # ==================================================================
    # CALLBACKS
    # ==================================================================

    def _on_open(
        self,
        *args,
        **kwargs,
    ):

        self.connected = True
        self.running = True

        self.logger.info(
            "ICICI Direct websocket opened"
        )

    def _on_close(
        self,
        *args,
        **kwargs,
    ):

        self.connected = False
        self.running = False

        self.logger.info(
            "ICICI Direct websocket closed"
        )

    def _on_error(
        self,
        error=None,
        *args,
        **kwargs,
    ):

        self.connected = False

        self.logger.error(
            "ICICI Direct websocket error: %s",
            error,
        )

    def _on_message(
        self,
        message,
        *args,
        **kwargs,
    ):

        try:

            mapped = map_feed_data(
                message
            )

            if mapped is None:
                return

            if isinstance(mapped, list):

                for item in mapped:

                    if isinstance(item, dict):

                        self._publish_mapped_tick(
                            item
                        )

                return

            if not isinstance(mapped, dict):

                self.logger.debug(
                    "Ignoring unsupported ICICI mapped tick type: %s",
                    type(mapped).__name__,
                )

                return

            self._publish_mapped_tick(
                mapped
            )

        except Exception as exc:

            self.logger.exception(
                "Error handling ICICI websocket message: %s",
                exc,
            )

    # ==================================================================
    # MARKET DATA PUBLISH
    # ==================================================================

    def _publish_mapped_tick(
        self,
        mapped,
    ):

        symbol = (
            mapped.get("symbol")
            or mapped.get("stock_code")
            or mapped.get("trading_symbol")
        )

        exchange = (
            mapped.get("exchange")
            or mapped.get("exchange_code")
            or "NSE"
        )

        if not symbol:
            return

        mode = (
            mapped.get("mode")
            or mapped.get("subscription_mode")
            or 2
        )

        try:

            mode = int(mode)

        except (
            ValueError,
            TypeError,
        ):

            mode = 2

        topic_mode = _MODE_TO_TOPIC.get(
            mode,
            "QUOTE",
        )

        data = self._normalize(
            mapped,
            symbol,
            exchange,
            topic_mode,
        )

        topic = mapped.get(
            "topic"
        )

        if not topic:

            topic = (
                f"{exchange}_"
                f"{symbol}_"
                f"{topic_mode}"
            )

        self.publish_market_data(
            topic,
            data,
        )

    # ==================================================================
    # TOKEN RESOLUTION
    # ==================================================================

    def _resolve_token(
        self,
        symbol,
        exchange,
    ):

        token = get_token(
            symbol,
            exchange,
        )

        if token is None:
            return None

        try:

            return int(token)

        except (
            ValueError,
            TypeError,
        ):

            self.logger.error(
                "Non-integer ICICI token for "
                "%s:%s -> %r",
                exchange,
                symbol,
                token,
            )

            return None

    # ==================================================================
    # SUBSCRIBE
    # ==================================================================

    def subscribe(
        self,
        symbol,
        exchange,
        token=None,
        mode=2,
        depth_level=5,
    ):

        try:

            if self.websocket is None:

                result = self.connect()

                if (
                    isinstance(result, dict)
                    and result.get("status") == "error"
                ):

                    return result

            if self.websocket is None:

                return self._create_error_response(
                    "NOT_INITIALIZED",
                    "ICICI websocket is not initialized",
                )

            # ----------------------------------------------------------
            # Token
            # ----------------------------------------------------------

            if token is None:

                token = self._resolve_token(
                    symbol,
                    exchange,
                )

            else:

                try:

                    token = int(token)

                except (
                    ValueError,
                    TypeError,
                ):

                    token = None

            if token is None:

                return self._create_error_response(
                    "TOKEN_NOT_FOUND",
                    f"No ICICI token for "
                    f"{exchange}:{symbol}",
                )

            try:

                mode = int(mode)

            except (
                ValueError,
                TypeError,
            ):

                mode = 2

            topic_mode = _MODE_TO_TOPIC.get(
                mode,
                "QUOTE",
            )

            self.token_info[token] = {
                "symbol": symbol,
                "exchange": exchange,
                "mode": mode,
                "topic_mode": topic_mode,
                "token": token,
                "depth_level": depth_level,
            }

            result = self.websocket.subscribe(
                symbol=symbol,
                exchange=exchange,
                token=token,
                mode=mode,
                depth_level=depth_level,
            )

            if (
                isinstance(result, dict)
                and result.get("success") is False
            ):

                self.token_info.pop(
                    token,
                    None,
                )

                return self._create_error_response(
                    "SUBSCRIBE_ERROR",
                    result.get(
                        "message",
                        "ICICI subscription failed",
                    ),
                )

            self.logger.info(
                "Subscribed ICICI %s:%s token=%s mode=%s",
                exchange,
                symbol,
                token,
                mode,
            )

            return self._create_success_response(
                f"Subscribed {exchange}:{symbol}",
                symbol=symbol,
                exchange=exchange,
                mode=mode,
                token=str(token),
            )

        except Exception as exc:

            self.logger.exception(
                "Error subscribing ICICI %s:%s: %s",
                exchange,
                symbol,
                exc,
            )

            return self._create_error_response(
                "SUBSCRIBE_ERROR",
                str(exc),
            )

    # ==================================================================
    # UNSUBSCRIBE
    # ==================================================================

    def unsubscribe(
        self,
        symbol,
        exchange,
        token=None,
        mode=2,
    ):

        try:

            if self.websocket is None:

                return self._create_success_response(
                    "ICICI websocket is not active"
                )

            if token is None:

                token = self._resolve_token(
                    symbol,
                    exchange,
                )

            else:

                try:

                    token = int(token)

                except (
                    ValueError,
                    TypeError,
                ):

                    token = None

            if token is None:

                return self._create_error_response(
                    "TOKEN_NOT_FOUND",
                    f"No ICICI token for "
                    f"{exchange}:{symbol}",
                )

            result = self.websocket.unsubscribe(
                symbol=symbol,
                exchange=exchange,
                token=token,
                mode=mode,
            )

            self.token_info.pop(
                token,
                None,
            )

            self.logger.info(
                "Unsubscribed ICICI %s:%s token=%s",
                exchange,
                symbol,
                token,
            )

            return result

        except Exception as exc:

            self.logger.exception(
                "Error unsubscribing ICICI %s:%s: %s",
                exchange,
                symbol,
                exc,
            )

            return self._create_error_response(
                "UNSUBSCRIBE_ERROR",
                str(exc),
            )

    # ==================================================================
    # UNSUBSCRIBE ALL
    # ==================================================================

    def unsubscribe_all(self):

        try:

            if self.websocket is None:

                self.token_info.clear()

                return self._create_success_response(
                    "No ICICI websocket subscriptions"
                )

            result = self.websocket.unsubscribe_all()

            self.token_info.clear()

            return result

        except Exception as exc:

            self.logger.exception(
                "Error removing all ICICI subscriptions: %s",
                exc,
            )

            return self._create_error_response(
                "UNSUBSCRIBE_ALL_ERROR",
                str(exc),
            )

    # ==================================================================
    # NORMALIZATION
    # ==================================================================

    def _normalize(
        self,
        tick,
        symbol,
        exchange,
        topic_mode="QUOTE",
    ):

        if not isinstance(tick, dict):
            tick = {}

        # --------------------------------------------------------------
        # LTP
        # --------------------------------------------------------------

        ltp = tick.get("ltp")

        if ltp is None:
            ltp = tick.get("last_price")

        if ltp is None:
            ltp = tick.get("last")

        if ltp is None:
            ltp = 0

        # --------------------------------------------------------------
        # Previous close
        # --------------------------------------------------------------

        close = tick.get("close")

        if close is None:
            close = tick.get("previous_close")

        if close is None:
            close = tick.get("prev_close")

        data = {
            "symbol": symbol,
            "exchange": exchange,
            "token": str(
                tick.get(
                    "token",
                    "",
                )
            ),
            "ltp": ltp,
            "last_price": ltp,
            "ltt": (
                tick.get("ltt")
                or tick.get("last_trade_time")
                or tick.get("timestamp")
            ),
            "timestamp": tick.get(
                "timestamp"
            ),
        }

        # --------------------------------------------------------------
        # Change
        # --------------------------------------------------------------

        if close is not None:

            data["close"] = close
            data["prev_close"] = close

            try:

                close_float = float(close)
                ltp_float = float(ltp)

                if close_float != 0:

                    change = (
                        ltp_float
                        - close_float
                    )

                    data["change"] = round(
                        change,
                        2,
                    )

                    data["change_percent"] = round(
                        change
                        / close_float
                        * 100,
                        2,
                    )

            except (
                TypeError,
                ValueError,
            ):

                pass

        # --------------------------------------------------------------
        # Quote/depth
        # --------------------------------------------------------------

        if topic_mode in (
            "QUOTE",
            "DEPTH",
        ):

            data["volume"] = (
                tick.get(
                    "volume",
                    tick.get(
                        "total_quantity_traded",
                        0,
                    ),
                )
                or 0
            )

            data["last_quantity"] = (
                tick.get(
                    "last_quantity",
                    tick.get(
                        "ltq",
                        0,
                    ),
                )
                or 0
            )

            data["average_price"] = (
                tick.get(
                    "average_price",
                    tick.get(
                        "avg_price",
                        0,
                    ),
                )
                or 0
            )

            data["total_buy_quantity"] = (
                tick.get(
                    "total_buy_quantity",
                    0,
                )
                or 0
            )

            data["total_sell_quantity"] = (
                tick.get(
                    "total_sell_quantity",
                    0,
                )
                or 0
            )

            for key in (
                "open",
                "high",
                "low",
            ):

                if key in tick:
                    data[key] = tick[key]

            oi = tick.get("oi")

            if oi is None:
                oi = tick.get("open_interest")

            if oi is not None:

                data["oi"] = oi
                data["open_interest"] = oi

            bid = tick.get("bid")

            if bid is None:
                bid = tick.get("best_bid_price")

            ask = tick.get("ask")

            if ask is None:
                ask = tick.get("best_offer_price")

            if bid is not None:
                data["bid"] = bid

            if ask is not None:
                data["ask"] = ask

        # --------------------------------------------------------------
        # Depth
        # --------------------------------------------------------------

        if topic_mode == "DEPTH":

            if "depth" in tick:

                data["depth"] = tick["depth"]

            elif "market_depth" in tick:

                data["depth"] = tick[
                    "market_depth"
                ]

            if "upper_limit" in tick:

                data["upper_circuit"] = (
                    tick["upper_limit"]
                )

            if "lower_limit" in tick:

                data["lower_circuit"] = (
                    tick["lower_limit"]
                )

        return data

    # ==================================================================
    # DISCONNECT
    # ==================================================================

    def disconnect(self):

        self.running = False
        self.connected = False

        try:

            if self.websocket is not None:

                result = self.websocket.disconnect()

            else:

                result = self._create_success_response(
                    "ICICI websocket disconnected"
                )

        except Exception as exc:

            self.logger.exception(
                "Error disconnecting ICICI websocket: %s",
                exc,
            )

            result = self._create_error_response(
                "DISCONNECT_ERROR",
                str(exc),
            )

        finally:

            self.websocket = None
            self.ws_client = None

            self.breeze = None

            self.token_info.clear()

            try:

                self.cleanup_zmq()

            except Exception:

                self.logger.exception(
                    "Error cleaning ICICI adapter ZMQ resources"
                )

        return result

    def close(self):
        return self.disconnect()


# Backward-compatible class name.
IciciWebSocketAdapter = ICICIAdapter
