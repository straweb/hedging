# broker/icici/mapping/gtt_data.py
# GTT payload transforms for the ICICI Direct (Breeze) plugin.
#
# Breeze exposes GTT through the "gttorder" endpoint in two shapes:
#   - single leg:  gtt_type "single", order_details = [one leg]
#   - OCO:         gtt_type "oco",   order_details = [target leg, stoploss leg]
#     (the SDK also knows "cover_oco", which adds a fresh entry order; OpenAlgo
#     GTTs bracket an existing position, so they always map to plain "oco")
#
# A leg is {"gtt_leg_type"?, "action", "limit_price", "trigger_price"} - the
# child order Breeze fires is always a LIMIT at limit_price once trigger_price
# is crossed, which is why MARKET pricetypes are MPP-converted upstream
# (see api/gtt_api.py).
#
# Wire reference: breeze-connect SDK 1.0.69 README, sections
# gtt_single_leg_place_order / gtt_three_leg_place_order / gtt_order_book.

from broker.icici.mapping.transform_data import (
    BREEZE_INDEX_TO_OPENALGO,
    format_strike,
    map_exchange,
    reverse_map_product_type,
)
from utils.logging import get_logger

logger = get_logger(__name__)

GTT_TYPE_SINGLE = "single"
GTT_TYPE_OCO = "oco"

# Breeze GTT book leg statuses (capitalised in the payload).
GTT_STATUS_MAP = {
    "active": "active",
    "triggered": "triggered",
    "executed": "triggered",
    "cancelled": "cancelled",
    "rejected": "rejected",
    "freezed": "cancelled",
    "expired": "cancelled",
}

# Breeze book rows capitalise asset classes; reverse_map_product_type expects
# the lowercase spellings used on the order endpoints.
_PRODUCT_ALIASES = {"futures": "futures", "options": "options", "cash": "cash", "margin": "margin", "mtf": "mtf", "btst": "btst"}


def resolve_single_trigger(data):
    """Resolve the active trigger for a SINGLE GTT.

    Same fallback order as zerodha/fyers: the legacy ``trigger_price`` alias
    first, then whichever of the new flat fields is populated.
    """
    if data.get("trigger_price") not in (None, "", 0, 0.0):
        return float(data["trigger_price"])
    sl = float(data.get("triggerprice_sl") or 0)
    tg = float(data.get("triggerprice_tg") or 0)
    return sl if sl > 0 else tg


def index_or_stock(stock_code):
    """Breeze wants to know whether the GTT rides an index or a stock."""
    return "index" if stock_code in BREEZE_INDEX_TO_OPENALGO else "stock"


def build_order_details(data):
    """Build the Breeze ``order_details`` list from the OpenAlgo flat fields.

    SINGLE -> one leg {action, limit_price, trigger_price}.
    OCO    -> two legs keyed gtt_leg_type:
                target   <- triggerprice_tg / target   (trigger ABOVE the LTP)
                stoploss <- triggerprice_sl / stoploss (trigger BELOW the LTP)
              OpenAlgo's invariant is triggerprice_sl < triggerprice_tg; when a
              caller violates it the legs are swapped (with a warning) because
              Breeze keys the legs off their type, not their order.
              Both legs carry the single OpenAlgo ``action`` - an OCO brackets
              an existing position, so the exit side is the same on both legs.
    """
    action = (data.get("action") or "BUY").lower()
    trigger_type = (data.get("trigger_type") or "SINGLE").upper()
    quantity = data.get("quantity")

    if trigger_type != "OCO":
        return [
            {
                "action": action,
                "limit_price": str(data.get("price", "") or "0"),
                "trigger_price": str(resolve_single_trigger(data)),
            }
        ]

    tg_trigger = float(data.get("triggerprice_tg") or 0)
    sl_trigger = float(data.get("triggerprice_sl") or 0)
    tg_price = data.get("target")
    sl_price = data.get("stoploss")

    if sl_trigger >= tg_trigger:
        logger.warning(
            f"ICICI GTT OCO: triggerprice_sl ({sl_trigger}) >= triggerprice_tg "
            f"({tg_trigger}) breaks the OpenAlgo invariant; swapping so the "
            f"target leg keeps the higher trigger."
        )
        tg_trigger, sl_trigger = sl_trigger, tg_trigger
        tg_price, sl_price = sl_price, tg_price

    return [
        {
            "gtt_leg_type": "target",
            "action": action,
            "limit_price": str(tg_price if tg_price is not None else "0"),
            "trigger_price": str(tg_trigger),
        },
        {
            "gtt_leg_type": "stoploss",
            "action": action,
            "limit_price": str(sl_price if sl_price is not None else "0"),
            "trigger_price": str(sl_trigger),
        },
    ]


