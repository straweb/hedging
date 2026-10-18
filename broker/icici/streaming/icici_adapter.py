# broker/icici/streaming/icici_adapter.py
# Live-feed adapter for the ICICI Direct Breeze API.
#
# Breeze streams over python-socket.io (engine.io over websocket) rather than
# a raw WebSocket, so this adapter drives socketio.Client instead of the
# websocket-client WebSocketApp the other OpenAlgo adapters use. The wire
# contract comes from the official Breeze SDK:
#
#   - connect to https://livestream.icicidirect.com with
#     auth={"user": <icici user id>, "token": <session key>}
#   - join a room with emit("join", "<prefix>.<type>!<token>") and receive
#     "stock" events carrying a CSV-style list of fields
#   - re-join every room on socket reconnect (the SDK's rewatch pattern)
#
# The "stock" payload layouts (verified against the SDK parser):
#   quotes (type 1), NSE/BSE  21 fields: open@1 last@2 high@3 low@4 change@5
#                             bid@6 bidQty@7 ask@8 askQty@9 ltq@10 avg@11
#                             ttq@12 buyQty@13 sellQty@14 ttv@15 ... ltt@19 close@20
#   quotes (type 1), F&O      23 fields: ... OI@12 CHNGOI@13 ttq@14 ... ltt@21 close@22
#   depth  (type 2)                      time@1, 5-level ladder @2
#   MCX    (prefix 6)         22 base fields: volume@1 ttq@4 last@5 ltq@6
#                             ltt@7 avg@8 buy@9 sell@10 close@12 open@13
#                             high@14 low@15 OI@17, ladder from 22
#   index  (prefix 3)                    open@1 high@2 low@3 prevClose@4
#                             ltp@5 ltq@6 ltt@7 volume@8 pct@9 abs@10 ...
#                             bid@12 bidQty@13 ask@14 askQty@15 oi@16

import json
import os
import sys
import threading
import time
from typing import Any

import socketio

from database.auth_db import get_auth_token
from database.token_db import get_br_symbol, get_symbol, get_token
from utils.logging import get_logger

sys.path.append(os.path.join(os.path.dirname(__file__), "../../../"))

from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter
from websocket_proxy.mapping import SymbolMapper

from broker.icici.api.breeze_client import decode_session_token
from broker.icici.streaming.icici_mapping import (
    IciciCapabilityRegistry,
    IciciExchangeMapper,
)
from broker.icici.api.baseurl import LIVE_STREAM_URL

logger = get_logger(__name__)

# Import the real threading module even when the Flask process is
# monkey-patched by eventlet - the proxy subprocess runs unpatched, but the
# adapter module may be imported from either context (zerodha pattern).
from utils import runtime as _runtime

_real_threading = _runtime.original("threading")


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


