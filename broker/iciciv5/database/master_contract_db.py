"""
ICICI Direct Breeze master contract (SecurityMaster.zip) loader.

Downloads ICICI's SecurityMaster archive and builds the shared OpenAlgo
``symtoken`` table:

- NSE / BSE cash: symbol "<ShortName>-EQ", brsymbol = ICICI ShortName
  (ICICI's own scrip vocabulary — RELIND for Reliance Industries etc. —
  the cash master carries no official-NSE-symbol column, so quotes/orders
  use these codes and parse_symbol resolves them via brsymbol).
- NFO / BFO derivatives: OpenAlgo contract symbols
  (NIFTY19OCT2625000CE / BANKNIFTY26NOV26FUT) built from the underlying
  ShortName + expiry + strike + option right; index underlyings are
  translated to the OpenAlgo names the option tools use
  (CNXBAN -> BANKNIFTY, BSESEN -> SENSEX, ...); stock underlyings keep
  ICICI's own codes so option-symbol prefixes match the cash symbols
  users see in search. brsymbol equals the symbol (Breeze orders/quotes
  use the parsed underlying, not this string).
- MCX: same contract-symbol scheme from the MCX file.

Notes:

- Strikes are stored in RUPEES exactly as ICICI publishes them (no
  paise conversion).
- Index spot rows are NOT created — ICICI's SecurityMaster has no index
  tokens, so NSE_INDEX/BSE_INDEX quotes resolve through
  mapping.transform_data's index vocabulary and live streaming is not
  available for index spots (REST quotes/history are).
"""

import csv
import io
import os
import zipfile
from datetime import datetime

import pandas as pd
import requests
from sqlalchemy import Column, Float, Index, Integer, Sequence, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from broker.icici.api.baseurl import SECURITY_MASTER_URL
from broker.icici.mapping.transform_data import to_openalgo_underlying
from database.engine_factory import create_db_engine
from extensions import socketio
from utils.logging import get_logger


logger = get_logger(__name__)


DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_db_engine(DATABASE_URL)

db_session = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

Base = declarative_base()

Base.query = db_session.query_property()


class SymToken(Base):
    __tablename__ = "symtoken"

    id = Column(Integer, Sequence("symtoken_id_seq"), primary_key=True)

    symbol = Column(String, nullable=False, index=True)

    brsymbol = Column(String, nullable=False, index=True)

    name = Column(String)

    exchange = Column(String, index=True)

    brexchange = Column(String, index=True)

    token = Column(String, index=True)

    expiry = Column(String)

    strike = Column(Float)

    lotsize = Column(Integer)

    instrumenttype = Column(String)

    tick_size = Column(Float)

    __table_args__ = (
        Index("idx_symbol_exchange", "symbol", "exchange"),
    )


def init_db():
    logger.info("Initializing ICICI master contract DB")

    Base.metadata.create_all(bind=engine)


def delete_symtoken_table():
    logger.info("Deleting symtoken table rows")

    SymToken.query.delete()

    db_session.commit()


def copy_from_dataframe(df):
    logger.info("Performing bulk insert of %d rows", len(df))

    data_dict = df.to_dict(orient="records")

    existing_tokens = {
        result.token
        for result in db_session.query(SymToken.token).all()
    }

    filtered = [
        row for row in data_dict if row["token"] not in existing_tokens
    ]

    try:
        if filtered:
            db_session.bulk_insert_mappings(SymToken, filtered)

            db_session.commit()

            logger.info(
                "Bulk insert completed with %d new records", len(filtered)
            )

        else:
            logger.info("No new records to insert")

    except Exception:
        logger.exception("Bulk insert failed")

        db_session.rollback()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def clean(value) -> str:
    if value is None:
        return ""

    return str(value).strip().strip('"')


def parse_int(value, default=1) -> int:
    try:
        return int(float(clean(value)))
    except (TypeError, ValueError):
        return default


def parse_float(value, default=0.0) -> float:
    try:
        return float(clean(value))
    except (TypeError, ValueError):
        return default


