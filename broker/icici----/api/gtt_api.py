"""
ICICI Direct Breeze GTT API integration for OpenAlgo.

Breeze supports:
    - Single-leg GTT
    - Three-leg OCO GTT
    - Modify single-leg GTT
    - Modify three-leg GTT
    - Cancel single-leg GTT
    - Cancel three-leg GTT
    - GTT order book

The authenticated BreezeConnect instance is created by
broker.icici.api.breeze_client.get_breeze_client().
"""

from __future__ import annotations

from typing import Any
from datetime import datetime, timedelta

from broker.icici.api.breeze_client import get_breeze_client
from utils.logging import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _success_dict(response: Any) -> dict:
    """
    Extract Breeze Success dictionary.

    Example:

        {
            "Success": {
                "gtt_order_id": "123"
            },
            "Status": 200,
            "Error": None
        }
    """

    if not isinstance(response, dict):
        return {}

    success = response.get("Success")

    return success if isinstance(success, dict) else {}


def _extract_gtt_id(response: Any) -> str | None:
    success = _success_dict(response)

    gtt_id = (
        success.get("gtt_order_id")
        or success.get("gttOrderId")
        or success.get("order_id")
        or success.get("orderId")
    )

    if not gtt_id:
        return None

    return str(gtt_id)


def _error_message(response: Any) -> str:
    if not isinstance(response, dict):
        return "Invalid Breeze response"

    error = response.get("Error")

    if error:
        return str(error)

    success = response.get("Success")

    if isinstance(success, dict):
        return str(
            success.get("message")
            or success.get("Message")
            or "Unknown Breeze error"
        )

    return "Unknown Breeze error"


def _status(response: Any) -> int:
    if not isinstance(response, dict):
        return 500

    try:
        return int(response.get("Status") or 500)
    except (TypeError, ValueError):
        return 500


def _require(value: Any, name: str):
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} is required")


def _normalise_action(value: Any) -> str:
    value = str(value or "").lower()

    if value in ("buy", "b"):
        return "buy"

    if value in ("sell", "s"):
        return "sell"

    raise ValueError(
        f"Invalid GTT action: {value}. Expected buy or sell."
    )


def _normalise_exchange(value: Any) -> str:
    value = str(value or "").upper()

    if value not in {
        "NSE",
        "BSE",
        "NFO",
        "BFO",
    }:
        raise ValueError(
            f"Unsupported ICICI GTT exchange: {value}"
        )

    return value


# ---------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------

