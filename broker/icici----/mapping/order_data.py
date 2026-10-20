"""
ICICI Direct Breeze -> OpenAlgo response mappings.

This module exposes the function names expected by OpenAlgo services.

OpenAlgo service calls:

    map_order_data(order_data=...)
    map_trade_data(trade_data=...)
    map_position_data(position_data=...)

Do not rename the keyword arguments.
"""

from typing import Any


# ---------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------

def _success_rows(data):
    """
    Extract Breeze Success rows.

    Breeze responses commonly look like:

        {
            "Success": [...],
            "Status": 200,
            "Error": None
        }

    Empty responses can look like:

        {
            "Success": None,
            "Status": 200,
            "Error": "No Positions available."
        }
    """

    if data is None:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        success = data.get("Success")

        if success is None:
            return []

        if isinstance(success, list):
            return success

        if isinstance(success, dict):
            return [success]

    return []


def _get(data: dict, *keys, default=None):
    for key in keys:
        if key in data:
            value = data[key]

            if value is not None and value != "":
                return value

    return default


def _string(value, default=""):
    if value is None:
        return default

    return str(value)


def _int(value, default=0):
    try:
        if value is None or value == "":
            return default

        return int(float(value))

    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        if value is None or value == "":
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def _exchange(value):
    value = _string(value).upper()

    mapping = {
        "NSE": "NSE",
        "BSE": "BSE",
        "NFO": "NFO",
        "BFO": "BFO",
        "MCX": "MCX",
        "CDS": "CDS",
        "BCD": "BCD",
    }

    return mapping.get(value, value)


def _action(value):
    value = _string(value).upper()

    if value in ("B", "BUY"):
        return "BUY"

    if value in ("S", "SELL"):
        return "SELL"

    return value


def _product(value):
    value = _string(value).upper()

    mapping = {
        "CASH": "CNC",
        "DELIVERY": "CNC",
        "CNC": "CNC",

        "MARGIN": "MIS",
        "MIS": "MIS",

        "FUTURE": "NRML",
        "FUTURES": "NRML",
        "OPTION": "NRML",
        "OPTIONS": "NRML",
        "NRML": "NRML",
    }

    return mapping.get(value, value or "MIS")


def _price_type(value):
    value = _string(value).upper()

    mapping = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "STOPLOSS": "SL",
        "STOP LOSS": "SL",
        "STOPLIMIT": "SL",
        "SL": "SL",
        "SL-M": "SL-M",
        "STOPLOSS_MARKET": "SL-M",
    }

    return mapping.get(value, value)

def _status(value):
    value = _string(value).upper().strip()

    mapping = {
        "ORDERED": "OPEN",
        "REQUESTED": "OPEN",
        "QUEUED": "OPEN",
        "ACCEPTED": "OPEN",
        "OPEN": "OPEN",

        "EXECUTED": "COMPLETE",
        "COMPLETED": "COMPLETE",
        "TRADED": "COMPLETE",
        "COMPLETE": "COMPLETE",
        "FILLED": "complete",

        "PARTIALLY EXECUTED": "PARTIALLY FILLED",
        "PARTIALLY EXECUTED AND EXPIRED": "PARTIALLY FILLED",
        "PARTIALLY EXECUTED AND CANCELLED": "PARTIALLY FILLED",
        "PARTIALLY FILLED": "PARTIALLY FILLED",

        "CANCELED": "CANCELLED",
        "CANCELLED": "CANCELLED",

        "REJECTED": "REJECTED",
        "EXPIRED": "EXPIRED",
    }

    return mapping.get(
        value,
        value,
    )

# def _status(value):
#     value = _string(value).strip().upper()

#     mapping = {
#         # Open / pending
#         "REQUESTED": "open",
#         "ORDERED": "open",
#         "QUEUED": "open",
#         "OPEN": "open",
#         "PENDING": "open",
#         "ACCEPTED": "open",
#         "CONFIRMED": "open",

