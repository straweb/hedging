# api/data.py
# Market data for the ICICI Direct Breeze API.
#
# Breeze enforces roughly one request per second on its quote and historical
# chart endpoints, so every call passes through the shared rate limiter with
# broker backpressure (BrokerBusyError under the gthread worker), matching
# the dhan plugin's behaviour.

import calendar
import json
import threading
import time
from datetime import datetime, timedelta

import pandas as pd

from broker.icici.api.breeze_client import is_success, request_raw
from broker.icici.mapping.transform_data import get_breeze_params
from database.token_db import get_token
from utils.broker_backpressure import BrokerBusyError, check_queue_wait
from utils.logging import get_logger

logger = get_logger(__name__)

# Rate limiter: Breeze allows ~1 req/s on quotes and historical charts.
_rate_limit_lock = threading.Lock()
_last_api_call_time = {"quote": 0.0, "history": 0.0}
ICICI_QUOTE_INTERVAL = 1.05
ICICI_HISTORY_INTERVAL = 1.05


def _apply_rate_limit(category="quote"):
    """Per-category spacing so bursts queue instead of erroring.

    Raises BrokerBusyError under the gthread worker when the reserved slot is
    too far away; unbounded otherwise (eventlet / dev server).
    """
    global _last_api_call_time
    interval = ICICI_HISTORY_INTERVAL if category == "history" else ICICI_QUOTE_INTERVAL
    sleep_time = 0

    with _rate_limit_lock:
        current_time = time.time()
        time_since_last_call = current_time - _last_api_call_time[category]
        if time_since_last_call < interval:
            sleep_time = interval - time_since_last_call
        check_queue_wait(sleep_time, "data")
        _last_api_call_time[category] = current_time + sleep_time

    if sleep_time > 0:
        logger.debug(
            f"Rate limiting ({category}): sleeping {sleep_time:.2f}s before Breeze API call"
        )
        time.sleep(sleep_time)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class BrokerData:
    def __init__(self, auth_token):
        """Initialize the ICICI Direct data handler with the session token."""
        self.auth_token = auth_token
        # Breeze intraday resolutions. intervals_service exposes these keys.
        self.timeframe_map = {
            "1m": "1minute",
            "5m": "5minute",
            "30m": "30minute",
            "D": "1day",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _quotes_payload(self, symbol, exchange):
        """Build the quotes request body for an OpenAlgo symbol."""
        params = get_breeze_params(symbol, exchange)
        body = {
            "stock_code": params["stock_code"],
            "exchange_code": params["exchange_code"],
        }
        if params["product_type"]:
            body["product_type"] = params["product_type"]
        if params["expiry_date"]:
            body["expiry_date"] = params["expiry_date"]
        if params["right"]:
            body["right"] = params["right"]
        if params["strike_price"]:
            body["strike_price"] = params["strike_price"]
        return params, body

    @staticmethod
    def _extract_quote_row(data, exchange_code):
        """Pick the quote row out of a Breeze quotes response.

        BSE responses carry a preamble row, so the SDK reads data[1] there;
        everywhere else data[0] is the instrument.
        """
        rows = data.get("Success")
        if isinstance(rows, dict):
            return rows
        if isinstance(rows, list) and rows:
            if exchange_code.upper() == "BSE" and len(rows) > 1:
                return rows[1]
            return rows[0]
        return None

    @staticmethod
    def _empty_history():
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "oi"]
        )

    @staticmethod
    def _parse_breeze_datetime(value, is_daily):
        """Breeze datetime ("2025-02-03T09:21:00.000Z") -> IST epoch seconds."""
        text = str(value or "")
        try:
            if is_daily:
                # Anchor daily bars to IST midnight of the bar's date.
                day = datetime.strptime(text[:10], "%Y-%m-%d")
                return int(day.timestamp() + 19800)
            utc_dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
            return int(calendar.timegm(utc_dt.timetuple())) + 19800
        except (ValueError, TypeError):
            return None

    def _history_chunk(self, params, interval, start_date, end_date):
        """One historical-charts request for a date range; returns a DataFrame."""
        body = {
            "interval": interval,
            "from_date": start_date,
            "to_date": end_date,
            "stock_code": params["stock_code"],
            "exchange_code": params["exchange_code"],
        }
        if params["product_type"]:
            body["product_type"] = params["product_type"]
        if params["expiry_date"]:
            body["expiry_date"] = params["expiry_date"]
        if params["right"]:
            body["right"] = params["right"]
        if params["strike_price"]:
            body["strike_price"] = params["strike_price"]

        _apply_rate_limit("history")
        data = request_raw(
            "historicalcharts", self.auth_token, "GET", json.dumps(body, separators=(",", ":"))
        )[1]

        if not is_success(data):
            error = data.get("Error") if isinstance(data, dict) else str(data)
            raise Exception(f"Error fetching historical data: {error or 'unknown error'}")

        rows = data.get("Success") or []
        is_daily = interval == "day"

        records = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            timestamp = self._parse_breeze_datetime(row.get("datetime"), is_daily)
            if timestamp is None:
                continue
            records.append(
                {
                    "timestamp": timestamp,
                    "open": _num(row.get("open")),
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "close": _num(row.get("close")),
                    "volume": _num(row.get("volume")),
                    "oi": _num(row.get("open_interest") or row.get("oi")),
                }
            )

        return pd.DataFrame(records, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_history(self, symbol, exchange, interval, start_date, end_date):
        """Historical bars as the shared DataFrame contract.

        Breeze caps intraday ranges, so intraday windows are fetched in
        chunks (5-day for 1-minute, 30-day for 5/30-minute) and merged.
        """
        try:
            if interval not in self.timeframe_map:
                raise Exception(
                    f"Unsupported interval '{interval}' for ICICI Direct. "
                    f"Supported: {', '.join(self.timeframe_map.keys())}"
                )

            params = get_breeze_params(symbol, exchange)
            breeze_interval = self.timeframe_map[interval]
            # The v1 endpoint expects minute/day spellings (see the SDK).
            body_interval = {"1minute": "minute", "1day": "day"}.get(breeze_interval, breeze_interval)
            is_daily = body_interval == "day"

            if isinstance(start_date, str):
                start = datetime.strptime(start_date, "%Y-%m-%d")
            else:
                start = datetime.combine(start_date, datetime.min.time())
            if isinstance(end_date, str):
                end = datetime.strptime(end_date, "%Y-%m-%d")
            else:
                end = datetime.combine(end_date, datetime.min.time())

            frames = []
            if is_daily:
                frames.append(
                    self._history_chunk(
                        params,
                        "day",
                        start.strftime("%Y-%m-%dT00:00:00.000Z"),
                        end.strftime("%Y-%m-%dT23:59:59.000Z"),
                    )
                )
            else:
                chunk_days = 5 if body_interval == "minute" else 30
                cursor = start
                while cursor <= end:
                    chunk_end = min(cursor + timedelta(days=chunk_days), end)
                    frames.append(
                        self._history_chunk(
                            params,
                            body_interval,
                            cursor.strftime("%Y-%m-%dT03:45:00.000Z"),
                            chunk_end.strftime("%Y-%m-%dT10:00:00.000Z"),
                        )
                    )
                    cursor = chunk_end + timedelta(days=1)

            df = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
                not f.empty for f in frames
            ) else self._empty_history()

            if df.empty:
                return self._empty_history()

            df = df.drop_duplicates(subset="timestamp", keep="last")
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df

        except BrokerBusyError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Breeze history for {symbol}/{exchange}: {e}")
            raise Exception(f"Error fetching historical data: {str(e)}") from e

    def get_quotes(self, symbol, exchange):
        """Latest quote in the shared quotes contract (raises on API error)."""
        try:
            params, body = self._quotes_payload(symbol, exchange)

            _apply_rate_limit("quote")
            data = request_raw(
                "quotes", self.auth_token, "GET", json.dumps(body, separators=(",", ":"))
            )[1]

            if not is_success(data):
                error = data.get("Error") if isinstance(data, dict) else str(data)
                raise Exception(f"Error fetching quotes: {error or 'unknown error'}")

            row = self._extract_quote_row(data, params["exchange_code"])
            if row is None:
                return {
                    "ltp": 0.0,
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "volume": 0,
                    "oi": 0,
                    "bid": 0.0,
                    "ask": 0.0,
                    "prev_close": 0.0,
                }

            return {
                "ltp": _num(row.get("ltp")),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "volume": int(_num(row.get("total_quantity_traded") or row.get("volumn") or row.get("volume"))),
                "oi": int(_num(row.get("oi") or row.get("open_interest"))),
                "bid": _num(row.get("best_bid_price")),
                "ask": _num(row.get("best_offer_price")),
                "prev_close": _num(row.get("previous_close")),
            }

        except BrokerBusyError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Breeze quotes for {symbol}/{exchange}: {e}")
            raise Exception(f"Error fetching quotes: {str(e)}") from e

    def get_multiquotes(self, symbols):
        """Sequential quotes for a symbol list (Breeze has no batch endpoint).

        Returns one entry per symbol: {symbol, exchange, data:{...}} on
        success or {symbol, exchange, error} on failure.
        """
        results = []
        for item in symbols or []:
            symbol = item.get("symbol")
            exchange = item.get("exchange")
            try:
                quote = self.get_quotes(symbol, exchange)
                results.append({"symbol": symbol, "exchange": exchange, "data": quote})
            except BrokerBusyError:
                raise
            except Exception as e:
                results.append({"symbol": symbol, "exchange": exchange, "error": str(e)})
        return results

    def get_depth(self, symbol, exchange):
        """Top-of-book depth padded to five levels (Breeze REST has one level)."""
        try:
            params, body = self._quotes_payload(symbol, exchange)

            _apply_rate_limit("quote")
            data = request_raw(
                "quotes", self.auth_token, "GET", json.dumps(body, separators=(",", ":"))
            )[1]

            if not is_success(data):
                error = data.get("Error") if isinstance(data, dict) else str(data)
                raise Exception(f"Error fetching depth: {error or 'unknown error'}")

            row = self._extract_quote_row(data, params["exchange_code"])
            if row is None:
                raise Exception(f"No depth data returned for {symbol} on {exchange}")

            bids = [
                {
                    "price": _num(row.get("best_bid_price")),
                    "quantity": int(_num(row.get("best_bid_quantity"))),
                }
            ]
            asks = [
                {
                    "price": _num(row.get("best_offer_price")),
                    "quantity": int(_num(row.get("best_offer_quantity"))),
                }
            ]
            while len(bids) < 5:
                bids.append({"price": 0.0, "quantity": 0})
            while len(asks) < 5:
                asks.append({"price": 0.0, "quantity": 0})

            return {
                "bids": bids,
                "asks": asks,
                "ltp": _num(row.get("ltp")),
                "ltq": int(_num(row.get("ltq"))),
                "volume": int(_num(row.get("total_quantity_traded") or row.get("volumn") or row.get("volume"))),
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "prev_close": _num(row.get("previous_close")),
                "oi": int(_num(row.get("oi") or row.get("open_interest"))),
                "totalbuyqty": int(_num(row.get("best_bid_quantity"))),
                "totalsellqty": int(_num(row.get("best_offer_quantity"))),
            }

        except BrokerBusyError:
            raise
        except Exception as e:
            logger.error(f"Error fetching Breeze depth for {symbol}/{exchange}: {e}")
            raise Exception(f"Error fetching market depth: {str(e)}") from e
