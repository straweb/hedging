"""
broker/icici/api/data.py

ICICI Direct Breeze market-data implementation for OpenAlgo.

This module follows the same public BrokerData contract used by the other
OpenAlgo broker integrations.

Public normalized methods:

    get_quotes()
    get_multiquotes()
    get_depth()
    get_market_depth()
    get_history()
    get_supported_intervals()

OpenAlgo-normalized quote format:

    {
        "ask": float,
        "bid": float,
        "high": float,
        "low": float,
        "ltp": float,
        "open": float,
        "prev_close": float,
        "volume": int,
        "oi": int,
    }

OpenAlgo-normalized depth format:

    {
        "asks": [
            {"price": float, "quantity": int},
            ...
        ],
        "bids": [
            {"price": float, "quantity": int},
            ...
        ],
        "high": float,
        "low": float,
        "ltp": float,
        "ltq": int,
        "oi": int,
        "open": float,
        "prev_close": float,
        "totalbuyqty": int,
        "totalsellqty": int,
        "volume": int,
    }

OpenAlgo-normalized historical format:

    pandas.DataFrame(
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "oi",
        ]
    )

Historical timestamps are epoch seconds, matching the convention used by
other OpenAlgo broker integrations.

Breeze-specific response formats are kept internal to this module.
"""

from datetime import date, datetime, time, timezone

import pandas as pd

from broker.icici.api.breeze_client import get_breeze_client
from database.token_db import get_br_symbol
from utils.logging import get_logger


logger = get_logger(__name__)


class ICICIBreezeAPIError(Exception):
    """Normalized ICICI Breeze market-data exception."""

    pass


# ----------------------------------------------------------------------
# Symbol / exchange helpers
# ----------------------------------------------------------------------


def _broker_symbol(symbol, exchange):
    """
    Convert OpenAlgo symbol to ICICI Breeze stock code.

    Falls back to the supplied OpenAlgo symbol when no mapping exists.
    """

    return get_br_symbol(symbol, exchange) or symbol


def _exchange_code(exchange):
    """
    Convert OpenAlgo exchange name to Breeze exchange code.
    """

    value = str(exchange or "").strip().upper()

    mapping = {
        "NSE": "nse",
        "BSE": "bse",
        "NFO": "nfo",
        "BFO": "bfo",
        "MCX": "mcx",
        "CDS": "cds",
        "NSE_INDEX": "nse",
        "BSE_INDEX": "bse",
        "MCX_INDEX": "mcx",
    }

    return mapping.get(value, value.lower())


def _openalgo_exchange(exchange):
    """
    Normalize exchange to OpenAlgo uppercase form.
    """

    return str(exchange or "").strip().upper()


# ----------------------------------------------------------------------
# Generic Breeze response helpers
# ----------------------------------------------------------------------


def _success_list(response):
    """
    Return Breeze Success as a list.

    Breeze APIs may return:

        "Success": [{...}, {...}]

    or:

        "Success": {...}

    """

    if not isinstance(response, dict):
        return []

    success = response.get("Success")

    if isinstance(success, list):
        return success

    if isinstance(success, dict):
        return [success]

    return []


def _success_data(response):
    """
    Return the first Breeze Success object.
    """

    success = _success_list(response)

    if success:
        return success[0]

    return {}


def _raise_breeze_error(response, default_message):
    """
    Convert a Breeze error response into ICICIBreezeAPIError.
    """

    if not isinstance(response, dict):
        raise ICICIBreezeAPIError(default_message)

    error = response.get("Error")

    if error:
        raise ICICIBreezeAPIError(str(error))

    status = response.get("Status")

    if status not in (None, 200, "200"):
        raise ICICIBreezeAPIError(
            f"ICICI Breeze request failed with status {status}"
        )

    raise ICICIBreezeAPIError(default_message)


def _number(value, default=0.0):
    """
    Safely convert a broker value to float.
    """

    try:
        if value is None:
            return float(default)

        if isinstance(value, str):
            value = value.strip()

            if not value:
                return float(default)

        return float(value)

    except (TypeError, ValueError):
        return float(default)


