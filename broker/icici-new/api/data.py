"""
ICICI Direct Breeze market-data APIs for OpenAlgo.

THE INDEX FIX (this revision): OpenAlgo's option tools fetch the
underlying LTP via ``get_quotes("NIFTY", "NSE_INDEX")`` — an OpenAlgo
pseudo-exchange that Breeze does not know. Earlier revisions forwarded it
verbatim, Breeze received an EMPTY exchange_code and answered
"Exchange-code cannot be empty", breaking the option chain (and every
other options tool, which all start from the underlying LTP).

Handled here for EVERY quote/depth/history call:

- exchange translation incl. index pseudo-exchanges
  (NSE_INDEX/BSE_INDEX -> nse/bse, MCX_INDEX -> mcx),
- ICICI's own index vocabulary for the stock code
  (BANKNIFTY -> CNXBAN, FINNIFTY -> NIFFIN, MIDCPNIFTY -> NIFSEL,
  NIFTYNXT50 -> NIFNEX, SENSEX -> BSESEN, SENSEX50 -> BSEFOC),
- a candidate probe (mapped code, then the raw OpenAlgo symbol) with the
  verified code cached per (symbol, exchange), as recommended for index
  quote quirks across the broker family.

Quotes/depth pad best bid/offer to the OpenAlgo 5-level contract (Breeze's
REST quote carries no multi-level ladder; mode-3 streaming depth does).

History uses Breeze's v2 historicalcharts endpoint (1s/1m/5m/30m/D only —
the plugin's timeframe_map offers exactly those) with date chunking and
epoch-seconds timestamps (candle start, IST).
"""

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from broker.icici.api.breeze_client import breeze_request, breeze_request_v2, response_error, response_status
from broker.icici.mapping.transform_data import (
    get_breeze_exchange,
    parse_symbol,
    to_breeze_underlying,
)
from utils.logging import get_logger


logger = get_logger(__name__)


IST = timezone(timedelta(hours=5, minutes=30))

# Verified index/cash stock codes per (exchange, symbol) — avoids re-probing
# candidates on every poll.
_stock_code_cache: dict[tuple[str, str], str] = {}


class ICICIBreezeAPIError(Exception):
    """Raised when Breeze returns no usable quote/depth payload."""


def _success_data(response) -> dict | None:
    """First Success object of a Breeze response (dict or 1-row list)."""

    if not isinstance(response, dict):
        return None

    success = response.get("Success")

    if isinstance(success, dict):
        return success

    if isinstance(success, list) and success:
        if isinstance(success[0], dict):
            return success[0]

    return None


def _num(row: dict, *keys, default=0.0) -> float:
    for key in keys:
        value = row.get(key)

        if value in (None, ""):
            continue

        try:
            return float(value)
        except (TypeError, ValueError):
            continue

    return default


def _int(row: dict, *keys, default=0) -> int:
    for key in keys:
        value = row.get(key)

        if value in (None, ""):
            continue

        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue

    return default


# ---------------------------------------------------------------------------
# BrokerData
# ---------------------------------------------------------------------------


