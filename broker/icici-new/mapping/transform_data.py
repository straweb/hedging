"""
ICICI Direct Breeze symbol/order translation for OpenAlgo.

Breeze speaks a very specific vocabulary, encoded here:

- Exchange codes: REST order endpoints take UPPERCASE codes; the quotes,
  trades, portfolio and GTT endpoints take lowercase ones (the GTT server
  literally rejects "Exchange-code should be 'nfo'."). OpenAlgo index
  pseudo-exchanges map to the parent cash exchange
  (NSE_INDEX -> nse, BSE_INDEX -> bse, MCX_INDEX -> mcx).
- Orders/quotes never use contract symbols: Breeze wants the UNDERLYING
  stock code plus expiry_date/right/strike_price fields. ICICI's underlying
  vocabulary differs from NSE's for indices (BANKNIFTY is CNXBAN,
  FINNIFTY is NIFFIN, MIDCPNIFTY is NIFSEL, NIFTYNXT50 is NIFNEX, SENSEX
  is BSESEN, SENSEX50 is BSEFOC) — mapped below.
- OpenAlgo contract symbols (NIFTY19OCT2625000CE style) are parsed back
  into those structured fields and rebuilt from book rows.
"""

import re
from datetime import datetime

from database.token_db import get_br_symbol
from utils.logging import get_logger


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exchange maps
# ---------------------------------------------------------------------------

# Lowercase Breeze exchange codes (quotes / trades / portfolio / GTT / v2
# history).
OPENALGO_TO_BREEZE_EXCHANGE = {
    "NSE": "nse",
    "BSE": "bse",
    "NFO": "nfo",
    "BFO": "bfo",
    "MCX": "mcx",
    "CDS": "ndx",
    "NSE_INDEX": "nse",
    "BSE_INDEX": "bse",
    "MCX_INDEX": "mcx",
}

# Order endpoints accept/expect UPPERCASE exchange codes.
OPENALGO_TO_BREEZE_EXCHANGE_UPPER = {
    key: value.upper() for key, value in OPENALGO_TO_BREEZE_EXCHANGE.items()
}


def get_breeze_exchange(exchange: str, upper: bool = False) -> str:
    """
    OpenAlgo exchange -> Breeze exchange code.

    ``upper=True`` for the order/modify/cancel endpoints, lowercase
    (default) for quotes / trades / portfolio / GTT / history.
    """

    mapping = (
        OPENALGO_TO_BREEZE_EXCHANGE_UPPER
        if upper
        else OPENALGO_TO_BREEZE_EXCHANGE
    )

    key = str(exchange or "").strip().upper()

    return mapping.get(key, key.lower() if not upper else key)


# ---------------------------------------------------------------------------
# ICICI index vocabulary
# ---------------------------------------------------------------------------

# OpenAlgo index/derivative symbol -> ICICI underlying stock code. ICICI's
# SecurityMaster names indices with their own codes (FONSE FUTIDX /
# FOBSE FUTIND "ShortName" column); orders and quotes for index derivatives
# MUST use these, and index spot quotes also resolve through them.
ICICI_INDEX_UNDERLYINGS = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "CNXBAN",
    "FINNIFTY": "NIFFIN",
    "MIDCPNIFTY": "NIFSEL",
    "NIFTYNXT50": "NIFNEX",
    "SENSEX": "BSESEN",
    "BANKEX": "BANKEX",
    "SENSEX50": "BSEFOC",
}

# Reverse map: ICICI underlying -> OpenAlgo symbol (used to rebuild
# OpenAlgo symbols from order/position/holding rows).
BREEZE_TO_OPENALGO_UNDERLYINGS = {
    value: key for key, value in ICICI_INDEX_UNDERLYINGS.items()
}


def to_breeze_underlying(openalgo_symbol: str) -> str:
    """OpenAlgo symbol -> ICICI underlying stock code."""

    symbol = str(openalgo_symbol or "").strip().upper()

    return ICICI_INDEX_UNDERLYINGS.get(symbol, symbol)


def to_openalgo_underlying(breeze_stock_code: str) -> str:
    """ICICI underlying stock code -> OpenAlgo symbol."""

    code = str(breeze_stock_code or "").strip().upper()

    return BREEZE_TO_OPENALGO_UNDERLYINGS.get(code, code)