#         # Completed
#         "EXECUTED": "complete",
#         "COMPLETED": "complete",
#         "TRADED": "complete",
#         "COMPLETE": "complete",
#         "FILLED": "complete",

#         # Partial
#         "PARTIALLY EXECUTED": "open",
#         "PARTIALLY FILLED": "open",

#         # Cancelled
#         "CANCELLED": "cancelled",
#         "CANCELED": "cancelled",

#         # Rejected
#         "REJECTED": "rejected",
#         "REJECT": "rejected",

#         # Expired
#         "EXPIRED": "expired",
#     }

#     return mapping.get(
#         value,
#         value.lower(),
#     )


def _order_status(order):
    status = _status(
        _get(
            order,
            "status",
            "order_status",
        )
    )

    pending_quantity = _int(
        _get(
            order,
            "pending_quantity",
        )
    )

    cancelled_quantity = _int(
        _get(
            order,
            "cancelled_quantity",
        )
    )

    # ICICI can report cancellation quantities separately.
    # Never expose a fully cancelled order as open.
    if cancelled_quantity > 0 and pending_quantity == 0:
        return "cancelled"

    return status


# def _status(value):
#     value = _string(value).upper().strip()

#     mapping = {
#         "ORDERED": "OPEN",
#         "REQUESTED": "OPEN",
#         "QUEUED": "OPEN",

#         "OPEN": "OPEN",

#         "EXECUTED": "COMPLETE",
#         "COMPLETED": "COMPLETE",
#         "TRADED": "COMPLETE",
#         "COMPLETE": "COMPLETE",

#         "PARTIALLY EXECUTED": "PARTIALLY FILLED",
#         "PARTIALLY EXECUTED AND EXPIRED": "PARTIALLY FILLED",
#         "PARTIALLY EXECUTED AND CANCELLED": "PARTIALLY FILLED",
#         "PARTIALLY FILLED": "PARTIALLY FILLED",

#         "CANCELED": "CANCELLED",
#         "CANCELLED": "CANCELLED",

#         "REJECTED": "REJECTED",
#         "EXPIRED": "EXPIRED",
#     }

#     return mapping.get(value, value)


# =====================================================================
# ORDER BOOK
# =====================================================================
def map_order_data(order_data):
    rows = _success_rows(order_data)

    result = []

    for order in rows:

        if not isinstance(order, dict):
            continue

        result.append(
            {
                "orderid": _string(
                    _get(
                        order,
                        "order_id",
                        "orderid",
                    )
                ),

                "exchangeorderid": _string(
                    _get(
                        order,
                        "exchange_order_id",
                        "exchangeorderid",
                    )
                ),

                "symbol": _string(
                    _get(
                        order,
                        "stock_code",
                        "tradingsymbol",
                        "symbol",
                    )
                ),

                "exchange": _exchange(
                    _get(
                        order,
                        "exchange_code",
                        "exchange",
                    )
                ),

                "action": _action(
                    _get(
                        order,
                        "action",
                        "transaction_type",
                    )
                ),

                "quantity": _int(
                    _get(
                        order,
                        "quantity",
                        "order_quantity",
                    )
                ),

                "pending_quantity": _int(
                    _get(
                        order,
                        "pending_quantity",
                    )
                ),

                "cancelled_quantity": _int(
                    _get(
                        order,
                        "cancelled_quantity",
                    )
                ),

                "price": _float(
                    _get(
                        order,
                        "price",
                        "limit_price",
                    )
                ),

                "average_price": _float(
                    _get(
                        order,
                        "average_price",
                        "averagePrice",
                    )
                ),

                "trigger_price": _float(
                    _get(
                        order,
                        "stoploss",
                        "trigger_price",
                    )
                ),

                "pricetype": _price_type(
                    _get(
                        order,
                        "order_type",
                        "pricetype",
                        "orderType",
                    )
                ),

                "product": _product(
                    _get(
                        order,
                        "product_type",
                        "product",
                    )
                ),

                "status": _order_status(
                    order
                ),

                "validity": _string(
                    _get(
                        order,
                        "validity",
                    ),
                    "DAY",
                ),

                "disclosed_quantity": _int(
                    _get(
                        order,
                        "disclosed_quantity",
                    )
                ),

                "order_datetime": _string(
                    _get(
                        order,
                        "order_datetime",
                        "order_time",
                    )
                ),

                "rejection_reason": _string(
                    _get(
                        order,
                        "message",
                        "rejection_reason",
                    )
                ),
            }
        )

    return result