class IciciWebSocketAdapter(BaseBrokerWebSocketAdapter):
    """ICICI Direct live market data adapter (socket.io transport)."""

    def __init__(self):
        super().__init__()

        self.broker_name = "icici"
        self.user_id = None
        self.session_key = None

        self.sio = None
        self.connected = False
        self.running = False
        self._disconnected = False

        # correlation id -> subscription metadata (source of truth for replay)
        self.subscriptions = {}
        # livestream room -> (symbol, exchange, mode)
        self.token_map = {}

        self.lock = _real_threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, broker_name, user_id, auth_data=None):
        """Resolve the Breeze session (user_id + session_key) for the feed."""
        self.broker_name = broker_name or "icici"
        self.user_id = user_id

        if auth_data:
            auth_token = auth_data.get("auth_token") or ""
        else:
            auth_token = get_auth_token(user_id) or ""

        if not auth_token:
            raise ValueError(
                f"No ICICI Direct session token found for user {user_id}. Please re-authenticate."
            )

        try:
            feed_user_id, session_key = decode_session_token(auth_token)
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(f"Malformed ICICI Direct session token for user {user_id}: {e}")

        # The socket.io auth needs the ICICI user id, which may differ from
        # the OpenAlgo username in the auth table's name column.
        self.user_id = feed_user_id
        self.session_key = session_key

        self.running = True
        self._disconnected = False
        logger.info("ICICI Direct adapter initialized for user feed")

    def connect(self):
        """Open the socket.io market-data connection."""
        if not self.session_key:
            return {
                "status": "error",
                "code": "AUTH_ERROR",
                "message": "Adapter not initialized. Call initialize() first.",
            }

        if self.sio is None:
            self.sio = socketio.Client(
                reconnection=True,
                reconnection_attempts=0,  # infinite; disconnect() stops it
                reconnection_delay=2,
                reconnection_delay_max=30,
                logger=False,
                engineio_logger=False,
            )
            self.sio.on("connect", self._on_connect)
            self.sio.on("disconnect", self._on_socket_disconnect)
            self.sio.on("stock", self._on_stock)
            self.sio.on("connect_error", self._on_connect_error)

        try:
            self.sio.connect(
                LIVE_STREAM_URL,
                auth={"user": self.user_id, "token": self.session_key},
                transports="websocket",
                wait_timeout=10,
            )
            # on('connect') fires within connect() and sets self.connected.
            return {
                "status": "success",
                "message": "Connected to ICICI Direct live stream"
                if self.connected
                else "ICICI Direct live stream connection pending",
            }
        except Exception as e:
            logger.error(f"ICICI Direct live stream connect failed: {e}")
            return {"status": "error", "code": "CONNECTION_ERROR", "message": str(e)}

    def disconnect(self):
        """Disconnect and release resources (idempotent)."""
        if self._disconnected:
            return
        self._disconnected = True
        self.running = False
        self.connected = False

        if self.sio is not None:
            try:
                if self.sio.connected:
                    self.sio.emit("disconnect", "transport close")
                self.sio.disconnect()
            except Exception as e:
                logger.warning(f"Error while disconnecting ICICI stream: {e}")
            self.sio = None

        self.subscriptions = {}
        self.token_map = {}
        self.cleanup_zmq()

    # ------------------------------------------------------------------
    # Socket.io handlers
    # ------------------------------------------------------------------

    def _on_connect(self):
        """(Re)connection callback - replay every room from subscriptions."""
        self.connected = True
        logger.info("Connected to ICICI Direct live stream")

        with self.lock:
            channels = list({sub["channel"] for sub in self.subscriptions.values()})

        for channel in channels:
            try:
                self.sio.emit("join", channel)
            except Exception as e:
                logger.error(f"Failed to rejoin room {channel}: {e}")
        if channels:
            logger.info(f"Rejoined {len(channels)} ICICI stream room(s) after connect")

    def _on_socket_disconnect(self, *args):
        self.connected = False
        logger.info("ICICI Direct live stream disconnected; auto-reconnect is active")

    def _on_connect_error(self, data=None):
        self.connected = False
        logger.error(f"ICICI Direct live stream connect error: {data}")

    # ------------------------------------------------------------------
    # Subscription API
    # ------------------------------------------------------------------

    def subscribe(self, symbol, exchange, mode=2, depth_level=5):
        """Subscribe a symbol; returns the shared proxy response contract."""
        if not IciciCapabilityRegistry.is_mode_supported(mode):
            return self._create_error_response(
                "INVALID_MODE", f"Invalid mode {mode}. Must be 1 (LTP), 2 (Quote), or 3 (Depth)"
            )
        if mode == 3 and not IciciCapabilityRegistry.is_depth_level_supported(depth_level):
            return self._create_error_response(
                "INVALID_DEPTH", f"Invalid depth level {depth_level}. ICICI Direct supports 5 levels"
            )

        token_info = SymbolMapper.get_token_from_symbol(symbol, exchange)
        if not token_info:
            return self._create_error_response(
                "SYMBOL_NOT_FOUND", f"Symbol {symbol} not found for exchange {exchange}"
            )

        token = str(token_info["token"])
        brexchange = token_info["brexchange"]

        # Index spot quotes ride the dedicated prefix-3 rooms, which only the
        # OpenAlgo exchange argument can identify (index rows store the
        # venue's own brexchange, NSE/BSE).
        if exchange in ("NSE_INDEX", "BSE_INDEX"):
            prefix = "3"
        else:
            prefix = IciciExchangeMapper.get_prefix(brexchange)
        channel_type = "2" if mode == 3 else "1"
        channel = f"{prefix}.{channel_type}!{token}"
        correlation_id = f"{symbol}_{exchange}_{mode}"

        with self.lock:
            self.subscriptions[correlation_id] = {
                "symbol": symbol,
                "exchange": exchange,
                "brexchange": brexchange,
                "token": token,
                "mode": mode,
                "depth_level": depth_level,
                "channel": channel,
            }
            self.token_map[channel] = (symbol, exchange, mode)

        if self.connected and self.sio is not None:
            try:
                self.sio.emit("join", channel)
            except Exception as e:
                logger.error(f"Failed to join room {channel}: {e}")
                return self._create_error_response("SUBSCRIPTION_ERROR", str(e))

        return self._create_success_response(
            f"Subscribed to {symbol}.{exchange}",
            symbol=symbol,
            exchange=exchange,
            mode=mode,
            depth_level=depth_level,
        )

    def unsubscribe(self, symbol, exchange, mode=2):
        """Unsubscribe a symbol; emits leave for its room."""
        token_info = SymbolMapper.get_token_from_symbol(symbol, exchange)
        if not token_info:
            return self._create_error_response(
                "SYMBOL_NOT_FOUND", f"Symbol {symbol} not found for exchange {exchange}"
            )

        token = str(token_info["token"])
        brexchange = token_info["brexchange"]
        if exchange in ("NSE_INDEX", "BSE_INDEX"):
            prefix = "3"
        else:
            prefix = IciciExchangeMapper.get_prefix(brexchange)
        channel_type = "2" if mode == 3 else "1"
        channel = f"{prefix}.{channel_type}!{token}"
        correlation_id = f"{symbol}_{exchange}_{mode}"

        with self.lock:
            if correlation_id not in self.subscriptions:
                return self._create_error_response(
                    "NOT_SUBSCRIBED", f"Not subscribed to {symbol}.{exchange}"
                )
            del self.subscriptions[correlation_id]
            # Only leave the room when no other subscription still uses it.
            if not any(sub["channel"] == channel for sub in self.subscriptions.values()):
                self.token_map.pop(channel, None)
                still_needed = False
            else:
                still_needed = True

        if not still_needed and self.connected and self.sio is not None:
            try:
                self.sio.emit("leave", channel)
            except Exception as e:
                logger.error(f"Failed to leave room {channel}: {e}")
                return self._create_error_response("UNSUBSCRIPTION_ERROR", str(e))

        return self._create_success_response(
            f"Unsubscribed from {symbol}.{exchange}",
            symbol=symbol,
            exchange=exchange,
            mode=mode,
        )

    # ------------------------------------------------------------------
    # Tick parsing / publishing
    # ------------------------------------------------------------------

    def _on_stock(self, data):
        """'stock' event: a CSV-style list whose first field is the room id."""
        try:
            if not isinstance(data, (list, tuple)) or not data:
                return

            room = str(data[0])
            if "!" not in room:
                # ICICI streams unframed payloads for some index rooms; the
                # room cannot be resolved, so the tick is dropped safely.
                logger.debug(f"Dropping unframed ICICI tick: {data[:4]}")
                return

            lookup = self.token_map.get(room)
            if lookup is None:
                # Depth rooms ("4.2!x") and quote rooms ("4.1!x") map
                # separately; also try the sibling channel type.
                logger.debug(f"Received tick for untracked room {room}")
                return

            symbol, exchange, mode = lookup
            prefix = room.split("!")[0].split(".")[0]
            channel_type = room.split("!")[0].split(".")[1]

            if prefix == "6":
                market_data = self._normalize_mcx(data)
            elif prefix == "3":
                market_data = self._normalize_index(data)
            elif channel_type == "2":
                market_data = self._normalize_depth(data)
            else:
                market_data = self._normalize_quotes(data)

            if not market_data:
                return

            mode_name = {1: "LTP", 2: "QUOTE", 3: "DEPTH"}.get(mode, "QUOTE")
            topic = f"{exchange}_{symbol}_{mode_name}"
            market_data.update(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "mode": mode,
                    "timestamp": int(time.time() * 1000),
                }
            )
            self.publish_market_data(topic, market_data)

        except Exception as e:
            logger.error(f"Error processing ICICI tick: {e}", exc_info=True)

    @staticmethod
    def _ladder(ladder_rows, bse_style=False, bfo_style=False):
        """Depth ladder rows -> {buy:[], sell:[]} in the shared shape."""
        buy, sell = [], []
        for level in ladder_rows or []:
            if bse_style:
                # [buyRate, buyQty, sellRate, sellQty]
                buy.append({"price": _num(level[0]), "quantity": _int(level[1]), "orders": 0})
                sell.append({"price": _num(level[2]), "quantity": _int(level[3]), "orders": 0})
            elif bfo_style:
                # [buyRate, buyQty, buyOrders, sellRate, sellQty, sellOrders]
                buy.append({"price": _num(level[0]), "quantity": _int(level[1]), "orders": _int(level[2])})
                sell.append({"price": _num(level[3]), "quantity": _int(level[4]), "orders": _int(level[5])})
            else:
                # [buyRate, buyQty, buyOrders, buyFlag, sellRate, sellQty, sellOrders, sellFlag]
                buy.append({"price": _num(level[0]), "quantity": _int(level[1]), "orders": _int(level[2])})
                sell.append({"price": _num(level[4]), "quantity": _int(level[5]), "orders": _int(level[6])})
        return {"buy": buy, "sell": sell}

    def _normalize_quotes(self, data):
        """Standard quotes layout (prefixes 1/4/8/13)."""
        try:
            length = len(data)
            open_, last, high, low = _num(data[1]), _num(data[2]), _num(data[3]), _num(data[4])
            change = _num(data[5])
            if length >= 23:
                # F&O layout: OI@12 CHNGOI@13 ttq@14 buyQty@15 sellQty@16
                # ttv@17 ... ltt@21 close@22
                oi, oi_change = _int(data[12]), _int(data[13])
                ttq = _int(data[14])
                total_buy = _int(data[15])
                total_sell = _int(data[16])
                ltt = _int(data[21], 0)
                close = _num(data[22])
            elif length >= 21:
                # NSE/BSE layout: ttq@12 buyQty@13 sellQty@14 ttv@15 ...
                # ltt@19 close@20
                oi, oi_change = 0, 0
                ttq = _int(data[12])
                total_buy = _int(data[13])
                total_sell = _int(data[14])
                ltt = _int(data[19], 0)
                close = _num(data[20])
            else:
                close = 0.0
                oi = oi_change = ttq = total_buy = total_sell = 0
                ltt = 0

            prev_close = close if close > 0 else (last - change if last else 0.0)
            change_percent = (change / prev_close * 100) if prev_close else 0.0

            result = {
                "ltp": round(last, 2),
                "ltt": ltt,
                "volume": ttq,
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "last_trade_quantity": _int(data[10]),
                "average_price": round(_num(data[11]), 2),
                "total_buy_quantity": total_buy,
                "total_sell_quantity": total_sell,
                "change_absolute": round(change, 2),
                "change_percent": round(change_percent, 2),
            }
            if oi:
                result["oi"] = oi
                result["oi_change"] = oi_change
            return result
        except (IndexError, TypeError, ValueError) as e:
            logger.debug(f"Malformed ICICI quotes payload: {e}")
            return {}

    def _normalize_depth(self, data):
        """Depth layout: room@0, time@1, ladder@2."""
        try:
            ladder = data[2]
            if not isinstance(ladder, (list, tuple)):
                return {}

            prefix = str(data[0]).split("!")[0].split(".")[0]
            bse_style = prefix == "1"
            bfo_style = prefix == "8"
            depth = self._ladder(ladder, bse_style=bse_style, bfo_style=bfo_style)

            result = {
                "ltp": _num(depth["buy"][0]["price"]) if depth["buy"] else 0.0,
                "ltt": _int(data[1], 0),
                "volume": 0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
                "last_quantity": 0,
                "average_price": 0.0,
                "total_buy_quantity": sum(l["quantity"] for l in depth["buy"]),
                "total_sell_quantity": sum(l["quantity"] for l in depth["sell"]),
                "change_absolute": 0.0,
                "change_percent": 0.0,
                "depth": depth,
            }
            return result
        except (IndexError, TypeError, ValueError) as e:
            logger.debug(f"Malformed ICICI depth payload: {e}")
            return {}

    def _normalize_mcx(self, data):
        """MCX layout: volume@1 ttq@4 last@5 ltq@6 ltt@7 avg@8 buy@9 sell@10
        close@12 open@13 high@14 low@15 OI@17, ladder from 22."""
        try:
            if len(data) < 18:
                return {}
            close, open_, high, low = _num(data[12]), _num(data[13]), _num(data[14]), _num(data[15])
            last = _num(data[5])
            change = last - close if close else 0.0
            change_percent = (change / close * 100) if close else 0.0

            result = {
                "ltp": round(last, 2),
                "ltt": _int(data[7], 0),
                "volume": _int(data[4]),
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "last_trade_quantity": _int(data[6]),
                "average_price": round(_num(data[8]), 2),
                "total_buy_quantity": _int(data[9]),
                "total_sell_quantity": _int(data[10]),
                "change_absolute": round(change, 2),
                "change_percent": round(change_percent, 2),
                "oi": _int(data[17]),
            }

            if len(data) > 22 and isinstance(data[22], (list, tuple)):
                result["depth"] = self._ladder(data[22])
            return result
        except (IndexError, TypeError, ValueError) as e:
            logger.debug(f"Malformed ICICI MCX payload: {e}")
            return {}

    def _normalize_index(self, data):
        """Index layout (prefix 3): open@1 high@2 low@3 prevClose@4 ltp@5
        ltq@6 ltt@7 volume@8 pct@9 abs@10 ... bid@12 bidQty@13 ask@14
        askQty@15 oi@16."""
        try:
            if len(data) < 16:
                return {}
            prev_close = _num(data[4])
            last = _num(data[5])
            change = _num(data[10]) if len(data) > 10 else 0.0
            change_percent = _num(data[9]) if len(data) > 9 else (
                (change / prev_close * 100) if prev_close else 0.0
            )

            result = {
                "ltp": round(last, 2),
                "ltt": _int(data[7], 0),
                "volume": _int(data[8]),
                "open": round(_num(data[1]), 2),
                "high": round(_num(data[2]), 2),
                "low": round(_num(data[3]), 2),
                "close": round(prev_close, 2),
                "last_trade_quantity": _int(data[6]),
                "average_price": 0.0,
                "total_buy_quantity": _int(data[13]),
                "total_sell_quantity": _int(data[15]),
                "change_absolute": round(change, 2),
                "change_percent": round(change_percent, 2),
            }
            if len(data) > 16:
                result["oi"] = _int(data[16])
            return result
        except (IndexError, TypeError, ValueError) as e:
            logger.debug(f"Malformed ICICI index payload: {e}")
            return {}

    # ------------------------------------------------------------------
    # Introspection helpers (used by tests)
    # ------------------------------------------------------------------

    def get_subscriptions_snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.subscriptions))
