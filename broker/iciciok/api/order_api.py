import threading
import time
from types import SimpleNamespace
from datetime import datetime, timedelta

from broker.icici.api.breeze_client import get_breeze_client

from broker.icici.mapping.order_data import map_order_data

from database.token_db import (
    get_br_symbol,
    get_oa_symbol,
)

from utils.logging import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_ORDER_LOCK = threading.Lock()
_LAST_ORDER_TIME = 0.0

# Stay below ICICI Breeze order-action rate limits.
_ORDER_INTERVAL = 0.105


def _throttle():
    global _LAST_ORDER_TIME

    with _ORDER_LOCK:
        now = time.monotonic()

        wait = _ORDER_INTERVAL - (
            now - _LAST_ORDER_TIME
        )

        if wait > 0:
            time.sleep(wait)

        _LAST_ORDER_TIME = time.monotonic()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exchange(exchange):
    return str(
        exchange or ""
    ).upper()


def _product(data):
    """
    Convert OpenAlgo product to Breeze product.

    NSE/BSE equity:
        CNC  -> cash
        MIS  -> margin

    NFO/BFO:
        option instrument -> options
        futures instrument -> futures
    """
    exchange = _exchange(
        data.get("exchange")
    )

    value = str(
        data.get("product") or ""
    ).upper()

    right = str(
        data.get("right") or ""
    ).strip().lower()

    strike = data.get(
        "strike_price"
    )

    expiry = data.get(
        "expiry_date"
    )

    is_option = (
        right in {
            "call",
            "put",
            "ce",
            "pe",
        }
        or (
            strike not in {
                None,
                "",
                "0",
                0,
                0.0,
            }
            and bool(expiry)
        )
    )

    if exchange in {
        "NFO",
        "BFO",
    }:
        if is_option:
            return "options"

        return "futures"

    if value == "CNC":
        return "cash"

    if value in {
        "NRML",
        "MIS",
    }:
        return "margin"

    return "margin"


def _order_type(pricetype):
    value = str(
        pricetype or ""
    ).upper()

    if value == "MARKET":
        return "market"

    if value == "SL":
        return "stoploss"

    if value == "SL-M":
        return "stoploss"

    return "limit"


def _action(action):
    return str(
        action or ""
    ).lower()


def _expiry(data):
    value = (
        data.get("expiry_date")
        or ""
    )

    return str(value)


def _right(data):
    value = str(
        data.get(
            "right",
            "",
        )
        or ""
    ).lower()

    if value in {
        "call",
        "ce",
    }:
        return "call"

    if value in {
        "put",
        "pe",
    }:
        return "put"

    return "others"


def _strike_price(data):
    value = (
        data.get(
            "strike_price"
        )
        or ""
    )

    if value == "":
        return "0"

    return str(value)


def _place_payload(data):
    symbol = (
        get_br_symbol(
            data["symbol"],
            data["exchange"],
        )
        or data["symbol"]
    )

    payload = {
        "stock_code": symbol,
        "exchange_code": _exchange(
            data["exchange"]
        ),
        "product": _product(data),
        "action": _action(
            data.get("action")
        ),
        "order_type": _order_type(
            data.get("pricetype")
        ),
        "quantity": str(
            data.get(
                "quantity",
                0,
            )
        ),
        "price": str(
            data.get(
                "price",
                "0",
            )
            or "0"
        ),
        "validity": "day",
        "stoploss": str(
            data.get(
                "trigger_price",
                "0",
            )
            or "0"
        ),
        "disclosed_quantity": str(
            data.get(
                "disclosed_quantity",
                "0",
            )
            or "0"
        ),
        "expiry_date": _expiry(data),
        "right": _right(data),
        "strike_price": _strike_price(data),
        "user_remark": str(
            data.get(
                "strategy",
                "OpenAlgo",
            )
            or "OpenAlgo"
        ),
    }

    return payload


def _extract_order_id(response):
    if not isinstance(
        response,
        dict,
    ):
        return None

    success = response.get(
        "Success"
    )

    if isinstance(
        success,
        list,
    ):
        success = (
            success[0]
            if success
            else {}
        )

    if isinstance(
        success,
        dict,
    ):
        return (
            success.get("order_id")
            or success.get("orderid")
            or success.get("orderId")
        )

    return None


def _response_status(response):
    if not isinstance(
        response,
        dict,
    ):
        return 500

    value = response.get(
        "Status"
    )

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 500


def _make_response(response):
    """
    Breeze returns a dictionary instead of a requests/httpx Response.

    OpenAlgo service code expects:
        res.status

    Return a small compatible response object while preserving the original
    Breeze dictionary separately as response_data.
    """
    return SimpleNamespace(
        status=_response_status(
            response
        ),
        status_code=_response_status(
            response
        ),
        text=str(response),
    )


def _error_response(message, status=400):
    response_data = {
        "status": "error",
        "message": str(message),
        "Status": status,
        "Error": str(message),
    }

    response = SimpleNamespace(
        status=status,
        status_code=status,
        text=str(response_data),
    )

    return (
        response,
        response_data,
        None,
    )


def _success_response(
    response_data,
    orderid,
):
    status = _response_status(
        response_data
    )

    response = _make_response(
        response_data
    )

    if status == 200 and orderid:
        return (
            response,
            response_data,
            orderid,
        )

    return (
        response,
        response_data,
        orderid,
    )


