# broker/icici/mapping/order_data.py
# Book mapping for the ICICI Direct (Breeze) plugin.
#
# Breeze book rows identify instruments by (exchange_code, stock_code,
# product_type, expiry_date, right, strike_price) and carry no numeric token,
# so every row is resolved back to the OpenAlgo symbol through the symtoken
# table on the fields that make a contract unique.

import threading

from broker.icici.mapping.transform_data import (
    BREEZE_INDEX_TO_OPENALGO,
    format_strike,
    map_exchange,
    reverse_map_product_type,
    to_oa_expiry,
)
from database.symbol import SymToken
from utils.logging import get_logger

logger = get_logger(__name__)

# Breeze order/position statuses -> OpenAlgo normalized statuses.
STATUS_COMPLETE = {"executed"}
STATUS_OPEN = {"ordered", "queued", "requested", "partially executed"}
STATUS_REJECTED = {"rejected"}
STATUS_CANCELLED = {
    "cancelled",
    "expired",
    "freezed",
    "partially executed and expired",
    "partially executed and cancelled",
}

_resolver_lock = threading.Lock()
_resolver_cache = {}
_RESOLVER_CACHE_MAX = 8192


def _resolve_symbol(exchange_code, stock_code, expiry_date, strike_price, right):
    """Resolve a Breeze instrument quintuple to an OpenAlgo symbol.

    Returns (symbol, instrumenttype) or (None, None). Equity rows resolve on
    (brexchange, brsymbol); derivatives additionally pin expiry, strike and
    right so two contracts of the same underlying cannot collapse into one.
    """
    oa_exchange = map_exchange(exchange_code)
    cache_key = (oa_exchange, exchange_code, stock_code, expiry_date or "", strike_price or "", right or "")
    with _resolver_lock:
        cached = _resolver_cache.get(cache_key)
    if cached is not None:
        return cached

    symbol = None
    instrumenttype = None
    try:
        query = SymToken.query.filter_by(brexchange=exchange_code, brsymbol=stock_code)
        row = None
        if expiry_date:
            # Derivative: pin every identity field the row carries.
            expiry = to_oa_expiry(expiry_date)
            rows = query.filter_by(expiry=expiry).all() if expiry else query.all()
            strike = format_strike(strike_price)
            if right and right.lower() in ("call", "put"):
                wanted_type = "CE" if right.lower() == "call" else "PE"
                for candidate in rows:
                    if (
                        candidate.instrumenttype == wanted_type
                        and format_strike(candidate.strike) == strike
                    ):
                        row = candidate
                        break
            else:
                for candidate in rows:
                    if candidate.instrumenttype == "FUT":
                        row = candidate
                        break
        else:
            row = query.first()
        if row is not None:
            symbol = row.symbol
            instrumenttype = (row.instrumenttype or "EQ").upper()
    except Exception as e:
        # Mapping must never take down a book render; the caller falls back
        # to the raw broker symbol.
        logger.warning(f"Breeze symbol resolution failed for {stock_code}/{exchange_code}: {e}")

    result = (symbol, instrumenttype)
    with _resolver_lock:
        if len(_resolver_cache) >= _RESOLVER_CACHE_MAX:
            _resolver_cache.clear()
        _resolver_cache[cache_key] = result
    return result


def _derive_instrumenttype(row):
    """Instrument type for a Breeze row that did not resolve to a DB row."""
    right = (row.get("right") or "").lower()
    if right in ("call", "put"):
        return "CE" if right == "call" else "PE"
    if row.get("expiry_date") or row.get("expiryDate"):
        return "FUT"
    return "EQ"


def map_order_data(order_data):
    """Annotate Breeze order/trade/position rows with OpenAlgo identity.

    Writes tradingSymbol (OpenAlgo symbol), exchangeSegment (OpenAlgo
    exchange) and productType (OpenAlgo product) onto every row, matching the
    field names the shared book services expect.
    """
    if not order_data:
        return order_data if isinstance(order_data, list) else []

    for row in order_data:
        if not isinstance(row, dict):
            continue

        exchange_code = row.get("exchange_code") or row.get("exchangeSegment") or ""
        stock_code = row.get("stock_code") or ""
        expiry_date = row.get("expiry_date") or ""
        strike_price = row.get("strike_price") or ""
        right = row.get("right") or ""

        symbol, instrumenttype = _resolve_symbol(
            exchange_code, stock_code, expiry_date, strike_price, right
        )
        if not symbol:
            # Underlying-level fallback for unresolved derivatives (stale
            # master contract), mirroring dhan's never-null rule (#1463).
            symbol = BREEZE_INDEX_TO_OPENALGO.get(stock_code, stock_code)
            instrumenttype = _derive_instrumenttype(row)
            logger.warning(
                f"Breeze symbol not resolved for {stock_code}/{exchange_code}; "
                f"falling back to '{symbol}'"
            )

        oa_exchange = map_exchange(exchange_code)
        row["tradingSymbol"] = symbol
        row["exchangeSegment"] = oa_exchange
        row["productType"] = reverse_map_product_type(
            row.get("product_type") or row.get("productType") or "", instrumenttype
        ) or row.get("product_type") or "MIS"

    return order_data