def _leg_status(legs):
    """Roll the per-leg statuses up to one OpenAlgo GTT status.

    A Breeze GTT carries status on each leg, not on the row: active wins (the
    trigger can still fire), then triggered/executed, then rejected, and a
    row whose legs are all cancelled/expired/freezed reads as cancelled.
    """
    statuses = [str(leg.get("status") or "").strip().lower() for leg in legs]
    if any(s == "active" for s in statuses):
        return "active"
    if any(s in ("triggered", "executed") for s in statuses):
        return "triggered"
    if any(s == "rejected" for s in statuses):
        return "rejected"
    return "cancelled"


def map_gtt_book(rows):
    """Normalise Breeze ``gttorder`` book rows into the OpenAlgo GTT shape.

    Breeze returns one row per GTT with the legs inline in ``order_details``
    (a single-element list for SINGLE, target+stoploss for OCO), so an OCO is
    already a single row. Fields resolved here follow the same conventions as
    the other books: symbol through the symtoken table, exchange via the
    Breeze -> OpenAlgo map, product via reverse_map_product_type.
    """
    from broker.icici.mapping.order_data import _resolve_symbol

    normalised = []

    for row in rows or []:
        if not isinstance(row, dict):
            logger.warning(f"ICICI GTT book: expected a dict row, got {type(row)}; skipping")
            continue

        legs_raw = row.get("order_details") or []
        if not isinstance(legs_raw, list):
            legs_raw = []

        exchange_code = row.get("exchange_code") or ""
        stock_code = row.get("stock_code") or ""
        expiry = row.get("expiry_date") or ""
        strike = row.get("strike_price")
        right = row.get("right") or ""

        oa_exchange = map_exchange(exchange_code)
        symbol, instrumenttype = _resolve_symbol(
            exchange_code, stock_code, expiry, strike if strike not in (None, "") else "", right
        )
        if not symbol:
            symbol = BREEZE_INDEX_TO_OPENALGO.get(stock_code, stock_code)
            instrumenttype = "CE" if (right or "").lower() == "call" else (
                "PE" if (right or "").lower() == "put" else ("FUT" if expiry else "EQ")
            )
            logger.warning(
                f"ICICI GTT symbol not resolved for {stock_code}/{exchange_code}; "
                f"falling back to '{symbol}'"
            )

        product_type = str(row.get("product_type") or "").lower()
        product = (
            reverse_map_product_type(_PRODUCT_ALIASES.get(product_type, product_type), instrumenttype)
            or "CNC"
        )

        action_map = {"buy": "BUY", "sell": "SELL"}

        legs = []
        trigger_prices = []
        for leg in legs_raw:
            if not isinstance(leg, dict):
                continue
            try:
                trigger_price = float(leg.get("trigger_price") or 0)
            except (TypeError, ValueError):
                trigger_price = 0.0
            try:
                limit_price = float(leg.get("limit_price") or 0)
            except (TypeError, ValueError):
                limit_price = 0.0
            try:
                quantity = int(float(row.get("quantity") or 0))
            except (TypeError, ValueError):
                quantity = 0

            legs.append(
                {
                    "action": action_map.get((leg.get("action") or "").lower(), (leg.get("action") or "").upper()),
                    "quantity": quantity,
                    "price": limit_price,
                    "pricetype": "LIMIT",
                    "product": product,
                }
            )
            trigger_prices.append(trigger_price)

        gtt_type = str(row.get("gtt_type") or "").strip().lower()
        trigger_type = "single" if gtt_type.startswith("single") else "two-leg"

        # OpenAlgo presents trigger prices low -> high.
        trigger_prices.sort()

        normalised.append(
            {
                "trigger_id": str(
                    (legs_raw[0].get("gtt_order_id") if legs_raw and isinstance(legs_raw[0], dict) else None)
                    or row.get("gtt_order_id")
                    or ""
                ),
                "trigger_type": trigger_type,
                "status": _leg_status(legs_raw),
                "symbol": symbol,
                "exchange": oa_exchange,
                "trigger_prices": trigger_prices,
                "last_price": 0.0,
                "legs": legs,
                "created_at": row.get("order_datetime") or "",
                "updated_at": "",
                "expires_at": "",
            }
        )

    return normalised


def format_strike_for_gtt(strike):
    """Strike formatting shared with the order transform (0 -> "0")."""
    return format_strike(strike)