def _position_rows(response):
    if not response:
        return []

    if not isinstance(
        response,
        dict,
    ):
        return []

    positions = response.get(
        "Success"
    )

    if positions is None:
        positions = response.get(
            "data"
        )

    if isinstance(
        positions,
        dict,
    ):
        positions = (
            positions.get(
                "positions"
            )
            or positions.get(
                "data"
            )
            or []
        )

    if not isinstance(
        positions,
        list,
    ):
        return []

    return positions


def _position_quantity(position):
    for key in (
        "quantity",
        "net_quantity",
        "net_qty",
        "netQuantity",
    ):
        value = position.get(
            key
        )

        if value not in (
            None,
            "",
        ):
            try:
                return int(
                    float(value)
                )
            except (
                TypeError,
                ValueError,
            ):
                pass

    return 0


def _normalise_product_type(value):
    value = str(
        value or ""
    ).strip().lower()

    if value in {
        "cash",
        "cnc",
    }:
        return "cash"

    if value in {
        "margin",
        "mis",
    }:
        return "margin"

    if value in {
        "futures",
        "future",
    }:
        return "futures"

    if value in {
        "options",
        "option",
    }:
        return "options"

    return value


def _position_matches(
    position,
    tradingsymbol,
    exchange,
    product,
):
    exchange = _exchange(
        exchange
    )

    br_symbol = (
        get_br_symbol(
            tradingsymbol,
            exchange,
        )
        or tradingsymbol
    )

    position_symbol = (
        position.get(
            "stock_code"
        )
        or position.get(
            "symbol"
        )
        or position.get(
            "trading_symbol"
        )
    )

    if not position_symbol:
        return False

    if str(
        position_symbol
    ).upper() != str(
        br_symbol
    ).upper():
        return False

    position_exchange = (
        position.get(
            "exchange_code"
        )
        or position.get(
            "exchange"
        )
    )

    if position_exchange:
        if _exchange(
            position_exchange
        ) != exchange:
            return False

    requested_product = _product(
        {
            "symbol": tradingsymbol,
            "exchange": exchange,
            "product": product,
            "right": position.get(
                "right"
            ),
            "strike_price": position.get(
                "strike_price"
            ),
            "expiry_date": position.get(
                "expiry_date"
            ),
        }
    )

    position_product = (
        position.get(
            "product_type"
        )
        or position.get(
            "product"
        )
    )

    if position_product:
        if (
            _normalise_product_type(
                position_product
            )
            != requested_product
        ):
            return False

    return True


# ---------------------------------------------------------------------------
# Position cache
# ---------------------------------------------------------------------------

_position_cache = {}
_position_cache_lock = threading.Lock()
_POSITION_CACHE_TTL = 1.0


def _get_cached_positions(auth):
    with _position_cache_lock:
        cached = _position_cache.get(
            auth
        )

        if cached:
            age = (
                time.monotonic()
                - cached["timestamp"]
            )

            if age < _POSITION_CACHE_TTL:
                logger.debug(
                    "ICICI position book served from cache"
                )

                return cached["data"]

    positions_data = get_positions(
        auth
    )

    with _position_cache_lock:
        _position_cache[auth] = {
            "data": positions_data,
            "timestamp": time.monotonic(),
        }

    return positions_data


def _invalidate_position_cache(auth):
    with _position_cache_lock:
        _position_cache.pop(
            auth,
            None,
        )


# ---------------------------------------------------------------------------
# Open position
# ---------------------------------------------------------------------------

def get_open_position(
    tradingsymbol,
    exchange,
    product,
    auth,
):
    """
    Return OpenAlgo-compatible net position quantity as a string.
    """
    try:
        response = _get_cached_positions(
            auth
        )

        for position in _position_rows(
            response
        ):
            if _position_matches(
                position,
                tradingsymbol,
                exchange,
                product,
            ):
                quantity = _position_quantity(
                    position
                )

                logger.debug(
                    "ICICI net position %s:%s:%s = %s",
                    exchange,
                    tradingsymbol,
                    product,
                    quantity,
                )

                return str(
                    quantity
                )

    except Exception as exc:
        logger.exception(
            "Error getting ICICI open position for %s:%s: %s",
            exchange,
            tradingsymbol,
            exc,
        )

    return "0"


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def place_order_api(data, auth):
    """
    Place an ICICI Direct Breeze order.

    Returns:

        (response, response_data, orderid)

    `response` is the Breeze response object expected by
    OpenAlgo's service layer, with `.status` available.
    """

    breeze = get_breeze_client(
        auth
    )

    _throttle()

    payload = _place_payload(
        data
    )

    logger.info(
        "Payload for ICICI place_order_api: %s",
        payload,
    )

    try:

        response_data = breeze.place_order(
            **payload
        )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze place_order exception"
        )

        response_data = {
            "Success": "",
            "Status": 500,
            "Error": str(exc),
        }

    logger.info(
        "ICICI Breeze raw order response: %s",
        response_data,
    )

    status_code = 500

    if isinstance(
        response_data,
        dict,
    ):

        status_code = response_data.get(
            "Status",
            500,
        )

        try:
            status_code = int(
                status_code
            )
        except (
            TypeError,
            ValueError,
        ):
            status_code = 500

    order_id = _extract_order_id(
        response_data
    )

    # -------------------------------------------------------------
    # Create a response-like object for OpenAlgo service layer.
    #
    # place_order_service.py expects:
    #
    #     res.status
    #
    # Breeze returns a dictionary rather than the requests.Response
    # object used by some other OpenAlgo brokers.
    # -------------------------------------------------------------

    class _BreezeResponse:

        def __init__(
            self,
            status,
        ):
            self.status = status

        def __repr__(self):
            return (
                f"<BreezeResponse "
                f"status={self.status}>"
            )

    response = _BreezeResponse(
        status_code
    )

    return (
        response,
        response_data,
        order_id,
    )


