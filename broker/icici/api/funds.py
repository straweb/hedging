# api/funds.py
# Funds for the ICICI Direct Breeze API.

import json

from broker.icici.api.breeze_client import extract_error, is_success, request_raw
from broker.icici.api.order_api import get_positions
from utils.logging import get_logger

logger = get_logger(__name__)

ZERO_MARGIN = {
    "availablecash": "0.00",
    "collateral": "0.00",
    "m2munrealized": "0.00",
    "m2mrealized": "0.00",
    "utiliseddebits": "0.00",
}


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def test_auth_token(auth_token):
    """Validate the stored session token with a lightweight funds call.

    Returns (True, None) or (False, error_message). Required by the auth
    session-resume path in blueprints/auth.py.
    """
    try:
        data = request_raw("funds", auth_token, "GET", "{}")[1]
        if is_success(data):
            return True, None
        return False, extract_error(data) or "ICICI Direct session token is invalid or expired"
    except Exception as e:
        logger.error(f"Error testing Breeze auth token: {e}")
        return False, f"Error validating authentication: {str(e)}"


def get_margin_data(auth_token):
    """Funds summary in the shared five-key string format."""
    try:
        data = request_raw("funds", auth_token, "GET", "{}")[1]

        if not is_success(data):
            logger.error(f"Error fetching Breeze funds: {extract_error(data)}")
            return dict(ZERO_MARGIN)

        funds = data.get("Success") or {}

        # Breeze funds payload: unallocated_balance is the free cash,
        # block_by_trade_* are the per-segment utilised amounts. There is no
        # collateral or M2M split in the payload.
        available_cash = _num(funds.get("unallocated_balance"))
        utilised = (
            _num(funds.get("block_by_trade_equity"))
            + _num(funds.get("block_by_trade_fno"))
            + _num(funds.get("block_by_trade_commodity"))
            + _num(funds.get("block_by_trade_currency"))
        )

        # Unrealized M2M from the live positions, summed where reported.
        total_unrealized = 0.0
        try:
            positions = get_positions(auth_token)
            if isinstance(positions, list):
                for position in positions:
                    pnl = position.get("pnl")
                    if pnl is not None:
                        total_unrealized += _num(pnl)
        except Exception as e:
            logger.warning(f"Could not enrich funds with position M2M: {e}")

        return {
            "availablecash": f"{available_cash:.2f}",
            "collateral": "0.00",
            "m2munrealized": f"{total_unrealized:.2f}",
            "m2mrealized": "0.00",
            "utiliseddebits": f"{utilised:.2f}",
        }
    except KeyError:
        return {}
    except Exception as e:
        logger.error(f"Error fetching Breeze margin data: {e}")
        return dict(ZERO_MARGIN)
