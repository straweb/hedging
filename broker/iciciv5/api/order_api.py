"""
ICICI Direct Breeze order APIs for OpenAlgo.

Books are queried per exchange with VALIDATED windows (Breeze rejects
empty/blank params with .NET null-reference errors and empty books arrive
as "Status 500: No Data Found", normalised upstream by breeze_client).

Design notes:

- Breeze books are windowed: order/trade books sweep
  NSE/BSE/NFO/BFO/MCX in <=9-day chunks over ``ICICI_BOOK_LOOKBACK_DAYS``
  (default 7). Windows entirely in the past are cached per day so the UI
  polling cadence only ever re-fetches today's slice.
- Every fetched book feeds an order-id -> exchange index so
  cancel/modify resolve the exchange even when the caller omits it
  (OpenAlgo passes the OpenAlgo exchange, Breeze wants its own code).
- place_order_api returns a response shim exposing ``.status`` (the
  services layer checks ``response.status == 200``).
"""

import os
import threading
import time
from datetime import datetime, timedelta

from broker.icici.api.breeze_client import (
    breeze_request,
    response_error,
    response_status,
)
from broker.icici.mapping.order_data import row_symbol
from broker.icici.mapping.transform_data import (
    get_breeze_exchange,
    transform_modify_order_data,
    transform_order_data,
)
from utils.logging import get_logger


logger = get_logger(__name__)


ORDER_ENDPOINT = "order"
TRADE_ENDPOINT = "trades"
POSITIONS_ENDPOINT = "portfoliopositions"
HOLDINGS_ENDPOINT = "portfolioholdings"
SQUAREOFF_ENDPOINT = "squareoff"

BOOK_EXCHANGES = ("nse", "bse", "nfo", "bfo", "mcx")

_BOOK_WINDOW_DAYS = 9  # Breeze-validated chunk size

_BOOK_TTL_SECONDS = 4.0

# Today's slice is re-fetched at most once per this interval, however fast
# the UI polls. Books change fast, but 5 exchanges x 2 endpoints per poll
# burns ICICI's daily quota in minutes; the UI cadence stays smooth.
_TODAY_TTL_SECONDS = float(os.getenv("ICICI_BOOK_TODAY_TTL_SECONDS", "5"))

_POSITIONS_TTL_SECONDS = float(os.getenv("ICICI_POSITIONS_TTL_SECONDS", "3"))

_HOLDINGS_TTL_SECONDS = float(os.getenv("ICICI_HOLDINGS_TTL_SECONDS", "60"))

_INDEX_TTL_SECONDS = 30.0

_index_lock = threading.RLock()

# auth -> (monotonic ts, {orderid: exchange_code_upper})
_order_index: dict[str, tuple[float, dict]] = {}

# auth -> (monotonic ts, {(endpoint, exchange, day): rows})  — completed-day cache
_book_cache: dict[str, tuple[float, dict]] = {}

# auth -> {(endpoint, exchange): (monotonic ts, rows)}  — today-slice cache
# so rapid UI polls do not burn the daily quota.
_today_cache: dict[str, dict[tuple[str, str], tuple[float, list]]] = {}

# auth -> (monotonic ts, positions rows)  — short-TTL position cache for
# smart orders / open-position lookups.
_position_cache: dict[str, tuple[float, list]] = {}

# auth -> (monotonic ts, holdings rows)
_holdings_cache: dict[str, tuple[float, list]] = {}

_position_lock = threading.RLock()

_holdings_lock = threading.RLock()


def _lookback_days() -> int:
    try:
        return max(1, min(90, int(os.getenv("ICICI_BOOK_LOOKBACK_DAYS", "7"))))
    except (TypeError, ValueError):
        return 7


class PlaceOrderResponse:
    """Minimal response shim carrying ``status`` for the services layer."""

    def __init__(self, status_code: int):
        self.status = status_code
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Windowed book helpers
# ---------------------------------------------------------------------------

def _book_auth_key(auth) -> str:
    return auth if isinstance(auth, str) else "global"