# ---------------------------------------------------------------------------
# Symbol parsing (OpenAlgo contract symbol -> structured Breeze fields)
# ---------------------------------------------------------------------------

# NIFTY19OCT2625000CE / BANKNIFTY26NOV26FUT / CRUDEOILM20MAY26FUT
_OPTION_RE = re.compile(
    r"^(?P<underlying>[A-Z0-9&\-]+?)"
    r"(?P<day>\d{2})(?P<month>[A-Z]{3})(?P<year>\d{2})"
    r"(?P<strike>\d+(?:\.\d+)?)"
    r"(?P<right>CE|PE)$"
)

_FUTURE_RE = re.compile(
    r"^(?P<underlying>[A-Z0-9&\-]+?)"
    r"(?P<day>\d{2})(?P<month>[A-Z]{3})(?P<year>\d{2})"
    r"(?P<fut>FUT)$"
)


def parse_expiry(value) -> datetime | None:
    """
    Parse an ICICI/Breeze expiry ("19-Oct-2026", "19-OCT-26", "19OCT26")
    or an OpenAlgo symbol tail into a datetime.
    """

    if isinstance(value, datetime):
        return value

    text = str(value or "").strip()
    if not text:
        return None

    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d%b%y", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def breeze_expiry(value) -> str:
    """
    Expiry in the Breeze REST format ("19-Oct-2026").
    """

    parsed = parse_expiry(value)

    if parsed is None:
        return ""

    return parsed.strftime("%d-%b-%Y")


def openalgo_expiry_tag(value) -> str:
    """
    Expiry as the OpenAlgo symbol tag ("19OCT26").
    """

    parsed = parse_expiry(value)

    if parsed is None:
        return ""

    return parsed.strftime("%d%b%y").upper()


def parse_symbol(symbol: str, exchange: str) -> dict:
    """
    Parse an OpenAlgo symbol + exchange into Breeze order/quote fields:

        {
            "stock_code":  ICICI underlying ("CNXBAN" for BANKNIFTY),
            "product":     "cash" | "futures" | "options",
            "expiry_date": "19-Oct-2026" ("" for cash),
            "right":       "call" | "put" | "" (others not traded here),
            "strike_price": "25000" ("" for cash/futures),
        }

    Raises ValueError for unparseable derivative symbols.
    """

    exchange_u = str(exchange or "").strip().upper()
    symbol_u = str(symbol or "").strip().upper()

    if exchange_u in ("NSE", "BSE"):
        base = symbol_u[:-3] if symbol_u.endswith("-EQ") else symbol_u

        # The ICICI scrip code (brsymbol) differs from the OpenAlgo
        # display symbol for most stocks (RELIND vs RELIANCE) — the
        # master contract's mapping is the source of truth.
        brsymbol = str(get_br_symbol(symbol, exchange) or "").strip().upper()

        return {
            "stock_code": brsymbol or to_breeze_underlying(base),
            "product": "cash",
            "expiry_date": "",
            "right": "",
            "strike_price": "",
        }

    if exchange_u in ("NSE_INDEX", "BSE_INDEX"):
        # Index spot quotes resolve through the ICICI index vocabulary.
        return {
            "stock_code": to_breeze_underlying(symbol_u),
            "product": "cash",
            "expiry_date": "",
            "right": "",
            "strike_price": "",
        }

    # Derivatives: NFO / BFO / MCX (and their index pseudo-exchanges).
    match = _OPTION_RE.match(symbol_u)

    if match:
        underlying = to_breeze_underlying(match.group("underlying"))

        return {
            "stock_code": underlying,
            "product": "options",
            "expiry_date": breeze_expiry(
                f"{match.group('day')}-{match.group('month')}-"
                f"{match.group('year')}"
            ),
            "right": "call" if match.group("right") == "CE" else "put",
            "strike_price": _format_strike(match.group("strike")),
        }

    match = _FUTURE_RE.match(symbol_u)

    if match:
        underlying = to_breeze_underlying(match.group("underlying"))

        return {
            "stock_code": underlying,
            "product": "futures",
            "expiry_date": breeze_expiry(
                f"{match.group('day')}-{match.group('month')}-"
                f"{match.group('year')}"
            ),
            "right": "",
            "strike_price": "",
        }

    # Currency derivatives stored on CDS use ndx + similar tails.
    match = _FUTURE_RE.match(symbol_u) or _OPTION_RE.match(symbol_u)

    if match:
        raise ValueError(f"Unsupported ICICI derivative symbol: {symbol}")

    raise ValueError(
        f"Cannot parse ICICI symbol '{symbol}' on {exchange} — expected "
        "OpenAlgo contract format (e.g. NIFTY19OCT2625000CE, "
        "BANKNIFTY26NOV26FUT) or an equity symbol."
    )