def parse_expiry(value) -> datetime | None:
    text = clean(value)

    if not text:
        return None

    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def expiry_tag(value) -> str:
    """Expiry as the OpenAlgo symbol tag ("19OCT26")."""

    parsed = parse_expiry(value)

    if parsed is None:
        return ""

    return parsed.strftime("%d%b%y").upper()


def expiry_display(value) -> str:
    """Expiry as stored in symtoken.expiry ("19-Oct-2026")."""

    parsed = parse_expiry(value)

    if parsed is None:
        return clean(value)

    return parsed.strftime("%d-%b-%Y")


def format_strike(strike: str) -> str:
    try:
        number = float(clean(strike))
    except (TypeError, ValueError):
        return clean(strike)

    if number.is_integer():
        return str(int(number))

    return str(number)


def contract_symbol(underlying: str, series: str, expiry, strike, right) -> str:
    """OpenAlgo contract symbol from parsed SecurityMaster columns."""

    tag = expiry_tag(expiry)

    if not tag:
        return ""

    series_u = str(series or "").strip().upper()

    if "FUT" in series_u:
        return f"{underlying}{tag}FUT"

    right_u = str(right or "").strip().upper()

    if right_u.startswith("C"):
        suffix = "CE"
    elif right_u.startswith("P"):
        suffix = "PE"
    else:
        return ""

    strike_text = format_strike(strike)

    return f"{underlying}{tag}{strike_text}{suffix}"


FILE_EXCHANGES = (
    ("FONSE", "NFO"),
    ("FOBSE", "BFO"),
    ("MCX", "MCX"),
    ("NSE", "NSE"),
    ("BSE", "BSE"),
)


def exchange_for_file(filename: str) -> str | None:
    upper = filename.upper()

    # Order matters: FONSE/FOBSE must be checked before the plain
    # NSE/BSE cash files.
    for marker, exchange in FILE_EXCHANGES:
        if marker in upper:
            return exchange

    return None


DERIVATIVE_SERIES = ("FUT", "OPT")


