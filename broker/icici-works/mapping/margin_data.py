# broker/icici/mapping/margin_data.py
# Margin payload helpers for the ICICI Direct (Breeze) plugin.
#
# Breeze exposes a basket margin calculator at POST "margincalculator". The
# request is a list of leg dicts (one per order) plus a single exchange_code,
# so a multi-exchange OpenAlgo basket is grouped per exchange and one call is
# made per group. Wire reference: breeze-connect SDK 1.0.69 README,
# "Margin Calculator":
#
#   {
#     "strike_price": "0", "quantity": "30", "right": "others",
#     "product": "futures", "action": "buy", "price": "49500",
#     "expiry_date": "27-Feb-2025", "stock_code": "CNXBAN",
#     "cover_order_flow": "N", "fresh_order_type": "N",
#     "cover_limit_rate": "0", "cover_sltp_price": "0",
#     "fresh_limit_rate": "0", "open_quantity": "0"
#   }
#
# Response (note the API's own "margin_calulation" spelling):
#   Success.span_margin_required / non_span_margin_required / order_value ...

from datetime import datetime

from broker.icici.mapping.transform_data import (
    DERIVATIVE_EXCHANGES,
    get_breeze_params,
    parse_expiry,
)
from utils.logging import get_logger

logger = get_logger(__name__)

# The margincalculator endpoint prices F&O baskets; equity legs are valued at
# order value instead (delivery margin == order value on ICICI Direct).
CASH_EXCHANGES = {"NSE", "BSE"}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _expiry_for_margin(iso_expiry):
    """ISO instant ("2025-02-27T06:00:00.000Z") -> "27-Feb-2025"."""
    parsed = parse_expiry(iso_expiry[:10]) if iso_expiry else None
    if not parsed:
        return ""
    return parsed.strftime("%d-%b-%Y")


def _ltp_for(symbol, exchange, auth):
    """Best-effort LTP for legs whose price is 0 (market orders)."""
    try:
        from broker.icici.api.data import BrokerData

        quotes = BrokerData(auth).get_quotes(symbol, exchange)
        if isinstance(quotes, dict) and quotes.get("ltp"):
            return float(quotes["ltp"])
    except Exception as e:
        logger.warning(f"Margin: could not fetch LTP for {symbol}/{exchange}: {e}")
    return 0.0


def transform_margin_positions(positions, auth):
    """OpenAlgo margin positions -> ({exchange_code: [legs]}, skipped_labels).

    Derivative exchanges (NFO/BFO/MCX) build margincalculator legs; equity
    legs are carried separately as (exchange, order_value) pairs because the
    endpoint does not price cash orders. Legs priced 0 (market orders) get
    their LTP fetched through the quotes handler.
    """
    groups = {}
    cash_values = []
    skipped = []

    for position in positions or []:
        symbol = position.get("symbol")
        exchange = position.get("exchange")
        if not symbol or not exchange:
            skipped.append(f"{symbol or '?'} ({exchange or '?'}) - missing symbol/exchange")
            continue

        try:
            params = get_breeze_params(symbol, exchange)
        except Exception as e:
            logger.warning(f"Margin: cannot resolve {symbol}/{exchange}: {e}")
            skipped.append(f"{symbol} ({exchange}) - not in master contract")
            continue

        quantity = _num(position.get("quantity"))
        price = _num(position.get("price"))
        if price <= 0:
            price = _ltp_for(symbol, exchange, auth)
        if price <= 0:
            skipped.append(f"{symbol} ({exchange}) - no price available")
            continue

        action = (position.get("action") or "BUY").lower()
        margin_price = price
        if (position.get("pricetype") or "").upper() in ("SL", "SL-M"):
            # The calculator prices plain orders; a stop leg is valued at its
            # trigger (the rate the order would actually transact around).
            trigger = _num(position.get("trigger_price"))
            if trigger > 0:
                margin_price = trigger

        right = params["right"] or "others"
        leg = {
            "stock_code": params["stock_code"],
            "product": params["product_type"],
            "action": action,
            "quantity": str(int(quantity)),
            "price": f"{margin_price:.2f}",
            "right": right,
            "expiry_date": _expiry_for_margin(params["expiry_date"]),
            "strike_price": params["strike_price"] or "0",
            "cover_order_flow": "N",
            "fresh_order_type": "N",
            "cover_limit_rate": "0",
            "cover_sltp_price": "0",
            "fresh_limit_rate": "0",
            "open_quantity": "0",
        }

        if params["exchange_code"] in CASH_EXCHANGES or params["exchange_code"] not in DERIVATIVE_EXCHANGES:
            # Cash leg: the endpoint does not price delivery orders; the
            # margin is the order value.
            cash_values.append((params["exchange_code"], quantity * margin_price, symbol))
            continue

        groups.setdefault(params["exchange_code"], []).append(leg)

    return groups, cash_values, skipped


def parse_margin_response(responses, cash_values):
    """Aggregate margincalculator responses into the shared OpenAlgo shape.

    ``responses`` maps exchange_code -> parsed Breeze payload. The shared
    contract (see the fyers plugin) is
    {"status": "success", "data": {"total_margin_required", "span_margin",
    "exposure_margin"}}; Breeze's non-span margin (exposure + premium) maps
    onto exposure_margin.
    """
    span_total = 0.0
    non_span_total = 0.0

    for exchange_code, data in (responses or {}).items():
        if not isinstance(data, dict) or data.get("Success") is None:
            return {
                "status": "error",
                "message": f"Margin calculation failed for {exchange_code}",
            }
        success = data["Success"] or {}
        span_total += _num(success.get("span_margin_required"))
        non_span_total += _num(success.get("non_span_margin_required"))

    total = span_total + non_span_total
    for _exchange, value, _symbol in cash_values or []:
        # Equity delivery margin == order value; fold it into the total.
        total += value

    return {
        "status": "success",
        "data": {
            "total_margin_required": round(total, 2),
            "span_margin": round(span_total, 2),
            "exposure_margin": round(non_span_total, 2),
        },
    }