def _format_strike(strike: str) -> str:
    """
    Normalise a strike for Breeze: "25000.00" -> "25000",
    "2500.50" stays "2500.50".
    """

    try:
        number = float(strike)
    except (TypeError, ValueError):
        return str(strike)

    if number.is_integer():
        return str(int(number))

    return str(number)


def build_openalgo_symbol(
    stock_code: str,
    expiry,
    strike,
    right,
    product_type: str,
) -> str:
    """
    Rebuild an OpenAlgo symbol from Breeze book row fields
    (stock_code / expiry_date / right / strike_price / product_type).
    """

    underlying = to_openalgo_underlying(stock_code)

    product = str(product_type or "").strip().lower()

    if product in ("cash", "mtf", "btst", ""):
        return f"{underlying}-EQ"

    expiry_tag = openalgo_expiry_tag(expiry)

    if not expiry_tag:
        return underlying

    if product == "futures":
        return f"{underlying}{expiry_tag}FUT"

    # Options.
    right_u = str(right or "").strip().upper()

    if right_u in ("C", "CALL", "CE"):
        suffix = "CE"
    elif right_u in ("P", "PUT", "PE"):
        suffix = "PE"
    else:
        return f"{underlying}{expiry_tag}FUT"

    strike_text = _format_strike(strike)

    return f"{underlying}{expiry_tag}{strike_text}{suffix}"


# ---------------------------------------------------------------------------
# Order payload transform
# ---------------------------------------------------------------------------

def transform_order_data(data: dict) -> dict:
    """
    OpenAlgo order payload -> Breeze place_order body.

    - MARKET is native (Breeze applies its own Market Price Protection).
    - SL  -> order_type "stoploss" with price (limit) + stoploss (trigger).
    - SL-M has no Breeze equivalent: emulated as a stoploss whose limit
      price is the trigger pushed through the shared MPP slabs.
    """

    symbol = data.get("symbol", "")
    exchange = data.get("exchange", "")

    parsed = parse_symbol(symbol, exchange)

    pricetype = str(data.get("pricetype") or "MARKET").strip().upper()
    action = str(data.get("action") or "").strip().upper()

    order_type = "market"
    price = "0"
    stoploss = ""

    raw_price = data.get("price") or "0"
    trigger = data.get("trigger_price") or ""

    if pricetype == "LIMIT":
        order_type = "limit"
        price = str(raw_price)

    elif pricetype == "SL":
        order_type = "stoploss"
        price = str(raw_price)
        stoploss = str(trigger)

    elif pricetype == "SL-M":
        # Emulate trigger-market: stoploss trigger + a protected limit.
        try:
            trigger_value = float(trigger or raw_price or 0)

            slab_price = _slm_protected_price(
                trigger_value, action, symbol, parsed["product"]
            )

            order_type = "stoploss"
            price = str(slab_price)
            stoploss = _format_strike(trigger_value)

        except (TypeError, ValueError):
            order_type = "stoploss"
            price = str(raw_price)
            stoploss = str(trigger)

    payload = {
        "stock_code": parsed["stock_code"],
        "exchange_code": get_breeze_exchange(exchange, upper=True),
        "product": parsed["product"],
        "action": "buy" if action == "BUY" else "sell",
        "order_type": order_type,
        "quantity": str(data.get("quantity") or "0"),
        "price": price,
        "validity": "day",
        "disclosed_quantity": "0",
        "user_remark": str(data.get("remarks") or "OpenAlgo"),
    }

    if stoploss:
        payload["stoploss"] = stoploss

    if parsed["expiry_date"]:
        payload["expiry_date"] = parsed["expiry_date"]

    if parsed["right"]:
        payload["right"] = parsed["right"]

    if parsed["strike_price"]:
        payload["strike_price"] = parsed["strike_price"]

    # Order modifiers passed through when supplied.
    if data.get("validity"):
        validity = str(data["validity"]).strip().lower()

        if validity in ("day", "ioc", "vtc"):
            payload["validity"] = validity

    if data.get("validity_date"):
        payload["validity_date"] = breeze_expiry(data["validity_date"])

    return payload


