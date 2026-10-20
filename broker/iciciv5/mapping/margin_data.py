"""
ICICI Direct Breeze funds/holdings value normalisation.
"""

from typing import Any


def _first(row: dict, *keys, default=0.0):
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


def map_fund_data(response: Any) -> dict:
    """
    Breeze GET /funds response -> OpenAlgo funds dict.

    Breeze returns a per-segment list of balances; cash segments carry the
    clear/collateral/utilised values the OpenAlgo view aggregates.
    M2M fields are not part of the Breeze funds payload (they are 0.00).
    """

    rows = []

    if isinstance(response, dict):
        success = response.get("Success")

        if isinstance(success, list):
            rows = [row for row in success if isinstance(row, dict)]
        elif isinstance(success, dict):
            rows = [success]

    if not rows:
        return {}

    availablecash = 0.0
    collateral = 0.0
    utilised = 0.0

    for row in rows:
        segment = str(
            _first(row, "segment", "exchange_code", "type", default="")
        ).lower()

        # Currency/commodity segments are excluded from the equity view so
        # the displayed cash matches what the equity screens can spend.
        if segment in ("cds", "ndx", "commodity", "mcx"):
            continue

        availablecash += _to_float(
            _first(
                row,
                "clear_balance",
                "clearbalance",
                "available_margin",
                "total_balance",
                default=0,
            )
        )

        collateral += _to_float(
            _first(
                row,
                "collateral_margin",
                "collateral",
                "pledge_margin",
                default=0,
            )
        )

        utilised += _to_float(
            _first(
                row,
                "margin_utilized",
                "margin_utilised",
                "utilised_margin",
                "utilised_debits",
                default=0,
            )
        )

    return {
        "availablecash": f"{availablecash:.2f}",
        "collateral": f"{collateral:.2f}",
        "m2munrealized": "0.00",
        "m2mrealized": "0.00",
        "utiliseddebits": f"{utilised:.2f}",
    }