def _integer(value, default=0):
    """
    Safely convert a broker value to int.
    """

    try:
        return int(float(_number(value, default)))

    except (TypeError, ValueError):
        return int(default)


def _first_value(data, *keys, default=None):
    """
    Return the first non-empty value from a dictionary.
    """

    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)

        if value is not None and value != "":
            return value

    return default


# ----------------------------------------------------------------------
# Historical-data helpers
# ----------------------------------------------------------------------


def _historical_product_type(exchange, product=None):
    """
    Determine Breeze Historical Data V2 product type.

    Breeze requires:

        cash
        futures
        options
    """

    if product:
        value = str(product).strip().lower()

        product_map = {
            "cash": "cash",
            "equity": "cash",
            "cnc": "cash",

            "futures": "futures",
            "future": "futures",
            "fut": "futures",
            "nrml": "futures",

            "options": "options",
            "option": "options",
            "opt": "options",
        }

        mapped = product_map.get(value)

        if mapped:
            return mapped

    exchange_value = _openalgo_exchange(exchange)

    if exchange_value in (
        "NSE",
        "BSE",
        "NSE_INDEX",
        "BSE_INDEX",
    ):
        return "cash"

    if exchange_value in (
        "NFO",
        "BFO",
    ):
        return "options"

    if exchange_value in (
        "MCX",
        "MCX_INDEX",
    ):
        return "futures"

    return "cash"


def _historical_interval(interval):
    """
    Convert OpenAlgo timeframe to Breeze Historical Data V2 interval.
    """

    value = str(interval or "").strip().lower()

    mapping = {
        "1s": "1second",
        "1sec": "1second",
        "1second": "1second",

        "1m": "1minute",
        "1min": "1minute",
        "1minute": "1minute",

        "3m": "3minute",
        "3min": "3minute",
        "3minute": "3minute",

        "5m": "5minute",
        "5min": "5minute",
        "5minute": "5minute",

        "10m": "10minute",
        "10min": "10minute",
        "10minute": "10minute",

        "15m": "15minute",
        "15min": "15minute",
        "15minute": "15minute",

        "30m": "30minute",
        "30min": "30minute",
        "30minute": "30minute",

        "1h": "1hour",
        "1hr": "1hour",
        "1hour": "1hour",

        "d": "1day",
        "day": "1day",
        "1d": "1day",
        "1day": "1day",
    }

    return mapping.get(value, value)


def _normalize_historical_date(value, end_of_day=False):
    """
    Convert an OpenAlgo date/time value to a Breeze-compatible datetime.

    The Breeze client accepts datetime values, but normalization here ensures
    that all broker entry points use the same date semantics.

    Date-only start:
        YYYY-MM-DD 00:00:00

    Date-only end:
        YYYY-MM-DD 23:59:59

    Timezone-aware values are converted to UTC and made naive.
    """

    if value is None:
        raise ValueError("Historical date cannot be None")

    if isinstance(value, datetime):
        parsed = value

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

        if end_of_day and parsed.time() == time(0, 0, 0):
            parsed = parsed.replace(
                hour=23,
                minute=59,
                second=59,
                microsecond=0,
            )

        return parsed

    if isinstance(value, date):
        return datetime.combine(
            value,
            time(23, 59, 59) if end_of_day else time(0, 0, 0),
        )

    text = str(value).strip().strip("\"'")

    if not text:
        raise ValueError("Historical date cannot be empty")

    iso_text = text

    if iso_text.endswith("Z"):
        iso_text = iso_text[:-1] + "+00:00"

    parsed = None

    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    if parsed is None:
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%d-%m-%Y",
            "%d/%m/%Y",

            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d %H:%M:%S",

            "%d-%m-%Y %H:%M",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %H:%M:%S",

            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ]

        for date_format in formats:
            try:
                parsed = datetime.strptime(text, date_format)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError(
            f"Unsupported historical date format: {value!r}. "
            "Expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
        )

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    if end_of_day and parsed.time() == time(0, 0, 0):
        parsed = parsed.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )

    return parsed


