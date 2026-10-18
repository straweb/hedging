# broker/icici/mapping/transform_data.py
# Mapping between the OpenAlgo order contract and the ICICI Direct Breeze API.
#
# Two Breeze quirks shape this file:
#   1. Instrument identity is expressed as (stock_code, exchange_code,
#      product_type, expiry_date, right, strike_price) - not a token. The
#      stock_code is ICICI's internal short name (RELIANCE, NIFTY, CNXBAN for
#      Bank Nifty, BSESEN for SENSEX, ICIBAN for ICICI Bank), so the master
#      contract stores it in brsymbol and every outbound call re-derives the
#      quintuple from the SymToken row.
#   2. product values are asset classes: "cash"/"margin" on equity,
#      "futures"/"options" on derivatives. The OpenAlgo MIS/CNC/NRML product
#      therefore maps by exchange+instrument, not one-to-one.

import math
from datetime import datetime
from decimal import Decimal

from database.token_db import get_symbol_info
from utils.logging import get_logger
from utils.mpp_slab import get_instrument_type_from_symbol, get_mpp_percentage

logger = get_logger(__name__)

# OpenAlgo exchange -> Breeze exchange_code (what goes into the payload and
# what the master contract stores as brexchange).
OPENALGO_TO_BREEZE_EXCHANGE = {
    "NSE": "NSE",
    "BSE": "BSE",
    "NFO": "NFO",
    "BFO": "BFO",
    "MCX": "MCX",
    "CDS": "NDX",
    "NSE_INDEX": "NSE",
    "BSE_INDEX": "BSE",
}

# Breeze exchange_code -> OpenAlgo exchange. Falls back to the raw value so an
# unmapped code degrades to a visible label rather than None (issue #1463
# class of crash in the positions UI).
BREEZE_TO_OPENALGO_EXCHANGE = {
    "NSE": "NSE",
    "BSE": "BSE",
    "NFO": "NFO",
    "BFO": "BFO",
    "MCX": "MCX",
    "NDX": "CDS",
}

# ICICI internal index codes -> OpenAlgo index symbols. Sourced from the
# index rows of NSEScripMaster.txt / BSEScripMaster.txt.
BREEZE_INDEX_TO_OPENALGO = {
    "NIFTY": "NIFTY",
    "CNXBAN": "BANKNIFTY",
    "NIFFIN": "FINNIFTY",
    "NIFNEX": "NIFTYNXT50",
    "NIFSEL": "MIDCPNIFTY",
    "INDVIX": "INDIAVIX",
    "CNXIT": "NIFTYIT",
    "NIF100": "NIFTY100",
    "NIF200": "NIFTY200",
    "NIF500": "NIFTY500",
    "NIFATO": "NIFTYAUTO",
    "CNXINF": "NIFTYINFRA",
    "NIFMID": "NIFTYMIDCAP50",
    "NIFCPS": "NIFTYCPSE",
    "CNXPSE": "NIFTYPSE",
    "BSESEN": "SENSEX",
    "BANKEX": "BANKEX",
}

DERIVATIVE_EXCHANGES = ("NFO", "BFO", "MCX", "NDX")


def _tick_decimals(tick: float) -> int:
    """Number of decimal places implied by a tick size (0.05 -> 2)."""
    return max(0, -Decimal(str(tick)).as_tuple().exponent)


def _snap_to_tick(value: float, tick: float, direction: str) -> float:
    """Snap ``value`` to a multiple of ``tick`` (floor or ceil)."""
    ratio = round(value / tick, 6)  # tame float noise before the floor/ceil
    k = math.floor(ratio) if direction == "floor" else math.ceil(ratio)
    return round(k * tick, _tick_decimals(tick))


def format_strike(strike) -> str:
    """Render a strike without trailing zeros: 24500.0 -> "24500", 292.50 -> "292.5"."""
    try:
        if strike is None or strike == "":
            return "0"
        return f"{float(strike):.6f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return str(strike)


def parse_expiry(value):
    """Parse a stored expiry ("27-FEB-25", "27-Feb-2025") into a date."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_breeze_expiry(value):
    """Stored expiry -> the ISO instant Breeze expects ("2025-02-27T06:00:00.000Z")."""
    parsed = parse_expiry(value)
    if not parsed:
        return ""
    return parsed.strftime("%Y-%m-%d") + "T06:00:00.000Z"


def to_oa_expiry(value):
    """Any expiry representation -> OpenAlgo's stored "27-FEB-25" format."""
    parsed = parse_expiry(value)
    if not parsed:
        return ""
    return parsed.strftime("%d-%b-%y").upper()