# def map_order_data(order_data):
#     """
#     REQUIRED OpenAlgo service hook.

#     OpenAlgo calls:

#         map_order_data(order_data=order_data)

#     Returns a list of normalized OpenAlgo order records.
#     """

#     rows = _success_rows(order_data)

#     result = []

#     for order in rows:
#         if not isinstance(order, dict):
#             continue

#         result.append(
#             {
#                 "orderid": _string(
#                     _get(
#                         order,
#                         "order_id",
#                         "orderid",
#                     )
#                 ),

#                 "exchangeorderid": _string(
#                     _get(
#                         order,
#                         "exchange_order_id",
#                         "exchangeorderid",
#                     )
#                 ),

#                 "symbol": _string(
#                     _get(
#                         order,
#                         "stock_code",
#                         "tradingsymbol",
#                         "symbol",
#                     )
#                 ),

#                 "exchange": _exchange(
#                     _get(
#                         order,
#                         "exchange_code",
#                         "exchange",
#                     )
#                 ),

#                 "action": _action(
#                     _get(
#                         order,
#                         "action",
#                         "transaction_type",
#                     )
#                 ),

#                 "quantity": _int(
#                     _get(
#                         order,
#                         "quantity",
#                         "order_quantity",
#                     )
#                 ),

#                 "pending_quantity": _int(
#                     _get(
#                         order,
#                         "pending_quantity",
#                     )
#                 ),

#                 "cancelled_quantity": _int(
#                     _get(
#                         order,
#                         "cancelled_quantity",
#                     )
#                 ),

#                 "price": _float(
#                     _get(
#                         order,
#                         "price",
#                         "limit_price",
#                     )
#                 ),

#                 "average_price": _float(
#                     _get(
#                         order,
#                         "average_price",
#                         "averagePrice",
#                     )
#                 ),

#                 "trigger_price": _float(
#                     _get(
#                         order,
#                         "stoploss",
#                         "trigger_price",
#                     )
#                 ),

#                 "pricetype": _price_type(
#                     _get(
#                         order,
#                         "order_type",
#                         "pricetype",
#                         "orderType",
#                     )
#                 ),

#                 "product": _product(
#                     _get(
#                         order,
#                         "product_type",
#                         "product",
#                     )
#                 ),

#                 "status": _status(
#                     _get(
#                         order,
#                         "status",
#                     )
#                 ),

#                 "validity": _string(
#                     _get(
#                         order,
#                         "validity",
#                     ),
#                     "DAY",
#                 ),

#                 "disclosed_quantity": _int(
#                     _get(
#                         order,
#                         "disclosed_quantity",
#                     )
#                 ),

#                 "order_datetime": _string(
#                     _get(
#                         order,
#                         "order_datetime",
#                         "order_time",
#                     )
#                 ),

#                 "rejection_reason": _string(
#                     _get(
#                         order,
#                         "message",
#                         "rejection_reason",
#                     )
#                 ),
#             }
#         )

#     return result


def transform_order_data(order_data):
    """
    Compatibility wrapper.

    Some OpenAlgo versions or broker-specific code may call the
    transformation function separately.
    """

    return map_order_data(order_data)


def transform_orderbook_data(order_data):
    return map_order_data(order_data)


# =====================================================================
# ORDER STATISTICS
# =====================================================================

# def calculate_order_statistics(order_data):
#     """
#     Calculate OpenAlgo orderbook statistics.
#     """

#     orders = map_order_data(order_data)

#     total = len(orders)