def _single_leg_payload(data: dict) -> dict:
    """
    Build Breeze single-leg GTT payload.
    """

    exchange_code = _normalise_exchange(
        data.get("exchange")
        or data.get("exchange_code")
    )

    stock_code = data.get(
        "stock_code"
        or data.get("symbol")
        or data.get("tradingsymbol")
    )

    _require(
        stock_code,
        "stock_code",
    )

    quantity = data.get("quantity")

    _require(
        quantity,
        "quantity",
    )

    order_details = [
        {
            "action": _normalise_action(
                data.get("action")
            ),

            "limit_price": str(
                data.get("price")
                or data.get("limit_price")
                or "0"
            ),

            "trigger_price": str(
                data.get("trigger_price")
                or data.get("trigger")
                or data.get("triggerprice")
                or "0"
            ),
        }
    ]

    payload = {
        "exchange_code": exchange_code,
        "stock_code": str(stock_code),
        "product": str(
            data.get("product")
            or "cash"
        ).lower(),
        "quantity": str(quantity),
        "gtt_type": "single",
        "index_or_stock": str(
            data.get("index_or_stock")
            or "stock"
        ).lower(),
        "trade_date": data.get(
            "trade_date"
        ),
        "order_details": order_details,
    }

    # Optional F&O parameters.
    if data.get("expiry_date"):
        payload["expiry_date"] = data["expiry_date"]

    if data.get("right"):
        payload["right"] = data["right"]

    if data.get("strike_price") is not None:
        payload["strike_price"] = str(
            data["strike_price"]
        )

    # Remove None values because Breeze can reject empty parameters.
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def _oco_payload(data: dict) -> dict:
    """
    Build Breeze three-leg Cover OCO GTT payload.

    Expected:

        fresh_order_action
        fresh_order_price
        fresh_order_type
        target
        stoploss
    """

    exchange_code = _normalise_exchange(
        data.get("exchange")
        or data.get("exchange_code")
    )

    stock_code = data.get(
        "stock_code"
        or data.get("symbol")
        or data.get("tradingsymbol")
    )

    _require(
        stock_code,
        "stock_code",
    )

    quantity = data.get("quantity")

    _require(
        quantity,
        "quantity",
    )

    fresh_action = _normalise_action(
        data.get("fresh_order_action")
        or data.get("action")
    )

    target_action = _normalise_action(
        data.get("target_action")
        or (
            "sell"
            if fresh_action == "buy"
            else "buy"
        )
    )

    stoploss_action = _normalise_action(
        data.get("stoploss_action")
        or (
            "sell"
            if fresh_action == "buy"
            else "buy"
        )
    )

    target = {
        "gtt_leg_type": "target",
        "action": target_action,
        "limit_price": str(
            data.get("target_price")
            or data.get("target_limit_price")
        ),
        "trigger_price": str(
            data.get("target_trigger_price")
            or data.get("target_trigger")
        ),
    }

    stoploss = {
        "gtt_leg_type": "stoploss",
        "action": stoploss_action,
        "limit_price": str(
            data.get("stoploss_price")
            or data.get("stoploss_limit_price")
        ),
        "trigger_price": str(
            data.get("stoploss_trigger_price")
            or data.get("stoploss_trigger")
        ),
    }

    payload = {
        "exchange_code": exchange_code,
        "stock_code": str(stock_code),
        "product": str(
            data.get("product")
            or "cash"
        ).lower(),
        "quantity": str(quantity),
        "gtt_type": "cover_oco",
        "fresh_order_action": fresh_action,
        "fresh_order_price": str(
            data.get("fresh_order_price")
            or data.get("entry_price")
            or "0"
        ),
        "fresh_order_type": str(
            data.get("fresh_order_type")
            or "limit"
        ).lower(),
        "index_or_stock": str(
            data.get("index_or_stock")
            or "stock"
        ).lower(),
        "trade_date": data.get(
            "trade_date"
        ),
        "order_details": [
            target,
            stoploss,
        ],
    }

    if data.get("expiry_date"):
        payload["expiry_date"] = data["expiry_date"]

    if data.get("right"):
        payload["right"] = data["right"]

    if data.get("strike_price") is not None:
        payload["strike_price"] = str(
            data["strike_price"]
        )

    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


# ---------------------------------------------------------------------
# PLACE
# ---------------------------------------------------------------------

def place_gtt_order(
    data: dict,
    auth,
):
    """
    Place an ICICI Breeze GTT.

    Returns:

        (response, response_data, trigger_id)

    This mirrors the Dhan GTT API interface used by OpenAlgo.
    """

    breeze = get_breeze_client(
        auth
    )

    gtt_type = str(
        data.get("trigger_type")
        or data.get("gtt_type")
        or "SINGLE"
    ).upper()

    try:

        if gtt_type in (
            "OCO",
            "COVER_OCO",
            "THREE_LEG",
        ):

            payload = _oco_payload(
                data
            )

            logger.info(
                "ICICI Breeze GTT OCO payload: %s",
                payload,
            )

            response = (
                breeze.gtt_three_leg_place_order(
                    **payload
                )
            )

        else:

            payload = _single_leg_payload(
                data
            )

            logger.info(
                "ICICI Breeze GTT single payload: %s",
                payload,
            )

            response = (
                breeze.gtt_single_leg_place_order(
                    **payload
                )
            )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze GTT placement failed"
        )

        return (
            None,
            {
                "status": "error",
                "message": str(exc),
            },
            None,
        )

    logger.info(
        "ICICI Breeze GTT raw response: %s",
        response,
    )

    trigger_id = _extract_gtt_id(
        response
    )

    return (
        response,
        response,
        trigger_id,
    )


# ---------------------------------------------------------------------
# MODIFY
# ---------------------------------------------------------------------