def get_breeze_params(symbol, exchange):
    """Resolve the Breeze instrument quintuple for an OpenAlgo symbol.

    Returns a dict with stock_code, exchange_code, product_type, expiry_date
    (ISO), right and strike_price - exactly the fields the quotes, history and
    order endpoints need. Raises when the master contract has no row, since
    every caller needs all fields to reach the API.
    """
    info = get_symbol_info(symbol, exchange)
    if info is None:
        raise Exception(
            f"Symbol {symbol} not found in the ICICI Direct master contract for exchange {exchange}. "
            "Please refresh the master contract."
        )

    brexchange = (getattr(info, "brexchange", None) or OPENALGO_TO_BREEZE_EXCHANGE.get(exchange, exchange))
    instrumenttype = (getattr(info, "instrumenttype", None) or "EQ").upper()

    if instrumenttype == "FUT":
        product_type = "futures"
        right = ""
        strike_price = ""
        expiry_date = to_breeze_expiry(getattr(info, "expiry", ""))
    elif instrumenttype in ("CE", "PE"):
        product_type = "options"
        right = "call" if instrumenttype == "CE" else "put"
        strike_price = format_strike(getattr(info, "strike", 0))
        expiry_date = to_breeze_expiry(getattr(info, "expiry", ""))
    else:
        # Equity and index quotes ride the cash product on their listing venue.
        product_type = "cash"
        right = ""
        strike_price = ""
        expiry_date = ""

    return {
        "stock_code": getattr(info, "brsymbol", None) or symbol,
        "exchange_code": brexchange,
        "product_type": product_type,
        "expiry_date": expiry_date,
        "right": right,
        "strike_price": strike_price,
        "instrumenttype": instrumenttype,
    }


def map_product_type(product, instrumenttype="EQ", brexchange="NSE"):
    """OpenAlgo product -> Breeze order product, given the instrument context.

    Equity: CNC is delivery ("cash"), MIS/NRML are the margin product.
    Derivatives: Breeze only knows the asset class, and every F&O order is a
    margin product - so MIS and NRML both collapse onto futures/options.
    """
    product = (product or "MIS").upper()
    instrumenttype = (instrumenttype or "EQ").upper()

    if instrumenttype == "FUT":
        return "futures"
    if instrumenttype in ("CE", "PE"):
        return "options"
    # Equity: Breeze's native "Cash" (positions/close flows) means delivery.
    if product in ("CNC", "CASH"):
        return "cash"
    return "margin"


def reverse_map_product_type(product, instrumenttype="EQ"):
    """Breeze order/position product -> OpenAlgo product.

    "margin" is MIS on equity and NRML on derivatives; asset-class products
    (futures/options) only exist on derivatives, so they are NRML.
    """
    product = (product or "").lower()
    instrumenttype = (instrumenttype or "EQ").upper()

    if product == "cash":
        return "CNC"
    if product in ("mtf", "btst"):
        return "CNC"
    if product == "margin":
        return "NRML" if instrumenttype in ("FUT", "CE", "PE") else "MIS"
    if product in ("futures", "options"):
        return "NRML"
    return None


def map_order_type(pricetype):
    """OpenAlgo pricetype -> Breeze order_type.

    MARKET is accepted by Breeze and converted to an aggressive limit order
    server-side under the SEBI algo-trading circular, so no client-side
    conversion is needed.
    """
    order_type_mapping = {
        "MARKET": "market",
        "LIMIT": "limit",
        "SL": "stoploss",
        "SL-M": "stoploss",
    }
    return order_type_mapping.get(pricetype, "market")


def map_validity(validity):
    """OpenAlgo validity -> Breeze validity (day/ioc/vtc)."""
    return "ioc" if (validity or "").upper() == "IOC" else "day"


def _slm_protected_price(symbol, exchange, action, trigger_price):
    """Derive a protective stop-limit price for an SL-M order from its trigger.

    Breeze has no resting stop-market: "stoploss" is a stop-limit and both
    price and trigger are required. SL-M is therefore mapped to a stoploss
    order whose limit sits MPP% beyond the trigger in the fill direction
    (SELL below, BUY above), snapped to the exchange tick so it is marketable
    once triggered. Mirrors the shared MPP handling used by the dhan/samco
    plugins; fails closed when the tick size cannot be resolved.
    """
    instrument_type = get_instrument_type_from_symbol(symbol)

    symbol_info = get_symbol_info(symbol, exchange)
    try:
        tick_size = float(getattr(symbol_info, "tick_size", None)) if symbol_info else 0.0
    except (TypeError, ValueError):
        tick_size = 0.0
    if not math.isfinite(tick_size) or tick_size <= 0:
        raise ValueError(
            f"Cannot resolve tick size from DB for {symbol}/{exchange}; required to "
            f"build a valid SL-M protective limit price"
        )

    pct = (get_mpp_percentage(trigger_price, instrument_type) or 0) / 100.0

    if action.upper() == "SELL":
        # Strictly BELOW the trigger, at least one tick away, tick-aligned.
        raw = min(trigger_price * (1 - pct), trigger_price - tick_size)
        limit = _snap_to_tick(raw, tick_size, "floor")
        if limit <= 0:
            raise ValueError(
                f"SL-M SELL trigger {trigger_price} for {symbol}/{exchange} is too low "
                f"to derive a positive protective limit at tick {tick_size}"
            )
    else:
        # Strictly ABOVE the trigger, at least one tick away, tick-aligned.
        raw = max(trigger_price * (1 + pct), trigger_price + tick_size)
        limit = _snap_to_tick(raw, tick_size, "ceil")

    return limit