# def place_order_api(data, auth):
#     breeze = get_breeze_client(
#         auth
#     )

#     _throttle()

#     payload = _place_payload(
#         data
#     )

#     logger.info(
#         "Payload for ICICI place_order_api: %s",
#         payload,
#     )

#     response_data = breeze.place_order(
#         **payload
#     )

#     logger.info(
#         "ICICI Breeze raw order response: %s",
#         response_data,
#     )

#     order_id = _extract_order_id(
#         response_data
#     )

#     if not order_id:
#         error = (
#             response_data.get(
#                 "Error"
#             )
#             if isinstance(
#                 response_data,
#                 dict,
#             )
#             else None
#         )

#         return (
#             None,
#             response_data,
#             None,
#         )

#     return (
#         200,
#         response_data,
#         order_id,
#     )

# def place_order_api(
#     data,
#     auth,
# ):
#     """
#     Place a regular ICICI Breeze order.

#     OpenAlgo contract:
#         (response, response_data, orderid)

#     `response.status` is provided so the OpenAlgo service layer can use the
#     same success convention as other broker adapters.
#     """
#     try:
#         breeze = get_breeze_client(
#             auth
#         )

#         _throttle()

#         payload = _place_payload(
#             data
#         )

#         logger.info(
#             "Payload for ICICI place_order_api: %s",
#             payload,
#         )

#         try:
#             response_data = (
#                 breeze.place_order(
#                     **payload
#                 )
#             )

#         except Exception as exc:
#             error_text = str(
#                 exc
#             )

#             logger.exception(
#                 "ICICI Breeze place_order failed: %s",
#                 error_text,
#             )

#             return _error_response(
#                 error_text,
#                 400,
#             )

#         logger.info(
#             "ICICI Breeze raw order response: %s",
#             response_data,
#         )

#         if not isinstance(
#             response_data,
#             dict,
#         ):
#             return _error_response(
#                 "ICICI Breeze returned an invalid response",
#                 500,
#             )

#         orderid = _extract_order_id(
#             response_data
#         )

#         status = _response_status(
#             response_data
#         )

#         if not orderid:
#             error = (
#                 response_data.get(
#                     "Error"
#                 )
#                 or response_data.get(
#                     "error"
#                 )
#                 or response_data.get(
#                     "message"
#                 )
#                 or "ICICI order placement failed"
#             )

#             if status == 200:
#                 status = 400

#             return _error_response(
#                 error,
#                 status,
#             )

#         return _success_response(
#             response_data,
#             orderid,
#         )

#     except Exception as exc:
#         logger.exception(
#             "Error in ICICI place_order_api: %s",
#             exc,
#         )

#         return _error_response(
#             f"Error placing ICICI order: {exc}",
#             500,
#         )


# ---------------------------------------------------------------------------
# Smart order
# ---------------------------------------------------------------------------

_symbol_locks = {}
_symbol_locks_lock = threading.Lock()


def _get_symbol_lock(
    symbol,
    exchange,
    product,
):
    key = (
        f"{symbol}:"
        f"{exchange}:"
        f"{product}"
    )

    with _symbol_locks_lock:
        if key not in _symbol_locks:
            _symbol_locks[key] = (
                threading.Lock()
            )

        return _symbol_locks[key]