def modify_gtt_order(
    data: dict,
    auth,
):
    """
    Modify an ICICI Breeze GTT.

    Returns:

        (response_dict, status_code)
    """

    breeze = get_breeze_client(
        auth
    )

    trigger_id = data.get(
        "trigger_id"
    )

    if not trigger_id:
        return (
            {
                "status": "error",
                "message": "trigger_id is required",
            },
            400,
        )

    gtt_type = str(
        data.get("trigger_type")
        or data.get("gtt_type")
        or "SINGLE"
    ).upper()

    try:

        if gtt_type in (
            "OCO",
            "COVER_OCO",
            "THREE_LEG",
        ):

            exchange_code = _normalise_exchange(
                data.get("exchange")
                or data.get("exchange_code")
            )

            order_details = [
                {
                    "gtt_leg_type": "target",
                    "action": _normalise_action(
                        data.get("target_action")
                        or "sell"
                    ),
                    "limit_price": str(
                        data.get("target_price")
                        or data.get("target_limit_price")
                    ),
                    "trigger_price": str(
                        data.get("target_trigger_price")
                        or data.get("target_trigger")
                    ),
                },
                {
                    "gtt_leg_type": "stoploss",
                    "action": _normalise_action(
                        data.get("stoploss_action")
                        or "sell"
                    ),
                    "limit_price": str(
                        data.get("stoploss_price")
                        or data.get("stoploss_limit_price")
                    ),
                    "trigger_price": str(
                        data.get("stoploss_trigger_price")
                        or data.get("stoploss_trigger")
                    ),
                },
            ]

            payload = {
                "exchange_code": exchange_code,
                "gtt_order_id": str(
                    trigger_id
                ),
                "gtt_type": "oco",
                "order_details": order_details,
            }

            logger.info(
                "ICICI Breeze GTT OCO modify payload: %s",
                payload,
            )

            response = (
                breeze.gtt_three_leg_modify_order(
                    **payload
                )
            )

        else:

            payload = {
                "exchange_code": _normalise_exchange(
                    data.get("exchange")
                    or data.get("exchange_code")
                ),
                "gtt_order_id": str(
                    trigger_id
                ),
                "gtt_type": "single",
                "order_details": [
                    {
                        "action": _normalise_action(
                            data.get("action")
                        ),
                        "limit_price": str(
                            data.get("price")
                            or data.get("limit_price")
                        ),
                        "trigger_price": str(
                            data.get("trigger_price")
                            or data.get("trigger")
                        ),
                    }
                ],
            }

            logger.info(
                "ICICI Breeze GTT single modify payload: %s",
                payload,
            )

            response = (
                breeze.gtt_single_leg_modify_order(
                    **payload
                )
            )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze GTT modification failed"
        )

        return (
            {
                "status": "error",
                "message": str(exc),
            },
            500,
        )

    logger.info(
        "ICICI Breeze GTT modify raw response: %s",
        response,
    )

    status = _status(
        response
    )

    trigger_id = _extract_gtt_id(
        response
    ) or str(trigger_id)

    if status == 200:
        return (
            {
                "status": "success",
                "trigger_id": trigger_id,
            },
            200,
        )

    return (
        {
            "status": "error",
            "message": _error_message(
                response
            ),
        },
        status,
    )


# ---------------------------------------------------------------------
# CANCEL
# ---------------------------------------------------------------------

# def cancel_gtt_order(
#     trigger_id,
#     auth,
#     exchange_code,
#     gtt_type="single",
# ):
#     if not trigger_id:
#         return (
#             {
#                 "status": "error",
#                 "message": "trigger_id is required",
#             },
#             400,
#         )

#     if not exchange_code:
#         return (
#             {
#                 "status": "error",
#                 "message": "exchange_code is required",
#             },
#             400,
#         )

#     breeze = get_breeze_client(auth)

#     try:

#         exchange_code = str(
#             exchange_code
#         ).upper()

#         if str(gtt_type).upper() in (
#             "OCO",
#             "COVER_OCO",
#             "THREE_LEG",
#         ):

#             response = (
#                 breeze.gtt_three_leg_cancel_order(
#                     exchange_code=exchange_code,
#                     gtt_order_id=str(trigger_id),
#                 )
#             )

