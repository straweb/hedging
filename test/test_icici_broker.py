# test/test_icici_broker.py
# Unit tests for the ICICI Direct (Breeze API) plugin: master-contract
# parsing, auth checksums, mapping transforms, book mapping, order API
# response handling, funds mapping and the streaming adapter's tick
# normalizers. Everything runs offline - the network layer is mocked.

import io
import json
import os
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("API_KEY_PEPPER", "test-pepper-value-at-least-32-chars")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BROKER_API_KEY", "test-api-key")
os.environ.setdefault("BROKER_API_SECRET", "test-api-secret")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import broker.icici.api.breeze_client as breeze_client  # noqa: E402
import broker.icici.api.data as icici_data  # noqa: E402
import broker.icici.api.funds as icici_funds  # noqa: E402
import broker.icici.api.order_api as icici_order  # noqa: E402
import broker.icici.database.master_contract_db as master_db  # noqa: E402
import broker.icici.mapping.order_data as order_data  # noqa: E402
import broker.icici.mapping.transform_data as transform_data  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.status = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


# ----------------------------------------------------------------------
# Master contract parsing
# ----------------------------------------------------------------------


def _build_security_master_zip():
    """A minimal SecurityMaster.zip exercising every parser branch."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "NSEScripMaster.txt",
            "Token, ShortName, Series, CompanyName, ticksize, Lotsize\n"
            '"11536", "TCS", "EQ", "TATA CONSULTANCY SERVICES LTD", "0.01", "1"\n'
            '"0", "DEADCO", "BE", "DEAD COMPANY LTD", "0.01", "1"\n'
            '"NA", "SUSP", "EQ", "SUSPENDED LTD", "0.01", "1"\n'
            '"NIFTY 50", "NIFTY", "0", "NIFTY 50", "0.01", "1"\n'
            '"NIFTY BANK", "CNXBAN", "0", "NIFTY BANK", "0.01", "1"\n'
            '"X", "UNMAPPEDIDX", "0", "Unmapped Index", "0.01", "1"\n',
        )
        z.writestr(
            "BSEScripMaster.txt",
            "Token,ShortName,Series,CompanyName,TickSize,LotSize\n"
            '"532628", "3IINFO", "C", "3I INFOTECH LIMITED", "0.01", "1"\n'
            '"751041", "3IIRE1", "C", "3I INFOTECH LTD RE", "0.01", "1"\n'
            '"SENSEX", "BSESEN", "DR", "BSE SENSEX", "0", "1"\n'
            '"999911", "BANKEX", "DR", "BANKEX", "0", "1"\n',
        )
        z.writestr(
            "FONSEScripMaster.txt",
            "Token,InstrumentName,ShortName,Series,ExpiryDate,StrikePrice,OptionType\n"
            '"61466", "FUTIDX", "CNXBAN", "FUTURE", "26-Mar-2026", "0", "XX"\n'
            '"35000", "OPTIDX", "CNXBAN", "OPTION", "26-Mar-2026", "69400", "CE"\n'
            '"35001", "OPTIDX", "CNXBAN", "OPTION", "26-Mar-2026", "69400", "PE"\n'
            '"99999", "FUTIDX", "NIF150", "FUTURE", "26-Mar-2026", "0", "XX"\n',
        )
        z.writestr("FOBSEScripMaster.txt", "Token,InstrumentName,ShortName,Series\n")
        z.writestr(
            "MCXScripMaster.txt",
            "Token,InstrumentName,ShortName,Series,CompanyName,TickSize,LotSize,ExpiryDate,OptionType,StrikePrice\n"
            '"569900", "FUTSTK", "CRUDE", "FUTURE", "CRUDEOIL", "100", "100", "19-Oct-2026", "XX", "0"\n'
            '"578665", "OPTSTK", "COPPER", "OPTION", "COPPER", "1", "2500", "23-Oct-2026", "CE", "1160"\n',
        )
        z.writestr(
            "CDNSEScripMaster.txt",
            "Token,InstrumentName,ShortName,Series\n"
            '"25", "UNDCUR", "USDINR", "SPOT"\n',
        )
        z.writestr("MFScripMaster.txt", "CompanyCode\n21\n")
    return buf.getvalue()


def test_master_contract_parses_equity_and_skips_dead_rows():
    df = master_db.process_security_master(_build_security_master_zip())

    tcs = df[(df.exchange == "NSE") & (df.symbol == "TCS")].iloc[0]
    assert tcs.token == "11536"
    assert tcs.instrumenttype == "EQ"
    assert tcs.lotsize == 1

    # Dead rows (Token 0 / NA) and the unmapped index are dropped.
    assert "DEADCO" not in set(df.symbol)
    assert "SUSP" not in set(df.symbol)
    assert "UNMAPPEDIDX" not in set(df.symbol)

    # Rights entitlement rows (company name ends with " RE") are dropped.
    assert "3IIRE1" not in set(df[df.exchange == "BSE"].symbol)

    # Index rows carry the OpenAlgo symbol with the ICICI code in brsymbol.
    nifty = df[(df.exchange == "NSE_INDEX") & (df.symbol == "NIFTY")].iloc[0]
    assert nifty.brsymbol == "NIFTY"
    assert nifty.instrumenttype == "INDEX"
    bank = df[(df.exchange == "NSE_INDEX") & (df.symbol == "BANKNIFTY")].iloc[0]
    assert bank.brsymbol == "CNXBAN"

    sensex = df[(df.exchange == "BSE_INDEX") & (df.symbol == "SENSEX")].iloc[0]
    assert sensex.brsymbol == "BSESEN"

    # No currency rows: CDNSE only ships spot.
    assert "CDS" not in set(df.exchange)


def test_master_contract_formats_derivatives():
    df = master_db.process_security_master(_build_security_master_zip())

    fut = df[(df.exchange == "NFO") & (df.instrumenttype == "FUT")].iloc[0]
    assert fut.symbol == "BANKNIFTY26MAR26FUT"
    assert fut.brsymbol == "CNXBAN"  # ICICI stock code, not the OA underlying
    assert fut.expiry == "26-MAR-26"
    assert fut["name"] == "BANKNIFTY"  # underlying root (row["name"], not .name)

    ce = df[(df.exchange == "NFO") & (df.instrumenttype == "CE")].iloc[0]
    assert ce.symbol == "BANKNIFTY26MAR2669400CE"
    assert ce.strike == 69400.0

    # NIF150 has no real F&O book and is skipped.
    assert "NIF150" not in set(df[df.exchange == "NFO"].brsymbol)

    # MCX: underlying comes from the company name, tick is paise -> rupees.
    crude = df[(df.exchange == "MCX") & (df.instrumenttype == "FUT")].iloc[0]
    assert crude.symbol == "CRUDEOIL19OCT26FUT"
    assert crude.brsymbol == "CRUDE"
    assert crude.tick_size == 1.0

    copper = df[(df.exchange == "MCX") & (df.instrumenttype == "CE")].iloc[0]
    assert copper.symbol == "COPPER23OCT261160CE"
    assert copper.lotsize == 2500
    assert copper.tick_size == 0.01

    # Uniqueness on both lookup keys.
    assert df.duplicated(subset=["exchange", "symbol"]).sum() == 0
    assert df.duplicated(subset=["exchange", "token"]).sum() == 0


# ----------------------------------------------------------------------
# Auth / checksum layer
# ----------------------------------------------------------------------


def test_session_token_roundtrip():
    encoded = breeze_client.encode_session_token("XY604721", "sessionkey")
    user_id, session_key = breeze_client.decode_session_token(encoded)
    assert user_id == "XY604721"
    assert session_key == "sessionkey"


def test_build_headers_checksum_is_body_sensitive(monkeypatch):
    body1 = json.dumps({"a": 1}, separators=(",", ":"))
    body2 = json.dumps({"a": 2}, separators=(",", ":"))

    h1 = breeze_client.build_headers(body1, "AUTH")
    h2 = breeze_client.build_headers(body2, "AUTH")
    h1b = breeze_client.build_headers(body1, "AUTH")

    assert h1["X-AppKey"] == "test-api-key"
    assert h1["X-SessionToken"] == "AUTH"
    assert h1["X-Checksum"].startswith("token ")
    assert h1["X-Checksum"] != h2["X-Checksum"]  # body participates
    assert h1["X-Checksum"] == h1b["X-Checksum"]  # deterministic per timestamp


def test_get_api_response_folds_breeze_status_into_response_status(monkeypatch):
    from broker.icici.api.breeze_client import request_raw

    captured = {}

    def fake_request(method, url, headers=None, content=None):
        captured["body"] = content
        return FakeResponse(200, {"Success": None, "Status": 401, "Error": "Invalid session."})

    class FakeClient:
        def get(self, url, headers=None, content=None):
            return fake_request("GET", url, headers, content)

        def post(self, url, headers=None, content=None):
            return fake_request("POST", url, headers, content)

    monkeypatch.setattr(breeze_client, "get_httpx_client", lambda: FakeClient())

    response, data = request_raw("funds", "AUTH", "GET", "{}")
    assert data["Status"] == 401
    assert response.status == 401  # business failure surfaced on the response


def test_authenticate_broker_success(monkeypatch):
    import broker.icici.api.auth_api as auth_api

    session_token = breeze_client.encode_session_token("XY604721", "sessionkey")

    class FakeClient:
        def get(self, url, headers=None, content=None):
            assert "customerdetails" in url
            body = json.loads(content)
            assert body["SessionToken"] == "apisession-123"
            assert body["AppKey"] == "test-api-key"
            return FakeResponse(
                200, {"Success": {"session_token": session_token}, "Status": 200, "Error": None}
            )

    monkeypatch.setattr(auth_api, "get_httpx_client", lambda: FakeClient())

    auth_token, error = auth_api.authenticate_broker("apisession-123")
    assert error is None
    assert auth_token == session_token


def test_authenticate_broker_failure(monkeypatch):
    import broker.icici.api.auth_api as auth_api

    class FakeClient:
        def get(self, url, headers=None, content=None):
            return FakeResponse(200, {"Success": None, "Status": 401, "Error": "Invalid session."})

    monkeypatch.setattr(auth_api, "get_httpx_client", lambda: FakeClient())

    auth_token, error = auth_api.authenticate_broker("bad-session")
    assert auth_token is None
    assert "Invalid session" in error


# ----------------------------------------------------------------------
# Mapping transforms
# ----------------------------------------------------------------------


class FakeSymbolInfo:
    def __init__(self, brsymbol, brexchange, instrumenttype, expiry="", strike=0.0, tick_size=0.05):
        self.brsymbol = brsymbol
        self.brexchange = brexchange
        self.instrumenttype = instrumenttype
        self.expiry = expiry
        self.strike = strike
        self.tick_size = tick_size


def _patch_symbol_info(monkeypatch, info):
    monkeypatch.setattr(transform_data, "get_symbol_info", lambda symbol, exchange: info)


def test_map_product_type_matrix():
    assert transform_data.map_product_type("CNC", "EQ", "NSE") == "cash"
    assert transform_data.map_product_type("CNC", "EQ", "BSE") == "cash"
    assert transform_data.map_product_type("CASH", "EQ", "NSE") == "cash"
    assert transform_data.map_product_type("MIS", "EQ", "NSE") == "margin"
    assert transform_data.map_product_type("NRML", "EQ", "NSE") == "margin"
    assert transform_data.map_product_type("MIS", "FUT", "NFO") == "futures"
    assert transform_data.map_product_type("NRML", "CE", "NFO") == "options"
    assert transform_data.map_product_type("CNC", "PE", "NFO") == "options"


def test_reverse_map_product_type_matrix():
    assert transform_data.reverse_map_product_type("cash") == "CNC"
    assert transform_data.reverse_map_product_type("margin", "EQ") == "MIS"
    assert transform_data.reverse_map_product_type("margin", "FUT") == "NRML"
    assert transform_data.reverse_map_product_type("futures", "FUT") == "NRML"
    assert transform_data.reverse_map_product_type("options", "CE") == "NRML"


def test_transform_data_limit_equity(monkeypatch):
    _patch_symbol_info(monkeypatch, FakeSymbolInfo("TCS", "NSE", "EQ"))
    payload = transform_data.transform_data(
        {
            "symbol": "TCS",
            "exchange": "NSE",
            "action": "BUY",
            "pricetype": "LIMIT",
            "price": "4200.5",
            "quantity": "1",
            "product": "CNC",
            "strategy": "Test",
        },
        "11536",
    )
    assert payload["stock_code"] == "TCS"
    assert payload["exchange_code"] == "NSE"
    assert payload["product"] == "cash"
    assert payload["action"] == "buy"
    assert payload["order_type"] == "limit"
    assert payload["quantity"] == "1"
    assert payload["price"] == "4200.5"
    assert "expiry_date" not in payload


def test_transform_data_market_sends_market_type(monkeypatch):
    _patch_symbol_info(monkeypatch, FakeSymbolInfo("CNXBAN", "NFO", "CE", "26-MAR-26", 69400.0))
    payload = transform_data.transform_data(
        {
            "symbol": "BANKNIFTY26MAR2669400CE",
            "exchange": "NFO",
            "action": "SELL",
            "pricetype": "MARKET",
            "quantity": "30",
            "product": "NRML",
        },
        "35000",
    )
    # ICICI converts market orders to aggressive limits server-side.
    assert payload["order_type"] == "market"
    assert payload["price"] == "0"
    assert payload["product"] == "options"
    assert payload["stock_code"] == "CNXBAN"
    assert payload["expiry_date"] == "2026-03-26T06:00:00.000Z"
    assert payload["right"] == "call"
    assert payload["strike_price"] == "69400"


def test_transform_data_slm_gets_protected_limit(monkeypatch):
    _patch_symbol_info(monkeypatch, FakeSymbolInfo("TCS", "NSE", "EQ", tick_size=0.05))
    payload = transform_data.transform_data(
        {
            "symbol": "TCS",
            "exchange": "NSE",
            "action": "SELL",
            "pricetype": "SL-M",
            "trigger_price": "4200",
            "quantity": "1",
            "product": "MIS",
        },
        "11536",
    )
    assert payload["order_type"] == "stoploss"
    assert float(payload["stoploss"]) == 4200.0
    # Protective limit must sit strictly below the sell trigger.
    assert float(payload["price"]) < 4200.0


def test_expiry_conversion_helpers():
    assert transform_data.to_breeze_expiry("26-MAR-26") == "2026-03-26T06:00:00.000Z"
    assert transform_data.to_breeze_expiry("26-Mar-2026") == "2026-03-26T06:00:00.000Z"
    assert transform_data.to_oa_expiry("26-Mar-2026") == "26-MAR-26"


# ----------------------------------------------------------------------
# Book mapping
# ----------------------------------------------------------------------


class FakeSymTokenRow:
    def __init__(self, symbol, instrumenttype="EQ"):
        self.symbol = symbol
        self.instrumenttype = instrumenttype


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter_by(self, **kwargs):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSymToken:
    def __init__(self, rows):
        self.query = FakeQuery(rows)


def test_map_order_data_resolves_symbols(monkeypatch):
    rows_db = [
        FakeSymTokenRow("RELIANCE"),
        FakeSymTokenRow("BANKNIFTY26MAR26FUT", "FUT"),
    ]

    original_query = order_data.SymToken
    monkeypatch.setattr(order_data, "SymToken", FakeSymToken(rows_db))

    order_rows = [
        {
            "exchange_code": "NSE",
            "stock_code": "RELIANCE",
            "product_type": "Cash",
            "action": "Buy",
            "status": "Executed",
        },
        {
            "exchange_code": "NFO",
            "stock_code": "CNXBAN",
            "expiry_date": "26-Mar-2026",
            "product_type": "Futures",
            "action": "Sell",
            "status": "Ordered",
        },
    ]
    mapped = order_data.map_order_data(order_rows)
    assert mapped[0]["tradingSymbol"] == "RELIANCE"
    assert mapped[0]["exchangeSegment"] == "NSE"
    assert mapped[0]["productType"] == "CNC"
    assert mapped[1]["tradingSymbol"] == "BANKNIFTY26MAR26FUT"
    assert mapped[1]["productType"] == "NRML"


def test_calculate_order_statistics_normalizes_statuses():
    order_rows = [
        {"action": "Buy", "status": "Executed"},
        {"action": "Sell", "status": "Ordered"},
        {"action": "Buy", "status": "Rejected"},
        {"action": "Buy", "status": "Cancelled"},
        {"action": "Sell", "status": "Partially Executed"},
    ]
    stats = order_data.calculate_order_statistics(order_rows)
    assert stats["total_buy_orders"] == 3
    assert stats["total_sell_orders"] == 2
    assert stats["total_completed_orders"] == 1
    assert stats["total_open_orders"] == 2
    assert stats["total_rejected_orders"] == 1
    statuses = {row["status"]: row["orderStatus"] for row in order_rows}
    assert statuses["Executed"] == "complete"
    assert statuses["Ordered"] == "open"
    assert statuses["Cancelled"] == "cancelled"


def test_transform_order_data_shape():
    orders = [
        {
            "tradingSymbol": "TCS",
            "exchangeSegment": "NSE",
            "action": "Buy",
            "quantity": "1",
            "price": "4200.5",
            "stoploss": "0",
            "order_type": "Limit",
            "productType": "CNC",
            "order_id": "20250205N300001234",
            "orderStatus": "open",
            "order_datetime": "05-Feb-2025 09:26",
        }
    ]
    transformed = order_data.transform_order_data(orders)[0]
    assert transformed["symbol"] == "TCS"
    assert transformed["action"] == "BUY"
    assert transformed["pricetype"] == "LIMIT"
    assert transformed["orderid"] == "20250205N300001234"
    assert transformed["order_status"] == "open"


def test_transform_tradebook_data_handles_field_variants():
    trades = [
        {"tradingSymbol": "TCS", "exchangeSegment": "NSE", "productType": "CNC",
         "action": "Buy", "quantity": "2", "trade_price": "4200", "order_id": "O1",
         "trade_date": "05-Feb-2025"},
        {"tradingSymbol": "TCS", "exchangeSegment": "NSE", "productType": "CNC",
         "action": "Sell", "quantity": "1", "average_price": "4300", "order_id": "O2"},
    ]
    rows = order_data.transform_tradebook_data(trades)
    assert rows[0]["average_price"] == 4200.0
    assert rows[0]["trade_value"] == 8400.0
    assert rows[1]["average_price"] == 4300.0  # fallback key


def test_transform_positions_data_derives_pnl():
    positions = [
        {"tradingSymbol": "TCS", "exchangeSegment": "NSE", "productType": "CNC",
         "quantity": "10", "average_price": "4000", "ltp": "4200", "pnl": None},
        {"tradingSymbol": "BANKNIFTY26MAR26FUT", "exchangeSegment": "NFO",
         "productType": "NRML", "quantity": "-30", "average_price": "50000",
         "ltp": "51000", "pnl": "1000"},
    ]
    rows = order_data.transform_positions_data(positions)
    assert rows[0]["pnl"] == 2000.0  # derived from (ltp - avg) * qty
    assert rows[1]["pnl"] == 1000.0  # broker-provided pnl wins
    assert rows[1]["quantity"] == -30.0


def test_holdings_statistics():
    holdings = [
        {"_oa_symbol": "TCS", "_exchange": "NSE", "quantity": "10",
         "average_price": "4000", "_ltp": "4200", "unrealized_profit": None},
    ]
    stats = order_data.calculate_portfolio_statistics(holdings)
    assert stats["totalinvvalue"] == 40000.0
    assert stats["totalholdingvalue"] == 42000.0
    assert stats["totalprofitandloss"] == 2000.0
    rows = order_data.transform_holdings_data(holdings)
    assert rows[0]["pnl"] == 2000.0
    assert rows[0]["pnlpercent"] == 5.0


# ----------------------------------------------------------------------
# Order API
# ----------------------------------------------------------------------


def test_place_order_api_returns_order_id(monkeypatch):
    captured = {}

    def fake_request(endpoint, auth, method, payload):
        captured["endpoint"] = endpoint
        captured["method"] = method
        captured["payload"] = json.loads(payload)
        return FakeResponse(200, {}), {
            "Success": {"order_id": "202502051400001234", "message": "ok"},
            "Status": 200,
            "Error": None,
        }

    monkeypatch.setattr(icici_order, "request_raw", fake_request)
    monkeypatch.setattr(
        icici_order, "get_token", lambda symbol, exchange: "11536"
    )
    monkeypatch.setattr(
        icici_order, "transform_data", lambda data, token: {"stock_code": "TCS"}
    )

    res, response_data, orderid = icici_order.place_order_api(
        {"symbol": "TCS", "exchange": "NSE"}, "AUTH"
    )
    assert orderid == "202502051400001234"
    assert res.status == 200
    assert captured["method"] == "POST"


def test_place_order_api_failure_has_no_order_id(monkeypatch):
    def fake_request(endpoint, auth, method, payload):
        return FakeResponse(400, {}), {"Success": None, "Status": 400, "Error": "Rejected"}

    monkeypatch.setattr(icici_order, "request_raw", fake_request)
    monkeypatch.setattr(icici_order, "get_token", lambda s, e: "11536")
    monkeypatch.setattr(icici_order, "transform_data", lambda data, token: {})

    res, response_data, orderid = icici_order.place_order_api(
        {"symbol": "TCS", "exchange": "NSE"}, "AUTH"
    )
    assert orderid is None
    assert res.status == 400


def test_cancel_order_recovers_exchange_from_book(monkeypatch):
    monkeypatch.setattr(
        icici_order,
        "get_order_book",
        lambda auth: [
            {"order_id": "O1", "exchange_code": "NFO", "status": "Ordered"},
            {"order_id": "O2", "exchange_code": "NSE", "status": "Executed"},
        ],
    )

    def fake_request(endpoint, auth, method, payload):
        assert method == "DELETE"
        assert json.loads(payload) == {"exchange_code": "NFO", "order_id": "O1"}
        return FakeResponse(200, {}), {"Success": {"message": "cancelled"}, "Status": 200}

    monkeypatch.setattr(icici_order, "request_raw", fake_request)

    message, status_code = icici_order.cancel_order("O1", "AUTH")
    assert status_code == 200
    assert message["status"] == "success"


def test_cancel_all_orders_filters_open_statuses(monkeypatch):
    monkeypatch.setattr(
        icici_order,
        "get_order_book",
        lambda auth: [
            {"order_id": "O1", "exchange_code": "NSE", "status": "Ordered"},
            {"order_id": "O2", "exchange_code": "NSE", "status": "Executed"},
            {"order_id": "O3", "exchange_code": "NFO", "status": "Rejected"},
        ],
    )
    monkeypatch.setattr(
        icici_order,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {"Success": {}, "Status": 200, "Error": None},
        ),
    )

    canceled, failed = icici_order.cancel_all_orders_api({}, "AUTH")
    assert canceled == ["O1"]
    assert failed == []


def test_get_positions_merges_positions(monkeypatch):
    def fake_request(endpoint, auth, method, payload):
        assert endpoint == "portfoliopositions"
        return FakeResponse(200, {}), {
            "Success": [{"stock_code": "TCS", "quantity": "10"}],
            "Status": 200,
            "Error": None,
        }

    monkeypatch.setattr(icici_order, "request_raw", fake_request)
    positions = icici_order.get_positions("AUTH")
    assert isinstance(positions, list)
    assert positions[0]["stock_code"] == "TCS"


# ----------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------


def _patch_broker_data_params(monkeypatch, params):
    monkeypatch.setattr(
        icici_data, "get_breeze_params", lambda symbol, exchange: params
    )


def test_get_quotes_normalizes_nfo_row(monkeypatch):
    _patch_broker_data_params(
        monkeypatch,
        {
            "stock_code": "CNXBAN",
            "exchange_code": "NFO",
            "product_type": "options",
            "expiry_date": "2026-03-26T06:00:00.000Z",
            "right": "call",
            "strike_price": "69400",
            "instrumenttype": "CE",
        },
    )
    monkeypatch.setattr(icici_data, "_apply_rate_limit", lambda category="quote": None)
    monkeypatch.setattr(
        icici_data,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {
                "Success": [
                    {
                        "ltp": 23832.85,
                        "open": 23825.0,
                        "high": 23840.6,
                        "low": 23808.0,
                        "previous_close": 23785.4,
                        "total_quantity_traded": "623325",
                        "best_bid_price": 23832.0,
                        "best_bid_quantity": "1500",
                        "best_offer_price": 23833.8,
                        "best_offer_quantity": "150",
                        "oi": "125000",
                    }
                ],
                "Status": 200,
                "Error": None,
            },
        ),
    )

    handler = icici_data.BrokerData("AUTH")
    quote = handler.get_quotes("BANKNIFTY26MAR2669400CE", "NFO")
    assert quote["ltp"] == 23832.85
    assert quote["volume"] == 623325
    assert quote["oi"] == 125000
    assert quote["prev_close"] == 23785.4


def test_get_quotes_bse_reads_second_row(monkeypatch):
    _patch_broker_data_params(
        monkeypatch,
        {
            "stock_code": "3IINFO",
            "exchange_code": "BSE",
            "product_type": "cash",
            "expiry_date": "",
            "right": "",
            "strike_price": "",
            "instrumenttype": "EQ",
        },
    )
    monkeypatch.setattr(icici_data, "_apply_rate_limit", lambda category="quote": None)
    monkeypatch.setattr(
        icici_data,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {
                "Success": [
                    {"ltp": 0, "open": 0, "high": 0, "low": 0, "previous_close": 0},
                    {
                        "ltp": 42.5,
                        "open": 42.0,
                        "high": 43.0,
                        "low": 41.5,
                        "previous_close": 41.0,
                        "total_quantity_traded": "1000",
                        "best_bid_price": 42.4,
                        "best_offer_price": 42.6,
                    },
                ],
                "Status": 200,
                "Error": None,
            },
        ),
    )

    handler = icici_data.BrokerData("AUTH")
    quote = handler.get_quotes("3IINFO", "BSE")
    assert quote["ltp"] == 42.5  # the SDK quirk: BSE rows start at index 1


def test_get_depth_pads_five_levels(monkeypatch):
    _patch_broker_data_params(
        monkeypatch,
        {
            "stock_code": "TCS",
            "exchange_code": "NSE",
            "product_type": "cash",
            "expiry_date": "",
            "right": "",
            "strike_price": "",
            "instrumenttype": "EQ",
        },
    )
    monkeypatch.setattr(icici_data, "_apply_rate_limit", lambda category="quote": None)
    monkeypatch.setattr(
        icici_data,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {
                "Success": [
                    {
                        "ltp": 4200.5,
                        "best_bid_price": 4200.0,
                        "best_bid_quantity": "50",
                        "best_offer_price": 4201.0,
                        "best_offer_quantity": "10",
                        "open": 4180,
                        "high": 4210,
                        "low": 4175,
                        "previous_close": 4190,
                        "total_quantity_traded": "900000",
                    }
                ],
                "Status": 200,
                "Error": None,
            },
        ),
    )

    handler = icici_data.BrokerData("AUTH")
    depth = handler.get_depth("TCS", "NSE")
    assert len(depth["bids"]) == 5
    assert len(depth["asks"]) == 5
    assert depth["bids"][0]["price"] == 4200.0
    assert depth["bids"][1]["price"] == 0.0  # padded
    assert depth["asks"][0]["quantity"] == 10


def test_get_history_rejects_unsupported_interval():
    handler = icici_data.BrokerData("AUTH")
    try:
        handler.get_history("TCS", "NSE", "15m", "2025-01-01", "2025-01-02")
        raise AssertionError("Expected an exception for unsupported interval")
    except Exception as e:
        assert "Unsupported interval" in str(e)


def test_timeframe_map_exposes_supported_intervals():
    handler = icici_data.BrokerData("AUTH")
    assert set(handler.timeframe_map.keys()) == {"1m", "5m", "30m", "D"}


# ----------------------------------------------------------------------
# Funds
# ----------------------------------------------------------------------


def test_get_margin_data_maps_breeze_fields(monkeypatch):
    monkeypatch.setattr(
        icici_funds,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {
                "Success": {
                    "unallocated_balance": "87500000",
                    "block_by_trade_equity": 1250.5,
                    "block_by_trade_fno": 0.0,
                    "block_by_trade_commodity": 0.0,
                    "block_by_trade_currency": 0.0,
                },
                "Status": 200,
                "Error": None,
            },
        ),
    )
    monkeypatch.setattr(
        icici_funds, "get_positions", lambda auth: []
    )

    data = icici_funds.get_margin_data("AUTH")
    assert data["availablecash"] == "87500000.00"
    assert data["utiliseddebits"] == "1250.50"
    assert data["m2munrealized"] == "0.00"


def test_get_margin_data_zeroes_on_error(monkeypatch):
    monkeypatch.setattr(
        icici_funds,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {"Success": None, "Status": 401, "Error": "Invalid session."},
        ),
    )
    data = icici_funds.get_margin_data("AUTH")
    assert data["availablecash"] == "0.00"
    assert data["utiliseddebits"] == "0.00"


def test_test_auth_token_success_and_failure(monkeypatch):
    monkeypatch.setattr(
        icici_funds,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {"Success": {"bank_account": "1"}, "Status": 200, "Error": None},
        ),
    )
    ok, error = icici_funds.test_auth_token("AUTH")
    assert ok is True and error is None

    monkeypatch.setattr(
        icici_funds,
        "request_raw",
        lambda endpoint, auth, method, payload: (
            FakeResponse(200, {}),
            {"Success": None, "Status": 401, "Error": "Session expired"},
        ),
    )
    ok, error = icici_funds.test_auth_token("AUTH")
    assert ok is False and "Session expired" in error


# ----------------------------------------------------------------------
# Streaming tick normalizers (no sockets)
# ----------------------------------------------------------------------


def _make_adapter():
    import types

    wp = types.ModuleType("websocket_proxy")
    base = types.ModuleType("websocket_proxy.base_adapter")
    mapping = types.ModuleType("websocket_proxy.mapping")

    class BaseBrokerWebSocketAdapter:
        def __init__(self, use_shared_zmq=False, shared_publisher=None):
            self.published = []

        def _create_success_response(self, message, **kwargs):
            d = {"status": "success", "message": message}
            d.update(kwargs)
            return d

        def _create_error_response(self, code, message):
            return {"status": "error", "code": code, "message": message}

        def publish_market_data(self, topic, data):
            self.published.append((topic, data))

        def cleanup_zmq(self):
            pass

    class SymbolMapper:
        """Symbol-aware fake: each test's instrument resolves to its room."""

        TOKENS = {
            ("RELIANCE", "NSE"): {"token": "11536", "brexchange": "NSE"},
            ("TCS", "NSE"): {"token": "11536", "brexchange": "NSE"},
            ("BANKNIFTY26MAR2669400CE", "NFO"): {"token": "35000", "brexchange": "NFO"},
            ("CRUDEOIL19OCT26FUT", "MCX"): {"token": "569900", "brexchange": "MCX"},
            ("NIFTY", "NSE_INDEX"): {"token": "NIFTY", "brexchange": "NSE"},
        }

        @staticmethod
        def get_token_from_symbol(symbol, exchange):
            return SymbolMapper.TOKENS.get((symbol, exchange))

    base.BaseBrokerWebSocketAdapter = BaseBrokerWebSocketAdapter
    mapping.SymbolMapper = SymbolMapper
    wp.base_adapter = base
    wp.mapping = mapping
    sys.modules["websocket_proxy"] = wp
    sys.modules["websocket_proxy.base_adapter"] = base
    sys.modules["websocket_proxy.mapping"] = mapping

    import broker.icici.streaming.icici_adapter as adapter_module

    return adapter_module.IciciWebSocketAdapter