def place_smartorder_api(
    data,
    auth,
):
    """
    Reconcile the live ICICI position to data['position_size'].

    OpenAlgo contract:
        (response, response_data, orderid)
    """
    res = None
    response_data = {
        "status": "error",
        "message": (
            "No action required "
            "or invalid parameters"
        ),
    }
    orderid = None

    try:
        symbol = data.get(
            "symbol"
        )
        exchange = data.get(
            "exchange"
        )
        product = data.get(
            "product"
        )

        if not all(
            [
                symbol,
                exchange,
                product,
            ]
        ):
            logger.debug(
                "Missing required parameters in ICICI place_smartorder_api"
            )

            return (
                res,
                response_data,
                orderid,
            )

        with _get_symbol_lock(
            symbol,
            exchange,
            product,
        ):
            position_size = int(
                float(
                    data.get(
                        "position_size",
                        "0",
                    )
                    or 0
                )
            )

            current_position = int(
                get_open_position(
                    symbol,
                    exchange,
                    product,
                    auth,
                )
            )

            logger.debug(
                "ICICI position_size=%s current_position=%s",
                position_size,
                current_position,
            )

            action = None
            quantity = 0

            # Target position = zero.
            if (
                position_size == 0
                and current_position == 0
            ):
                action = data.get(
                    "action",
                    "BUY",
                ).upper()

                quantity = int(
                    float(
                        data.get(
                            "quantity",
                            0,
                        )
                        or 0
                    )
                )

            elif (
                position_size == 0
                and current_position > 0
            ):
                action = "SELL"
                quantity = abs(
                    current_position
                )

            elif (
                position_size == 0
                and current_position < 0
            ):
                action = "BUY"
                quantity = abs(
                    current_position
                )

            # No current position.
            elif current_position == 0:
                action = (
                    "BUY"
                    if position_size > 0
                    else "SELL"
                )

                quantity = abs(
                    position_size
                )

            # Existing long/short position.
            else:
                if position_size > current_position:
                    action = "BUY"
                    quantity = (
                        position_size
                        - current_position
                    )

                elif position_size < current_position:
                    action = "SELL"
                    quantity = (
                        current_position
                        - position_size
                    )

            if (
                action
                and quantity > 0
            ):
                order_data = data.copy()

                order_data[
                    "action"
                ] = action

                order_data[
                    "quantity"
                ] = str(
                    quantity
                )

                (
                    res,
                    response_data,
                    orderid,
                ) = place_order_api(
                    order_data,
                    auth,
                )

                if (
                    res is not None
                    and getattr(
                        res,
                        "status",
                        500,
                    )
                    == 200
                ):
                    _invalidate_position_cache(
                        auth
                    )

                return (
                    res,
                    response_data,
                    orderid,
                )

            logger.debug(
                "ICICI smart order: no action required"
            )

            response_data = {
                "status": "success",
                "message": (
                    "No action needed. "
                    "Position already matched."
                ),
            }

            res = SimpleNamespace(
                status=200,
                status_code=200,
                text=str(
                    response_data
                ),
            )

            return (
                res,
                response_data,
                orderid,
            )

    except Exception as exc:
        error_msg = (
            "Error in "
            f"place_smartorder_api: {exc}"
        )

        logger.exception(
            error_msg
        )

        return (
            res,
            {
                "status": "error",
                "message": error_msg,
            },
            orderid,
        )


# ---------------------------------------------------------------------------
# Modify order
# ---------------------------------------------------------------------------

def modify_order(
    data,
    auth,
):
    """
    Modify an existing ICICI Breeze order.

    OpenAlgo contract:
        (response_data, status_code)
    """
    try:
        breeze = get_breeze_client(
            auth
        )

        _throttle()

        order_id = str(
            data["orderid"]
        )

        exchange = _exchange(
            data.get("exchange")
        )

        response_data = (
            breeze.modify_order(
                order_id=order_id,
                exchange_code=exchange,
                order_type=_order_type(
                    data.get(
                        "pricetype"
                    )
                ),
                stoploss=str(
                    data.get(
                        "trigger_price",
                        "0",
                    )
                    or "0"
                ),
                quantity=str(
                    data.get(
                        "quantity",
                        "0",
                    )
                    or "0"
                ),
                price=str(
                    data.get(
                        "price",
                        "0",
                    )
                    or "0"
                ),
                validity="day",
                disclosed_quantity=str(
                    data.get(
                        "disclosed_quantity",
                        "0",
                    )
                    or "0"
                ),
            )
        )

        logger.debug(
            "ICICI modify order response: %s",
            response_data,
        )

        if not isinstance(
            response_data,
            dict,
        ):
            return (
                {
                    "status": "error",
                    "message": (
                        "Invalid response from ICICI"
                    ),
                },
                500,
            )

        status = _response_status(
            response_data
        )

        success = response_data.get(
            "Success"
        )

        if status == 200 and success:
            modified_orderid = (
                order_id
            )

            if isinstance(
                success,
                dict,
            ):
                modified_orderid = (
                    success.get(
                        "order_id"
                    )
                    or success.get(
                        "orderid"
                    )
                    or order_id
                )

            return (
                {
                    "status": "success",
                    "orderid": str(
                        modified_orderid
                    ),
                },
                200,
            )

        error = (
            response_data.get(
                "Error"
            )
            or response_data.get(
                "error"
            )
            or response_data.get(
                "message"
            )
            or "Failed to modify order"
        )

        return (
            {
                "status": "error",
                "message": str(
                    error
                ),
            },
            status if status != 200 else 400,
        )

    except Exception as exc:
        logger.exception(
            "Error modifying ICICI order %s: %s",
            data.get("orderid"),
            exc,
        )

        return (
            {
                "status": "error",
                "message": (
                    f"Failed to modify order: {exc}"
                ),
            },
            500,
        )


# ---------------------------------------------------------------------------
# Cancel order
# ---------------------------------------------------------------------------