def _benign_book_error(error) -> bool:
    """
    Errors that mean "empty book / quota state", not a real failure —
    logged at most as noise, never as warnings:

    - "No Data Found" variants (empty book on that exchange/window),
    - "Object reference not set to an instance of an object." — Breeze's
      null-ref answer for books with no rows (e.g. MCX for an equity
      account),
    - "Limit exceed: API call per day" — the daily-quota breaker in
      breeze_client already warns once and suppresses further GETs.
    """

    text = str(error or "").lower()

    return any(
        marker in text
        for marker in (
            "no data",
            "no orders",
            "no trades",
            "no records",
            "object reference",
            "limit exceed",
        )
    )


def _day_windows(lookback_days: int) -> list[tuple[str, str, str]]:
    """
    <=9-day (from, to, cache_day) windows covering the lookback, oldest
    first. ``cache_day`` is the window's end date (a window is "completed"
    once its end is a past calendar day).
    """

    now = datetime.now()

    windows = []

    cursor_end = now

    while cursor_end > now - timedelta(days=lookback_days):
        window_start = max(
            cursor_end - timedelta(days=_BOOK_WINDOW_DAYS),
            now - timedelta(days=lookback_days),
        )

        windows.append(
            (
                window_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                cursor_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                cursor_end.strftime("%Y-%m-%d"),
            )
        )

        cursor_end = window_start

    windows.reverse()

    return windows


def _sweep_book(auth, endpoint: str) -> list[dict]:
    """
    Fetch ``endpoint`` (order/trades) for every exchange over the lookback
    windows, using the completed-day cache. Returns raw broker rows with
    ``exchange_code`` normalised onto each row.

    Cache keys are endpoint-qualified — order and trade sweeps share the
    day-cache store and must never read each other's rows.
    """

    cache_key = _book_auth_key(auth)

    lookback = _lookback_days()

    today = datetime.now().strftime("%Y-%m-%d")

    with _index_lock:
        cached = _book_cache.get(cache_key)

        if cached and (time.monotonic() - cached[0]) < _BOOK_TTL_SECONDS:
            day_cache = dict(cached[1])
        else:
            day_cache = {}

    collected: dict[str, dict] = {}

    for exchange in BOOK_EXCHANGES:
        for from_date, to_date, cache_day in _day_windows(lookback):
            cache_key_day = (endpoint, exchange, cache_day)

            is_today = cache_day == today

            rows = day_cache.get(cache_key_day)

            if is_today:
                # Today is always live data: re-read at most once per
                # _TODAY_TTL_SECONDS, whatever the polling cadence.
                with _index_lock:
                    today_entry = _today_cache.get(cache_key, {}).get(
                        (endpoint, exchange)
                    )

                if today_entry and (
                    (time.monotonic() - today_entry[0]) < _TODAY_TTL_SECONDS
                ):
                    rows = today_entry[1]

            elif rows is None:
                pass  # past window not cached yet — fetch below

            if rows is None:
                response = breeze_request(
                    auth,
                    "GET",
                    endpoint,
                    {
                        "exchange_code": exchange,
                        "from_date": from_date,
                        "to_date": to_date,
                    },
                )

                if response_status(response) != 200:
                    error = response_error(response)

                    if error and not _benign_book_error(error):
                        logger.warning(
                            "ICICI %s book %s failed: %s",
                            endpoint,
                            exchange,
                            error,
                        )

                    rows = []

                else:
                    rows = response.get("Success") or []

                    if isinstance(rows, dict):
                        rows = [rows]

                    if is_today:
                        # Cache successful live slices only — a suppressed
                        # (quota) or failed fetch must not pin empty rows.
                        with _index_lock:
                            _today_cache.setdefault(cache_key, {})[
                                (endpoint, exchange)
                            ] = (time.monotonic(), rows)

                if not is_today:
                    day_cache[cache_key_day] = rows

            for row in rows:
                if not isinstance(row, dict):
                    continue

                if not row.get("exchange_code"):
                    row["exchange_code"] = exchange.upper()

                row_id = str(
                    row.get("order_id")
                    or row.get("orderid")
                    or row.get("trade_id")
                    or id(row)
                )

                collected[f"{endpoint}:{exchange}:{row_id}"] = row

    with _index_lock:
        _book_cache[cache_key] = (time.monotonic(), day_cache)

    return list(collected.values())