def test_subscribe_builds_livestream_channel():
    Adapter = _make_adapter()
    adapter = Adapter()
    response = adapter.subscribe("RELIANCE", "NSE", mode=2)
    assert response["status"] == "success"
    subscription = adapter.get_subscriptions_snapshot()["RELIANCE_NSE_2"]
    assert subscription["channel"] == "4.1!11536"

    response = adapter.unsubscribe("RELIANCE", "NSE", mode=2)
    assert response["status"] == "success"
    assert adapter.get_subscriptions_snapshot() == {}


def test_normalize_equity_quotes_tick():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter.subscribe("RELIANCE", "NSE", mode=2)
    # len-21 NSE equity payload
    tick = [
        "4.1!11536",
        4200.0, 4205.5, 4210.0, 4195.0,  # open last high low
        5.5, 4205.0, 50, 4206.0, 10,     # change bPrice bQty sPrice sQty
        4205.25,                          # ltq
        4202.25,                          # avgPrice
        "150000", "75000", "80000", "90000000", "up",  # ttq buyQty sellQty ttv trend
        "100", "50",                      # lowerCkt upperCkt
        1717600000,                       # ltt (unix)
        4199.95,                          # close
    ]
    adapter._on_stock(tick)
    topic, payload = adapter.published[-1]
    assert topic == "NSE_RELIANCE_QUOTE"
    assert payload["ltp"] == 4205.5
    assert payload["open"] == 4200.0
    assert payload["close"] == 4199.95
    assert payload["volume"] == 150000
    assert payload["total_buy_quantity"] == 75000
    assert payload["symbol"] == "RELIANCE"