#         else:

#             response = (
#                 breeze.gtt_single_leg_cancel_order(
#                     exchange_code=exchange_code,
#                     gtt_order_id=str(trigger_id),
#                 )
#             )

#     except Exception as exc:

#         logger.exception(
#             "ICICI Breeze GTT cancellation failed"
#         )

#         return (
#             {
#                 "status": "error",
#                 "message": str(exc),
#             },
#             500,
#         )

#     logger.info(
#         "ICICI Breeze GTT cancel raw response: %s",
#         response,
#     )

#     status = _status(response)

#     if status == 200:

#         return (
#             {
#                 "status": "success",
#                 "trigger_id": str(trigger_id),
#             },
#             200,
#         )

#     return (
#         {
#             "status": "error",
#             "message": _error_message(response),
#         },
#         status,
#     )


def cancel_gtt_order(
    trigger_id,
    auth,
):
    """
    Cancel an ICICI Breeze GTT.

    Returns:

        (response_dict, status_code)
    """

    if not trigger_id:
        return (
            {
                "status": "error",
                "message": "trigger_id is required",
            },
            400,
        )

    breeze = get_breeze_client(
        auth
    )

    # Breeze cancellation requires exchange_code.
    # OpenAlgo may provide it directly or as part of the data object
    # depending on the service version.
    #
    # For the common service interface, default to NSE only if the
    # service has already resolved the exchange. Do NOT silently guess
    # in production; see note below.
    exchange_code = None

    try:

        response = (
            breeze.gtt_single_leg_cancel_order(
                exchange_code=exchange_code,
                gtt_order_id=str(
                    trigger_id
                ),
            )
        )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze GTT cancellation failed"
        )

        return (
            {
                "status": "error",
                "message": str(exc),
            },
            500,
        )

    logger.info(
        "ICICI Breeze GTT cancel raw response: %s",
        response,
    )

    status = _status(
        response
    )

    if status == 200:

        return (
            {
                "status": "success",
                "trigger_id": str(
                    trigger_id
                ),
            },
            200,
        )

    return (
        {
            "status": "error",
            "message": _error_message(
                response
            ),
        },
        status,
    )


# ---------------------------------------------------------------------
# ORDER BOOK
# ---------------------------------------------------------------------

def get_gtt_book(
    auth,
    include_history=False,
    exchange_code="NFO",
    from_date=None,
    to_date=None,
):
    """
    Fetch ICICI Breeze GTT order book.

    Breeze requires:

        exchange_code
        from_date
        to_date

    Example date:

        2025-02-05T06:00:00.00Z
    """

    breeze = get_breeze_client(
        auth
    )

    now = datetime.now()

    if to_date is None:
        to_date = (
            now + timedelta(days=4)
        ).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        # to_date = now.strftime(
        #     "%Y-%m-%dT%H:%M:%S.000Z"
        # )

    if from_date is None:
        from_date = (
            now - timedelta(days=5)
        ).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
    if not exchange_code:
        return (
            {
                "status": "error",
                "message": "exchange_code is required",
            },
            400,
        )

    if not from_date:
        return (
            {
                "status": "error",
                "message": "from_date is required",
            },
            400,
        )

    if not to_date:
        return (
            {
                "status": "error",
                "message": "to_date is required",
            },
            400,
        )

    try:

        response = breeze.gtt_order_book(
            exchange_code=str(
                exchange_code
            ).upper(),

            from_date=str(
                from_date
            ),

            to_date=str(
                to_date
            ),
        )

    except Exception as exc:

        logger.exception(
            "ICICI Breeze GTT order book failed"
        )

        return (
            {
                "status": "error",
                "message": str(exc),
            },
            500,
        )

    logger.info(
        "ICICI Breeze GTT order book raw response: %s",
        response,
    )

    status = _status(
        response
    )

    if status != 200:

        return (
            {
                "status": "error",
                "message": _error_message(
                    response
                ),
            },
            status,
        )

    return (
        {
            "status": "success",
            "data": response.get(
                "Success",
                [],
            ),
        },
        200,
    )
