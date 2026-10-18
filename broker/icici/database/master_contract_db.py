# database/master_contract_db.py
# Instrument master for the ICICI Direct (Breeze) plugin.
#
# The instrument universe comes from ICICI's downloadable SecurityMaster.zip
# (the same archive the official Breeze SDK consumes), not from the paginated
# namecodes endpoint. It contains one headered CSV per segment:
#
#   NSEScripMaster.txt   61 cols - equity (Series EQ/BE) + index rows (Series "0")
#   BSEScripMaster.txt   34 cols - Series "C" equities (+ index rows BSESEN/BANKEX)
#   FONSEScripMaster.txt 69 cols - NFO futures/options, ShortName = ICICI stock code
#   FOBSEScripMaster.txt 69 cols - BFO futures/options
#   MCXScripMaster.txt   27 cols - MCX futures/options (tick size in paise!)
#   CDNSEScripMaster.txt 69 cols - currency: only UNDCUR spot rows, no tradables
#   MFScripMaster.txt    17 cols - mutual funds, not tradable
#
# Column references (0-based), verified against the live archive:
#   NSE:  0 Token, 1 ShortName, 2 Series, 3 CompanyName, 4 tick, 5 Lotsize
#   BSE:  0 Token, 1 ShortName, 2 Series, 3 CompanyName, 4 TickSize, 5 LotSize
#   NFO/BFO: 0 Token, 1 InstrumentName, 2 ShortName, 3 Series, 4 ExpiryDate,
#            5 StrikePrice, 6 OptionType, 27 LotSize, 28 TickSize, 29 CompanyName
#   MCX:  0 Token, 1 InstrumentName, 2 ShortName, 3 Series, 4 CompanyName,
#         5 TickSize (paise), 6 LotSize, 7 ExpiryDate, 8 OptionType, 9 StrikePrice

import io
import os
import zipfile
from datetime import datetime

import pandas as pd
import requests
from sqlalchemy import Column, Float, Index, Integer, Sequence, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from database.auth_db import get_auth_token  # noqa: F401  (kept for parity)
from database.engine_factory import create_db_engine
from extensions import socketio
from utils.logging import get_logger

logger = get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_db_engine(DATABASE_URL)
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
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

    __table_args__ = (Index("idx_symbol_exchange", "symbol", "exchange"),)


def init_db():
    logger.info("Initializing Master Contract DB")
    Base.metadata.create_all(bind=engine)


def delete_symtoken_table():
    logger.info("Deleting Symtoken Table")
    SymToken.query.delete()
    db_session.commit()


def copy_from_dataframe(df):
    logger.info("Performing Bulk Insert")
    data_dict = df.to_dict(orient="records")

    existing_tokens = {result.token for result in db_session.query(SymToken.token).all()}
    filtered_data_dict = [row for row in data_dict if row["token"] not in existing_tokens]

    try:
        if filtered_data_dict:
            db_session.bulk_insert_mappings(SymToken, filtered_data_dict)
            db_session.commit()
            logger.info(
                f"Bulk insert completed successfully with {len(filtered_data_dict)} new records."
            )
        else:
            logger.info("No new records to insert.")
    except Exception as e:
        logger.exception(f"Error during bulk insert: {e}")
        db_session.rollback()