def test_normalize_fno_quotes_tick_includes_oi():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter.subscribe("RELIANCE", "NSE", mode=2)
    # len-23 F&O payload
    tick = [
        "4.1!11536",
        100.0, 101.0, 102.0, 99.0,      # open last high low
        1.0, 100.9, 75, 101.1, 25,      # change bPrice bQty sPrice sQty
        100.95, 100.95,                 # ltq avgPrice
        "125000", "5000",               # OI, CHNGOI
        "60000", "30000", "35000", "9000000", "up",  # ttq buyQty sellQty ttv trend
        "90", "110",
        1717600000, 99.5,               # ltt close
    ]
    adapter._on_stock(tick)
    _, payload = adapter.published[-1]
    assert payload["oi"] == 125000
    assert payload["oi_change"] == 5000
    assert payload["close"] == 99.5
    assert payload["volume"] == 60000


def test_normalize_depth_tick():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter.subscribe("RELIANCE", "NSE", mode=3)
    tick = [
        "4.2!11536",
        1717600000,
        [
            [4205.0, 50, 2, "B", 4206.0, 40, 1, "S"],
            [4204.9, 30, 1, "B", 4206.1, 20, 1, "S"],
        ],
    ]
    adapter._on_stock(tick)
    topic, payload = adapter.published[-1]
    assert topic == "NSE_RELIANCE_DEPTH"
    assert payload["depth"]["buy"][0]["price"] == 4205.0
    assert payload["depth"]["sell"][0]["price"] == 4206.0
    assert payload["total_buy_quantity"] == 80