def cancel_order(
    orderid,
    auth,
    exchange=None,
):
    """
    Cancel an ICICI Breeze order.

    Breeze requires exchange_code for cancel_order, so exchange is accepted
    explicitly. For backward compatibility, if it is not supplied we inspect
    the order book to determine the exchange.
    """
    try:
        breeze = get_breeze_client(
            auth
        )

        if not exchange:
            try:
                order_book = (
                    get_order_book(
                        auth
                    )
                )

                rows = (
                    order_book.get(
                        "Success"
                    )
                    if isinstance(
                        order_book,
                        dict,
                    )
                    else []
                ) or []

                if isinstance(
                    rows,
                    dict,
                ):
                    rows = [
                        rows
                    ]

                for order in rows:
                    candidate_id = (
                        order.get(
                            "order_id"
                        )
                        or order.get(
                            "order_reference"
                        )
                    )

                    if str(
                        candidate_id
                    ) == str(
                        orderid
                    ):
                        exchange = (
                            order.get(
                                "exchange_code"
                            )
                            or order.get(
                                "exchange"
                            )
                        )
                        break

            except Exception:
                logger.exception(
                    "Unable to determine exchange for ICICI order %s",
                    orderid,
                )

        exchange = _exchange(
            exchange or "NSE"
        )

        _throttle()

        response_data = (
            breeze.cancel_order(
                exchange_code=exchange,
                order_id=str(
                    orderid
                ),
            )
        )

        logger.debug(
            "ICICI cancel order response: %s",
            response_data,
        )

        status = _response_status(
            response_data
        )

        if (
            isinstance(
                response_data,
                dict,
            )
            and status == 200
        ):
            return (
                {
                    "status": "success",
                    "orderid": str(
                        orderid
                    ),
                },
                200,
            )

        error = (
            response_data.get(
                "Error"
            )
            if isinstance(
                response_data,
                dict,
            )
            else None
        ) or (
            response_data.get(
                "message"
            )
            if isinstance(
                response_data,
                dict,
            )
            else None
        ) or "Failed to cancel order"

        return (
            {
                "status": "error",
                "message": str(
                    error
                ),
            },
            status if status != 200 else 400,
        )

    except Exception as exc:
        logger.exception(
            "Error cancelling ICICI order %s: %s",
            orderid,
            exc,
        )

        return (
            {
                "status": "error",
                "message": (
                    f"Failed to cancel order: {exc}"
                ),
            },
            500,
        )


# ---------------------------------------------------------------------------
# Order / trade / portfolio APIs
# ---------------------------------------------------------------------------
# from datetime import datetime, timedelta



def get_order_book(auth, from_date=None, to_date=None):
    breeze = get_breeze_client(auth)

    now = datetime.now()

    if to_date is None:
        to_date = now.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

    if from_date is None:
        from_date = (
            now - timedelta(days=1)
        ).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

    logger.info(
        "ICICI Breeze order book request: from_date=%s, to_date=%s",
        from_date,
        to_date,
    )

    response = breeze.get_order_list(
        exchange_code="NSE",
        from_date=from_date,
        to_date=to_date,
    )
    # logger.info(
    #     "ICICI Breeze order book raw response: %s",
    #     response,
    # )

    if response.get("Status") != 200:
        logger.error(
            "ICICI Breeze get order book failed: %s",
            response.get("Error"),
        )
        # break

    return response

# Instead, paginate the date range in 10-day chunks:
# For your immediate testing, though, just changing timedelta(days=30) to timedelta(days=9) is enough.

def get_order_book(auth):
    breeze = get_breeze_client(auth)

    end = datetime.now()
    start = end - timedelta(days=90)

    all_orders = []

    current_start = start

    while current_start < end:
        current_end = min(
            current_start + timedelta(days=9),
            end,
        )

        from_date = current_start.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

        to_date = current_end.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

        logger.info(
            "ICICI Breeze order book request: "
            "from_date=%s, to_date=%s",
            from_date,
            to_date,
        )

        response = breeze.get_order_list(
            exchange_code="NSE",
            from_date=from_date,
            to_date=to_date,
        )

        # logger.info(
        #     "ICICI Breeze order book raw response: %s",
        #     response,
        # )

        if not isinstance(response, dict):
            break

        if response.get("Status") != 200:
            logger.error(
                "ICICI Breeze order book failed: %s",
                response.get("Error"),
            )
            break

        success = response.get("Success")

        if isinstance(success, list):
            all_orders.extend(success)

        current_start = current_end + timedelta(seconds=1)

    return {
        "Success": all_orders,
        "Status": 200,
        "Error": None,
    }


# def get_order_book(auth):
#     """
#     Fetch ICICI Direct order book for supported exchanges.

#     Breeze requires:
#         exchange_code
#         from_date
#         to_date
#     """

#     breeze = get_breeze_client(
#         auth
#     )

#     today = datetime.now().strftime(
#         "%Y-%m-%d"
#     )

#     orders = []

#     for exchange in (
#         "NSE",
#         "BSE",
#     ):

#         try:

#             response = breeze.get_order_list(
#                 exchange_code=exchange,
#                 from_date=today,
#                 to_date=today,
#             )

#             logger.info(
#                 "ICICI Breeze %s order book Status: %s",
#                 exchange,
#                 response.get("Status")
#                 if isinstance(
#                     response,
#                     dict,
#                 )
#                 else None,
#             )

#             logger.info(
#                 "ICICI Breeze %s order book Error: %s",
#                 exchange,
#                 response.get("Error")
#                 if isinstance(
#                     response,
#                     dict,
#                 )
#                 else None,
#             )

#             rows = (
#                 response.get(
#                     "Success"
#                 )
#                 if isinstance(
#                     response,
#                     dict,
#                 )
#                 else []
#             )

#             if isinstance(
#                 rows,
#                 dict,
#             ):
#                 rows = [
#                     rows
#                 ]

#             if isinstance(
#                 rows,
#                 list,
#             ):
#                 orders.extend(
#                     rows
#                 )

#         except Exception:
#             logger.exception(
#                 "Failed to fetch ICICI %s order book",
#                 exchange,
#             )

#     return {
#         "Success": orders,
#         "Status": 200,
#         "Error": None,
#     }