# ----------------------------------------------------------------------
# ICICI internal index codes -> OpenAlgo index symbols. Only codes with an
# OpenAlgo standard name are carried; everything else on the index rows is
# dropped so symbol search cannot offer an unquotable index.
# ----------------------------------------------------------------------
BREEZE_TO_OA_UNDERLYING = {
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

# Derivative underlyings that exist in the file but have no real NSE/BSE F&O
# book (ICICI-internal labels).
SKIP_UNDERLYINGS = {"BSEFOC", "NIF150"}

_EQUITY_PRIORITY = {"EQ": 0, "C": 0, "BE": 1}


def _cell(row, index):
    """Stripped cell value or '' when the row is short."""
    if index < len(row):
        return row[index].strip().strip('"').strip()
    return ""


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _expiry_parts(expiry_text):
    """'23-Nov-2026' -> (ddmmmyy '23NOV26', stored '23-NOV-26')."""
    parsed = None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(expiry_text.strip(), fmt)
            break
        except ValueError:
            continue
    if not parsed:
        return "", ""
    return parsed.strftime("%d%b%y").upper(), parsed.strftime("%d-%b-%y").upper()


def _format_strike(strike_text):
    """'24500.0' -> '24500', '292.50' -> '292.5' (OpenAlgo strike format)."""
    value = _num(strike_text, 0.0)
    if value == 0:
        return "0"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _token_ok(token):
    """A usable broker token: non-empty, not an all-zero placeholder or "NA".

    Dead/delisted NSE rows ship Token 0 / 00 and suspended scrips ship "NA";
    their reverse lookups would collide on a single value.
    """
    return bool(token) and token.upper() != "NA" and _int(token, -1) != 0


def _derivative_rows(csv_rows, records, brexchange, oa_exchange, tick_divisor=1.0):
    """FONSE/FOBSE/MCX rows -> derivative records (appended to `records`)."""
    for row in csv_rows:
        token = _cell(row, 0)
        if not _token_ok(token):
            continue
        instrument = _cell(row, 1).upper()
        stock_code = _cell(row, 2)
        series = _cell(row, 3).upper()
        if series not in ("FUTURE", "OPTION"):
            continue
        if stock_code in SKIP_UNDERLYINGS:
            continue

        underlying = BREEZE_TO_OA_UNDERLYING.get(stock_code, stock_code)

        # Column offsets differ between the F&O (69-col) and MCX (27-col) files.
        if oa_exchange == "MCX":
            # MCX ShortNames are ICICI-internal (CRUDE, SILMIN, ALUMIN); the
            # CompanyName column carries the market name OpenAlgo uses
            # (CRUDEOIL, SILVERM, ALUMINIUM).
            underlying = _cell(row, 4) or underlying
            expiry_text = _cell(row, 7)
            option_type = _cell(row, 8).upper()
            strike_text = _cell(row, 9)
            lotsize = _int(_cell(row, 6), 1)
            tick_size = _num(_cell(row, 5)) / tick_divisor
        else:
            expiry_text = _cell(row, 4)
            option_type = _cell(row, 6).upper()
            strike_text = _cell(row, 5)
            lotsize = _int(_cell(row, 27), 1)
            tick_size = _num(_cell(row, 28)) / tick_divisor

        expiry_sym, expiry_stored = _expiry_parts(expiry_text)
        if not expiry_sym:
            continue

        if series == "FUTURE":
            rows_symbol = f"{underlying}{expiry_sym}FUT"
            instrumenttype = "FUT"
            strike = 0.0
        else:
            if option_type not in ("CE", "PE"):
                continue
            strike = _num(strike_text)
            strike_str = _format_strike(strike_text)
            if strike_str in ("", "0"):
                continue
            rows_symbol = f"{underlying}{expiry_sym}{strike_str}{option_type}"
            instrumenttype = option_type

        records.append(
            {
                "symbol": rows_symbol,
                "brsymbol": stock_code,
                "name": underlying,
                "exchange": oa_exchange,
                "brexchange": brexchange,
                "token": token,
                "expiry": expiry_stored,
                "strike": strike,
                "lotsize": lotsize,
                "instrumenttype": instrumenttype,
                "tick_size": tick_size,
            }
        )


def process_security_master(zip_bytes):
    """Parse SecurityMaster.zip bytes into the symtoken DataFrame.

    Kept as a pure function over the archive so the CSV dialects, column
    offsets and symbol formatting can be unit tested without network access.
    """
    records = []
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))

    def read_rows(name):
        import csv

        with archive.open(name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
            next(reader, None)  # header row
            return list(reader)

    # --- NSE equity + index rows -----------------------------------------
    for row in read_rows("NSEScripMaster.txt"):
        token = _cell(row, 0)
        short_name = _cell(row, 1)
        series = _cell(row, 2)
        company = _cell(row, 3)
        if not short_name:
            continue
        if series == "0":
            # Index row; only carry codes with an OpenAlgo standard symbol.
            oa_symbol = BREEZE_TO_OA_UNDERLYING.get(short_name)
            if not oa_symbol:
                continue
            records.append(
                {
                    "symbol": oa_symbol,
                    "brsymbol": short_name,
                    "name": company or oa_symbol,
                    "exchange": "NSE_INDEX",
                    "brexchange": "NSE",
                    "token": short_name,
                    "expiry": "",
                    "strike": 0.0,
                    "lotsize": 1,
                    "instrumenttype": "INDEX",
                    "tick_size": _num(_cell(row, 4), 0.05) or 0.05,
                }
            )
            continue

        if series not in _EQUITY_PRIORITY:
            continue
        if not _token_ok(token):
            continue
        records.append(
            {
                "symbol": short_name,
                "brsymbol": short_name,
                "name": company or short_name,
                "exchange": "NSE",
                "brexchange": "NSE",
                "token": token,
                "expiry": "",
                "strike": 0.0,
                "lotsize": max(_int(_cell(row, 5), 1), 1),
                "instrumenttype": "EQ",
                "tick_size": _num(_cell(row, 4), 0.01) or 0.01,
            }
        )

    # --- BSE equity + index rows ------------------------------------------
    for row in read_rows("BSEScripMaster.txt"):
        token = _cell(row, 0)
        short_name = _cell(row, 1)
        series = _cell(row, 2)
        company = _cell(row, 3)
        if not short_name:
            continue

        if short_name in ("BSESEN", "BANKEX"):
            records.append(
                {
                    "symbol": BREEZE_TO_OA_UNDERLYING[short_name],
                    "brsymbol": short_name,
                    "name": company or short_name,
                    "exchange": "BSE_INDEX",
                    "brexchange": "BSE",
                    "token": short_name,
                    "expiry": "",
                    "strike": 0.0,
                    "lotsize": 1,
                    "instrumenttype": "INDEX",
                    "tick_size": 0.05,
                }
            )
            continue

        if series != "C" or not token or token == "0":
            continue
        # Rights entitlements share the file (company name ends with " RE").
        if (company or "").upper().endswith(" RE"):
            continue
        records.append(
            {
                "symbol": short_name,
                "brsymbol": short_name,
                "name": company or short_name,
                "exchange": "BSE",
                "brexchange": "BSE",
                "token": token,
                "expiry": "",
                "strike": 0.0,
                "lotsize": max(_int(_cell(row, 5), 1), 1),
                "instrumenttype": "EQ",
                "tick_size": _num(_cell(row, 4), 0.01) or 0.01,
            }
        )

    # --- NFO / BFO / MCX derivatives --------------------------------------
    _derivative_rows(read_rows("FONSEScripMaster.txt"), records, "NFO", "NFO")
    _derivative_rows(read_rows("FOBSEScripMaster.txt"), records, "BFO", "BFO")
    # MCX tick sizes ship in paise (SILVER "100" = Rs 1.00).
    _derivative_rows(
        read_rows("MCXScripMaster.txt"), records, "MCX", "MCX", tick_divisor=100.0
    )
    # CDNSE ships only UNDCUR spot rows (no tradable currency instruments),
    # and MFScripMaster is mutual funds - both are skipped by design.

    df = pd.DataFrame(records)

    # Equity symbols can repeat across series (EQ preferred over BE).
    if not df.empty:
        df["_series_prio"] = df["instrumenttype"].map(lambda it: 0 if it == "EQ" else -1)
        df = df.sort_values(by=["_series_prio"], kind="stable")
        dup_mask = df.duplicated(subset=["exchange", "symbol"], keep="first")
        if dup_mask.any():
            samples = df.loc[dup_mask, ["exchange", "symbol"]].head(6).to_dict("records")
            logger.info(
                f"ICICI master contract: dropping {int(dup_mask.sum())} duplicate "
                f"(exchange, symbol) equity rows (samples: {samples})"
            )
            df = df[~dup_mask]
        df = df.drop(columns=["_series_prio"])

        # (exchange, token) drives the reverse lookup in get_symbol() - it has
        # to be unique. Logged loudly rather than raised (#1929 convention).
        token_collisions = df[df.duplicated(subset=["exchange", "token"], keep=False)]
        if not token_collisions.empty:
            samples = [
                f"{r.exchange}/{r.token}: {r.brsymbol}"
                for r in token_collisions.sort_values(["exchange", "token"]).head(6).itertuples()
            ]
            logger.error(
                f"ICICI master contract: {len(token_collisions)} rows share a duplicate "
                f"(exchange, token). Samples: {samples}"
            )

    return df


def master_contract_download():
    """Download and ingest the ICICI SecurityMaster archive."""
    from broker.icici.api.baseurl import SECURITY_MASTER_URL

    logger.info("Downloading ICICI Direct Master Contract")
    try:
        response = requests.get(SECURITY_MASTER_URL, timeout=120)
        response.raise_for_status()

        delete_symtoken_table()
        token_df = process_security_master(response.content)
        copy_from_dataframe(token_df)

        logger.info(
            "ICICI master contract loaded: "
            f"{token_df['exchange'].value_counts().to_dict() if not token_df.empty else '{}'}"
        )

        return socketio.emit(
            "master_contract_download",
            {"status": "success", "message": "Successfully Downloaded"},
        )

    except Exception as e:
        logger.exception(f"Error during master contract download: {e}")
        return socketio.emit("master_contract_download", {"status": "error", "message": str(e)})


def search_symbols(symbol, exchange):
    return SymToken.query.filter(
        SymToken.symbol.like(f"%{symbol}%"), SymToken.exchange == exchange
    ).all()