def parse_file(filename: str, content: bytes) -> list[dict]:
    exchange = exchange_for_file(filename)

    if not exchange:
        return []

    rows: list[dict] = []

    reader = csv.reader(io.TextIOWrapper(io.BytesIO(content), encoding="utf-8"))

    for raw in reader:
        columns = [clean(value) for value in raw]

        try:
            token = columns[0] if columns else ""

            if not token:
                continue

            if exchange in ("NSE", "BSE"):
                # Cash equity: 0 Token, 1 ShortName, 2 Series,
                # 3 CompanyName, 4 ticksize, 5 Lotsize.
                brsymbol = columns[1] if len(columns) > 1 else ""

                if not brsymbol:
                    continue

                name = columns[3] if len(columns) > 3 else brsymbol

                rows.append(
                    {
                        "symbol": f"{brsymbol}-EQ",
                        "brsymbol": brsymbol,
                        "name": name,
                        "exchange": exchange,
                        "brexchange": exchange,
                        "token": token,
                        "expiry": "",
                        "strike": 0.0,
                        "lotsize": parse_int(
                            columns[5], 1
                        ) if len(columns) > 5 else 1,
                        "instrumenttype": "EQ",
                        "tick_size": parse_float(
                            columns[4], 0.05
                        ) if len(columns) > 4 else 0.05,
                    }
                )

                continue

            if exchange in ("NFO", "BFO"):
                # Derivatives: 0 Token, 1 InstrumentName, 2 ShortName
                # (underlying), 3 Series, 4 ExpiryDate, 5 StrikePrice,
                # 6 OptionType, 27 LotSize, 28 TickSize, 29 CompanyName.
                if len(columns) < 7:
                    continue

                series = columns[3]

                if not any(marker in series.upper() for marker in DERIVATIVE_SERIES):
                    continue

                underlying = to_openalgo_underlying(columns[2])

                if not underlying:
                    continue

                symbol = contract_symbol(
                    underlying,
                    series,
                    columns[4],
                    columns[5] if len(columns) > 5 else "0",
                    columns[6] if len(columns) > 6 else "",
                )

                if not symbol:
                    continue

                instrument_type = "FUT" if "FUT" in series.upper() else (
                    "CE" if columns[6].upper().startswith("C") else "PE"
                )

                lotsize = (
                    parse_int(columns[27], 1)
                    if len(columns) > 27 and columns[27]
                    else 1
                )

                tick_raw = (
                    parse_float(columns[28], 0.05)
                    if len(columns) > 28 and columns[28]
                    else 0.05
                )

                rows.append(
                    {
                        "symbol": symbol,
                        "brsymbol": symbol,
                        "name": (
                            columns[29] if len(columns) > 29 else underlying
                        ),
                        "exchange": exchange,
                        "brexchange": exchange,
                        "token": token,
                        "expiry": expiry_display(columns[4]),
                        "strike": parse_float(columns[5], 0.0),
                        "lotsize": max(lotsize, 1),
                        "instrumenttype": instrument_type,
                        "tick_size": tick_raw if tick_raw < 1 else tick_raw / 100,
                    }
                )

                continue

            if exchange == "MCX":
                # 0 Token, 1 InstrumentName, 2 ShortName, 3 Series,
                # 4 CompanyName, 5 TickSize, 6 LotSize, 7 ExpiryDate,
                # 8 OptionType, 9 StrikePrice.
                if len(columns) < 10:
                    continue

                series = columns[3]

                if not any(marker in series.upper() for marker in DERIVATIVE_SERIES):
                    continue

                underlying = to_openalgo_underlying(columns[2])

                if not underlying:
                    continue

                symbol = contract_symbol(
                    underlying,
                    series,
                    columns[7],
                    columns[9] if len(columns) > 9 else "0",
                    columns[8] if len(columns) > 8 else "",
                )

                if not symbol:
                    continue

                instrument_type = "FUT" if "FUT" in series.upper() else (
                    "CE" if columns[8].upper().startswith("C") else "PE"
                )

                tick_raw = parse_float(columns[5], 0.05) if columns[5] else 0.05

                rows.append(
                    {
                        "symbol": symbol,
                        "brsymbol": symbol,
                        "name": columns[4] if len(columns) > 4 else underlying,
                        "exchange": "MCX",
                        "brexchange": "MCX",
                        "token": token,
                        "expiry": expiry_display(columns[7]),
                        "strike": parse_float(columns[9], 0.0),
                        "lotsize": max(parse_int(columns[6], 1), 1),
                        "instrumenttype": instrument_type,
                        "tick_size": tick_raw if tick_raw < 1 else tick_raw / 100,
                    }
                )

        except Exception:
            logger.exception(
                "Error parsing ICICI master row in %s", filename
            )

    return rows


# ---------------------------------------------------------------------------
# Download entry point
# ---------------------------------------------------------------------------

def master_contract_download():
    """
    Download SecurityMaster.zip, rebuild the symtoken rows and emit the
    completion socket event (OpenAlgo master-contract contract).
    """

    try:
        logger.info("Starting ICICI Direct SecurityMaster download")

        init_db()

        response = requests.get(SECURITY_MASTER_URL, timeout=120)

        response.raise_for_status()

        rows: list[dict] = []

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            for filename in archive.namelist():
                if not filename.lower().endswith(".txt"):
                    continue

                rows.extend(
                    parse_file(filename, archive.read(filename))
                )

        if not rows:
            raise RuntimeError(
                "ICICI SecurityMaster contained no usable instruments"
            )

        df = pd.DataFrame(rows)

        df = df.drop_duplicates(subset=["exchange", "token"])

        # Weekly + monthly contracts can converge onto one expiry: keep
        # the first (nearest-listed) token per OpenAlgo symbol.
        df = df.drop_duplicates(subset=["exchange", "symbol"], keep="first")

        delete_symtoken_table()

        copy_from_dataframe(df)

        logger.info(
            "ICICI Direct master contract loaded: %d rows", len(df)
        )

        socketio.emit(
            "master_contract_download",
            {"status": "success", "broker": "icici", "count": len(df)},
        )

        return {"status": "success"}

    except Exception as exc:
        db_session.rollback()

        logger.exception("ICICI master contract download failed")

        socketio.emit(
            "master_contract_download",
            {
                "status": "error",
                "broker": "icici",
                "message": str(exc),
            },
        )

        return {"status": "error", "message": str(exc)}