def _historical_request_dates(from_date, to_date):
    """
    Normalize a historical request window.

    OpenAlgo semantics:

    - start date begins at 00:00
    - end date ends at 23:59 for completed days
    - today's end is clamped to the current time
    """

    start_date = _normalize_historical_date(
        from_date,
        end_of_day=False,
    )

    end_date = _normalize_historical_date(
        to_date,
        end_of_day=True,
    )

    now = datetime.now()

    if end_date.date() == now.date():
        end_date = now.replace(
            second=0,
            microsecond=0,
        )

    return start_date, end_date


# ----------------------------------------------------------------------
# Historical candle normalization
# ----------------------------------------------------------------------


def _normalize_candle(item):
    """
    Convert one Breeze candle into the internal normalized representation.

    Breeze commonly returns:

        {
            "datetime": "...",
            "open": "...",
            "high": "...",
            "low": "...",
            "close": "...",
            "volume": "...",
            ...
        }
    """

    if not isinstance(item, dict):
        return None

    timestamp = _first_value(
        item,
        "datetime",
        "timestamp",
        "time",
        "date",
    )

    if timestamp is None:
        return None

    # if _integer(
    #         _first_value(
    #             item,
    #             "volume",
    #             "total_quantity_traded",
    #             "ttq",
    #         )) == 0:
    logger.warning("history item %s", item)
    return {
        "timestamp": timestamp,
        "open": _number(
            _first_value(item, "open"),
        ),
        "high": _number(
            _first_value(item, "high"),
        ),
        "low": _number(
            _first_value(item, "low"),
        ),
        "close": _number(
            _first_value(item, "close"),
        ),
        "volume": _integer(
            _first_value(
                item,
                "volume",
                "total_quantity_traded",
                "ttq",
            ),
        ),
        "oi": _integer(
            _first_value(
                item,
                "open_interest",
                "oi",
                "OI",
            ),
        ),
    }


