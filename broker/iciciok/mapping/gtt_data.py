"""
ICICI Direct Breeze GTT -> OpenAlgo mapping.
"""

from __future__ import annotations


def _string(value, default=""):
    if value is None:
        return default

    return str(value)


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


def _status(value):
    value = _string(value).upper().strip()

    mapping = {
        "REQUESTED": "open",
        "QUEUED": "open",
        "ORDERED": "open",
        "OPEN": "open",

        "EXECUTED": "complete",
        "COMPLETED": "complete",

        "CANCELLED": "cancelled",
        "CANCELED": "cancelled",

        "REJECTED": "rejected",

        "EXPIRED": "expired",
    }

    return mapping.get(
        value,
        value.lower(),
    )


def _action(value):
    value = _string(value).upper()

    if value in ("BUY", "B"):
        return "BUY"

    if value in ("SELL", "S"):
        return "SELL"

    return value


def map_gtt_book(data):
    """
    Convert Breeze gtt_order_book response into OpenAlgo GTT records.
    """

    if not isinstance(data, list):
        return []

    result = []

    for gtt in data:

        if not isinstance(gtt, dict):
            continue

        legs = gtt.get(
            "order_details"
        ) or []

        if not isinstance(legs, list):
            legs = []

        for leg in legs:

            if not isinstance(leg, dict):
                continue

            result.append(
                {
                    "trigger_id": _string(
                        leg.get(
                            "gtt_order_id"
                        )
                        or gtt.get(
                            "gtt_order_id"
                        )
                    ),

                    "symbol": _string(
                        gtt.get(
                            "stock_code"
                        )
                    ),

                    "exchange": _string(
                        gtt.get(
                            "exchange_code"
                        )
                    ).upper(),

                    "product": _string(
                        gtt.get(
                            "product_type"
                        )
                    ),

                    "quantity": _int(
                        gtt.get(
                            "quantity"
                        )
                    ),

                    "action": _action(
                        leg.get(
                            "action"
                        )
                    ),

                    "trigger_price": _float(
                        leg.get(
                            "trigger_price"
                        )
                    ),

                    "price": _float(
                        leg.get(
                            "limit_price"
                        )
                    ),

                    "status": _status(
                        leg.get(
                            "status"
                        )
                    ),

                    "gtt_type": _string(
                        gtt.get(
                            "gtt_type"
                        )
                    ),

                    "leg_type": _string(
                        leg.get(
                            "gtt_leg_type"
                        )
                    ),

                    "order_datetime": _string(
                        gtt.get(
                            "order_datetime"
                        )
                    ),

                    "fresh_order_id": _string(
                        gtt.get(
                            "fresh_order_id"
                        )
                    ),

                    "expiry_date": _string(
                        gtt.get(
                            "expiry_date"
                        )
                    ),

                    "right": _string(
                        gtt.get(
                            "right"
                        )
                    ),

                    "strike_price": _float(
                        gtt.get(
                            "strike_price"
                        )
                    ),
                }
            )

    return result


def transform_place_gtt(data):
    """
    Compatibility helper if the generic GTT service expects
    a broker mapping function.

    The actual Breeze payload is constructed inside gtt_api.py.
    """

    return data


def transform_modify_gtt(data):
    return data
