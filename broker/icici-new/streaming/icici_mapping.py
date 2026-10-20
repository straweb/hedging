"""
ICICI Breeze tick -> OpenAlgo feed normalization.

Breeze Socket.IO ticks arrive as dicts keyed by the feed room
(``symbol`` = "4.1!<token>" for quotes, "4.2!<token>" for depth) with
loose field names that vary by segment (MCX adds its own keys). Mapping is
deliberately tolerant: numeric candidates per field, depth assembled from
bid/offer level keys when present, and unknown messages dropped silently
(order notifications and heartbeats carry no feed token and never reach
this module — the adapter filters them first).
"""

from utils.logging import get_logger


logger = get_logger(__name__)


MODE_LTP = 1
MODE_QUOTE = 2
MODE_DEPTH = 3


def _num(message: dict, *keys, default=None):
    for key in keys:
        value = message.get(key)

        if value in (None, ""):
            continue

        try:
            return float(value)
        except (TypeError, ValueError):
            continue

    return default


def _int(message: dict, *keys, default=0) -> int:
    value = _num(message, *keys)

    return int(value) if value is not None else default


def map_tick(message, symbol: str, exchange: str, token: str, mode: int) -> dict | None:
    """
    One Breeze tick -> OpenAlgo feed dict (or None when unusable).
    """

    if not isinstance(message, dict):
        return None

    try:
        ltp = _num(message, "last", "ltp", "last_price", "LTP", "last_px")

        if ltp is None:
            # A tick without any price cannot serve any mode.
            return None

        tick = {
            "ltp": ltp,
            "last_price": ltp,
            "symbol": symbol,
            "exchange": exchange,
            "token": str(token),
        }

        open_ = _num(message, "open", "o")
        high = _num(message, "high", "h")
        low = _num(message, "low", "l")
        close = _num(message, "close", "c", "prev_close")

        if open_ is not None:
            tick["open"] = open_

        if high is not None:
            tick["high"] = high

        if low is not None:
            tick["low"] = low

        if close is not None:
            tick["close"] = close
            tick["prev_close"] = close

        volume = _num(message, "v", "volume", "vol", "tot_traded_qty")

        if volume is not None:
            tick["volume"] = int(volume)

        oi = _num(message, "oi", "open_interest", "open_interest_lot")

        if oi is not None:
            tick["oi"] = int(oi)

        bid = _num(message, "bid", "best_bid_price", "bPrice")
        ask = _num(message, "offer", "ask", "best_offer_price", "sPrice")

        if bid is not None:
            tick["bid"] = bid

        if ask is not None:
            tick["ask"] = ask

        if mode == MODE_DEPTH:
            depth = _map_depth(message)

            if depth:
                tick["depth"] = depth

        return tick

    except Exception:
        logger.exception("Failed to map Breeze tick for %s:%s", exchange, symbol)

        return None


def _level(message: dict, price_keys, qty_keys) -> dict:
    price = _num(message, *price_keys)

    if price is None:
        return {"price": 0, "quantity": 0}

    quantity = _int(message, *qty_keys, default=0)

    return {"price": price, "quantity": quantity}


def _map_depth(message: dict) -> list[dict] | None:
    """
    Assemble the 5-level ladder from bid/offer level keys when the
    segment supplies them.
    """

    bids = []
    asks = []

    for level in range(1, 6):
        bid_keys = (
            f"bid{level}",
            f"b{level}",
            f"bid_price{level}",
            f"bid{level}_price",
        )

        bid_qty_keys = (
            f"bid{level}_qty",
            f"b{level}_qty",
            f"bid_qty{level}",
            f"bid{level}_quantity",
        )

        bid = _level(message, bid_keys, bid_qty_keys)

        if bid["price"] or bid["quantity"]:
            bids.append(bid)

        ask_keys = (
            f"offer{level}",
            f"ask{level}",
            f"o{level}",
            f"offer_price{level}",
            f"ask_price{level}",
        )

        ask_qty_keys = (
            f"offer{level}_qty",
            f"ask{level}_qty",
            f"offer_qty{level}",
            f"offer{level}_quantity",
        )

        ask = _level(message, ask_keys, ask_qty_keys)

        if ask["price"] or ask["quantity"]:
            asks.append(ask)

    if not bids and not asks:
        return None

    while len(bids) < 5:
        bids.append({"price": 0, "quantity": 0})

    while len(asks) < 5:
        asks.append({"price": 0, "quantity": 0})

    return {"buy": bids[:5], "sell": asks[:5]}
