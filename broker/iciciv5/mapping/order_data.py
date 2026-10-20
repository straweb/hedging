"""
ICICI Direct Breeze book rows -> OpenAlgo normalized rows.

Breeze order/trade/position/holding payloads carry the STRUCTURED contract
identity (stock_code + product_type + expiry_date + right + strike_price)
rather than a contract symbol, so the OpenAlgo symbol is rebuilt with
mapping.transform_data.build_openalgo_symbol. Every field read is tolerant
to the key-name drift Breeze shows across endpoints (snake_case on v1
endpoints, camelCase on some legacy rows).
"""

from database.token_db import get_br_symbol, get_oa_symbol
from utils.logging import get_logger

from broker.icici.mapping.transform_data import build_openalgo_symbol


logger = get_logger(__name__)


def _first(row: dict, *keys, default=""):
    """First non-empty value among candidate keys (tolerant lookup)."""

    for key in keys:
        value = row.get(key)

        if value not in (None, ""):
            return value

    return default


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def row_symbol(row: dict, default_exchange: str = "") -> tuple[str, str]:
    """
    Rebuild (openalgo_symbol, exchange) from a structured Breeze row.
    """

    exchange = str(
        _first(row, "exchange_code", "exch_code", "exchange", default="")
    ).strip().upper()

    if exchange in ("N", "NSE_FO", "NFO_IND"):
        exchange = "NFO"

    stock_code = str(
        _first(row, "stock_code", "stock", "underlying", default="")
    )

    if not stock_code:
        return "", exchange or default_exchange

    product_type = str(
        _first(row, "product_type", "product", "instrument", default="cash")
    )

    symbol = build_openalgo_symbol(
        stock_code,
        _first(row, "expiry_date", "expiry", default=""),
        _first(row, "strike_price", "strike", default=""),
        _first(row, "right", "option_type", default=""),
        product_type,
    )

    return symbol, exchange or default_exchange


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

_ORDER_STATUS_MAP = {
    "ordered": "open",
    "queued": "open",
    "requested": "open",
    "order placed": "open",
    "partially executed": "open",
    "partially executed and expired": "cancelled",
    "partially executed and cancelled": "cancelled",
    "executed": "complete",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "expired": "cancelled",
    "freezed": "cancelled",
}


def map_order_status(raw) -> str:
    """
    Breeze status -> OpenAlgo lowercase status. Terminal statuses always
    win over fill-state heuristics.
    """

    text = str(raw or "").strip().lower()

    if not text:
        return "open"

    mapped = _ORDER_STATUS_MAP.get(text)

    if mapped:
        return mapped

    if "execut" in text and "partial" not in text:
        return "complete"

    if "cancel" in text:
        return "cancelled"

    if "reject" in text:
        return "rejected"

    return "open"


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------

def map_order_data(order_data):
    """
    Mutate each broker order row in place into the OpenAlgo shape used by
    transform_order_data.

    NOTE: the parameter MUST be named ``order_data`` — services/
    orderbook_service.py calls ``map_order_data(order_data=order_data)``
    with a keyword argument, and a different name raises
    "map_order_data() got an unexpected keyword argument 'order_data'".
    """

    if not isinstance(order_data, list):
        order_data = [order_data] if isinstance(order_data, dict) else []

    for row in order_data:
        if not isinstance(row, dict):
            continue

        try:
            symbol, exchange = row_symbol(row)

            product_type = str(
                _first(row, "product_type", "product", default="cash")
            ).lower()

            product = {
                "cash": "CNC",
                "mtf": "MTF",
                "btst": "CNC",
            }.get(product_type, "NRML")

            right = str(_first(row, "right", default="")).lower()

            if product_type in ("options",) or right in ("call", "put"):
                product = "NRML"

            quantity = _to_int(
                _first(row, "quantity", "qty", "order_quantity", default=0)
            )

            traded = _to_int(
                _first(
                    row,
                    "traded_quantity",
                    "filled_quantity",
                    "executed_quantity",
                    default=0,
                )
            )

            status_raw = _first(
                row, "status", "order_status", "orderstatus", default=""
            )

            average_price = _to_float(
                _first(
                    row,
                    "average_price",
                    "avg_price",
                    "average_traded_price",
                    "trade_price",
                    default=0,
                )
            )

            row["symbol"] = symbol
            row["exchange"] = exchange
            row["action"] = str(
                _first(row, "action", "transaction_type", default="")
            ).lower()
            row["quantity"] = quantity
            row["price"] = _to_float(_first(row, "price", default=0))
            row["trigger_price"] = _to_float(
                _first(row, "stoploss", "trigger_price", default=0)
            )
            row["pricetype"] = str(
                _first(row, "order_type", "orderType", default="MARKET")
            ).upper()
            row["product"] = product
            row["orderid"] = str(
                _first(row, "order_id", "orderid", "orderNumber", default="")
            )
            row["order_status"] = map_order_status(status_raw)
            row["timestamp"] = str(
                _first(
                    row,
                    "order_date",
                    "order_datetime",
                    "trade_date",
                    "created_at",
                    default="",
                )
            )
            row["traded_quantity"] = traded
            row["average_price"] = average_price

        except Exception:
            logger.exception("Error mapping ICICI order row")

    return order_data