def get_trade_book(
    auth,
):
    try:
        breeze = get_breeze_client(
            auth
        )

        response = (
            breeze.get_trade_list()
        )

        if isinstance(
            response,
            dict,
        ):
            return response

        return {
            "status": "error",
            "message": (
                "Invalid trade book response"
            ),
        }

    except Exception as exc:
        logger.exception(
            "Error fetching ICICI trade book: %s",
            exc,
        )

        return {
            "status": "error",
            "message": (
                f"Error fetching trade book: {exc}"
            ),
        }


def get_positions(
    auth,
):
    try:
        breeze = get_breeze_client(
            auth
        )

        response = (
            breeze.get_portfolio_positions()
        )

        if isinstance(
            response,
            dict,
        ):
            return response

        return {
            "status": "error",
            "message": (
                "Invalid positions response"
            ),
        }

    except Exception as exc:
        logger.exception(
            "Error fetching ICICI positions: %s",
            exc,
        )

        return {
            "status": "error",
            "message": (
                f"Error fetching positions: {exc}"
            ),
        }


def get_holdings(
    auth,
):
    try:
        breeze = get_breeze_client(
            auth
        )

        response = (
            breeze.get_portfolio_holding()
        )

        if isinstance(
            response,
            dict,
        ):
            return response

        return {
            "status": "error",
            "message": (
                "Invalid holdings response"
            ),
        }

    except Exception as exc:
        logger.exception(
            "Error fetching ICICI holdings: %s",
            exc,
        )

        return {
            "status": "error",
            "message": (
                f"Error fetching holdings: {exc}"
            ),
        }


# ---------------------------------------------------------------------------
# Cancel all orders
# ---------------------------------------------------------------------------

def cancel_all_orders_api(
    data,
    auth,
):
    try:
        response = get_order_book(
            auth
        )

        if not isinstance(
            response,
            dict,
        ):
            return [], []

        orders = response.get(
            "Success"
        ) or []

        if isinstance(
            orders,
            dict,
        ):
            orders = [
                orders
            ]

        cancelled = []
        failed = []

        cancellable_statuses = {
            "requested",
            "queued",
            "ordered",
            "open",
            "partially executed",
            "trigger pending",
            "pending",
            "pending trigger",
        }

        for order in orders:
            status = str(
                order.get(
                    "order_status",
                    order.get(
                        "status",
                        "",
                    ),
                )
            ).strip().lower()

            if status not in (
                cancellable_statuses
            ):
                continue

            order_id = (
                order.get(
                    "order_id"
                )
                or order.get(
                    "order_reference"
                )
            )

            if not order_id:
                continue

            exchange = (
                order.get(
                    "exchange_code"
                )
                or order.get(
                    "exchange"
                )
                or "NSE"
            )

            try:
                cancel_response, status_code = (
                    cancel_order(
                        order_id,
                        auth,
                        exchange,
                    )
                )

                if status_code == 200:
                    cancelled.append(
                        str(order_id)
                    )
                else:
                    failed.append(
                        str(order_id)
                    )

            except Exception:
                logger.exception(
                    "Failed cancelling ICICI order %s",
                    order_id,
                )

                failed.append(
                    str(order_id)
                )

        return (
            cancelled,
            failed,
        )

    except Exception:
        logger.exception(
            "Error cancelling all ICICI orders"
        )

        return (
            [],
            [],
        )


# ---------------------------------------------------------------------------
# Close all positions
# ---------------------------------------------------------------------------

def close_all_positions(
    current_api_key,
    auth,
):
    try:
        response = get_positions(
            auth
        )

        positions = _position_rows(
            response
        )

        if not positions:
            return (
                {
                    "message": (
                        "No Open Positions Found"
                    )
                },
                200,
            )

        failed = []

        for position in positions:
            quantity = _position_quantity(
                position
            )

            if quantity == 0:
                continue

            exchange = (
                position.get(
                    "exchange_code"
                )
                or position.get(
                    "exchange"
                )
                or "NSE"
            )

            broker_symbol = (
                position.get(
                    "stock_code"
                )
                or position.get(
                    "symbol"
                )
                or position.get(
                    "trading_symbol"
                )
            )

            if not broker_symbol:
                continue

            try:
                symbol = (
                    get_oa_symbol(
                        brsymbol=broker_symbol,
                        exchange=exchange,
                    )
                    or broker_symbol
                )
            except Exception:
                symbol = broker_symbol

            product_type = (
                position.get(
                    "product_type"
                )
                or position.get(
                    "product"
                )
                or "margin"
            )

            product_type_normalized = (
                _normalise_product_type(
                    product_type
                )
            )

            if product_type_normalized == "cash":
                product = "CNC"
            elif product_type_normalized in {
                "futures",
                "options",
            }:
                product = "NRML"
            else:
                product = "MIS"

            action = (
                "SELL"
                if quantity > 0
                else "BUY"
            )

            data = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": exchange,
                "pricetype": "MARKET",
                "product": product,
                "quantity": str(
                    abs(quantity)
                ),
                "price": "0",
                "trigger_price": "0",
            }

            # Preserve derivative details where available.
            if position.get(
                "expiry_date"
            ):
                data[
                    "expiry_date"
                ] = position.get(
                    "expiry_date"
                )

            if position.get(
                "right"
            ):
                data[
                    "right"
                ] = position.get(
                    "right"
                )

            if position.get(
                "strike_price"
            ) is not None:
                data[
                    "strike_price"
                ] = position.get(
                    "strike_price"
                )

            try:
                (
                    res,
                    api_response,
                    orderid,
                ) = place_order_api(
                    data,
                    auth,
                )

                if (
                    res is None
                    or getattr(
                        res,
                        "status",
                        500,
                    ) != 200
                ):
                    failed.append(
                        symbol
                    )

                logger.debug(
                    "ICICI square-off response for %s: %s",
                    symbol,
                    api_response,
                )

            except Exception:
                logger.exception(
                    "ICICI square-off failed for %s:%s",
                    exchange,
                    symbol,
                )

                failed.append(
                    symbol
                )

        _invalidate_position_cache(
            auth
        )

        if failed:
            return (
                {
                    "status": "error",
                    "message": (
                        "All Open Positions "
                        "SquaredOff with some failures"
                    ),
                    "failed": failed,
                },
                200,
            )

        return (
            {
                "status": "success",
                "message": (
                    "All Open Positions "
                    "SquaredOff"
                ),
            },
            200,
        )

    except Exception as exc:
        logger.exception(
            "Error closing all ICICI positions: %s",
            exc,
        )

        return (
            {
                "status": "error",
                "message": (
                    f"Failed to square off positions: {exc}"
                ),
            },
            500,
        )