class BrokerData:
    """OpenAlgo market-data facade for ICICI Direct Breeze."""

    def __init__(self, auth_token):
        self.auth_token = auth_token

        # Breeze v2 history serves exactly these intervals.
        self.timeframe_map = {
            "1s": "1second",
            "1m": "1minute",
            "5m": "5minute",
            "30m": "30minute",
            "D": "1day",
        }

    # ------------------------------------------------------------------
    # Stock-code resolution (with index candidates)
    # ------------------------------------------------------------------

    def _resolve_stock_codes(self, symbol: str, exchange: str) -> list[str]:
        """
        Candidate ICICI stock codes for an OpenAlgo symbol, in probe
        order. Cached after the first successful quote.
        """

        cache_key = (str(exchange).upper(), str(symbol).upper())

        cached = _stock_code_cache.get(cache_key)

        if cached:
            return [cached]

        parsed = parse_symbol(symbol, exchange)

        candidates = [parsed["stock_code"]]

        base = str(symbol).upper()

        if base.endswith("-EQ"):
            base = base[:-3]

        if base not in candidates:
            candidates.append(base)

        return candidates

    def _remember_stock_code(
        self, symbol: str, exchange: str, stock_code: str
    ) -> None:
        if len(_stock_code_cache) > 2048:
            _stock_code_cache.clear()

        _stock_code_cache[(str(exchange).upper(), str(symbol).upper())] = (
            stock_code
        )

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def get_quotes(self, symbol, exchange):
        """
        One quote. Returns the OpenAlgo quote dict:

            {ask, bid, high, low, ltp, open, prev_close, volume, oi}
        """

        exchange_u = str(exchange or "").strip().upper()

        parsed = parse_symbol(symbol, exchange_u)

        quote_exchange = get_breeze_exchange(exchange_u)

        last_error = ""

        for stock_code in self._resolve_stock_codes(symbol, exchange_u):
            params = {
                "stock_code": stock_code,
                "exchange_code": quote_exchange,
            }

            if parsed["product"] in ("futures", "options"):
                params["product_type"] = parsed["product"]
                params["expiry_date"] = parsed["expiry_date"]
                params["right"] = parsed["right"] or "others"
                params["strike_price"] = parsed["strike_price"]

            response = breeze_request(self.auth_token, "GET", "quotes", params)

            status = response_status(response)

            if status == 200:
                row = _success_data(response)

                if row:
                    self._remember_stock_code(
                        symbol, exchange_u, stock_code
                    )

                    return self._map_quote(row)

                last_error = "no quote data"

                continue

            last_error = response_error(response) or f"HTTP {status}"

            # Try the next candidate when this code does not exist
            # (exchange-code validation, no-data, not-found). Hard
            # failures (throttle, auth) stop the probe immediately.
            error_lower = last_error.lower()

            if status == 500 and (
                "exchange" in error_lower
                or "no data" in error_lower
                or "not found" in error_lower
                or "no record" in error_lower
            ):
                continue

            break

        raise ICICIBreezeAPIError(
            last_error or f"No quote data for {symbol} on {exchange}"
        )

    @staticmethod
    def _map_quote(row: dict) -> dict:
        ltp = _num(row, "last", "ltp", "last_price", "LTP")

        return {
            "ask": _num(
                row, "best_offer_price", "sPrice", "offer_price", "ask"
            ),
            "bid": _num(row, "best_bid_price", "bPrice", "bid_price", "bid"),
            "high": _num(row, "high", "h"),
            "low": _num(row, "low", "l"),
            "ltp": ltp,
            "open": _num(row, "open", "o"),
            "prev_close": _num(row, "prev_close", "close", "c"),
            "volume": _int(
                row, "volume", "tot_traded_qty", "traded_volume", "v"
            ),
            "oi": _int(row, "oi", "open_interest", "open_interest_lot"),
        }

    # ------------------------------------------------------------------
    # Depth
    # ------------------------------------------------------------------

    def get_depth(self, symbol, exchange):
        """
        5-level depth. The REST quote carries best bid/offer only, so the
        ladder is padded around them (mode-3 streaming supplies the real
        ladder for subscribed symbols).
        """

        quote = self.get_quotes(symbol, exchange)

        ltp = quote.get("ltp") or 0.0

        bids = self._pad_ladder(
            quote.get("bid") or ltp, quote.get("bid") or ltp
        )

        asks = self._pad_ladder(
            quote.get("ask") or ltp, quote.get("ask") or ltp
        )

        return {
            "asks": asks,
            "bids": bids,
            "high": quote.get("high"),
            "low": quote.get("low"),
            "ltp": ltp,
            "ltq": 0,
            "oi": quote.get("oi"),
            "open": quote.get("open"),
            "prev_close": quote.get("prev_close"),
            "totalbuyqty": quote.get("volume") or 0,
            "totalsellqty": quote.get("volume") or 0,
            "volume": quote.get("volume") or 0,
        }

    @staticmethod
    def _pad_ladder(best, fallback) -> list[dict]:
        """
        Pad one price into the OpenAlgo 5-level contract: level 1 carries
        the real best price, the rest are zeroed placeholders.
        """

        ladder = [{"price": best, "quantity": 0}]

        while len(ladder) < 5:
            ladder.append({"price": 0, "quantity": 0})

        return ladder

    # ------------------------------------------------------------------
    # Multi quotes
    # ------------------------------------------------------------------

    def get_multiquotes(self, symbols):
        """
        Sequential per-symbol quotes (Breeze has no batch quote endpoint).
        ``symbols`` is a list of (symbol, exchange) tuples. Symbols that
        fail are skipped with a logged warning — one bad symbol must not
        poison the batch.
        """

        results = []

        for item in symbols:
            try:
                if isinstance(item, dict):
                    symbol = item.get("symbol")
                    exchange = item.get("exchange")
                else:
                    symbol, exchange = item

                quote = self.get_quotes(symbol, exchange)

                results.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        **quote,
                    }
                )

            except Exception as exc:
                logger.warning(
                    "ICICI multiquotes skipped %s: %s", item, exc
                )

        return results

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    # Conservative per-request spans (Breeze caps vary by interval; the
    # pacer in breeze_client keeps extra chunks cheap).
    _CHUNK_DAYS = {
        "1second": 1,
        "1minute": 5,
        "5minute": 15,
        "30minute": 30,
        "1day": 366,
    }

    def get_history(self, symbol, exchange, interval, start_date, end_date):
        """
        Historical candles -> pandas DataFrame with columns
        [timestamp, open, high, low, close, volume, oi] and epoch-second
        timestamps (candle start).
        """

        interval_key = str(interval or "").strip()

        breeze_interval = self.timeframe_map.get(
            interval_key, self.timeframe_map.get(interval_key.lower())
        )

        if not breeze_interval:
            raise ICICIBreezeAPIError(
                f"Unsupported ICICI history interval '{interval}' — "
                "Breeze v2 serves 1s/1m/5m/30m/D only"
            )

        exchange_u = str(exchange or "").strip().upper()

        # Index pseudo-exchanges resolve to the parent cash exchange for
        # history; derivatives keep their own segment.
        parsed = parse_symbol(symbol, exchange_u)

        params = {
            "interval": breeze_interval,
            "stock_code": parsed["stock_code"],
            # v2 uses "exch_code" (not exchange_code).
            "exch_code": get_breeze_exchange(exchange_u),
        }

        if parsed["product"] in ("futures", "options"):
            params["product_type"] = parsed["product"]
            params["expiry_date"] = parsed["expiry_date"]
            params["right"] = parsed["right"] or "others"
            params["strike_price"] = parsed["strike_price"]

        start = self._parse_date(start_date, "start_date")
        end = self._parse_date(end_date, "end_date")

        chunk_days = self._CHUNK_DAYS[breeze_interval]

        frames = []

        cursor_start = start

        while cursor_start <= end:
            cursor_end = min(
                cursor_start + timedelta(days=chunk_days), end
            )

            chunk_params = {
                **params,
                "from_date": cursor_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "to_date": cursor_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            }

            response = breeze_request_v2(
                self.auth_token, "historicalcharts", chunk_params
            )

            frame = self._map_history_response(response)

            if frame is not None:
                frames.append(frame)

            cursor_start = cursor_end + timedelta(days=1)

        if not frames:
            raise ICICIBreezeAPIError(
                f"No history returned for {symbol} on {exchange} "
                f"({interval})"
            )

        df = pd.concat(frames, ignore_index=True)

        df = df.drop_duplicates(subset=["timestamp"]).sort_values(
            "timestamp"
        )

        return df.reset_index(drop=True)

    @staticmethod
    def _parse_date(value, field: str) -> datetime:
        text = str(value or "").strip()

        if not text:
            raise ICICIBreezeAPIError(f"{field} is required for history")

        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        raise ICICIBreezeAPIError(f"Unparseable {field}: {value}")

    def _map_history_response(self, response) -> pd.DataFrame | None:
        status = response_status(response)

        if status != 200:
            error = response_error(response)

            if error and "no data" not in error.lower():
                logger.warning(
                    "ICICI history chunk failed: %s", error
                )

            return None

        rows = (response.get("Success") or []) if isinstance(
            response, dict
        ) else []

        if isinstance(rows, dict):
            rows = [rows]

        if not rows:
            return None

        records = []

        for row in rows:
            if not isinstance(row, dict):
                continue

            timestamp = self._history_epoch(row.get("datetime") or row.get("date"))

            if timestamp is None:
                continue

            records.append(
                {
                    "timestamp": timestamp,
                    "open": _num(row, "open", "o"),
                    "high": _num(row, "high", "h"),
                    "low": _num(row, "low", "l"),
                    "close": _num(row, "close", "c"),
                    "volume": _int(row, "volume", "v", "traded_qty"),
                    "oi": _int(row, "oi", "open_interest"),
                }
            )

        return pd.DataFrame(records) if records else None

    @staticmethod
    def _history_epoch(value):
        """
        Breeze v2 candle times arrive as epoch-seconds strings; some
        payloads carry ISO text. Epochs are used as-is; ISO is interpreted
        as IST wall time (naive) or UTC (Z suffix).
        """

        if value in (None, ""):
            return None

        text = str(value).strip()

        try:
            return int(float(text))
        except ValueError:
            pass

        cleaned = text.replace("Z", "+00:00")

        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)

        return int(parsed.timestamp())