#     complete = 0
#     open_orders = 0
#     pending = 0
#     partial = 0
#     rejected = 0
#     cancelled = 0
#     expired = 0

#     for order in orders:

#         status = _string(
#             order.get("status")
#         ).upper()

#         if status == "COMPLETE":
#             complete += 1

#         elif status == "OPEN":
#             open_orders += 1
#             pending += 1

#         elif status == "PARTIALLY FILLED":
#             partial += 1
#             pending += 1

#         elif status == "REJECTED":
#             rejected += 1

#         elif status == "CANCELLED":
#             cancelled += 1

#         elif status == "EXPIRED":
#             expired += 1

#     return {
#         "total": total,
#         "complete": complete,
#         "open": open_orders,
#         "pending": pending,
#         "partial": partial,
#         "rejected": rejected,
#         "cancelled": cancelled,
#         "expired": expired,
#     }

# def map_order_data(order_data):
#     return order_data or []


# def transform_order_data(order_data):
#     return order_data or []

def calculate_order_statistics(order_data):
    total_buy_orders = 0
    total_sell_orders = 0
    total_completed_orders = 0
    total_open_orders = 0
    total_rejected_orders = 0

    for order in order_data or []:

        action = str(
            order.get("action")
            or order.get("transaction_type")
            or ""
        ).upper()

        if action in ("BUY", "B"):
            total_buy_orders += 1

        elif action in ("SELL", "S"):
            total_sell_orders += 1

        status = str(
            order.get("status")
            or order.get("order_status")
            or ""
        ).lower()

        if status == "complete":
            total_completed_orders += 1

        elif status in ("open", "trigger pending"):
            total_open_orders += 1

        elif status == "rejected":
            total_rejected_orders += 1

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


# def calculate_order_statistics(order_data):
#     order_data = order_data or []

#     total_buy_orders = 0
#     total_sell_orders = 0
#     total_completed_orders = 0
#     total_open_orders = 0
#     total_rejected_orders = 0

#     for order in order_data:
#         action = str(
#             order.get("action")
#             or order.get("transaction_type")
#             or ""
#         ).upper()

#         if action in ("BUY", "B"):
#             total_buy_orders += 1

#         elif action in ("SELL", "S"):
#             total_sell_orders += 1

#         status = str(
#             order.get("order_status")
#             or order.get("status")
#             or ""
#         ).lower()

#         if status in ("complete", "completed", "filled"):
#             total_completed_orders += 1

#         elif status in ("open", "pending", "trigger pending"):
#             total_open_orders += 1

#         elif status in ("rejected", "reject"):
#             total_rejected_orders += 1

#     return {
#         "total_buy_orders": total_buy_orders,
#         "total_sell_orders": total_sell_orders,
#         "total_completed_orders": total_completed_orders,
#         "total_open_orders": total_open_orders,
#         "total_rejected_orders": total_rejected_orders,
#     }


# def map_trade_data(trade_data):
#     return trade_data or []


# def transform_tradebook_data(tradebook_data):
#     return tradebook_data or []


# def map_position_data(position_data):
#     return position_data or []


# def transform_positions_data(positions_data):
#     return positions_data or []

# =====================================================================
# TRADE BOOK
# =====================================================================