def _slm_protected_price(
    trigger: float, action: str, symbol: str, product: str
) -> float:
    """
    SL-M emulation price: push the trigger through the shared MPP slabs so
    the emulated stop-limit does not rest unfilled once triggered.
    """

    from utils.mpp_slab import calculate_protected_price

    instrument_type = "EQ" if product == "cash" else (
        "CE" if symbol.upper().endswith("CE") else (
            "PE" if symbol.upper().endswith("PE") else "FUT"
        )
    )

    return float(
        calculate_protected_price(
            trigger,
            action,
            symbol=symbol,
            instrument_type=instrument_type,
        )
    )


# ---------------------------------------------------------------------------
# Modify payload
# ---------------------------------------------------------------------------

def transform_modify_order_data(data: dict) -> dict:
    """
    OpenAlgo modify payload -> Breeze modify_order body (PUT /order).

    Only order_type / quantity / price / stoploss / validity fields are
    accepted by Breeze; the exchange resolves from the symbol (with the
    order-book index as backstop in order_api).
    """

    exchange = data.get("exchange", "")

    payload = {
        "order_id": str(data.get("orderid") or data.get("order_id") or ""),
        "exchange_code": get_breeze_exchange(exchange, upper=True),
    }

    parsed = parse_symbol(data.get("symbol", ""), exchange) if data.get(
        "symbol"
    ) else None

    pricetype = str(data.get("pricetype") or "").strip().upper()
    raw_price = data.get("price") or ""
    trigger = data.get("trigger_price") or ""

    if pricetype in ("LIMIT", "SL", "SL-M", "MARKET"):
        order_type = {
            "LIMIT": "limit",
            "SL": "stoploss",
            "SL-M": "stoploss",
            "MARKET": "market",
        }[pricetype]

        payload["order_type"] = order_type

    if pricetype in ("LIMIT", "SL") and raw_price:
        payload["price"] = str(raw_price)

    if pricetype == "SL-M" and trigger:
        try:
            trigger_value = float(trigger)

            product = parsed["product"] if parsed else "cash"

            payload["price"] = str(
                _slm_protected_price(
                    trigger_value,
                    str(data.get("action") or "BUY"),
                    data.get("symbol", ""),
                    product,
                )
            )
            payload["stoploss"] = str(trigger_value)

        except (TypeError, ValueError):
            pass

    if pricetype == "SL" and trigger:
        payload["stoploss"] = str(trigger)

    if data.get("quantity"):
        payload["quantity"] = str(data["quantity"])

    if data.get("validity"):
        validity = str(data["validity"]).strip().lower()

        if validity in ("day", "ioc", "vtc"):
            payload["validity"] = validity

    if data.get("validity_date"):
        payload["validity_date"] = breeze_expiry(data["validity_date"])

    return payload


# ---------------------------------------------------------------------------
# Misc helpers used across the plugin
# ---------------------------------------------------------------------------

def get_oa_symbol(brsymbol: str, exchange: str) -> str:
    """
    Best-effort broker symbol -> OpenAlgo symbol for rows that carry a
    complete OpenAlgo-shaped contract symbol. Structured rows (which Breeze
    always provides) are rebuilt in mapping.order_data via
    build_openalgo_symbol instead.
    """

    text = str(brsymbol or "").strip().upper()

    if not text:
        return ""

    exchange_u = str(exchange or "").strip().upper()

    if exchange_u in ("NSE", "BSE") and not text.endswith("-EQ"):
        return f"{text}-EQ"

    return text


def product_for_exchange(exchange: str, parsed: dict | None = None) -> str:
    """
    Default Breeze product for an exchange when no parsed contract is
    available (margin lookups etc.).
    """

    exchange_u = str(exchange or "").strip().upper()

    if exchange_u in ("NFO", "BFO", "MCX"):
        return "futures"

    return "cash"