def calculate_order_statistics(order_data):
    """Order-book statistics; normalizes each row's orderStatus in place."""
    total_buy_orders = total_sell_orders = 0
    total_completed_orders = total_open_orders = total_rejected_orders = 0

    if order_data:
        for order in order_data:
            action = (order.get("action") or "").lower()
            if action == "buy":
                total_buy_orders += 1
            elif action == "sell":
                total_sell_orders += 1

            status = (order.get("status") or "").lower()
            if status in STATUS_COMPLETE:
                total_completed_orders += 1
                order["orderStatus"] = "complete"
            elif status in STATUS_OPEN:
                total_open_orders += 1
                order["orderStatus"] = "open"
            elif status in STATUS_REJECTED:
                total_rejected_orders += 1
                order["orderStatus"] = "rejected"
            elif status in STATUS_CANCELLED:
                order["orderStatus"] = "cancelled"

    return {
        "total_buy_orders": total_buy_orders,
        "total_sell_orders": total_sell_orders,
        "total_completed_orders": total_completed_orders,
        "total_open_orders": total_open_orders,
        "total_rejected_orders": total_rejected_orders,
    }


def _map_pricetype(order_type):
    """Breeze order_type ("Limit"/"Market"/"StopLoss") -> OpenAlgo pricetype."""
    ot = (order_type or "").lower()
    if ot == "limit":
        return "LIMIT"
    if ot == "market":
        return "MARKET"
    if ot == "stoploss":
        return "SL"
    return (order_type or "").upper()


def transform_order_data(orders):
    """Breeze order rows -> the normalized orderbook row contract."""
    if isinstance(orders, dict):
        orders = [orders]

    transformed_orders = []
    for order in orders or []:
        if not isinstance(order, dict):
            logger.warning(f"Expected a dict order row, found {type(order)}; skipping")
            continue

        transformed_orders.append(
            {
                "symbol": order.get("tradingSymbol", ""),
                "exchange": order.get("exchangeSegment", ""),
                "action": (order.get("action") or "").upper(),
                "quantity": order.get("quantity", 0),
                "price": order.get("price", 0.0),
                "trigger_price": order.get("stoploss", 0.0) or 0.0,
                "pricetype": _map_pricetype(order.get("order_type", "")),
                "product": order.get("productType", ""),
                "orderid": order.get("order_id", ""),
                "order_status": order.get("orderStatus", ""),
                "timestamp": order.get("order_datetime", ""),
            }
        )
    return transformed_orders


def map_trade_data(trade_data):
    return map_order_data(trade_data)


def transform_tradebook_data(tradebook_data):
    """Breeze trade rows -> normalized tradebook rows.

    Breeze's trade payload spells fields differently across API versions, so
    numeric fields read through candidate keys instead of assuming one shape.
    """
    transformed_data = []
    for trade in tradebook_data or []:
        if not isinstance(trade, dict):
            continue

        quantity = trade.get("quantity") or trade.get("trade_quanity") or trade.get("traded_quantity") or 0
        price = (
            trade.get("trade_price")
            or trade.get("average_trade_price")
            or trade.get("average_price")
            or trade.get("price")
            or 0.0
        )
        try:
            quantity = float(quantity)
            price = float(price)
        except (TypeError, ValueError):
            quantity, price = 0.0, 0.0

        transformed_data.append(
            {
                "symbol": trade.get("tradingSymbol", ""),
                "exchange": trade.get("exchangeSegment", ""),
                "product": trade.get("productType", ""),
                "action": (trade.get("action") or "").upper(),
                "quantity": quantity,
                "average_price": price,
                "trade_value": round(quantity * price, 2),
                "orderid": trade.get("order_id", ""),
                "timestamp": trade.get("trade_date") or trade.get("trade_datetime") or "",
            }
        )
    return transformed_data


def map_position_data(position_data):
    return map_order_data(position_data)