def calculate_order_statistics(orders):
    statistics = {
        "total_buy_orders": 0,
        "total_sell_orders": 0,
        "total_completed_orders": 0,
        "total_open_orders": 0,
        "total_rejected_orders": 0,
    }

    for row in orders:
        action = str(row.get("action") or "").lower()

        if action == "buy":
            statistics["total_buy_orders"] += 1
        elif action == "sell":
            statistics["total_sell_orders"] += 1

        status = str(row.get("order_status") or "").lower()

        if status == "complete":
            statistics["total_completed_orders"] += 1
        elif status == "open":
            statistics["total_open_orders"] += 1
        elif status == "rejected":
            statistics["total_rejected_orders"] += 1

    return statistics


def transform_order_data(orders):
    """
    Project mapped order rows onto the documented OpenAlgo orderbook
    contract.
    """

    transformed = []

    for row in orders:
        traded = int(row.get("traded_quantity") or 0)
        quantity = int(row.get("quantity") or 0)

        transformed.append(
            {
                "symbol": row.get("symbol", ""),
                "exchange": row.get("exchange", ""),
                "action": row.get("action", ""),
                "quantity": quantity,
                "price": row.get("price", 0.0),
                "trigger_price": row.get("trigger_price", 0.0),
                "pricetype": row.get("pricetype", ""),
                "product": row.get("product", ""),
                "orderid": row.get("orderid", ""),
                "order_status": row.get("order_status", ""),
                "timestamp": row.get("timestamp", ""),
                # Extra keys consumed by the order-update poller and UI.
                "filled_quantity": traded,
                "pending_quantity": max(quantity - traded, 0),
                "average_price": row.get("average_price", 0.0),
            }
        )

    return transformed


# ---------------------------------------------------------------------------
# Trade book
# ---------------------------------------------------------------------------

def map_trade_data(trade_data):
    """
    Parameter MUST be named ``trade_data`` — services/tradebook_service.py
    calls ``map_trade_data(trade_data=trade_data)`` with a keyword argument.
    """

    if not isinstance(trade_data, list):
        trade_data = [trade_data] if isinstance(trade_data, dict) else []

    for row in trade_data:
        if not isinstance(row, dict):
            continue

        try:
            symbol, exchange = row_symbol(row)

            quantity = _to_int(
                _first(row, "quantity", "qty", "traded_quantity", default=0)
            )

            price = _to_float(
                _first(
                    row,
                    "trade_price",
                    "price",
                    "average_price",
                    "executed_price",
                    default=0,
                )
            )

            row["symbol"] = symbol
            row["exchange"] = exchange
            row["product"] = {
                "cash": "CNC",
                "mtf": "MTF",
                "btst": "CNC",
            }.get(
                str(_first(row, "product_type", "product", default="cash")).lower(),
                "NRML",
            )
            row["action"] = str(
                _first(row, "action", "transaction_type", "buy_sell", default="")
            ).lower()
            row["quantity"] = quantity
            row["average_price"] = price
            row["trade_value"] = round(quantity * price, 2)
            row["orderid"] = str(
                _first(row, "order_id", "orderid", "orderNumber", default="")
            )
            row["timestamp"] = str(
                _first(
                    row,
                    "trade_date",
                    "trade_datetime",
                    "datetime",
                    "created_at",
                    default="",
                )
            )

        except Exception:
            logger.exception("Error mapping ICICI trade row")

    return trade_data