def _index_orders(auth, rows) -> None:
    index = {}

    for row in rows:
        order_id = str(row.get("orderid") or row.get("order_id") or "")

        exchange = str(row.get("exchange_code") or "").upper()

        if order_id and exchange:
            index[order_id] = exchange

    with _index_lock:
        _order_index[auth if isinstance(auth, str) else "global"] = (
            time.monotonic(),
            index,
        )


def _resolve_order_exchange(order_id: str, auth) -> str | None:
    order_id = str(order_id)

    with _index_lock:
        cached = _order_index.get(auth if isinstance(auth, str) else "global")

        if cached and (time.monotonic() - cached[0]) < _INDEX_TTL_SECONDS:
            exchange = cached[1].get(order_id)

            if exchange:
                return exchange

    rows = get_order_book(auth)

    _index_orders(auth, rows)

    with _index_lock:
        cached = _order_index.get(auth if isinstance(auth, str) else "global")

        if cached:
            return cached[1].get(order_id)

    return None


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

def get_order_book(auth):
    """Raw order rows (pre-mapping) for all exchanges."""

    try:
        rows = _sweep_book(auth, ORDER_ENDPOINT)

        _index_orders(auth, rows)

        return rows

    except Exception:
        logger.exception("Error fetching ICICI order book")

        return []


def get_trade_book(auth):
    """Raw trade rows (pre-mapping) for all exchanges."""

    try:
        return _sweep_book(auth, TRADE_ENDPOINT)

    except Exception:
        logger.exception("Error fetching ICICI trade book")

        return []


def get_positions(auth, force: bool = False):
    """
    Raw position rows (GET /portfoliopositions).

    Cached for _POSITIONS_TTL_SECONDS so the UI cadence and smart-order
    polling do not burn quota. ``force=True`` bypasses the cache for paths
    that must act on live state (close-all square-offs).
    """

    cache_key = _book_auth_key(auth)

    if not force:
        with _position_lock:
            cached = _position_cache.get(cache_key)

            if cached and (
                (time.monotonic() - cached[0]) < _POSITIONS_TTL_SECONDS
            ):
                return cached[1]

    try:
        response = breeze_request(auth, "GET", POSITIONS_ENDPOINT)

        if response_status(response) != 200:
            error = response_error(response)

            if error and not _benign_book_error(error):
                logger.warning("ICICI positions failed: %s", error)

            return []

        rows = response.get("Success") or []

        if isinstance(rows, dict):
            rows = [rows]

        with _position_lock:
            _position_cache[cache_key] = (time.monotonic(), rows)

        return rows

    except Exception:
        logger.exception("Error fetching ICICI positions")

        return []


def get_holdings(auth):
    """
    Raw holding rows. NSE is queried first; BSE is merged only when NSE
    comes back empty (Breeze holdings are per exchange).

    Holdings barely move intraday, so the result is cached for
    _HOLDINGS_TTL_SECONDS (env ICICI_HOLDINGS_TTL_SECONDS) — the holdings
    page would otherwise spend 2 quota calls per poll.
    """

    cache_key = _book_auth_key(auth)

    with _holdings_lock:
        cached = _holdings_cache.get(cache_key)

        if cached and (
            (time.monotonic() - cached[0]) < _HOLDINGS_TTL_SECONDS
        ):
            return cached[1]

    collected: list[dict] = []

    any_success = False

    for exchange in ("nse", "bse"):
        response = breeze_request(
            auth,
            "GET",
            HOLDINGS_ENDPOINT,
            {"exchange_code": exchange},
        )

        if response_status(response) != 200:
            error = response_error(response)

            if error and not _benign_book_error(error):
                logger.warning(
                    "ICICI holdings %s failed: %s", exchange, error
                )

            continue

        any_success = True

        rows = response.get("Success") or []

        if isinstance(rows, dict):
            rows = [rows]

        for row in rows:
            if isinstance(row, dict):
                if not row.get("exchange_code"):
                    row["exchange_code"] = exchange.upper()

                collected.append(row)

        if collected:
            break

    if any_success:
        with _holdings_lock:
            _holdings_cache[cache_key] = (time.monotonic(), collected)

    return collected


