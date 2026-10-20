from __future__ import annotations

"""
ICICI Direct Breeze market-data WebSocket client.

The authenticated BreezeConnect object is supplied by the adapter.

This class does NOT:
    - create Breeze authentication sessions
    - generate API sessions
    - invent websocket URLs
    - implement the Breeze websocket handshake
    - create a separate raw websocket connection

The authenticated Breeze SDK owns the actual streaming connection.
"""

import sys
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Optional

from utils.logging import get_logger


logger = get_logger(
    "icici_websocket"
)


# ======================================================================
# EVENTLET COMPATIBILITY
# ======================================================================

if "eventlet" in sys.modules:

    import eventlet

    _real_threading = (
        eventlet.patcher.original(
            "threading"
        )
    )

else:

    _real_threading = threading


class IciciWebSocket:
    """
    Wrapper around an authenticated BreezeConnect instance.
    """

    SUBSCRIPTION_DELAY = 0.3
    SUBSCRIPTION_COLLECT_DELAY = 0.2

    MAX_SCRIPS_PER_SUBSCRIBE = 100

    def __init__(
        self,
        breeze=None,
        auth_token=None,
        api_key=None,
        api_secret=None,
        session_token=None,
        user_id=None,
        on_message_callback: Optional[Callable] = None,
        on_error_callback: Optional[Callable] = None,
        on_close_callback: Optional[Callable] = None,
        on_open_callback: Optional[Callable] = None,
    ):

        self.breeze = breeze

        self.auth_token = auth_token
        self.api_key = api_key
        self.api_secret = api_secret

        self.session_token = session_token
        self.user_id = user_id

        self.on_message_callback = (
            on_message_callback
        )

        self.on_error_callback = (
            on_error_callback
        )

        self.on_close_callback = (
            on_close_callback
        )

        self.on_open_callback = (
            on_open_callback
        )

        # Compatibility callbacks.
        self.on_connect = None
        self.on_disconnect = None

        self.connected = False
        self.running = False

        self.logger = logger

        self.lock = _real_threading.Lock()

        self._connection_ready = (
            _real_threading.Event()
        )

        self._stop_event = (
            _real_threading.Event()
        )

        self._subscription_thread = None

        self.subscriptions: dict[
            str,
            dict[str, Any],
        ] = {}

        self.pending_subscriptions = []

        self._sdk_callback_installed = False

        self.logger.info(
            "ICICI Direct WebSocket client initialized "
            "(breeze_present=%s)",
            bool(self.breeze),
        )

    # ==================================================================
    # LIFECYCLE
    # ==================================================================

    def start(self):

        if self.running:
            return True

        if self.breeze is None:

            raise RuntimeError(
                "Authenticated Breeze client is missing"
            )

        self.running = True
        self.connected = False

        self._stop_event.clear()
        self._connection_ready.clear()

        try:

            self._install_sdk_callbacks()

            result = self._start_breeze_stream()

            if result is False:

                self.running = False
                self.connected = False

                return False

            self.logger.info(
                "ICICI Direct Breeze streaming started"
            )

            self._start_subscription_worker()

            return True

        except Exception as exc:

            self.running = False
            self.connected = False

            self.logger.exception(
                "Unable to start ICICI Breeze streaming: %s",
                exc,
            )

            self._notify_error(
                exc
            )

            return False

    def connect(self):

        return self.start()

    def stop(self):

        self.running = False
        self.connected = False

        self._stop_event.set()
        self._connection_ready.clear()

        try:

            self._stop_breeze_stream()

        except Exception:

            self.logger.exception(
                "Error stopping ICICI Breeze streaming"
            )

        self.logger.info(
            "ICICI Direct WebSocket client stopped"
        )

    def disconnect(self):

        self.stop()

    def is_connected(self):

        return bool(
            self.running
            and self.connected
        )

    def wait_for_connection(
        self,
        timeout=15.0,
    ):

        return (
            self._connection_ready.wait(
                timeout=timeout
            )
        )

    # ==================================================================
    # SDK CALLBACKS
    # ==================================================================

    def _install_sdk_callbacks(self):

        if self._sdk_callback_installed:
            return

        if self.breeze is None:

            raise RuntimeError(
                "Breeze client is not available"
            )

        callback_pairs = (
            (
                "on_ticks",
                self._sdk_on_message,
            ),
            (
                "on_message",
                self._sdk_on_message,
            ),
            (
                "on_error",
                self._sdk_on_error,
            ),
            (
                "on_close",
                self._sdk_on_close,
            ),
            (
                "on_open",
                self._sdk_on_open,
            ),
            (
                "on_connect",
                self._sdk_on_open,
            ),
            (
                "on_disconnect",
                self._sdk_on_close,
            ),
        )

        installed = False

        for name, callback in callback_pairs:

            if not hasattr(
                self.breeze,
                name,
            ):

                continue

            try:

                setattr(
                    self.breeze,
                    name,
                    callback,
                )

                self.logger.debug(
                    "Installed Breeze callback: %s",
                    name,
                )

                installed = True

            except Exception as exc:

                self.logger.debug(
                    "Unable to install Breeze callback %s: %s",
                    name,
                    exc,
                )

        self._sdk_callback_installed = installed

    # ==================================================================
    # START BREEZE STREAM
    # ==================================================================

    def _start_breeze_stream(self):

        if self.breeze is None:

            raise RuntimeError(
                "Breeze client is not available"
            )

        candidates = (
            "ws_connect",
            "connect",
            "start_websocket",
            "start_websocket_connection",
        )

        for method_name in candidates:

            method = getattr(
                self.breeze,
                method_name,
                None,
            )

            if not callable(method):
                continue

            try:

                self.logger.info(
                    "Starting Breeze streaming using %s()",
                    method_name,
                )

                result = method()

                self.logger.debug(
                    "Breeze %s() returned %r",
                    method_name,
                    type(result),
                )

                return True

            except TypeError as exc:

                self.logger.debug(
                    "Breeze %s() requires different arguments: %s",
                    method_name,
                    exc,
                )

                continue

            except Exception:

                self.logger.exception(
                    "Breeze %s() failed",
                    method_name,
                )

                raise

        raise RuntimeError(
            "Installed breeze_connect package does not expose "
            "a supported streaming connection method"
        )

    # ==================================================================
    # STOP BREEZE STREAM
    # ==================================================================

    def _stop_breeze_stream(self):

        if self.breeze is None:
            return

        candidates = (
            "ws_disconnect",
            "disconnect",
            "close",
            "stop_websocket",
            "stop_websocket_connection",
        )

        for method_name in candidates:

            method = getattr(
                self.breeze,
                method_name,
                None,
            )

            if not callable(method):
                continue

            try:

                method()

                self.logger.info(
                    "Stopped Breeze streaming using %s()",
                    method_name,
                )

                return

            except TypeError:

                continue

            except Exception:

                self.logger.exception(
                    "Error calling Breeze %s()",
                    method_name,
                )

                return

    # ==================================================================
    # SDK EVENTS
    # ==================================================================

    def _sdk_on_open(
        self,
        *args,
        **kwargs,
    ):

        self.connected = True

        self._connection_ready.set()

        self.logger.info(
            "ICICI Direct Breeze stream connected"
        )

        if self.on_open_callback:

            try:

                self.on_open_callback()

            except Exception:

                self.logger.exception(
                    "ICICI on_open callback failed"
                )

        if self.on_connect:

            try:

                self.on_connect()

            except Exception:

                self.logger.exception(
                    "ICICI on_connect callback failed"
                )

        self._resubscribe_all()

    def _sdk_on_close(
        self,
        *args,
        **kwargs,
    ):

        self.connected = False

        self._connection_ready.clear()

        self.logger.info(
            "ICICI Direct Breeze stream closed"
        )

        if self.on_close_callback:

            try:

                self.on_close_callback()

            except Exception:

                self.logger.exception(
                    "ICICI on_close callback failed"
                )

        if self.on_disconnect:

            try:

                self.on_disconnect()

            except Exception:

                self.logger.exception(
                    "ICICI on_disconnect callback failed"
                )

    def _sdk_on_error(
        self,
        error=None,
        *args,
        **kwargs,
    ):

        self.connected = False

        self._connection_ready.clear()

        self.logger.error(
            "ICICI Direct Breeze stream error: %s",
            error,
        )

        self._notify_error(
            error
        )

    def _notify_error(
        self,
        error,
    ):

        if not self.on_error_callback:
            return

        try:

            self.on_error_callback(
                error
            )

        except Exception:

            self.logger.exception(
                "ICICI on_error callback failed"
            )

    def _sdk_on_message(
        self,
        message=None,
        *args,
        **kwargs,
    ):

        self.connected = True

        self._connection_ready.set()

        try:

            if self.on_message_callback:

                self.on_message_callback(
                    message
                )

        except Exception:

            self.logger.exception(
                "ICICI on_message callback failed"
            )

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

        if not self.running:

            return {
                "status": "error",
                "success": False,
                "message": (
                    "ICICI WebSocket is not running"
                ),
            }

        if self.breeze is None:

            return {
                "status": "error",
                "success": False,
                "message": (
                    "Authenticated Breeze client is missing"
                ),
            }

        if token is None:

            return {
                "status": "error",
                "success": False,
                "message": (
                    "ICICI subscription token missing"
                ),
            }

        try:

            mode = int(mode)

        except (
            ValueError,
            TypeError,
        ):

            mode = 2

        key = self._subscription_key(
            exchange,
            token,
            mode,
        )

        subscription = {
            "symbol": symbol,
            "exchange": exchange,
            "token": str(token),
            "mode": mode,
            "depth_level": depth_level,
        }

        with self.lock:

            self.subscriptions[key] = subscription

            # Avoid duplicate pending subscriptions.
            already_pending = any(
                str(item[2]) == str(token)
                and str(item[1]).upper()
                == str(exchange).upper()
                and int(item[3]) == mode
                for item in self.pending_subscriptions
            )

            if not already_pending:

                self.pending_subscriptions.append(
                    (
                        symbol,
                        exchange,
                        str(token),
                        mode,
                        depth_level,
                    )
                )

        self._start_subscription_worker()

        return {
            "status": "success",
            "success": True,
            "message": (
                f"Subscribed {exchange}:{symbol}"
            ),
            "symbol": symbol,
            "exchange": exchange,
            "token": str(token),
            "mode": mode,
        }

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

        if token is None:

            return {
                "status": "success",
                "success": True,
                "message": (
                    "ICICI subscription token missing"
                ),
            }

        try:

            mode = int(mode)

        except (
            ValueError,
            TypeError,
        ):

            mode = 2

        key = self._subscription_key(
            exchange,
            token,
            mode,
        )

        with self.lock:

            subscription = (
                self.subscriptions.get(
                    key
                )
            )

        if subscription is None:

            return {
                "status": "success",
                "success": True,
                "message": (
                    f"{exchange}:{symbol} "
                    "was not subscribed"
                ),
            }

        try:

            if self.connected:

                self._send_unsubscription(
                    symbol=symbol,
                    exchange=exchange,
                    token=str(token),
                    mode=mode,
                )

        finally:

            with self.lock:

                self.subscriptions.pop(
                    key,
                    None,
                )

                self.pending_subscriptions = [
                    item
                    for item in self.pending_subscriptions
                    if not (
                        str(item[2])
                        == str(token)
                        and str(item[1]).upper()
                        == str(exchange).upper()
                        and int(item[3]) == mode
                    )
                ]

        return {
            "status": "success",
            "success": True,
            "message": (
                f"Unsubscribed {exchange}:{symbol}"
            ),
        }

    # ==================================================================
    # UNSUBSCRIBE ALL
    # ==================================================================

    def unsubscribe_all(self):

        with self.lock:

            subscriptions = list(
                self.subscriptions.values()
            )

        for item in subscriptions:

            try:

                self.unsubscribe(
                    symbol=item["symbol"],
                    exchange=item["exchange"],
                    token=item["token"],
                    mode=item["mode"],
                )

            except Exception:

                self.logger.exception(
                    "Error unsubscribing %s:%s",
                    item["exchange"],
                    item["symbol"],
                )

        with self.lock:

            self.subscriptions.clear()
            self.pending_subscriptions.clear()

        return {
            "status": "success",
            "success": True,
            "message": (
                "All ICICI subscriptions removed"
            ),
        }

    # ==================================================================
    # SUBSCRIPTION WORKER
    # ==================================================================

    def _start_subscription_worker(self):

        if (
            self._subscription_thread
            and self._subscription_thread.is_alive()
        ):

            return

        self._subscription_thread = (
            _real_threading.Thread(
                target=self._process_pending_subscriptions,
                daemon=True,
                name="ICICI-Subscription",
            )
        )

        self._subscription_thread.start()

    def _process_pending_subscriptions(self):

        while (
            self.running
            and not self._stop_event.is_set()
        ):

            if not self.connected:

                if self._stop_event.wait(
                    0.5
                ):

                    return

                continue

            if self._stop_event.wait(
                self.SUBSCRIPTION_COLLECT_DELAY
            ):

                return

            with self.lock:

                pending = list(
                    self.pending_subscriptions
                )

                self.pending_subscriptions.clear()

            if not pending:
                continue

            grouped = defaultdict(list)

            for item in pending:

                (
                    symbol,
                    exchange,
                    token,
                    mode,
                    depth_level,
                ) = item

                grouped[mode].append(
                    (
                        symbol,
                        exchange,
                        token,
                        depth_level,
                    )
                )

            for mode, items in grouped.items():

                for start in range(
                    0,
                    len(items),
                    self.MAX_SCRIPS_PER_SUBSCRIBE,
                ):

                    batch = items[
                        start:
                        start
                        + self.MAX_SCRIPS_PER_SUBSCRIBE
                    ]

                    success = (
                        self._send_subscription(
                            batch,
                            mode,
                        )
                    )

                    if not success:

                        with self.lock:

                            self.pending_subscriptions.extend(
                                [
                                    (
                                        symbol,
                                        exchange,
                                        token,
                                        mode,
                                        depth_level,
                                    )
                                    for (
                                        symbol,
                                        exchange,
                                        token,
                                        depth_level,
                                    ) in batch
                                ]
                            )

                        break

                    if self._stop_event.wait(
                        self.SUBSCRIPTION_DELAY
                    ):

                        return

    # ==================================================================
    # BREEZE SUBSCRIPTION
    # ==================================================================

    def _send_subscription(
        self,
        batch,
        mode,
    ):

        if not self.connected:
            return False

        if self.breeze is None:
            return False

        try:

            for (
                symbol,
                exchange,
                token,
                depth_level,
            ) in batch:

                success = (
                    self._subscribe_one(
                        symbol=symbol,
                        exchange=exchange,
                        token=token,
                        mode=mode,
                        depth_level=depth_level,
                    )
                )

                if not success:

                    self.logger.error(
                        "ICICI Breeze subscription failed "
                        "for %s:%s token=%s",
                        exchange,
                        symbol,
                        token,
                    )

                    return False

            return True

        except Exception:

            self.logger.exception(
                "ICICI Breeze subscription failed"
            )

            return False

    def _subscribe_one(
        self,
        symbol,
        exchange,
        token,
        mode,
        depth_level=5,
    ):

        if self.breeze is None:
            return False

        exchange_code = self._exchange_code(
            exchange
        )

        mode_name = self._normalize_mode(
            mode
        )

        # --------------------------------------------------------------
        # Preferred Breeze SDK API.
        # --------------------------------------------------------------

        method = getattr(
            self.breeze,
            "subscribe_feeds",
            None,
        )

        if callable(method):

            attempts = [
                {
                    "stock_code": symbol,
                    "exchange_code": exchange_code,
                    "product_type": "",
                    "expiry_date": "",
                    "right": "",
                    "strike_price": "",
                    "get_exchange_quotes": True,
                    "get_market_depth": (
                        mode_name == "DEPTH"
                    ),
                }
            ]

            # Some SDK versions don't accept all keyword arguments.
            attempts.append(
                {
                    "stock_code": symbol,
                    "exchange_code": exchange_code,
                    "get_exchange_quotes": (
                        mode_name in (
                            "LTP",
                            "QUOTE",
                            "DEPTH",
                        )
                    ),
                    "get_market_depth": (
                        mode_name == "DEPTH"
                    ),
                }
            )

            attempts.append(
                {
                    "stock_code": symbol,
                    "exchange_code": exchange_code,
                }
            )

            for kwargs in attempts:

                try:

                    result = method(
                        **kwargs
                    )

                    self.logger.debug(
                        "Breeze subscribe_feeds "
                        "result: %r",
                        result,
                    )

                    if isinstance(
                        result,
                        dict,
                    ):

                        if result.get(
                            "success"
                        ) is False:

                            self.logger.error(
                                "Breeze rejected subscription: %r",
                                result,
                            )

                            return False

                    return True

                except TypeError:

                    continue

                except Exception:

                    self.logger.exception(
                        "Breeze subscribe_feeds failed "
                        "for %s:%s",
                        exchange,
                        symbol,
                    )

                    return False

        # --------------------------------------------------------------
        # Alternative SDK methods.
        # --------------------------------------------------------------

        candidates = (
            "subscribe",
            "subscribe_market_data",
            "subscribe_quotes",
        )

        for method_name in candidates:

            method = getattr(
                self.breeze,
                method_name,
                None,
            )

            if not callable(method):
                continue

            attempts = (
                (
                    symbol,
                    exchange_code,
                    token,
                    mode_name,
                ),
                (
                    symbol,
                    exchange_code,
                    token,
                ),
                (
                    token,
                    mode_name,
                ),
                (
                    token,
                ),
            )

            for args in attempts:

                try:

                    result = method(
                        *args
                    )

                    self.logger.debug(
                        "Breeze %s result: %r",
                        method_name,
                        result,
                    )

                    if isinstance(
                        result,
                        dict,
                    ):

                        if result.get(
                            "success"
                        ) is False:

                            return False

                    return True

                except TypeError:

                    continue

                except Exception:

                    self.logger.exception(
                        "Breeze %s failed",
                        method_name,
                    )

                    return False

        self.logger.error(
            "No compatible Breeze subscription API "
            "found in installed breeze_connect package"
        )

        return False

    # ==================================================================
    # UNSUBSCRIBE FROM BREEZE
    # ==================================================================

    def _send_unsubscription(
        self,
        symbol,
        exchange,
        token,
        mode,
    ):

        if self.breeze is None:
            return False

        exchange_code = self._exchange_code(
            exchange
        )

        mode_name = self._normalize_mode(
            mode
        )

        # --------------------------------------------------------------
        # Preferred SDK API.
        # --------------------------------------------------------------

        method = getattr(
            self.breeze,
            "unsubscribe_feeds",
            None,
        )

        if callable(method):

            attempts = (
                {
                    "stock_code": symbol,
                    "exchange_code": exchange_code,
                    "product_type": "",
                    "expiry_date": "",
                    "right": "",
                    "strike_price": "",
                    "get_exchange_quotes": True,
                    "get_market_depth": (
                        mode_name == "DEPTH"
                    ),
                },
                {
                    "stock_code": symbol,
                    "exchange_code": exchange_code,
                },
            )

            for kwargs in attempts:

                try:

                    result = method(
                        **kwargs
                    )

                    if isinstance(
                        result,
                        dict,
                    ):

                        if result.get(
                            "success"
                        ) is False:

                            return False

                    return True

                except TypeError:

                    continue

                except Exception:

                    self.logger.exception(
                        "Breeze unsubscribe_feeds failed"
                    )

                    return False

        # --------------------------------------------------------------
        # Alternative APIs.
        # --------------------------------------------------------------

        for method_name in (
            "unsubscribe",
            "unsubscribe_market_data",
        ):

            method = getattr(
                self.breeze,
                method_name,
                None,
            )

            if not callable(method):
                continue

            for args in (
                (
                    symbol,
                    exchange_code,
                    token,
                ),
                (
                    token,
                ),
            ):

                try:

                    result = method(
                        *args
                    )

                    if isinstance(
                        result,
                        dict,
                    ):

                        if result.get(
                            "success"
                        ) is False:

                            return False

                    return True

                except TypeError:

                    continue

                except Exception:

                    self.logger.exception(
                        "Breeze %s failed",
                        method_name,
                    )

                    return False

        return False

    # ==================================================================
    # RESUBSCRIBE
    # ==================================================================

    def _resubscribe_all(self):

        with self.lock:

            subscriptions = list(
                self.subscriptions.values()
            )

        if not subscriptions:
            return

        grouped = defaultdict(list)

        for item in subscriptions:

            grouped[
                item["mode"]
            ].append(
                (
                    item["symbol"],
                    item["exchange"],
                    item["token"],
                    item.get(
                        "depth_level",
                        5,
                    ),
                )
            )

        for mode, items in grouped.items():

            for start in range(
                0,
                len(items),
                self.MAX_SCRIPS_PER_SUBSCRIBE,
            ):

                if not self.connected:
                    return

                batch = items[
                    start:
                    start
                    + self.MAX_SCRIPS_PER_SUBSCRIBE
                ]

                if not self._send_subscription(
                    batch,
                    mode,
                ):

                    self.logger.error(
                        "Failed to resubscribe "
                        "ICICI batch"
                    )

                    return

    # ==================================================================
    # HELPERS
    # ==================================================================

    @staticmethod
    def _subscription_key(
        exchange,
        token,
        mode,
    ):

        return (
            f"{str(exchange).upper()}:"
            f"{str(token)}:"
            f"{int(mode)}"
        )

    @staticmethod
    def _exchange_code(
        exchange,
    ):

        value = str(
            exchange or ""
        ).upper()

        mapping = {
            "NSE": "NSE",
            "BSE": "BSE",
            "NFO": "NFO",
            "BFO": "BFO",
            "MCX": "MCX",
            "CDS": "CDS",
            "BCD": "BCD",
            "NSE_INDEX": "NSE",
            "BSE_INDEX": "BSE",
        }

        return mapping.get(
            value,
            value,
        )

    @staticmethod
    def _normalize_mode(
        mode,
    ):

        if mode is None:
            return "LTP"

        if isinstance(
            mode,
            int,
        ):

            return {
                1: "LTP",
                2: "QUOTE",
                3: "DEPTH",
            }.get(
                mode,
                "LTP",
            )

        value = str(
            mode
        ).upper().strip()

        return {
            "1": "LTP",
            "2": "QUOTE",
            "3": "DEPTH",
            "LTP": "LTP",
            "QUOTE": "QUOTE",
            "DEPTH": "DEPTH",
        }.get(
            value,
            "LTP",
        )
