# broker/icici/api/gtt_api.py
# GTT (Good Till Triggered) for the ICICI Direct Breeze API.
#
# Breeze exposes GTT through a single "gttorder" endpoint driven by the HTTP
# verb (wire reference: breeze-connect SDK 1.0.69):
#
#   POST   /gttorder   place   (gtt_type "single" | "oco"; body carries the
#                               instrument quintuple + order_details legs)
#   PUT    /gttorder   modify  ({exchange_code, gtt_order_id, gtt_type,
#                               order_details})
#   DELETE /gttorder   cancel  ({exchange_code, gtt_order_id})
#   GET    /gttorder   book    ({exchange_code, from_date, to_date})
#
# Like the order endpoints, cancel and modify need the exchange_code alongside
# the id, but OpenAlgo passes only the trigger id - so the GTT book is scanned
# (same pattern order_api uses for regular orders).
#
# Breeze GTT child orders are always LIMIT orders (a leg carries only
# limit_price / trigger_price), so MARKET pricetypes are MPP-converted to a
# protected LIMIT before the call, mirroring the zerodha/fyers plugins.

import json
import os
from datetime import datetime, timedelta, timezone

from broker.icici.api.baseurl import get_url
from broker.icici.api.breeze_client import extract_error, is_success, request_raw
from broker.icici.mapping.gtt_data import (
    GTT_TYPE_OCO,
    GTT_TYPE_SINGLE,
    build_order_details,
    index_or_stock,
    map_gtt_book,
)
from broker.icici.mapping.transform_data import get_breeze_params
from database.token_db import get_symbol_info
from utils.logging import get_logger
from utils.mpp_slab import calculate_protected_price

logger = get_logger(__name__)

# The book endpoints are exchange-scoped (same set order_api queries).
ORDER_EXCHANGES = ["NSE", "NFO", "BSE", "BFO", "MCX"]

IST = timezone(timedelta(hours=5, minutes=30))

# GTTs live for up to a year on ICICI Direct, so the book scan covers 365 days.
GTT_BOOK_LOOKBACK_DAYS = 365


class _FakeResponse:
    """Minimal stand-in so the service layer's ``res.status`` access keeps
    working when we short-circuit before issuing the HTTP call."""

    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.status = status_code
        self.text = text


def _trade_date_iso():
    """Today (IST) as the ISO instant Breeze's trade_date expects."""
    return datetime.now(IST).strftime("%Y-%m-%d") + "T06:00:00.000Z"