def transform_positions_data(positions_data):
    """Breeze position rows -> normalized position rows.

    Breeze reports ltp directly, so no multiquotes round-trip is needed. pnl
    may be null on freshly opened positions; it is then derived from
    (ltp - average_price) * quantity.
    """
    transformed_data = []
    for position in positions_data or []:
        if not isinstance(position, dict):
            continue

        symbol = position.get("tradingSymbol") or ""
        exchange = position.get("exchangeSegment") or ""

        try:
            quantity = float(position.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        try:
            average_price = float(position.get("average_price", 0) or 0)
        except (TypeError, ValueError):
            average_price = 0.0
        try:
            ltp = float(position.get("ltp", 0) or 0)
        except (TypeError, ValueError):
            ltp = 0.0
        try:
            pnl = position.get("pnl")
            if pnl is None or pnl == "":
                raise TypeError
            pnl = float(pnl)
        except (TypeError, ValueError):
            # Breeze leaves pnl null on freshly opened positions; derive it.
            pnl = (ltp - average_price) * quantity if (ltp and average_price) else 0.0

        transformed_data.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "product": position.get("productType") or "",
                "quantity": quantity,
                "average_price": round(average_price, 2),
                "ltp": round(ltp, 2),
                "pnl": round(pnl, 2),
            }
        )
    return transformed_data


def map_portfolio_data(portfolio_data):
    """Resolve and enrich Breeze holding rows.

    Enrichment writes the same private fields the dhan-style downstream
    statistics consume: _oa_symbol, _exchange and _ltp. Breeze reports
    current_market_price on every holding, so the enrichment is fully local
    and needs no multiquotes round-trip.
    """
    if portfolio_data is None or not isinstance(portfolio_data, list):
        logger.info("No holdings data available.")
        return {}

    for h in portfolio_data:
        if not isinstance(h, dict):
            continue
        symbol, _ = _resolve_symbol(
            h.get("exchange_code") or "NSE",
            h.get("stock_code") or "",
            h.get("expiry_date") or "",
            h.get("strike_price") or "",
            h.get("right") or "",
        )
        h["_oa_symbol"] = symbol or h.get("stock_code") or ""
        h["_exchange"] = map_exchange(h.get("exchange_code") or "NSE")
        try:
            h["_ltp"] = float(h.get("current_market_price", 0) or 0)
        except (TypeError, ValueError):
            h["_ltp"] = 0.0

    return portfolio_data


def calculate_portfolio_statistics(holdings_data):
    if not holdings_data:
        return {
            "totalholdingvalue": 0,
            "totalinvvalue": 0,
            "totalprofitandloss": 0,
            "totalpnlpercentage": 0,
        }

    def _num(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    totalinvvalue = sum(
        _num(h.get("average_price")) * _num(h.get("quantity")) for h in holdings_data
    )
    # Prefer the enriched LTP; fall back to average cost so the value starts
    # at investment (zero P&L) rather than zero.
    totalholdingvalue = sum(
        (_num(h.get("_ltp")) or _num(h.get("average_price"))) * _num(h.get("quantity"))
        for h in holdings_data
    )
    totalprofitandloss = totalholdingvalue - totalinvvalue
    totalpnlpercentage = (totalprofitandloss / totalinvvalue * 100) if totalinvvalue else 0
    return {
        "totalholdingvalue": totalholdingvalue,
        "totalinvvalue": totalinvvalue,
        "totalprofitandloss": totalprofitandloss,
        "totalpnlpercentage": totalpnlpercentage,
    }


def transform_holdings_data(holdings_data):
    transformed_data = []
    if not holdings_data:
        return transformed_data

    def _num(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    for h in holdings_data:
        if not isinstance(h, dict):
            continue
        qty = _num(h.get("quantity"))
        avg = _num(h.get("average_price"))
        ltp = _num(h.get("_ltp"))
        if ltp > 0 and avg > 0:
            pnl = round((ltp - avg) * qty, 2)
            pnlpercent = round((ltp - avg) / avg * 100, 2)
        else:
            pnl = round(_num(h.get("unrealized_profit")), 2)
            pnlpercent = 0.0

        transformed_data.append(
            {
                "symbol": h.get("_oa_symbol") or "",
                "exchange": h.get("_exchange") or "NSE",
                "quantity": qty,
                "product": "CNC",
                "average_price": round(avg, 2),
                "ltp": round(ltp, 2),
                "pnl": pnl,
                "pnlpercent": pnlpercent,
            }
        )
    return transformed_data
