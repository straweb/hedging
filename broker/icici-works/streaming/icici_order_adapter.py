# broker/icici/streaming/icici_order_adapter.py
# Live order-update adapter for the ICICI Direct Breeze API.
#
# Breeze pushes order notifications over python-socket.io on
# https://livefeeds.icicidirect.com (the "order" event) with the same
# auth envelope as the market-data socket: auth={"user": <icici user id>,
# "token": <session key>}. The payload is ICICI's TUX message - a flat list
# of string fields whose layout depends on the message type:
#
#   messageType data[11] == "4" | "5"  -> cash/equity order
#       stockCode@14 orderFlow@15 limitMarketFlag@16 orderType@17
#       limitRate@18 productType@19 orderStatus@20 orderDate@21
#       tradeDate@22 orderReference@23 quantity@24 openQty@25
#       executedQty@26 cancelledQty@27 expiredQty@28 disclosedQty@29
#       stopLossTrigger@30 ... avgExecutedRate@42
#   messageType data[11] == "6" | "7"  -> derivative order
#       stockCode@14 productType@15 optionType@16 exerciseType@17
#       strikePrice@18 expiryDate@19 validDate@20 orderFlow@21
#       limitMarketFlag@22 orderType@23 limitRate@24 orderStatus@25
#       orderReference@26 totalQty@27 executedQty@28 cancelledQty@29
#       expiredQty@30 stopLossTrigger@31 ... avgExecutedRate@39
#
# Field semantics come from the official Breeze SDK (breeze_connect.py
# parse_data + config.TUX_TO_USER_MAP). The socket.io transport differs from
# the raw WebSocketApp the base class drives, so the connect/run internals
# are overridden here while the event publication path
# (_handle_message -> _publish_event_fields) is reused as-is.

import socketio

from database.auth_db import get_auth_token
from utils.logging import get_logger
from websocket_proxy.order_adapter import BaseOrderUpdateAdapter

from broker.icici.api.baseurl import LIVE_FEEDS_URL
from broker.icici.api.breeze_client import decode_session_token

logger = get_logger(__name__)

# TUX order status code -> OpenAlgo lowercase order_status vocabulary.
_STATUS_MAP = {
    "R": "open",  # Requested
    "Q": "open",  # Queued
    "O": "open",  # Ordered
    "P": "open",  # Partially Executed
    "E": "complete",  # Executed
    "J": "rejected",
    "X": "cancelled",  # Expired
    "B": "cancelled",  # Partially Executed And Expired
    "D": "cancelled",  # Partially Executed And Cancelled
    "F": "cancelled",  # Freezed
    "C": "cancelled",
}

_ORDER_FLOW_MAP = {"B": "BUY", "S": "SELL"}

# limitMarketFlag: L=Limit, M=Market, S=StopLoss.
_PRICETYPE_MAP = {"L": "LIMIT", "M": "MARKET", "S": "SL"}

# TUX product code -> Breeze product spelling for reverse_map_product_type.
_PRODUCT_TYPE_MAP = {"F": "futures", "O": "options", "C": "cash", "B": "btst"}

_OPTION_TYPE_MAP = {"C": "call", "P": "put", "*": "others"}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _field(data, index):
    """Field accessor that tolerates short TUX payloads ("" when absent)."""
    return data[index] if index < len(data) else ""


