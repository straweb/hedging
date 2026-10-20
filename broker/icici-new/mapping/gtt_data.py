"""
ICICI Direct Breeze GTT (Good-Till-Triggered) payload transformations.

Breeze GTT endpoint rules encoded here (each produced live errors in
earlier revisions):

- exchange_code must be LOWERCASE ("Exchange-code should be 'nfo'."),
- GTT only exists on NSE / BSE / NFO / BFO (the server rejects MCX/CDS),
- the book window (from_date/to_date) may not span more than 10 days
  (clamped in api/gtt_api.py),
- legs carry their own trigger price ("price") plus the fresh order fields
  ("fresh_order_action" / "fresh_order_price" / "fresh_order_type").
"""

from datetime import datetime

from broker.icici.mapping.transform_data import (
    build_openalgo_symbol,
    parse_symbol,
    to_openalgo_underlying,
)


_GTT_EXCHANGES = {
    "NSE": "nse",
    "BSE": "bse",
    "NFO": "nfo",
    "BFO": "bfo",
    "NSE_INDEX": "nse",
    "BSE_INDEX": "bse",
}


def normalise_gtt_exchange(exchange: str) -> str:
    """
    OpenAlgo exchange -> lowercase Breeze GTT exchange code.

    Raises ValueError for exchanges Breeze GTT does not support (MCX,
    currency) — the server would reject them anyway.
    """

    key = str(exchange or "").strip().upper()

    code = _GTT_EXCHANGES.get(key)

    if not code:
        raise ValueError(
            f"ICICI Breeze GTT does not support exchange '{exchange}' "
            "(NSE/BSE/NFO/BFO only)"
        )

    return code


def _first(data: dict, *keys, default=""):
    for key in keys:
        value = data.get(key)

        if value not in (None, ""):
            return value

    return default


def _contract_fields(data: dict) -> dict:
    """
    Parse the OpenAlgo symbol into structured Breeze GTT fields.
    """

    parsed = parse_symbol(data.get("symbol", ""), data.get("exchange", ""))

    return parsed


def _index_or_stock(parsed: dict) -> str:
    from broker.icici.mapping.transform_data import (
        BREEZE_TO_OPENALGO_UNDERLYINGS,
    )

    if parsed["stock_code"] in BREEZE_TO_OPENALGO_UNDERLYINGS:
        return "index"

    return "stock"


def _base_fields(parsed: dict, data: dict) -> dict:
    fields = {
        "exchange_code": normalise_gtt_exchange(data.get("exchange", "")),
        "stock_code": parsed["stock_code"],
        "product": parsed["product"],
        "quantity": str(data.get("quantity") or "0"),
    }

    if parsed["expiry_date"]:
        fields["expiry_date"] = parsed["expiry_date"]

    if parsed["right"]:
        fields["right"] = parsed["right"]

    if parsed["strike_price"]:
        fields["strike_price"] = parsed["strike_price"]

    return fields


def _leg(parsed: dict, data: dict, trigger: str, fresh_price: str) -> dict:
    leg = _base_fields(parsed, data)

    leg.update(
        {
            # The leg's "price" is the TRIGGER price on Breeze GTTs.
            "price": str(trigger),
            "action": str(data.get("action") or "buy").lower(),
            "order_type": "limit",
            "validity": "day",
            "fresh_order_action": str(data.get("action") or "buy").lower(),
            "fresh_order_price": str(fresh_price),
            "fresh_order_type": "limit",
        }
    )

    return leg


def transform_place_gtt(data: dict) -> dict:
    """
    OpenAlgo flat GTT payload -> Breeze place-GTT body.

    - SINGLE: one of ``triggerprice_sl`` / ``triggerprice_tg`` carries the
      trigger; the fresh order uses the OpenAlgo ``price`` as its limit.
    - OCO: two legs (stoploss + target) with their own triggers and
      limits; gtt_type becomes Breeze's "cover_oco".
    """

    parsed = _contract_fields(data)

    trigger_type = str(data.get("trigger_type") or "SINGLE").strip().upper()

    trigger_sl = _first(data, "triggerprice_sl", "trigger_price", default="")
    trigger_tg = _first(data, "triggerprice_tg", default="")
    limit_price = _first(data, "price", default="")
    stoploss_limit = _first(data, "stoploss", default=limit_price)
    target_limit = _first(data, "target", default=limit_price)

    base = _base_fields(parsed, data)

    payload = {
        **base,
        "gtt_type": "single",
        "index_or_stock": _index_or_stock(parsed),
        "trade_date": datetime.now().strftime("%d-%b-%Y"),
    }

    if trigger_type == "OCO":
        legs = []

        if str(trigger_sl) not in ("", "0", "0.0"):
            legs.append(
                _leg(parsed, data, trigger_sl, stoploss_limit or limit_price)
            )

        if str(trigger_tg) not in ("", "0", "0.0"):
            legs.append(
                _leg(parsed, data, trigger_tg, target_limit or limit_price)
            )

        if not legs:
            raise ValueError(
                "OCO GTT requires triggerprice_sl and/or triggerprice_tg"
            )

        primary = legs[0]

        payload.update(
            {
                "gtt_type": "cover_oco",
                "fresh_order_action": primary["fresh_order_action"],
                "fresh_order_price": primary["fresh_order_price"],
                "fresh_order_type": primary["fresh_order_type"],
                "order_details": legs,
            }
        )

        return payload

    # Single.
    trigger = trigger_sl or trigger_tg

    if trigger in ("", None):
        raise ValueError("GTT requires a trigger price")

    leg = _leg(parsed, data, trigger, limit_price)

    payload.update(
        {
            "fresh_order_action": leg["fresh_order_action"],
            "fresh_order_price": leg["fresh_order_price"],
            "fresh_order_type": leg["fresh_order_type"],
            "order_details": [leg],
        }
    )

    return payload