def transform_tradebook_data(trades):
    transformed = []

    for row in trades:
        transformed.append(
            {
                "symbol": row.get("symbol", ""),
                "exchange": row.get("exchange", ""),
                "product": row.get("product", ""),
                "action": row.get("action", ""),
                "quantity": row.get("quantity", 0),
                "average_price": row.get("average_price", 0.0),
                "trade_value": row.get("trade_value", 0.0),
                "orderid": row.get("orderid", ""),
                "timestamp": row.get("timestamp", ""),
            }
        )

    return transformed


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def map_position_data(positions):
    if not isinstance(positions, list):
        positions = [positions] if isinstance(positions, dict) else []

    for row in positions:
        if not isinstance(row, dict):
            continue

        try:
            symbol, exchange = row_symbol(row)

            product_type = str(
                _first(row, "product_type", "product", default="cash")
            ).lower()

            row["symbol"] = symbol
            row["exchange"] = exchange
            row["product"] = "CNC" if product_type == "cash" else "NRML"

            buy_qty = _to_int(
                _first(row, "buy_quantity", "buy_qty", "bQty", default=0)
            )

            sell_qty = _to_int(
                _first(row, "sell_quantity", "sell_qty", "sQty", default=0)
            )

            quantity = _first(row, "quantity", "net_quantity", "netQty", default=None)

            quantity = (
                _to_int(quantity) if quantity not in (None, "") else buy_qty - sell_qty
            )

            row["quantity"] = quantity

            average_price = _to_float(
                _first(
                    row,
                    "average_price",
                    "avg_price",
                    "buy_avg_price",
                    "average_traded_price",
                    default=0,
                )
            )

            if not average_price and quantity:
                denominator = buy_qty if quantity > 0 else sell_qty

                amount = _to_float(
                    _first(
                        row,
                        "buy_amount",
                        "sell_amount",
                        "buy_value",
                        "sell_value",
                        default=0,
                    )
                )

                if denominator:
                    average_price = amount / denominator

            row["average_price"] = average_price

            ltp = _to_float(
                _first(
                    row,
                    "last_price",
                    "ltp",
                    "last_traded_price",
                    "market_price",
                    default=0,
                )
            )

            row["ltp"] = ltp

            pnl = _first(row, "pnl", "profit_and_loss", "m2m", default=None)

            if pnl in (None, ""):
                pnl = (
                    (ltp - average_price) * quantity
                    if ltp and average_price
                    else 0.0
                )

            row["pnl"] = _to_float(pnl)

        except Exception:
            logger.exception("Error mapping ICICI position row")

    return positions


def transform_positions_data(positions):
    transformed = []

    for row in positions:
        transformed.append(
            {
                "symbol": row.get("symbol", ""),
                "exchange": row.get("exchange", ""),
                "product": row.get("product", ""),
                "quantity": row.get("quantity", 0),
                "pnl": row.get("pnl", 0.0),
                "average_price": row.get("average_price", 0.0),
                "ltp": row.get("ltp", 0.0),
            }
        )

    return transformed


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

def map_portfolio_data(holdings):
    if not isinstance(holdings, list):
        holdings = [holdings] if isinstance(holdings, dict) else []

    for row in holdings:
        if not isinstance(row, dict):
            continue

        try:
            stock_code = str(
                _first(row, "stock_code", "symbol", "scrip", default="")
            )

            symbol = f"{stock_code}-EQ" if stock_code else ""

            quantity = _to_int(
                _first(
                    row,
                    "quantity",
                    "holding_quantity",
                    "demat_quantity",
                    default=0,
                )
            )

            average_price = _to_float(
                _first(
                    row,
                    "average_price",
                    "avg_price",
                    "buy_avg",
                    "acquisition_price",
                    default=0,
                )
            )

            ltp = _to_float(
                _first(
                    row,
                    "last_price",
                    "ltp",
                    "market_price",
                    "current_price",
                    default=0,
                )
            )

            pnl = (ltp - average_price) * quantity if ltp and average_price else 0.0

            row["symbol"] = symbol
            row["exchange"] = str(
                _first(row, "exchange_code", "exchange", default="NSE")
            ).upper() or "NSE"
            row["quantity"] = quantity
            row["product"] = "CNC"
            row["average_price"] = average_price
            row["pnl"] = round(pnl, 2)

            row["pnlpercent"] = (
                round((pnl / (average_price * quantity)) * 100, 2)
                if average_price and quantity
                else 0.0
            )

        except Exception:
            logger.exception("Error mapping ICICI holding row")

    return holdings


def calculate_portfolio_statistics(holdings):
    statistics = {
        "totalholdingvalue": 0.0,
        "totalinvvalue": 0.0,
        "totalprofitandloss": 0.0,
        "totalpnlpercentage": 0.0,
    }

    total_investment = 0.0

    for row in holdings:
        quantity = _to_int(row.get("quantity", 0))

        average_price = _to_float(row.get("average_price", 0.0))

        investment = quantity * average_price

        statistics["totalinvvalue"] += investment

        statistics["totalprofitandloss"] += _to_float(row.get("pnl", 0.0))

        # Market value when an LTP accompanied the row, else cost value.
        statistics["totalholdingvalue"] += (
            quantity * _to_float(row.get("ltp", 0.0)) or investment
        )

        total_investment += investment

    if total_investment:
        statistics["totalpnlpercentage"] = round(
            (statistics["totalprofitandloss"] / total_investment) * 100, 2
        )

    for key in ("totalholdingvalue", "totalinvvalue", "totalprofitandloss"):
        statistics[key] = round(statistics[key], 2)

    return statistics


def transform_holdings_data(holdings):
    transformed = []

    for row in holdings:
        transformed.append(
            {
                "symbol": row.get("symbol", ""),
                "exchange": row.get("exchange", ""),
                "quantity": row.get("quantity", 0),
                "product": row.get("product", "CNC"),
                "average_price": row.get("average_price", 0.0),
                "pnl": row.get("pnl", 0.0),
                "pnlpercent": row.get("pnlpercent", 0.0),
            }
        )

    return transformed
