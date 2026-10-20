"""
ICICI Direct Breeze funds adapter for OpenAlgo.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from broker.icici.api.breeze_client import get_breeze_client


logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.5


def _as_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert Breeze numeric values to float.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return float(value)

    try:
        if isinstance(value, str):
            value = value.strip()

            if not value:
                return default

            # Breeze normally returns plain numeric strings.
            # Also tolerate commas if they ever appear.
            value = value.replace(",", "")

        return float(value)

    except (TypeError, ValueError):
        logger.warning(
            "Unable to convert ICICI funds value %r to float",
            value,
        )
        return default


def _extract_success(response: Any) -> dict:
    """
    Normalize the Breeze get_funds response.

    Expected Breeze structure:

    {
        "Success": {
            "total_bank_balance": ...,
            "allocated_equity": ...,
            "allocated_fno": ...,
            "block_by_trade_balance": ...,
            "unallocated_balance": ...
        },
        "Status": 200,
        "Error": None
    }
    """

    if not isinstance(response, dict):
        raise RuntimeError(
            "Invalid ICICI Direct funds response"
        )

    success = response.get("Success")

    # Some Breeze responses may represent Success as a list.
    if isinstance(success, list):
        success = success[0] if success else {}

    if not isinstance(success, dict):
        success = {}

    if success:
        return success

    error = response.get("Error")

    if error:
        raise RuntimeError(
            f"ICICI Direct funds API error: {error}"
        )

    raise RuntimeError(
        "ICICI Direct returned no funds data"
    )


def _get_funds_once(breeze) -> dict:
    """
    Call Breeze get_funds once.

    The Breeze SDK can raise an exception when ICICI returns
    a temporary non-JSON/503 response.
    """
    response = breeze.get_funds()

    return _extract_success(response)


def get_margin_data(auth_token):
    """
    Return ICICI Direct funds in the OpenAlgo funds format.

    OpenAlgo calls this function as:

        get_margin_data(auth_token)

    The Breeze client is obtained from the broker-specific
    breeze_client module so the existing authentication/session
    handling remains unchanged.
    """

    if not auth_token:
        raise RuntimeError(
            "ICICI Direct auth token is missing"
        )

    breeze = get_breeze_client(auth_token)

    last_error = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            success = _get_funds_once(breeze)

            total_bank_balance = _as_float(
                success.get("total_bank_balance")
            )

            allocated_equity = _as_float(
                success.get("allocated_equity")
            )

            allocated_fno = _as_float(
                success.get("allocated_fno")
            )

            allocated_commodity = _as_float(
                success.get("allocated_commodity")
            )

            allocated_currency = _as_float(
                success.get("allocated_currency")
            )

            blocked_balance = _as_float(
                success.get("block_by_trade_balance")
            )

            # This is the value your previous working implementation
            # was receiving from Breeze and should be displayed as
            # available cash by OpenAlgo.
            unallocated_raw = success.get(
                "unallocated_balance"
            )

            if (
                unallocated_raw is None
                or str(unallocated_raw).strip() == ""
            ):
                allocated_total = (
                    allocated_equity
                    + allocated_fno
                    + allocated_commodity
                    + allocated_currency
                )

                available_cash = (
                    total_bank_balance
                    - allocated_total
                )
            else:
                available_cash = _as_float(
                    unallocated_raw
                )

            # Keep collateral compatible with the existing
            # OpenAlgo funds response. Equity allocation is the
            # most directly relevant collateral field for ICICI.
            collateral = allocated_equity

            result = {
                "availablecash": str(
                    available_cash
                ),
                "collateral": str(
                    collateral
                ),
                "m2munrealized": "0",
                "m2mrealized": "0",
                "utiliseddebits": str(
                    blocked_balance
                ),
            }

            logger.debug(
                "ICICI Direct funds mapped successfully: %s",
                result,
            )

            return result

        except Exception as exc:
            last_error = exc

            logger.warning(
                "ICICI Direct get_funds attempt %d/%d failed: %s",
                attempt,
                _MAX_ATTEMPTS,
                exc,
            )

            if attempt < _MAX_ATTEMPTS:
                time.sleep(
                    _RETRY_DELAY_SECONDS * attempt
                )

    raise RuntimeError(
        f"ICICI Direct funds request failed after "
        f"{_MAX_ATTEMPTS} attempts: {last_error}"
    )


# OpenAlgo broker-service compatibility.
get_funds = get_margin_data