def _timestamp_to_epoch(value):
    """
    Convert a Breeze timestamp into epoch seconds.

    Breeze timestamps can arrive as strings such as:

        2026-10-04 09:15:00
        2026-10-04T09:15:00
        2026-10-04T09:15:00.000Z

    Intraday timestamps without timezone are assumed to be IST, which is
    consistent with Indian-market broker candle timestamps.
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    if isinstance(value, pd.Timestamp):
        parsed = value.to_pydatetime()
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()

        if not text:
            return None

        # Breeze occasionally uses a trailing Z.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = None

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            pass

        if parsed is None:
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%d-%m-%Y",
                "%d/%m/%Y",
            ]

            for date_format in formats:
                try:
                    parsed = datetime.strptime(
                        text,
                        date_format,
                    )
                    break
                except ValueError:
                    continue

        if parsed is None:
            parsed = pd.to_datetime(
                value,
                errors="coerce",
            )

            if pd.isna(parsed):
                return None

            parsed = parsed.to_pydatetime()

    if parsed.tzinfo is None:
        # Broker timestamps are Indian-market local time.
        try:
            from zoneinfo import ZoneInfo

            parsed = parsed.replace(
                tzinfo=ZoneInfo("Asia/Kolkata")
            )
        except Exception:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

    return int(parsed.timestamp())


def _candles_to_dataframe(candles):
    """
    Convert normalized candle dictionaries to the OpenAlgo DataFrame format.
    """

    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "oi",
    ]

    if not candles:
        return pd.DataFrame(columns=columns)

    rows = []

    for candle in candles:
        if not isinstance(candle, dict):
            continue

        timestamp = _timestamp_to_epoch(
            candle.get("timestamp")
        )

        if timestamp is None:
            continue

        rows.append(
            {
                "timestamp": timestamp,
                "open": _number(candle.get("open")),
                "high": _number(candle.get("high")),
                "low": _number(candle.get("low")),
                "close": _number(candle.get("close")),
                "volume": _integer(candle.get("volume")),
                "oi": _integer(candle.get("oi")),
            }
        )

    if not rows:
        return pd.DataFrame(columns=columns)

    dataframe = pd.DataFrame(
        rows,
        columns=columns,
    )

    dataframe["timestamp"] = pd.to_numeric(
        dataframe["timestamp"],
        errors="coerce",
    )

    dataframe = dataframe.dropna(
        subset=["timestamp"]
    )

    dataframe["timestamp"] = dataframe["timestamp"].astype(
        "int64"
    )

    for column in (
        "open",
        "high",
        "low",
        "close",
    ):
        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        ).fillna(0.0).astype(float)

    dataframe["volume"] = pd.to_numeric(
        dataframe["volume"],
        errors="coerce",
    ).fillna(0).astype("int64")

    dataframe["oi"] = pd.to_numeric(
        dataframe["oi"],
        errors="coerce",
    ).fillna(0).astype("int64")

    dataframe = (
        dataframe
        .sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return dataframe[columns]


# ----------------------------------------------------------------------
# Market-depth helpers
# ----------------------------------------------------------------------


def _depth_side(depth, side):
    """
    Extract a Breeze depth side.

    Breeze responses have appeared in different forms across API versions,
    therefore this helper accepts several common field names.
    """

    if not isinstance(depth, dict):
        return []

    if side == "buy":
        return (
            depth.get("buy")
            or depth.get("bids")
            or depth.get("buy_side")
            or depth.get("Buy")
            or []
        )

    return (
        depth.get("sell")
        or depth.get("asks")
        or depth.get("sell_side")
        or depth.get("Sell")
        or []
    )


def _normalize_depth_levels(levels):
    """
    Normalize a depth side to exactly five OpenAlgo levels.

    OpenAlgo expects:

        {"price": float, "quantity": int}
    """

    result = []

    for item in (levels or [])[:5]:
        if not isinstance(item, dict):
            continue

        price = _first_value(
            item,
            "price",
            "buy_price",
            "sell_price",
            "bid_price",
            "ask_price",
        )

        quantity = _first_value(
            item,
            "quantity",
            "buy_quantity",
            "sell_quantity",
            "bid_quantity",
            "ask_quantity",
        )

        result.append(
            {
                "price": _number(price),
                "quantity": _integer(quantity),
            }
        )

    while len(result) < 5:
        result.append(
            {
                "price": 0.0,
                "quantity": 0,
            }
        )

    return result


# ----------------------------------------------------------------------
# BrokerData
# ----------------------------------------------------------------------


class BrokerData:
    """
    ICICI Direct Breeze OpenAlgo market-data handler.

    The class exposes the same normalized public data interface as the other
    OpenAlgo broker integrations.
    """

    def __init__(self, auth_token):
        self.auth_token = auth_token

        # intervals_service reads this mapping.
        #
        # Keep OpenAlgo timeframe names as the keys and Breeze intervals as
        # values.
        self.timeframe_map = {
            "1s": "1second",
            "1sec": "1second",
            "1second": "1second",

            "1m": "1minute",
            "1min": "1minute",
            "1minute": "1minute",

            "3m": "3minute",
            "3min": "3minute",
            "3minute": "3minute",

            "5m": "5minute",
            "5min": "5minute",
            "5minute": "5minute",

            "10m": "10minute",
            "10min": "10minute",
            "10minute": "10minute",

            "15m": "15minute",
            "15min": "15minute",
            "15minute": "15minute",

            "30m": "30minute",
            "30min": "30minute",
            "30minute": "30minute",

            "1h": "1hour",
            "1hr": "1hour",
            "1hour": "1hour",

            "D": "1day",
            "d": "1day",
            "1d": "1day",
            "day": "1day",
            "1day": "1day",
        }

    # ------------------------------------------------------------------
    # Internal Breeze client
    # ------------------------------------------------------------------

    def _client(self):
        """
        Return the authenticated Breeze client.
        """

        return get_breeze_client(
            self.auth_token
        )

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quotes(self, symbol, exchange):
        """
        Return a normalized OpenAlgo quote.

        Returns:

            {
                "ask": float,
                "bid": float,
                "high": float,
                "low": float,
                "ltp": float,
                "open": float,
                "prev_close": float,
                "volume": int,
                "oi": int,
            }
        """

        try:
            broker_symbol = _broker_symbol(
                symbol,
                exchange,
            )

            response = self._client().get_quotes(
                stock_code=broker_symbol,
                exchange_code=_exchange_code(exchange),
            )

            if not isinstance(response, dict):
                raise ICICIBreezeAPIError(
                    "Invalid ICICI Breeze quote response"
                )

            success = _success_data(response)

            if not success:
                _raise_breeze_error(
                    response,
                    "ICICI Breeze returned no quote data",
                )

            return {
                "ask": _number(
                    _first_value(
                        success,
                        "best_offer_price",
                        "sPrice",
                        "ask",
                    )
                ),
                "bid": _number(
                    _first_value(
                        success,
                        "best_bid_price",
                        "bPrice",
                        "bid",
                    )
                ),
                "high": _number(
                    _first_value(
                        success,
                        "high",
                    )
                ),
                "low": _number(
                    _first_value(
                        success,
                        "low",
                    )
                ),
                "ltp": _number(
                    _first_value(
                        success,
                        "ltp",
                        "last",
                    )
                ),
                "open": _number(
                    _first_value(
                        success,
                        "open",
                    )
                ),
                "prev_close": _number(
                    _first_value(
                        success,
                        "previous_close",
                        "prev_close",
                        "close",
                    )
                ),
                "volume": _integer(
                    _first_value(
                        success,
                        "total_quantity_traded",
                        "volume",
                        "ttq",
                    )
                ),
                "oi": _integer(
                    _first_value(
                        success,
                        "open_interest",
                        "oi",
                        "OI",
                    )
                ),
            }

        except ICICIBreezeAPIError:
            raise

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze quotes: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching quotes: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Multiple quotes
    # ------------------------------------------------------------------

    def get_multiquotes(self, symbols):
        """
        Return normalized quotes for multiple instruments.

        Input:

            [
                {
                    "symbol": "SBIN",
                    "exchange": "NSE",
                },
                ...
            ]

        Output:

            [
                {
                    "symbol": "SBIN",
                    "exchange": "NSE",
                    "data": {...},
                },
                ...
            ]

        A failed individual instrument does not discard the other results.
        """

        results = []

        for item in symbols:
            symbol = item.get("symbol")
            exchange = item.get("exchange")

            try:
                data = self.get_quotes(
                    symbol,
                    exchange,
                )

                results.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "data": data,
                    }
                )

            except Exception as exc:
                logger.warning(
                    "ICICI multiquote failed for %s:%s: %s",
                    exchange,
                    symbol,
                    exc,
                )

                results.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "error": str(exc),
                    }
                )

        return results

    # ------------------------------------------------------------------
    # Market depth
    # ------------------------------------------------------------------

    def get_depth(self, symbol, exchange):
        """
        Return normalized five-level OpenAlgo market depth.

        Breeze quote/depth responses differ between instrument types and API
        versions, so missing depth fields are zero-filled rather than changing
        the OpenAlgo response structure.
        """

        try:
            broker_symbol = _broker_symbol(
                symbol,
                exchange,
            )

            response = self._client().get_quotes(
                stock_code=broker_symbol,
                exchange_code=_exchange_code(exchange),
            )

            if not isinstance(response, dict):
                raise ICICIBreezeAPIError(
                    "Invalid ICICI Breeze market-depth response"
                )

            success = _success_data(response)

            if not success:
                _raise_breeze_error(
                    response,
                    "ICICI Breeze returned no market-depth data",
                )

            depth = (
                success.get("depth")
                or success.get("market_depth")
                or success.get("marketDepth")
                or {}
            )

            buy_levels = _depth_side(
                depth,
                "buy",
            )

            sell_levels = _depth_side(
                depth,
                "sell",
            )

            # Some Breeze responses expose the depth arrays directly on the
            # quote object rather than under "depth".
            if not buy_levels:
                buy_levels = (
                    success.get("buy")
                    or success.get("bids")
                    or []
                )

            if not sell_levels:
                sell_levels = (
                    success.get("sell")
                    or success.get("asks")
                    or []
                )

            bid_quantity = sum(
                level.get("quantity", 0)
                for level in _normalize_depth_levels(
                    buy_levels
                )
                if isinstance(level, dict)
            )

            ask_quantity = sum(
                level.get("quantity", 0)
                for level in _normalize_depth_levels(
                    sell_levels
                )
                if isinstance(level, dict)
            )

            quote = self.get_quotes(
                symbol,
                exchange,
            )

            ltq = _integer(
                _first_value(
                    success,
                    "last_traded_quantity",
                    "last_traded_qty",
                    "ltq",
                    "last_quantity",
                )
            )

            total_buy_quantity = _integer(
                _first_value(
                    success,
                    "total_buy_quantity",
                    "total_buy_qty",
                    "totalbuyqty",
                ),
                    bid_quantity,
            )

            total_sell_quantity = _integer(
                _first_value(
                    success,
                    "total_sell_quantity",
                    "total_sell_qty",
                    "totalsellqty",
                ),
                    ask_quantity,
            )

            return {
                "asks": _normalize_depth_levels(
                    sell_levels
                ),
                "bids": _normalize_depth_levels(
                    buy_levels
                ),
                "high": quote["high"],
                "low": quote["low"],
                "ltp": quote["ltp"],
                "ltq": ltq,
                "oi": quote["oi"],
                "open": quote["open"],
                "prev_close": quote["prev_close"],
                "totalbuyqty": total_buy_quantity,
                "totalsellqty": total_sell_quantity,
                "volume": quote["volume"],
            }

        except ICICIBreezeAPIError:
            raise

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze market depth: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching market depth: {exc}"
            ) from exc

    def get_market_depth(self, symbol, exchange):
        """
        Alias for get_depth.

        Kept for parity with brokers that expose get_market_depth().
        """

        return self.get_depth(
            symbol,
            exchange,
        )

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    def _fetch_historical_data(
        self,
        symbol,
        exchange,
        interval,
        from_date,
        to_date,
        product=None,
        expiry_date=None,
        right=None,
        strike_price=None,
    ):
        """
        Internal Breeze Historical Data V2 request.

        This method returns the raw Breeze response internally.
        Public get_history() always returns the normalized DataFrame.
        """

        broker_symbol = _broker_symbol(
            symbol,
            exchange,
        )

        breeze_interval = _historical_interval(
            interval
        )

        product_type = _historical_product_type(
            exchange,
            product,
        )

        start_date, end_date = _historical_request_dates(
            from_date,
            to_date,
        )

        logger.debug(
            "ICICI Breeze history request: "
            "symbol=%s broker_symbol=%s exchange=%s "
            "interval=%s from=%s to=%s product_type=%s",
            symbol,
            broker_symbol,
            _exchange_code(exchange),
            breeze_interval,
            start_date,
            end_date,
            product_type,
        )

        response = self._client().get_historical_data_v2(
            interval=breeze_interval,
            from_date=start_date,
            to_date=end_date,
            stock_code=broker_symbol,
            exchange_code=_exchange_code(exchange),
            product_type=product_type,
            expiry_date=expiry_date or "",
            right=right or "",
            strike_price=strike_price or "",
        )

        if not isinstance(response, dict):
            raise ICICIBreezeAPIError(
                "Invalid ICICI Breeze historical-data response"
            )

        return response

    def get_history(
        self,
        symbol,
        exchange,
        timeframe,
        from_date,
        to_date,
    ):
        """
        Return normalized OpenAlgo historical data.

        Output:

            DataFrame[
                timestamp,
                open,
                high,
                low,
                close,
                volume,
                oi
            ]

        timestamp is epoch seconds.
        """

        try:
            if timeframe not in self.timeframe_map:
                raise ICICIBreezeAPIError(
                    f"Unsupported timeframe: {timeframe}. "
                    f"Supported: {', '.join(self.get_supported_intervals())}"
                )

            response = self._fetch_historical_data(
                symbol=symbol,
                exchange=exchange,
                interval=timeframe,
                from_date=from_date,
                to_date=to_date,
            )

            success = _success_list(
                response
            )

            if not success:
                error = response.get("Error")

                if error:
                    raise ICICIBreezeAPIError(
                        str(error)
                    )

                return pd.DataFrame(
                    columns=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "oi",
                    ]
                )

            candles = []

            for item in success:
                candle = _normalize_candle(
                    item
                )

                if candle:
                    candles.append(candle)

            dataframe = _candles_to_dataframe(
                candles
            )

            logger.debug(
                "ICICI Breeze history response: "
                "symbol=%r exchange=%r timeframe=%r rows=%d",
                symbol,
                exchange,
                timeframe,
                len(dataframe),
            )

            return dataframe

        except ICICIBreezeAPIError:
            raise

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze historical data: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching historical data: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Direct historical-data compatibility
    # ------------------------------------------------------------------

    def get_historical_data(
        self,
        symbol,
        exchange,
        interval,
        from_date,
        to_date,
        product=None,
        expiry_date=None,
        right=None,
        strike_price=None,
    ):
        """
        Direct normalized historical-data method.

        Unlike the old implementation, this does NOT expose the raw Breeze
        response. It returns the same normalized DataFrame format as
        get_history().

        Derivative callers can explicitly provide product/expiry/right/strike.
        """

        try:
            response = self._fetch_historical_data(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                product=product,
                expiry_date=expiry_date,
                right=right,
                strike_price=strike_price,
            )

            success = _success_list(
                response
            )

            if not success:
                error = response.get("Error")

                if error:
                    raise ICICIBreezeAPIError(
                        str(error)
                    )

                return pd.DataFrame(
                    columns=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "oi",
                    ]
                )

            candles = []

            for item in success:
                candle = _normalize_candle(
                    item
                )

                if candle:
                    candles.append(candle)

            return _candles_to_dataframe(
                candles
            )

        except ICICIBreezeAPIError:
            raise

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze historical data: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching historical data: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Supported intervals
    # ------------------------------------------------------------------

    def get_supported_intervals(self):
        """
        Return OpenAlgo-supported historical intervals.

        Keep this list aligned with the intervals exposed by
        timeframe_map and Breeze Historical Data V2.
        """

        return [
            "1m",
            "5m",
            "30m",
            "1h",
            "D",
        ]

    # ------------------------------------------------------------------
    # Portfolio methods
    # ------------------------------------------------------------------

    def get_positions(self):
        """
        Return ICICI Breeze portfolio positions.

        This remains broker-native because positions are handled by the
        corresponding OpenAlgo order/portfolio normalization layer.
        """

        try:
            return self._client().get_portfolio_position()

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze positions: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching positions: {exc}"
            ) from exc

    def get_holdings(self):
        """
        Return ICICI Breeze portfolio holdings.
        """

        try:
            return self._client().get_portfolio_holding()

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze holdings: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching holdings: {exc}"
            ) from exc

    def get_trade_book(self):
        """
        Return ICICI Breeze trade book.
        """

        try:
            return self._client().get_trade_list()

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze trade book: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching trade book: {exc}"
            ) from exc

    def get_order_book(self):
        """
        Return ICICI Breeze order book.
        """

        try:
            return self._client().get_order_list()

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze order book: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching order book: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------

    def get_option_chain(
        self,
        symbol,
        exchange,
        expiry_date,
        right="",
        strike_price="",
    ):
        """
        Return ICICI Breeze option-chain data.

        This method intentionally keeps the broker-native option-chain
        response because option-chain normalization is handled separately
        from the standard quote/history data contract.
        """

        try:
            broker_symbol = _broker_symbol(
                symbol,
                exchange,
            )

            return self._client().get_option_chain_quotes(
                stock_code=broker_symbol,
                exchange_code=_exchange_code(exchange),
                expiry_date=expiry_date,
                right=right,
                strike_price=strike_price,
            )

        except Exception as exc:
            logger.exception(
                "Error fetching ICICI Breeze option chain: %s",
                exc,
            )

            raise ICICIBreezeAPIError(
                f"Error fetching option chain: {exc}"
            ) from exc