def test_normalize_mcx_tick():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter.subscribe("CRUDEOIL19OCT26FUT", "MCX", mode=2)
    tick = [
        "6.1!569900",
        "5000", "", "0",          # AndiOPVolume, Reserved, IndexFlag
        "1200", 6100.0, 10, 1717600000, 6099.5,  # ttq last ltq ltt avg
        "500", "600", "",         # TotalBuyQnt TotalSellQnt ReservedStr
        6050.0, 6040.0, 6120.0, 6030.0, "",  # close open high low
        "45000", "500", 0.0, 0.0, 0.0, 0.0,  # OI TotalTrades ...
    ]
    adapter._on_stock(tick)
    topic, payload = adapter.published[-1]
    assert topic == "MCX_CRUDEOIL19OCT26FUT_QUOTE"
    assert payload["ltp"] == 6100.0
    assert payload["open"] == 6040.0
    assert payload["close"] == 6050.0
    assert payload["oi"] == 45000
    assert payload["volume"] == 1200
    assert payload["change_absolute"] == 50.0


def test_normalize_index_tick():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)
    tick = [
        "3.1!NIFTY",
        24000.0, 24100.0, 23950.0, 23990.0,
        24050.0, 100, 1717600000,
        "250000", "0.25", "60.0",
        24010.0, 24045.0, 50, 24055.0, 40, 0,
    ]
    adapter._on_stock(tick)
    topic, payload = adapter.published[-1]
    assert topic == "NSE_INDEX_NIFTY_QUOTE"
    assert payload["ltp"] == 24050.0
    assert payload["open"] == 24000.0
    assert payload["change_percent"] == 0.25


def test_untracked_room_is_dropped_silently():
    Adapter = _make_adapter()
    adapter = Adapter()
    adapter._on_stock(["4.1!999999", 1, 2, 3, 4, 5])
    assert adapter.published == []
