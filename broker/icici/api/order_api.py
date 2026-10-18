# broker/icici/api/order_api.py
# Order management for the ICICI Direct Breeze API.
#
# Two Breeze constraints shape this module:
#   1. The order endpoints are exchange-scoped: the order book and trade book
#      must be requested per exchange_code and merged here.
#   2. Cancel and modify require the exchange_code alongside the order_id.
#      OpenAlgo passes only the orderid, so the exchange is recovered from the
#      merged order book (the same approach the groww plugin uses).

import json
import os
from datetime import datetime, timedelta, timezone

from broker.icici.api.baseurl import get_url
from broker.icici.api.breeze_client import (
    extract_error,
    is_success,
    request_raw,
)
from broker.icici.mapping.transform_data import (
    map_exchange_type,
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from database.token_db import get_br_symbol, get_symbol, get_token
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger
from utils.position_read import read_position_book, refuse_smart_order_on_read_failure
from utils.smart_order_guard import PositionBookCache, SymbolLocks

logger = get_logger(__name__)

# ICICI Direct segments OpenAlgo supports. Currency (NDX) is excluded - the
# security master carries no tradable currency rows.
ORDER_EXCHANGES = ["NSE", "NFO", "BSE", "BFO", "MCX"]

IST = timezone(timedelta(hours=5, minutes=30))

# Statuses an open order can sit in, for cancel-all.
OPEN_ORDER_STATUSES = {
    "ordered",
    "queued",
    "requested",
    "partially executed",
}


def _trade_date():
    """Current IST trade date as a string."""
    return datetime.now(IST).strftime("%Y-%m-%d")


def _date_range_iso():
    """(from_date, to_date) ISO instants covering the full IST trade date."""
    today = _trade_date()
    return f"{today}T00:00:00.000Z", f"{today}T23:59:59.000Z"


def _iter_book(endpoint, auth):
    """Yield (exchange_code, rows) for every exchange on a book endpoint."""
    from_date, to_date = _date_range_iso()
    for exchange_code in ORDER_EXCHANGES:
        payload = json.dumps(
            {
                "exchange_code": exchange_code,
                "from_date": from_date,
                "to_date": to_date,
            },
            separators=(",", ":"),
        )
        data = request_raw(endpoint, auth, "GET", payload)[1]
        if isinstance(data, dict) and data.get("Status") in (None, 200, "200"):
            rows = data.get("Success") or []
            if isinstance(rows, list) and rows:
                yield exchange_code, rows
        # A failing exchange (no segment access, no trades) is skipped; the
        # books are the union of the exchanges that answer.


def _merged_book(endpoint, auth):
    """Union of a book endpoint across exchanges, or an error dict."""
    merged = []
    exchanges_answered = 0

    for exchange_code, rows in _iter_book(endpoint, auth):
        merged.extend(rows)
        exchanges_answered += 1

    if exchanges_answered > 0:
        return merged

    # No exchange yielded rows - distinguish "everything empty" from
    # "every call failed" with a single probe, so the book services see an
    # error dict (books return bare lists on success, dicts on failure).
    from_date, to_date = _date_range_iso()
    probe = request_raw(
        endpoint,
        auth,
        "GET",
        json.dumps(
            {"exchange_code": "NSE", "from_date": from_date, "to_date": to_date},
            separators=(",", ":"),
        ),
    )[1]
    if isinstance(probe, dict) and probe.get("Status") not in (None, 200, "200"):
        return probe
    return merged


def get_order_book(auth):
    """Merged order book across all supported Breeze exchanges (bare list)."""
    return _merged_book("order", auth)


def get_trade_book(auth):
    """Merged trade book across all supported Breeze exchanges (bare list)."""
    return _merged_book("trades", auth)


def get_positions(auth):
    """Portfolio positions across segments (bare list, [] when empty)."""
    data = request_raw("portfoliopositions", auth, "GET", "{}")[1]
    if isinstance(data, dict) and data.get("Status") in (None, 200, "200"):
        rows = data.get("Success") or []
        return rows if isinstance(rows, list) else []
    return data if isinstance(data, dict) else {"Error": "Unknown positions response"}


def get_holdings(auth):
    """Demat + F&O portfolio holdings for the NSE view (bare list)."""
    data = request_raw("portfolioholdings", auth, "GET", json.dumps(
        {"exchange_code": "NSE"}, separators=(",", ":")))[1]
    if isinstance(data, dict) and data.get("Status") in (None, 200, "200"):
        rows = data.get("Success") or []
        return rows if isinstance(rows, list) else []
    return data if isinstance(data, dict) else {"Error": "Unknown holdings response"}


# --- Per-Symbol Smart Order Lock ---
# Ensures only one smart order per symbol executes at a time.
_symbol_locks = SymbolLocks(name="icici smart orders")

# --- Position Book Cache ---
# Caches get_positions() for 1 second; invalidated after each smart order.
_position_cache = PositionBookCache()


def _get_symbol_lock(symbol, exchange, product):
    return _symbol_locks.hold(symbol, exchange, product)


def _position_book_ok(positions_data):
    """Breeze returns the position book as a bare list, [] when empty.

    Every failure is a dict, mirroring the dhan plugin's convention.
    """
    return isinstance(positions_data, list)


def _get_cached_positions(auth):
    """Get positions from cache if fresh, otherwise fetch from broker API."""
    return _position_cache.get(
        auth,
        lambda: read_position_book("icici", lambda: get_positions(auth), _position_book_ok),
    )


def _invalidate_position_cache(auth):
    """Invalidate the position cache so the next queued order fetches fresh data."""
    _position_cache.invalidate(auth)


def _position_matches(position, params, product):
    """True when a Breeze position row is the requested instrument.

    Derivatives are pinned on expiry/right/strike because every contract of an
    underlying shares its stock_code; equities match on stock_code alone.
    """
    if position.get("exchange_code") != params["exchange_code"]:
        return False
    if position.get("stock_code") != params["stock_code"]:
        return False

    instrumenttype = params["instrumenttype"]
    if instrumenttype in ("CE", "PE"):
        if (position.get("right") or "").lower() != params["right"]:
            return False
        if str(position.get("strike_price") or "").rstrip("0").rstrip(".") != params["strike_price"]:
            return False
    elif instrumenttype == "FUT":
        pass  # expiry matched below
    else:
        return True  # equity: stock_code + exchange is unique

    if instrumenttype in ("FUT", "CE", "PE"):
        # Position expiry arrives as "27-Feb-2025"; the params value is ISO.
        pos_expiry = (position.get("expiry_date") or "").lower()
        wanted_day = params["expiry_date"][:10] if params["expiry_date"] else ""
        try:
            parsed = datetime.strptime(pos_expiry, "%d-%b-%Y")
            if parsed.strftime("%Y-%m-%d") != wanted_day:
                return False
        except ValueError:
            # Unparseable expiry - fall back to a substring match on the date
            # rather than rejecting every row.
            if wanted_day and wanted_day not in pos_expiry:
                return False

    return True


def get_open_position(tradingsymbol, exchange, product, auth):
    """Net quantity for a symbol as a string ("0" when flat/unresolvable)."""
    from broker.icici.mapping.transform_data import get_breeze_params

    net_qty = "0"

    try:
        params = get_breeze_params(tradingsymbol, exchange)
    except Exception as e:
        logger.warning(f"get_open_position: cannot resolve {tradingsymbol}/{exchange}: {e}")
        # Fall back to a plain stock_code + exchange match.
        params = {
            "stock_code": get_br_symbol(tradingsymbol, exchange) or tradingsymbol,
            "exchange_code": map_exchange_type(exchange) or exchange,
            "instrumenttype": "EQ",
            "expiry_date": "",
            "right": "",
            "strike_price": "",
        }

    positions_data = _get_cached_positions(auth)
    if isinstance(positions_data, dict):
        logger.error(
            f"Error getting positions for {tradingsymbol}: "
            f"{positions_data.get('errorMessage') or positions_data.get('Error') or 'API Error'}"
        )
        return net_qty

    if positions_data and isinstance(positions_data, list):
        for position in positions_data:
            if _position_matches(position, params, product):
                net_qty = str(position.get("quantity", "0") or "0")
                break

    return net_qty


def place_order_api(data, auth):
    """Place an order; returns (res, response_data, orderid)."""
    token = get_token(data["symbol"], data["exchange"])
    newdata = transform_data(data, token)
    payload = json.dumps(newdata, separators=(",", ":"))

    logger.debug(
        "Placing Breeze order payload: "
        f"{ {k: v for k, v in newdata.items() if k != 'user_remark'} }"
    )

    res, response_data = request_raw("order", auth, "POST", payload)

    if res is None:
        # Connection-level failure - synthesize a response-like object.
        class _FailedResponse:
            status = 503

        res = _FailedResponse()

    orderid = None
    if is_success(response_data):
        success = response_data.get("Success") or {}
        if isinstance(success, dict):
            orderid = success.get("order_id")
        if not orderid:
            logger.error(f"Breeze place order returned no order_id: {response_data}")
    else:
        logger.error(f"Breeze place order failed: {extract_error(response_data)}")

    return res, response_data, orderid


@refuse_smart_order_on_read_failure
def place_smartorder_api(data, auth):
    """Place a smart order, resizing to the target position."""
    AUTH_TOKEN = auth
    res = None

    symbol = data.get("symbol")
    exchange = data.get("exchange")
    product = data.get("product")
    symbol_lock = _get_symbol_lock(symbol, exchange, product)

    with symbol_lock as acquired:
        if not acquired:
            return SymbolLocks.busy(symbol)
        position_size = int(data.get("position_size", "0"))

        current_position = int(
            float(get_open_position(symbol, exchange, map_product_type(product), AUTH_TOKEN) or 0)
        )

        logger.debug(f"position_size : {position_size}")
        logger.debug(f"Open Position : {current_position}")

        action = None
        quantity = 0

        if position_size == 0 and current_position == 0 and int(data["quantity"]) != 0:
            res, response, orderid = place_order_api(data, AUTH_TOKEN)
            _invalidate_position_cache(AUTH_TOKEN)
            return res, response, orderid

        elif position_size == current_position:
            if int(data["quantity"]) == 0:
                response = {
                    "status": "success",
                    "message": "No OpenPosition Found. Not placing Exit order.",
                }
            else:
                response = {
                    "status": "success",
                    "message": "No action needed. Position size matches current position",
                }
            return res, response, None

        if position_size == 0 and current_position > 0:
            action = "SELL"
            quantity = abs(current_position)
        elif position_size == 0 and current_position < 0:
            action = "BUY"
            quantity = abs(current_position)
        elif current_position == 0:
            action = "BUY" if position_size > 0 else "SELL"
            quantity = abs(position_size)
        else:
            if position_size > current_position:
                action = "BUY"
                quantity = position_size - current_position
            elif position_size < current_position:
                action = "SELL"
                quantity = current_position - position_size

        if action:
            order_data = data.copy()
            order_data["action"] = action
            order_data["quantity"] = str(quantity)

            res, response, orderid = place_order_api(order_data, AUTH_TOKEN)
            _invalidate_position_cache(AUTH_TOKEN)
            return res, response, orderid


def _find_order_exchange(orderid, auth):
    """Recover the exchange_code for an order id from the merged order book."""
    orders = get_order_book(auth)
    if isinstance(orders, list):
        for order in orders:
            if str(order.get("order_id")) == str(orderid):
                return order.get("exchange_code")
    return None


def cancel_order(orderid, auth):
    """Cancel an order; the exchange is recovered from the order book."""
    AUTH_TOKEN = auth

    exchange_code = _find_order_exchange(orderid, AUTH_TOKEN)
    if not exchange_code:
        return {
            "status": "error",
            "message": f"Order {orderid} not found in the ICICI Direct order book",
        }, 404

    payload = json.dumps(
        {"exchange_code": exchange_code, "order_id": str(orderid)}, separators=(",", ":")
    )
    res, data = request_raw("order", AUTH_TOKEN, "DELETE", payload)
    status_code = res.status if res is not None else 503

    if is_success(data):
        return {"status": "success", "orderid": orderid}, 200
    return {
        "status": "error",
        "message": extract_error(data) or "Failed to cancel order",
    }, status_code


def modify_order(data, auth):
    """Modify an open order."""
    AUTH_TOKEN = auth

    transformed = transform_modify_order_data(data)
    payload = json.dumps(transformed, separators=(",", ":"))

    logger.debug(f"Modify order payload: {payload}")

    res, response_data = request_raw("order", AUTH_TOKEN, "PUT", payload)
    status_code = res.status if res is not None else 503

    if is_success(response_data):
        return {"status": "success", "orderid": data["orderid"]}, 200
    return {
        "status": "error",
        "message": extract_error(response_data) or "Failed to modify order",
    }, status_code


def cancel_all_orders_api(data, auth):
    """Cancel every open order; returns (canceled_orders, failed_cancellations)."""
    AUTH_TOKEN = auth

    order_book_response = get_order_book(AUTH_TOKEN)
    if not isinstance(order_book_response, list):
        logger.error("Could not retrieve the order book for cancel-all")
        return [], []

    orders_to_cancel = [
        order
        for order in order_book_response
        if (order.get("status") or "").lower() in OPEN_ORDER_STATUSES
    ]
    logger.debug(f"Orders to cancel: {[o.get('order_id') for o in orders_to_cancel]}")

    canceled_orders = []
    failed_cancellations = []

    for order in orders_to_cancel:
        orderid = order.get("order_id")
        exchange_code = order.get("exchange_code")
        if not orderid or not exchange_code:
            continue

        payload = json.dumps(
            {"exchange_code": exchange_code, "order_id": str(orderid)}, separators=(",", ":")
        )
        res, cancel_response = request_raw("order", AUTH_TOKEN, "DELETE", payload)
        status_code = res.status if res is not None else 503
        if status_code == 200 and is_success(cancel_response):
            canceled_orders.append(orderid)
        else:
            failed_cancellations.append(orderid)

    return canceled_orders, failed_cancellations


def close_all_positions(current_api_key, auth):
    """Square off every non-zero position with a market order."""
    AUTH_TOKEN = auth

    positions_response = get_positions(AUTH_TOKEN)
    logger.debug(f"Positions response for closing all: {positions_response}")

    if positions_response is None or not isinstance(positions_response, list) or not positions_response:
        return {"message": "No Open Positions Found"}, 200

    for position in positions_response:
        try:
            net_qty = int(float(position.get("quantity", 0) or 0))
        except (TypeError, ValueError):
            continue
        if net_qty == 0:
            continue

        action = "SELL" if net_qty > 0 else "BUY"
        quantity = abs(net_qty)

        symbol = position.get("tradingSymbol") or ""
        if not symbol:
            from broker.icici.mapping.order_data import _resolve_symbol

            resolved, _ = _resolve_symbol(
                position.get("exchange_code") or "",
                position.get("stock_code") or "",
                position.get("expiry_date") or "",
                position.get("strike_price") or "",
                position.get("right") or "",
            )
            symbol = resolved or position.get("stock_code") or ""

        if not symbol:
            logger.warning(
                f"Skipping position close - unresolved symbol for {position.get('stock_code')}"
            )
            continue

        place_order_payload = {
            "apikey": current_api_key,
            "strategy": "Squareoff",
            "symbol": symbol,
            "action": action,
            "exchange": position.get("exchange_code") or "NSE",
            "pricetype": "MARKET",
            "product": position.get("product_type") or "margin",
            "quantity": str(quantity),
        }

        logger.debug(f"Close position payload: {place_order_payload}")

        _, api_response, _ = place_order_api(place_order_payload, AUTH_TOKEN)
        logger.debug(f"Close position response: {api_response}")

    return {"status": "success", "message": "All Open Positions SquaredOff"}, 200
