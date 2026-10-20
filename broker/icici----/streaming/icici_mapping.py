"""
ICICI Direct Breeze websocket feed mapping.

Converts Breeze websocket messages into the normalized structure
consumed by OpenAlgo's websocket adapter.
"""

from typing import Any


def _first(data, *keys, default=None):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)

        if value is not None and value != "":
            return value

    return default


def _float(value, default=0.0):

    try:
        if value is None or value == "":
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def _int(value, default=0):

    try:
        if value is None or value == "":
            return default

        return int(float(value))

    except (TypeError, ValueError):
        return default


def _exchange(value):

    if value is None:
        return ""

    return str(value).upper()


def map_feed_data(data: Any):
    """
    Convert one Breeze websocket message into OpenAlgo format.

    The adapter can pass either:

        dict
        list[dict]

    Unknown/heartbeat messages return None.
    """

    if data is None:
        return None

    if isinstance(data, list):

        result = []

        for item in data:

            mapped = map_feed_data(item)

            if mapped is not None:
                result.append(mapped)

        return result

    if not isinstance(data, dict):
        return None

    # -------------------------------------------------------------
    # Breeze heartbeat / acknowledgement messages
    # -------------------------------------------------------------

    message_type = str(
        _first(
            data,
            "messageType",
            "event",
            "type",
            default="",
        )
    ).lower()

    if message_type in (
        "heartbeat",
        "ping",
        "pong",
        "ack",
    ):
        return None

    # -------------------------------------------------------------
    # Symbol
    # -------------------------------------------------------------

    symbol = _first(
        data,
        "stock_code",
        "symbol",
        "tradingsymbol",
        "stockCode",
    )

    exchange = _exchange(
        _first(
            data,
            "exchange_code",
            "exchange",
            "exchangeCode",
        )
    )

    # -------------------------------------------------------------
    # Price
    # -------------------------------------------------------------

    ltp = _float(
        _first(
            data,
            "last",
            "ltp",
            "last_price",
            "lastPrice",
            "close",
        )
    )

    open_price = _float(
        _first(
            data,
            "open",
            "open_price",
            "openPrice",
        )
    )

    high = _float(
        _first(
            data,
            "high",
            "high_price",
            "highPrice",
        )
    )

    low = _float(
        _first(
            data,
            "low",
            "low_price",
            "lowPrice",
        )
    )

    previous_close = _float(
        _first(
            data,
            "previous_close",
            "prev_close",
            "previousClose",
        )
    )

    volume = _int(
        _first(
            data,
            "volume",
            "total_quantity",
            "totalQuantity",
        )
    )

    # -------------------------------------------------------------
    # Feed
    # -------------------------------------------------------------

    if not symbol and not exchange and ltp == 0:
        return None

    return {
        "symbol": str(symbol or ""),
        "exchange": exchange,
        "ltp": ltp,
        "open": open_price,
        "high": high,
        "low": low,
        "prev_close": previous_close,
        "volume": volume,

        # Keep the raw broker payload available for debugging/
        # adapter-level processing without changing OpenAlgo fields.
        "raw": data,
    }


# Compatibility names for code that may use older ICICI naming.
transform_feed_data = map_feed_data
map_market_data = map_feed_data