def _book_window_iso():
    """(from_date, to_date) covering the last 365 days for the GTT book."""
    today = datetime.now(IST)
    from_date = (today - timedelta(days=GTT_BOOK_LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT06:00:00.000Z"
    )
    to_date = today.strftime("%Y-%m-%dT06:00:00.000Z")
    return from_date, to_date


def _apply_mpp_if_market(data):
    """Convert MARKET pricetype -> MPP-protected LIMIT for the GTT legs.

    A Breeze GTT leg has no order-type field - the child order is always a
    LIMIT at ``limit_price``. MARKET therefore has to be emulated the same way
    zerodha/fyers do it: compute a Market-Price-Protection buffer around the
    relevant base price and override the limit fields.

    SINGLE -> buffer applied to the trigger price (the leg's own limit).
    OCO    -> buffer applied to each leg's trigger; ``stoploss`` and ``target``
              overridden (the single ``action`` gives the direction for both).
    """
    if (data.get("pricetype") or "").upper() != "MARKET":
        return

    action = (data.get("action") or "").upper()
    symbol = data.get("symbol")
    exchange = data.get("exchange")

    sym_info = get_symbol_info(symbol, exchange) if symbol and exchange else None
    try:
        tick_size = float(getattr(sym_info, "tick_size", None)) if sym_info else 0.0
    except (TypeError, ValueError):
        tick_size = 0.0
    instrument_type = (getattr(sym_info, "instrumenttype", None) if sym_info else None) or ""

    def _protect(price):
        return calculate_protected_price(
            price=float(price),
            action=action,
            symbol=symbol,
            instrument_type=instrument_type or None,
            tick_size=tick_size if tick_size > 0 else None,
        )

    trigger_type = (data.get("trigger_type") or "").upper()
    if trigger_type == "OCO":
        sl_trigger = float(data.get("triggerprice_sl") or 0)
        tg_trigger = float(data.get("triggerprice_tg") or 0)
        if sl_trigger > 0:
            data["stoploss"] = _protect(sl_trigger)
        if tg_trigger > 0:
            data["target"] = _protect(tg_trigger)
    else:
        trigger = data.get("trigger_price")
        if trigger not in (None, "", 0, 0.0):
            data["price"] = _protect(float(trigger))

    data["pricetype"] = "LIMIT"
    logger.info(
        f"ICICI GTT MARKET->LIMIT: trigger_type={trigger_type}, action={action}, "
        f"symbol={symbol}, price={data.get('price')}, stoploss={data.get('stoploss')}, "
        f"target={data.get('target')}"
    )


def _place_payload(data):
    """Build the gttorder POST body from the OpenAlgo flat GTT request."""
    params = get_breeze_params(data["symbol"], data["exchange"])

    trigger_type = (data.get("trigger_type") or "SINGLE").upper()
    gtt_type = GTT_TYPE_OCO if trigger_type == "OCO" else GTT_TYPE_SINGLE

    payload = {
        "exchange_code": params["exchange_code"],
        "stock_code": params["stock_code"],
        "product": params["product_type"],
        "quantity": str(int(float(data.get("quantity", 0) or 0))),
        "gtt_type": gtt_type,
        "index_or_stock": index_or_stock(params["stock_code"]),
        "trade_date": _trade_date_iso(),
        "order_details": build_order_details(data),
    }

    # Derivative identity fields (equity GTTs ride the cash product and carry
    # no expiry/right/strike).
    if params["instrumenttype"] == "FUT":
        payload["expiry_date"] = params["expiry_date"]
    elif params["instrumenttype"] in ("CE", "PE"):
        payload["expiry_date"] = params["expiry_date"]
        payload["right"] = params["right"]
        payload["strike_price"] = params["strike_price"]

    return payload


def _find_gtt_row(trigger_id, auth):
    """Scan the GTT book for a trigger id; returns (row, exchange_code).

    Cancel and modify need the exchange_code Breeze issued the GTT on, and
    OpenAlgo passes only the id - so the book is queried over a 365-day
    window per supported exchange, and the first row whose leg id matches
    wins.
    """
    from_date, to_date = _book_window_iso()

    for exchange_code in ORDER_EXCHANGES:
        payload = json.dumps(
            {
                "exchange_code": exchange_code,
                "from_date": from_date,
                "to_date": to_date,
            },
            separators=(",", ":"),
        )
        data = request_raw("gttorder", auth, "GET", payload)[1]
        if not is_success(data):
            continue
        rows = data.get("Success") or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            legs = row.get("order_details") or []
            for leg in legs:
                if isinstance(leg, dict) and str(leg.get("gtt_order_id")) == str(trigger_id):
                    return row, exchange_code
    return None, None


def place_gtt_order(data, auth):
    """Place a GTT trigger on ICICI Direct. Returns (response, response_data, trigger_id).

    SINGLE sends one leg; OCO sends the target + stoploss pair. There is no
    separate OCO endpoint - ``gtt_type`` ("single" vs "oco") is the switch.
    """
    try:
        _apply_mpp_if_market(data)
        payload = _place_payload(data)

        logger.debug(f"ICICI place_gtt payload: {json.dumps(payload)}")

        response, response_data = request_raw("gttorder", auth, "POST", json.dumps(payload, separators=(",", ":")))

        trigger_id = None
        if is_success(response_data):
            success = response_data.get("Success") or {}
            if isinstance(success, dict):
                trigger_id = success.get("gtt_order_id")
            if not trigger_id:
                logger.error(f"Breeze place GTT returned no gtt_order_id: {response_data}")
        else:
            logger.error(f"Breeze place GTT failed: {extract_error(response_data)}")

        return response, response_data, trigger_id

    except Exception as e:
        logger.exception("Error during ICICI GTT placement")
        return _FakeResponse(500), {"Error": f"General error: {e}"}, None


def modify_gtt_order(data, auth):
    """Modify a pending GTT. Returns (response_dict, status_code).

    Breeze accepts only the legs (plus their gtt_type) on modify - the
    instrument identity is immutable, so OpenAlgo modify fields that restate
    it are ignored. The trigger's exchange_code and original gtt_type are
    recovered from the GTT book.
    """
    trigger_id = data.get("trigger_id")
    if not trigger_id:
        return {"status": "error", "message": "trigger_id is required"}, 400

    try:
        row, exchange_code = _find_gtt_row(trigger_id, auth)
        if row is None:
            return {"status": "error", "message": f"GTT trigger {trigger_id} not found"}, 404

        gtt_type = str(row.get("gtt_type") or "").strip().lower()
        # Normalise the book's capitalised spellings back to the API values.
        if gtt_type.startswith("single"):
            gtt_type = GTT_TYPE_SINGLE
        elif "cover" in gtt_type:
            gtt_type = "cover_oco"
        else:
            gtt_type = GTT_TYPE_OCO

        # Single-leg rows rebuild from the single-trigger fields; OCO rows
        # from the target/stoploss fields.
        if gtt_type == GTT_TYPE_SINGLE and not data.get("trigger_price"):
            sl = float(data.get("triggerprice_sl") or 0)
            tg = float(data.get("triggerprice_tg") or 0)
            if sl > 0:
                data["trigger_price"] = sl
            elif tg > 0:
                data["trigger_price"] = tg

        payload = {
            "exchange_code": exchange_code,
            "gtt_order_id": str(trigger_id),
            "gtt_type": gtt_type,
            "order_details": build_order_details(data),
        }

        logger.debug(f"ICICI modify_gtt payload ({trigger_id}): {json.dumps(payload)}")

        response, response_data = request_raw(
            "gttorder", auth, "PUT", json.dumps(payload, separators=(",", ":"))
        )

        if is_success(response_data):
            return {"status": "success", "trigger_id": str(trigger_id)}, 200

        return {
            "status": "error",
            "message": extract_error(response_data) or "Failed to modify GTT",
        }, response.status if response is not None else 500

    except Exception as e:
        logger.exception("Error during ICICI GTT modification")
        return {"status": "error", "message": f"General error: {e}"}, 500


def cancel_gtt_order(trigger_id, auth):
    """Cancel a pending GTT. Returns (response_dict, status_code).

    The exchange_code is recovered from the GTT book, same as regular order
    cancellation.
    """
    if not trigger_id:
        return {"status": "error", "message": "trigger_id is required"}, 400

    try:
        _, exchange_code = _find_gtt_row(trigger_id, auth)
        if not exchange_code:
            return {"status": "error", "message": f"GTT trigger {trigger_id} not found"}, 404

        payload = json.dumps(
            {"exchange_code": exchange_code, "gtt_order_id": str(trigger_id)},
            separators=(",", ":"),
        )
        logger.debug(f"ICICI cancel_gtt payload ({trigger_id}): {payload}")

        response, response_data = request_raw("gttorder", auth, "DELETE", payload)

        if is_success(response_data):
            return {"status": "success", "trigger_id": str(trigger_id)}, 200

        return {
            "status": "error",
            "message": extract_error(response_data) or "Failed to cancel GTT",
        }, response.status if response is not None else 500

    except Exception as e:
        logger.exception("Error during ICICI GTT cancellation")
        return {"status": "error", "message": f"General error: {e}"}, 500


def get_gtt_book(auth, include_history=False):
    """List the user's GTTs. Returns (response_dict, status_code).

    ``data`` is a list of OpenAlgo-normalised GTT objects (see
    map_gtt_book). Breeze returns one row per GTT with both legs inline, so
    an OCO is a single row. Active triggers only unless ``include_history``.
    """
    try:
        from_date, to_date = _book_window_iso()

        rows = []
        exchanges_answered = 0
        probe_error = None

        for exchange_code in ORDER_EXCHANGES:
            payload = json.dumps(
                {
                    "exchange_code": exchange_code,
                    "from_date": from_date,
                    "to_date": to_date,
                },
                separators=(",", ":"),
            )
            data = request_raw("gttorder", auth, "GET", payload)[1]
            if is_success(data):
                exchanges_answered += 1
                success = data.get("Success") or []
                if isinstance(success, list):
                    rows.extend(success)
            elif probe_error is None:
                probe_error = data

        if exchanges_answered == 0 and probe_error is not None:
            return {
                "status": "error",
                "message": extract_error(probe_error) or "Failed to fetch GTT book",
            }, 500

        normalised = map_gtt_book(rows)

        if not include_history:
            normalised = [gtt for gtt in normalised if gtt.get("status") == "active"]

        return {"status": "success", "data": normalised}, 200

    except Exception as e:
        logger.exception("Error during ICICI GTT book fetch")
        return {"status": "error", "message": f"General error: {e}"}, 500