# ---------------------------------------------------------------------------
# Open position (smart orders)
# ---------------------------------------------------------------------------

def get_open_position(symbol, exchange, product, auth):
    """
    Net position quantity for (symbol, exchange, product) as a STR
    (OpenAlgo smart-order contract).
    """

    try:
        with _position_lock:
            cached = _position_cache.get(
                auth if isinstance(auth, str) else "global"
            )

            rows = (
                cached[1]
                if cached and (time.monotonic() - cached[0]) < 3.0
                else None
            )

        if rows is None:
            rows = get_positions(auth)

        target_symbol = str(symbol or "").upper()

        for row in rows:
            if not isinstance(row, dict):
                continue

            row_symbol_name, row_exchange = row_symbol(row)

            if (
                str(row_symbol_name).upper() == target_symbol
                and str(row_exchange).upper() == str(exchange or "").upper()
            ):
                quantity = row.get("quantity")

                try:
                    return str(int(float(quantity or 0)))
                except (TypeError, ValueError):
                    return "0"

        return "0"

    except Exception:
        logger.exception("Error resolving ICICI open position")

        return "0"


# ---------------------------------------------------------------------------
# Place
# ---------------------------------------------------------------------------

def _extract_order_id(response) -> str | None:
    if not isinstance(response, dict):
        return None

    success = response.get("Success")

    if isinstance(success, dict):
        order_id = (
            success.get("order_id")
            or success.get("orderid")
            or success.get("OrderID")
        )

        if order_id:
            return str(order_id)

    return None


def place_order_api(data, auth):
    """
    Place one order. Returns (response, response_data, orderid) with
    ``response.status`` set for the services layer.
    """

    try:
        payload = transform_order_data(data)
    except ValueError as exc:
        return (
            PlaceOrderResponse(400),
            {"status": "error", "message": str(exc)},
            None,
        )
    except Exception as exc:
        logger.exception("ICICI order payload transform failed")

        return (
            PlaceOrderResponse(400),
            {"status": "error", "message": f"Invalid order payload: {exc}"},
            None,
        )

    logger.info("Payload for ICICI place_order: %s", payload)

    response = breeze_request(auth, "POST", ORDER_ENDPOINT, payload)

    status_code = response_status(response)

    order_id = _extract_order_id(response) if status_code == 200 else None

    if status_code == 200 and order_id:
        return (
            PlaceOrderResponse(200),
            {"status": "success", "orderid": order_id},
            order_id,
        )

    message = response_error(response) or "Failed to place order"

    logger.error("ICICI place_order failed: %s", message)

    return (
        PlaceOrderResponse(status_code),
        {"status": "error", "message": message},
        None,
    )


def place_smartorder_api(data, auth):
    """
    Smart order: compute the quantity delta between the requested position
    and the live net position, then place only the difference.
    """

    symbol = data.get("symbol")
    exchange = data.get("exchange")
    product = data.get("product") or "MIS"
    action = str(data.get("action") or "").upper()

    try:
        quantity = int(float(data.get("quantity") or 0))
    except (TypeError, ValueError):
        return (
            PlaceOrderResponse(400),
            {"status": "error", "message": "Invalid quantity"},
            None,
        )

    if not symbol or quantity <= 0 or action not in ("BUY", "SELL"):
        return (
            PlaceOrderResponse(400),
            {"status": "error", "message": "Invalid smart order payload"},
            None,
        )

    current = int(get_open_position(symbol, exchange, product, auth) or 0)

    desired = quantity if action == "BUY" else -quantity

    delta = desired - current

    if delta == 0:
        return (
            PlaceOrderResponse(200),
            {
                "status": "success",
                "message": "Position already at target — no order placed",
                "orderid": None,
            },
            None,
        )

    order_payload = dict(data)

    order_payload["action"] = "BUY" if delta > 0 else "SELL"

    order_payload["quantity"] = abs(delta)

    return place_order_api(order_payload, auth)