def map_trade_data(trade_data):
    """
    REQUIRED OpenAlgo service hook.

    OpenAlgo calls:

        map_trade_data(trade_data=trade_data)
    """

    rows = _success_rows(trade_data)

    result = []

    for trade in rows:

        if not isinstance(trade, dict):
            continue

        result.append(
            {
                "tradeid": _string(
                    _get(
                        trade,
                        "trade_id",
                        "tradeid",
                        "order_id",
                    )
                ),

                "orderid": _string(
                    _get(
                        trade,
                        "order_id",
                        "orderid",
                    )
                ),

                "symbol": _string(
                    _get(
                        trade,
                        "stock_code",
                        "tradingsymbol",
                        "symbol",
                    )
                ),

                "exchange": _exchange(
                    _get(
                        trade,
                        "exchange_code",
                        "exchange",
                    )
                ),

                "action": _action(
                    _get(
                        trade,
                        "action",
                        "transaction_type",
                    )
                ),

                "quantity": _int(
                    _get(
                        trade,
                        "quantity",
                    )
                ),

                "price": _float(
                    _get(
                        trade,
                        "average_cost",
                        "average_price",
                        "price",
                    )
                ),

                "average_price": _float(
                    _get(
                        trade,
                        "average_cost",
                        "average_price",
                    )
                ),

                "product": _product(
                    _get(
                        trade,
                        "product_type",
                        "product",
                    )
                ),

                "trade_date": _string(
                    _get(
                        trade,
                        "trade_date",
                        "trade_datetime",
                    )
                ),

                "brokerage": _float(
                    _get(
                        trade,
                        "brokerage_amount",
                        "brokerage",
                    )
                ),

                "segment": _string(
                    _get(
                        trade,
                        "segment",
                    )
                ),
            }
        )

    return result


def transform_tradebook_data(trade_data):
    return map_trade_data(trade_data)


# =====================================================================
# POSITION BOOK
# =====================================================================

def map_position_data(position_data):
    """
    REQUIRED OpenAlgo service hook.

    OpenAlgo calls:

        map_position_data(position_data=position_data)
    """

    rows = _success_rows(position_data)

    result = []

    for position in rows:

        if not isinstance(position, dict):
            continue

        quantity = _int(
            _get(
                position,
                "quantity",
            )
        )

        # Ignore zero quantity rows.
        if quantity == 0:
            continue

        result.append(
            {
                "symbol": _string(
                    _get(
                        position,
                        "stock_code",
                        "tradingsymbol",
                        "symbol",
                    )
                ),

                "exchange": _exchange(
                    _get(
                        position,
                        "exchange_code",
                        "exchange",
                    )
                ),

                "product": _product(
                    _get(
                        position,
                        "product_type",
                        "product",
                    )
                ),

                "quantity": quantity,

                "average_price": _float(
                    _get(
                        position,
                        "average_price",
                        "price",
                    )
                ),

                "ltp": _float(
                    _get(
                        position,
                        "ltp",
                        "last_price",
                    )
                ),

                "pnl": _float(
                    _get(
                        position,
                        "pnl",
                    )
                ),

                "action": _action(
                    _get(
                        position,
                        "action",
                    )
                ),

                "expiry": _string(
                    _get(
                        position,
                        "expiry_date",
                        "expiry",
                    )
                ),

                "strike": _float(
                    _get(
                        position,
                        "strike_price",
                        "strike",
                    )
                ),

                "option_type": _string(
                    _get(
                        position,
                        "right",
                        "option_type",
                    )
                ),

                "underlying": _string(
                    _get(
                        position,
                        "underlying",
                    )
                ),
            }
        )

    return result


def transform_positions_data(position_data):
    return map_position_data(position_data)


# =====================================================================
# HOLDINGS
# =====================================================================

def map_holding_data(holding_data):

    if not isinstance(holding_data, dict):
        return {}

    return {
        "symbol": _string(
            _get(
                holding_data,
                "stock_code",
                "tradingsymbol",
                "symbol",
            )
        ),

        "exchange": _exchange(
            _get(
                holding_data,
                "exchange_code",
                "exchange",
            )
        ),

        "quantity": _int(
            _get(
                holding_data,
                "quantity",
                "holdings_quantity",
            )
        ),

        "average_price": _float(
            _get(
                holding_data,
                "average_price",
                "average_cost",
            )
        ),

        "ltp": _float(
            _get(
                holding_data,
                "ltp",
                "last_price",
            )
        ),

        "pnl": _float(
            _get(
                holding_data,
                "pnl",
            )
        ),
    }


def transform_holdings_data(holding_data):

    rows = _success_rows(holding_data)

    return [
        map_holding_data(row)
        for row in rows
        if isinstance(row, dict)
    ]