def transform_modify_gtt(data: dict) -> dict:
    """
    OpenAlgo GTT modify payload -> Breeze modify-GTT body
    (PUT /gttorder: exchange_code + gtt_order_id + gtt_type + order_details).
    """

    trigger_type = str(
        data.get("trigger_type")
        or ("OCO" if data.get("triggerprice_tg") else "SINGLE")
    ).strip().upper()

    payload = {
        "exchange_code": normalise_gtt_exchange(data.get("exchange", "")),
        "gtt_order_id": str(
            _first(data, "trigger_id", "gtt_order_id", "orderid", default="")
        ),
        "gtt_type": "cover_oco" if trigger_type == "OCO" else "single",
    }

    if data.get("symbol"):
        parsed = _contract_fields(data)

        trigger_sl = _first(data, "triggerprice_sl", "trigger_price", default="")
        trigger_tg = _first(data, "triggerprice_tg", default="")
        limit_price = _first(data, "price", default="")
        stoploss_limit = _first(data, "stoploss", default=limit_price)
        target_limit = _first(data, "target", default=limit_price)

        if trigger_type == "OCO":
            legs = []

            if str(trigger_sl) not in ("", "0", "0.0"):
                legs.append(
                    _leg(parsed, data, trigger_sl, stoploss_limit or limit_price)
                )

            if str(trigger_tg) not in ("", "0", "0.0"):
                legs.append(
                    _leg(parsed, data, trigger_tg, target_limit or limit_price)
                )

            if legs:
                payload["order_details"] = legs

        else:
            if trigger_sl:
                payload["order_details"] = [
                    _leg(parsed, data, trigger_sl, limit_price)
                ]

    return payload


# ---------------------------------------------------------------------------
# Book normalisation
# ---------------------------------------------------------------------------

_GTT_STATUS_MAP = {
    "active": "active",
    "open": "active",
    "triggered": "triggered",
    "executed": "triggered",
    "completed": "triggered",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "deleted": "cancelled",
    "expired": "expired",
    "rejected": "rejected",
}


def _row_float(row: dict, *keys, default=0.0) -> float:
    for key in keys:
        value = row.get(key)

        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue

    return default


def map_gtt_book(rows) -> list:
    """
    Breeze GTT book rows -> OpenAlgo normalized trigger list
    (same shape as broker/zerodha/mapping/gtt_data.map_gtt_book).
    """

    normalised = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        try:
            exchange = str(
                row.get("exchange_code") or row.get("exchange") or ""
            ).strip().upper()

            exchange = {
                "NSE": "NSE",
                "BSE": "BSE",
                "NFO": "NFO",
                "BFO": "BFO",
            }.get(exchange, exchange)

            stock_code = str(row.get("stock_code") or "")

            gtt_type = str(row.get("gtt_type") or "single")

            status_raw = str(row.get("status") or "").lower()

            legs = []

            trigger_prices = []

            details = row.get("order_details") or []

            if isinstance(details, dict):
                details = [details]

            for leg in details:
                if not isinstance(leg, dict):
                    continue

                trigger = _row_float(leg, "price", "trigger_price")

                if trigger:
                    trigger_prices.append(trigger)

                legs.append(
                    {
                        "action": str(leg.get("action") or "").lower(),
                        "quantity": int(_row_float(leg, "quantity", default=0)),
                        "price": _row_float(
                            leg, "fresh_order_price", "limit_price"
                        ),
                        "pricetype": "LIMIT",
                        "product": str(
                            leg.get("product") or row.get("product") or ""
                        ).upper()
                        or "NRML",
                    }
                )

            symbol = build_openalgo_symbol(
                stock_code,
                row.get("expiry_date") or "",
                row.get("strike_price") or "",
                row.get("right") or "",
                row.get("product") or "cash",
            )

            normalised.append(
                {
                    "trigger_id": str(
                        row.get("gtt_order_id")
                        or row.get("gttorderid")
                        or row.get("id")
                        or ""
                    ),
                    "trigger_type": (
                        "OCO" if "oco" in gtt_type.lower() else "SINGLE"
                    ),
                    "status": _GTT_STATUS_MAP.get(status_raw, status_raw),
                    "symbol": symbol,
                    "exchange": exchange,
                    "trigger_prices": trigger_prices,
                    "last_price": 0,
                    "legs": legs,
                    "created_at": str(
                        row.get("created_at")
                        or row.get("trade_date")
                        or ""
                    ),
                    "updated_at": str(row.get("updated_at") or ""),
                    "expires_at": str(
                        row.get("expires_at") or row.get("expiry_date") or ""
                    ),
                }
            )

        except Exception:
            continue

    return normalised