def transform_data(data, token):
    """OpenAlgo order dict -> Breeze placespottrade payload."""
    params = get_breeze_params(data["symbol"], data["exchange"])

    quantity = str(int(float(data.get("quantity", 0) or 0)))
    price = str(data.get("price", "") or "0")
    trigger_price = float(data.get("trigger_price", 0) or 0)

    payload = {
        "stock_code": params["stock_code"],
        "exchange_code": params["exchange_code"],
        "product": map_product_type(
            data.get("product", "MIS"), params["instrumenttype"], params["exchange_code"]
        ),
        "action": (data.get("action") or "BUY").lower(),
        "order_type": map_order_type(data.get("pricetype", "MARKET")),
        "quantity": quantity,
        "price": price,
        "validity": map_validity(data.get("validity")),
        "user_remark": (data.get("strategy") or "OpenAlgo")[:100],
    }

    if data.get("pricetype") in ("SL", "SL-M"):
        if trigger_price <= 0:
            raise ValueError("Trigger price is required for Stop Loss orders")
        payload["stoploss"] = str(trigger_price)
        if data["pricetype"] == "SL-M":
            # Breeze stoploss orders are stop-LIMIT; derive a marketable limit.
            protected = _slm_protected_price(
                data["symbol"], data["exchange"], data["action"], trigger_price
            )
            payload["order_type"] = "stoploss"
            payload["price"] = str(protected)
            logger.info(
                f"ICICI SL-M -> stoploss: Symbol={data['symbol']}, Action={data['action']}, "
                f"Trigger={trigger_price}, ProtectedLimit={protected}"
            )
    elif data.get("pricetype") == "LIMIT":
        payload["price"] = price
    else:
        # MARKET orders let ICICI apply its own aggressive-limit conversion.
        payload["price"] = "0"

    disclosed_qty = int(float(data.get("disclosed_quantity", 0) or 0))
    if disclosed_qty > 0:
        payload["disclosed_quantity"] = str(disclosed_qty)

    # Derivative identity fields
    if params["instrumenttype"] == "FUT":
        payload["expiry_date"] = params["expiry_date"]
    elif params["instrumenttype"] in ("CE", "PE"):
        payload["expiry_date"] = params["expiry_date"]
        payload["right"] = params["right"]
        payload["strike_price"] = params["strike_price"]

    return payload


def transform_modify_order_data(data):
    """OpenAlgo modify request -> Breeze modify payload."""
    params = get_breeze_params(data["symbol"], data["exchange"])

    modified = {
        "order_id": data["orderid"],
        "exchange_code": params["exchange_code"],
    }

    modified["order_type"] = map_order_type(data.get("pricetype", "LIMIT"))
    modified["quantity"] = str(int(float(data.get("quantity", 0) or 0)))
    modified["validity"] = map_validity(data.get("validity"))

    trigger_price = float(data.get("trigger_price", 0) or 0)
    if data.get("pricetype") in ("SL", "SL-M"):
        if trigger_price <= 0:
            raise ValueError("Trigger price is required for Stop Loss orders")
        modified["stoploss"] = str(trigger_price)
        if data["pricetype"] == "SL-M":
            protected = _slm_protected_price(
                data["symbol"], data["exchange"], data.get("action", "BUY"), trigger_price
            )
            modified["order_type"] = "stoploss"
            modified["price"] = str(protected)
        else:
            modified["price"] = str(data.get("price", "") or "0")
    elif data.get("pricetype") == "LIMIT":
        modified["price"] = str(data.get("price", "") or "0")
    else:
        modified["price"] = "0"

    disclosed_qty = int(float(data.get("disclosed_quantity", 0) or 0))
    if disclosed_qty > 0:
        modified["disclosed_quantity"] = str(disclosed_qty)

    return modified


def map_exchange(brexchange):
    """Breeze exchange_code -> OpenAlgo exchange (never None)."""
    return BREEZE_TO_OPENALGO_EXCHANGE.get(brexchange, brexchange)


def map_exchange_type(exchange):
    """OpenAlgo exchange -> Breeze exchange_code."""
    return OPENALGO_TO_BREEZE_EXCHANGE.get(exchange)
