# api/margin_api.py
# Margin calculation for the ICICI Direct Breeze API.

from utils.logging import get_logger

logger = get_logger(__name__)


def calculate_margin_api(positions, auth):
    """Report that ICICI Direct offers no pre-trade margin calculator.

    The Breeze "margin" endpoint only reports account-level limits after
    allocation; there is no position-specific or basket margin API to call.
    Raises like the paytm plugin so the margin service surfaces a clear
    message instead of fabricating numbers.
    """
    logger.warning("ICICI Direct does not provide a position-specific margin calculator API")
    raise NotImplementedError(
        "ICICI Direct does not support the margin calculator API"
    )