def _get_breeze_error(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None

    error = response.get("Error")

    if error:
        return str(error)

    return None


def _extract_gtt_order_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None

    success = response.get("Success")

    if isinstance(success, dict):
        order_id = success.get("gtt_order_id")

        if order_id:
            return str(order_id)

    return None


def _validate_gtt_response(response: Any) -> tuple[bool, str | None]:
    if not isinstance(response, dict):
        return False, "Invalid Breeze response"

    status = response.get("Status")

    if status != 200:
        return False, _get_breeze_error(response) or "GTT request failed"

    if response.get("Error"):
        return False, str(response["Error"])

    return True, None


# ---------------------------------------------------------------------
# SINGLE LEG GTT
# ---------------------------------------------------------------------

def gtt_single_leg_place_order(
    breeze,
    *,
    exchange_code: str,
    stock_code: str,
    product: str,
    quantity: str,
    expiry_date: str = "",
    right: str = "",
    strike_price: str = "",
    index_or_stock: str,
    trade_date: str,
    action: str,
    limit_price: str,
    trigger_price: str,
):
    """
    Place a native ICICI Breeze single-leg GTT.

    Example:

        BUY 1 SBIN
        trigger = 800
        limit   = 799
    """

    payload = {
        "exchange_code": exchange_code,
        "stock_code": stock_code,
        "product": product,
        "quantity": str(quantity),
        "expiry_date": expiry_date,
        "right": right,
        "strike_price": str(strike_price),
        "gtt_type": "single",
        "index_or_stock": index_or_stock,
        "trade_date": trade_date,
        "order_details": [
            {
                "action": action.lower(),
                "limit_price": str(limit_price),
                "trigger_price": str(trigger_price),
            }
        ],
    }

    logger.info(
        "ICICI Breeze GTT single-leg payload: %s",
        payload,
    )

    response = breeze.gtt_single_leg_place_order(
        **payload
    )

    logger.info(
        "ICICI Breeze GTT single-leg response: %s",
        response,
    )

    success, error = _validate_gtt_response(
        response
    )

    if not success:
        return (
            None,
            response,
            error,
        )

    gtt_order_id = _extract_gtt_order_id(
        response
    )

    return (
        gtt_order_id,
        response,
        None,
    )


# ---------------------------------------------------------------------
# SINGLE LEG MODIFY
# ---------------------------------------------------------------------

def gtt_single_leg_modify_order(
    breeze,
    *,
    exchange_code: str,
    gtt_order_id: str,
    action: str,
    limit_price: str,
    trigger_price: str,
):
    payload = {
        "exchange_code": exchange_code,
        "gtt_order_id": str(gtt_order_id),
        "gtt_type": "single",
        "order_details": [
            {
                "action": action.lower(),
                "limit_price": str(limit_price),
                "trigger_price": str(trigger_price),
            }
        ],
    }

    logger.info(
        "ICICI Breeze GTT modify payload: %s",
        payload,
    )

    response = breeze.gtt_single_leg_modify_order(
        **payload
    )

    logger.info(
        "ICICI Breeze GTT modify response: %s",
        response,
    )

    success, error = _validate_gtt_response(
        response
    )

    return (
        success,
        response,
        error,
    )


# ---------------------------------------------------------------------
# SINGLE LEG CANCEL
# ---------------------------------------------------------------------

def gtt_single_leg_cancel_order(
    breeze,
    *,
    exchange_code: str,
    gtt_order_id: str,
):
    payload = {
        "exchange_code": exchange_code,
        "gtt_order_id": str(gtt_order_id),
    }

    logger.info(
        "ICICI Breeze GTT cancel payload: %s",
        payload,
    )

    response = breeze.gtt_single_leg_cancel_order(
        **payload
    )

    logger.info(
        "ICICI Breeze GTT cancel response: %s",
        response,
    )

    success, error = _validate_gtt_response(
        response
    )

    return (
        success,
        response,
        error,
    )


# ---------------------------------------------------------------------
# GTT ORDER BOOK
# ---------------------------------------------------------------------

def get_gtt_order_book(
    breeze,
    *,
    exchange_code: str,
    from_date: str,
    to_date: str,
):

    response = breeze.gtt_order_book(
        exchange_code=exchange_code,
        from_date=from_date,
        to_date=to_date,
    )

    logger.info(
        "ICICI Breeze GTT order book response: %s",
        response,
    )

    success, error = _validate_gtt_response(
        response
    )

    if not success:
        return (
            [],
            response,
            error,
        )

    data = response.get("Success")

    if not isinstance(data, list):
        data = []

    return (
        data,
        response,
        None,
    )

# example 

def place_gtt_order_api(data, auth):
    breeze = get_breeze_client(auth)

    response = breeze.gtt_single_leg_place_order(
        exchange_code=data["exchange_code"],
        stock_code=data["stock_code"],
        product=data.get("product", "cash"),
        quantity=str(data["quantity"]),
        expiry_date=data.get("expiry_date", ""),
        right=data.get("right", ""),
        strike_price=str(data.get("strike_price", "0")),
        gtt_type="single",
        index_or_stock=data.get(
            "index_or_stock",
            "stock",
        ),
        trade_date=data["trade_date"],
        order_details=[
            {
                "action": data["action"].lower(),
                "limit_price": str(data["limit_price"]),
                "trigger_price": str(data["trigger_price"]),
            }
        ],
    )

    logger.info(
        "ICICI Breeze GTT response: %s",
        response,
    )

    if not isinstance(response, dict):
        return (
            None,
            response,
            "Invalid Breeze response",
        )

    if response.get("Status") != 200:
        return (
            None,
            response,
            response.get("Error")
            or "GTT placement failed",
        )

    success = response.get("Success") or {}

    gtt_order_id = success.get(
        "gtt_order_id"
    )

    if not gtt_order_id:
        return (
            None,
            response,
            "GTT order ID missing",
        )

    return (
        gtt_order_id,
        response,
        None,
    )

def map_gtt_order_data(gtt_data):
    if not isinstance(gtt_data, dict):
        return []

    rows = gtt_data.get("Success")

    if not isinstance(rows, list):
        return []

    result = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        legs = row.get(
            "order_details"
        )

        if not isinstance(legs, list):
            legs = []

        for leg in legs:

            if not isinstance(leg, dict):
                continue

            status = _status(
                leg.get("status")
            )

            result.append(
                {
                    "orderid": str(
                        leg.get(
                            "gtt_order_id"
                        )
                        or ""
                    ),

                    "symbol": str(
                        row.get(
                            "stock_code"
                        )
                        or ""
                    ),

                    "exchange": _exchange(
                        row.get(
                            "exchange_code"
                        )
                    ),

                    "action": _action(
                        leg.get(
                            "action"
                        )
                    ),

                    "quantity": _int(
                        row.get(
                            "quantity"
                        )
                    ),

                    "price": _float(
                        leg.get(
                            "limit_price"
                        )
                    ),

                    "trigger_price": _float(
                        leg.get(
                            "trigger_price"
                        )
                    ),

                    "pricetype": "LIMIT",

                    "product": _product(
                        row.get(
                            "product_type"
                        )
                    ),

                    "status": status,

                    "order_datetime": str(
                        row.get(
                            "order_datetime"
                        )
                        or ""
                    ),

                    "gtt_type": str(
                        row.get(
                            "gtt_type"
                        )
                        or ""
                    ),

                    "gtt_leg_type": str(
                        leg.get(
                            "gtt_leg_type"
                        )
                        or ""
                    ),

                    "fresh_order_id": str(
                        row.get(
                            "fresh_order_id"
                        )
                        or ""
                    ),
                }
            )

    return result

# response = breeze.gtt_three_leg_place_order(
#     exchange_code="NSE",
#     stock_code="SAKSOF",
#     product="cash",
#     quantity="1",

#     expiry_date="",

#     right="others",

#     strike_price="0",

#     gtt_type="cover_oco",

#     fresh_order_action="buy",

#     fresh_order_price="135",

#     fresh_order_type="limit",

#     index_or_stock="stock",

#     trade_date="2026-10-05T06:00:00.000Z",

#     order_details=[
#         {
#             "gtt_leg_type": "target",
#             "action": "sell",
#             "limit_price": "145",
#             "trigger_price": "144.50",
#         },
#         {
#             "gtt_leg_type": "stoploss",
#             "action": "sell",
#             "limit_price": "125",
#             "trigger_price": "125.50",
#         },
#     ],
# )

# gtt_id, response, error = gtt_single_leg_place_order(
#     breeze,
#     exchange_code="NSE",
#     stock_code="SAKSOF",
#     product="cash",
#     quantity="1",
#     index_or_stock="stock",
#     trade_date="2026-10-05T06:00:00.000Z",
#     action="buy",
#     limit_price="135",
#     trigger_price="135",
# )
# {
#     "Success": {
#         "gtt_order_id": "2026100500001234",
#         "message": "Your GTT Order Request Placed Successfully"
#     },
#     "Status": 200,
#     "Error": None
# }

# {
#     "order_details": [
#         {
#             "gtt_leg_type": None,
#             "action": "Buy",
#             "trigger_price": 7.0,
#             "limit_price": 6.0,
#             "status": "Cancelled",
#             "gtt_order_id": "2025020500001234"
#         }
#     ],
#     "exchange_code": "NFO",
#     "product_type": "Options",
#     "stock_code": "NIFTY",
#     "quantity": 75,
#     "index_or_stock": "Index",
#     "gtt_type": "Single",
#     "fresh_order_id": None,
#     "order_datetime": "05-FEB-2025 11:19:32"
# }