class IciciOrderUpdateAdapter(BaseOrderUpdateAdapter):
    """ICICI Direct order notifications over the socket.io live-feeds socket."""

    def __init__(self, user_id, feed_user_id, session_key):
        super().__init__(broker_name="icici", user_id=user_id)

        # The socket.io auth envelope needs the ICICI user id, which may
        # differ from the OpenAlgo username this adapter is keyed by.
        self.feed_user_id = feed_user_id
        self.session_key = session_key

        self._sio = None

    # ------------------------------------------------------------------
    # Lifecycle (socket.io transport - overrides the WebSocketApp layer)
    # ------------------------------------------------------------------

    @property
    def connected(self):
        sio = self._sio
        return bool(self._running and sio is not None and sio.connected)

    def connect(self):
        """Start the background connection thread. Idempotent."""
        import threading

        with self._lock:
            if self._running:
                return
            self._generation += 1
            generation = self._generation
            self._shutting_down = False
            self._auth_rejected = False
            self._running = True
            self._thread = threading.Thread(
                target=self._run_forever,
                args=(generation,),
                daemon=True,
                name=f"order-adapter-{self.broker_name}-{self.user_id}",
            )
            self._thread.start()

    def disconnect(self):
        """Stop the adapter and close the socket. Idempotent."""
        with self._lock:
            self._generation += 1
            self._shutting_down = True
            self._running = False
            sio = self._sio
            self._sio = None

        if sio is not None:
            try:
                sio.disconnect()
            except Exception:
                pass

    def _run_forever(self, generation=None):
        """Reconnect loop - same backoff schedule as the base class."""
        import time

        if generation is None:
            generation = self._generation

        from websocket_proxy.order_adapter import _RECONNECT_BACKOFFS

        attempt = 0
        while self._is_current(generation):
            try:
                self._connect_once(generation)
            except Exception as e:
                if self._looks_auth_rejected(e):
                    self._auth_rejected = True
                else:
                    self.logger.warning(
                        f"Order-update connection error ({self.broker_name}/{self.user_id}): {e}"
                    )
            if not self._is_current(generation):
                break

            if self._auth_rejected:
                with self._lock:
                    if self._generation == generation:
                        self._running = False
                self.logger.info(
                    f"Order-update adapter idle for {self.broker_name}/{self.user_id}: "
                    "the broker rejected the stored token. It reconnects on the "
                    "next broker login."
                )
                break

            delay = _RECONNECT_BACKOFFS[min(attempt, len(_RECONNECT_BACKOFFS) - 1)]
            attempt += 1
            self.logger.info(
                f"Order-update reconnect attempt {attempt} in {delay}s "
                f"({self.broker_name}/{self.user_id})"
            )
            slept = 0.0
            while slept < delay and self._is_current(generation):
                time.sleep(0.2)
                slept += 0.2

        self.logger.info(f"Order-update adapter stopped for {self.broker_name}/{self.user_id}")

    def _connect_once(self, generation):
        """Open one socket.io connection and block until it drops."""
        sio = socketio.Client(
            reconnection=False,  # this loop owns reconnection
            logger=False,
            engineio_logger=False,
        )

        def on_connect():
            if not self._is_current(generation):
                sio.disconnect()
                return
            self.logger.info(f"Order-update WS connected: {self.broker_name}/{self.user_id}")

        def on_disconnect(*args):
            self.logger.info(
                f"Order-update WS closed ({self.broker_name}/{self.user_id})"
            )

        def on_connect_error(data=None):
            if not self._is_current(generation):
                return
            if self._looks_auth_rejected(data):
                self._auth_rejected = True
                self.logger.warning(
                    f"Order-update WS token rejected ({self.broker_name}/{self.user_id}): {data}. "
                    "Not retrying until the next broker login."
                )
            else:
                self.logger.warning(
                    f"Order-update WS connect error ({self.broker_name}/{self.user_id}): {data}"
                )

        def on_order(data):
            # The base helper normalizes via self.normalize() and publishes.
            self._handle_message(data)

        sio.on("connect", on_connect)
        sio.on("disconnect", on_disconnect)
        sio.on("connect_error", on_connect_error)
        sio.on("order", on_order)

        with self._lock:
            if not self._is_current(generation):
                return
            self._sio = sio

        try:
            sio.connect(
                LIVE_FEEDS_URL,
                auth={"user": self.feed_user_id, "token": self.session_key},
                transports="websocket",
                wait_timeout=10,
            )
            # Blocks until the socket closes; on return the run loop decides
            # whether to reconnect (or stand down on an auth rejection).
            sio.wait()
        finally:
            with self._lock:
                if self._sio is sio:
                    self._sio = None
            try:
                if sio.connected:
                    sio.disconnect()
            except Exception:
                pass

    @staticmethod
    def _looks_auth_rejected(error):
        """Heuristic: did the socket handshake fail on credentials?"""
        text = str(error or "").lower()
        return "401" in text or "403" in text or "unauthorized" in text or "auth" in text

    # ------------------------------------------------------------------
    # Normalization (required by the base contract)
    # ------------------------------------------------------------------

    def normalize(self, raw_message):
        """TUX 'order' payload -> OrderUpdateEvent kwargs (or None)."""
        if not isinstance(raw_message, (list, tuple)) or len(raw_message) < 32:
            return None
        if "!" in str(raw_message[0]):
            return None  # room-framed market data, not an order message

        message_type = str(raw_message[11])
        if message_type in ("4", "5") and len(raw_message) >= 43:
            fields = self._normalize_equity(raw_message)
        elif message_type in ("6", "7") and len(raw_message) >= 40:
            fields = self._normalize_derivative(raw_message)
        else:
            return None

        if not fields:
            return None

        fields.setdefault("orderid", "")
        fields.setdefault("symbol", "")
        fields.setdefault("exchange", "")
        fields.setdefault("action", "")
        fields.setdefault("quantity", 0)
        fields.setdefault("price", 0.0)
        fields.setdefault("trigger_price", 0.0)
        fields.setdefault("pricetype", "")
        fields.setdefault("product", "")
        fields.setdefault("order_status", "")
        fields.setdefault("filled_quantity", 0)
        fields.setdefault("pending_quantity", 0)
        fields.setdefault("average_price", 0.0)
        fields.setdefault("rejection_reason", "")
        return fields

    def _resolve_symbol(self, exchange_code, stock_code, expiry, strike, right):
        """OpenAlgo symbol for a TUX instrument identity (best effort)."""
        try:
            from broker.icici.mapping.order_data import _resolve_symbol

            symbol, _ = _resolve_symbol(exchange_code, stock_code, expiry, strike, right)
            if symbol:
                return symbol
        except Exception:
            pass
        try:
            from broker.icici.mapping.transform_data import (
                BREEZE_INDEX_TO_OPENALGO,
                map_exchange,
            )

            map_exchange(exchange_code)
            return BREEZE_INDEX_TO_OPENALGO.get(stock_code, stock_code)
        except Exception:
            return stock_code

    def _normalize_equity(self, data):
        order_id = str(_field(data, 23) or "")
        stock_code = str(_field(data, 14) or "")
        exchange_code = str(_field(data, 13) or "")
        status = _STATUS_MAP.get(str(_field(data, 20)).upper(), str(_field(data, 20)).lower())
        product = _PRODUCT_TYPE_MAP.get(str(_field(data, 19)).upper(), "")

        from broker.icici.mapping.transform_data import map_exchange, reverse_map_product_type

        return {
            "orderid": order_id,
            "symbol": self._resolve_symbol(exchange_code, stock_code, "", "", ""),
            "exchange": map_exchange(exchange_code),
            "action": _ORDER_FLOW_MAP.get(str(_field(data, 15)).upper(), str(_field(data, 15)).upper()),
            "quantity": _int(_field(data, 24)),
            "price": _num(_field(data, 18)),
            "trigger_price": _num(_field(data, 30)),
            "pricetype": _PRICETYPE_MAP.get(str(_field(data, 16)).upper(), ""),
            "product": reverse_map_product_type(product, "EQ") or "",
            "order_status": status,
            "filled_quantity": _int(_field(data, 26)),
            "pending_quantity": _int(_field(data, 25)),
            "average_price": _num(_field(data, 42)),
        }

    def _normalize_derivative(self, data):
        exchange_code = str(_field(data, 13) or "")
        stock_code = str(_field(data, 14) or "")
        strike = str(_field(data, 18) or "")
        expiry = str(_field(data, 19) or "")
        right = _OPTION_TYPE_MAP.get(str(_field(data, 16)).upper(), "")
        product = _PRODUCT_TYPE_MAP.get(str(_field(data, 15)).upper(), "")
        status = _STATUS_MAP.get(str(_field(data, 25)).upper(), str(_field(data, 25)).lower())

        instrumenttype = (
            "CE" if right == "call" else ("PE" if right == "put" else ("FUT" if expiry else "EQ"))
        )

        from broker.icici.mapping.transform_data import map_exchange, reverse_map_product_type

        total = _int(_field(data, 27))
        executed = _int(_field(data, 28))
        cancelled = _int(_field(data, 29))
        expired = _int(_field(data, 30))

        return {
            "orderid": str(_field(data, 26) or ""),
            "symbol": self._resolve_symbol(exchange_code, stock_code, expiry, strike, right),
            "exchange": map_exchange(exchange_code),
            "action": _ORDER_FLOW_MAP.get(str(_field(data, 21)).upper(), str(_field(data, 21)).upper()),
            "quantity": total,
            "price": _num(_field(data, 24)),
            "trigger_price": _num(_field(data, 31)),
            "pricetype": _PRICETYPE_MAP.get(str(_field(data, 22)).upper(), ""),
            "product": reverse_map_product_type(product, instrumenttype) or "",
            "order_status": status,
            "filled_quantity": executed,
            "pending_quantity": max(total - executed - cancelled - expired, 0),
            "average_price": _num(_field(data, 39)),
        }


def create_icici_order_adapter(user_id: str):
    """Factory for services/order_update_service.py.

    Resolves the stored Breeze session token for ``user_id`` and splits it
    into the socket.io credential pair. Returns None when there is no live
    ICICI session so the service simply skips the feed.
    """
    import os

    from dotenv import load_dotenv

    load_dotenv()

    auth_token = get_auth_token(user_id, bypass_cache=True)
    if not auth_token:
        logger.warning(
            f"No ICICI Direct session token found for user {user_id}; "
            "order-update adapter not started"
        )
        return None

    try:
        feed_user_id, session_key = decode_session_token(auth_token)
    except (ValueError, UnicodeDecodeError) as e:
        logger.warning(f"Malformed ICICI Direct session token for user {user_id}: {e}")
        return None

    return IciciOrderUpdateAdapter(
        user_id=user_id, feed_user_id=feed_user_id, session_key=session_key
    )