# ---------------------------------------------------------------------------
# Modify / cancel
# ---------------------------------------------------------------------------

def modify_order(data, auth):
    """
    Modify an order. Returns (response_message, status_code).
    """

    try:
        payload = transform_modify_order_data(data)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}, 400
    except Exception as exc:
        logger.exception("ICICI modify payload transform failed")

        return {"status": "error", "message": f"Invalid modify payload: {exc}"}, 400

    order_id = payload.get("order_id") or ""

    if not payload.get("exchange_code"):
        exchange = _resolve_order_exchange(order_id, auth)

        if exchange:
            payload["exchange_code"] = exchange

    logger.info("Payload for ICICI modify_order: %s", payload)

    response = breeze_request(auth, "PUT", ORDER_ENDPOINT, payload)

    status_code = response_status(response)

    if status_code == 200:
        return {"status": "success", "orderid": order_id}, 200

    message = response_error(response) or "Failed to modify order"

    logger.error("ICICI modify_order failed: %s", message)

    return {"status": "error", "message": message}, status_code


def cancel_order(orderid, auth):
    """
    Cancel one order. Returns (response_message, status_code).
    """

    order_id = str(orderid or "")

    if not order_id:
        return {"status": "error", "message": "Order id is required"}, 400

    exchange_upper = _resolve_order_exchange(order_id, auth)

    if not exchange_upper:
        return (
            {
                "status": "error",
                "message": f"Order {order_id} not found in ICICI order book",
            },
            404,
        )

    payload = {
        "exchange_code": exchange_upper,
        "order_id": order_id,
    }

    logger.info("Payload for ICICI cancel_order: %s", payload)

    response = breeze_request(auth, "DELETE", ORDER_ENDPOINT, payload)

    status_code = response_status(response)

    if status_code == 200:
        return {"status": "success", "orderid": order_id}, 200

    message = response_error(response) or "Failed to cancel order"

    logger.error("ICICI cancel_order failed: %s", message)

    return {"status": "error", "message": message}, status_code


def cancel_all_orders_api(data=None, auth=None):
    """
    Cancel every open order. Returns (canceled[], failed[]).
    """

    canceled = []
    failed = []

    rows = get_order_book(auth)

    for row in rows:
        if not isinstance(row, dict):
            continue

        order_id = str(row.get("orderid") or row.get("order_id") or "")

        status = str(row.get("order_status") or "").lower()

        if not order_id or status not in ("open",):
            continue

        result, status_code = cancel_order(order_id, auth)

        if status_code == 200:
            canceled.append(order_id)
        else:
            failed.append(order_id)

    return canceled, failed


def close_all_positions(api_key=None, auth=None):
    """
    Square off every open position with market orders.
    Returns (response_message, status_code).
    """

    # Live fetch — a stale cached view here could square off a position
    # that was already closed.
    rows = get_positions(auth, force=True)

    closed = 0
    failed = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        try:
            quantity = int(float(row.get("quantity") or 0))

        except (TypeError, ValueError):
            continue

        if quantity == 0:
            continue

        symbol, exchange = row_symbol(row)

        if not symbol or not exchange:
            failed += 1

            continue

        result, response_data, orderid = place_order_api(
            {
                "symbol": symbol,
                "exchange": exchange,
                "action": "SELL" if quantity > 0 else "BUY",
                "quantity": abs(quantity),
                "pricetype": "MARKET",
                "product": "NRML",
                "remarks": "OpenAlgo close-all",
            },
            auth,
        )

        if result.status == 200:
            closed += 1
        else:
            failed += 1

    if failed == 0:
        return {
            "status": "success",
            "message": f"Closed {closed} position(s)",
        }, 200

    return {
        "status": "error",
        "message": f"Closed {closed}, failed {failed} position(s)",
    }, 207 if closed else 500
