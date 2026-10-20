"""
ICICI Direct Breeze funds -> OpenAlgo funds view.
"""

from broker.icici.api.breeze_client import breeze_request, response_status
from broker.icici.mapping.margin_data import map_fund_data
from utils.logging import get_logger


logger = get_logger(__name__)


def get_margin_data(auth):
    """
    Account funds. Returns the OpenAlgo funds dict ({} on error):

        {availablecash, collateral, m2munrealized, m2mrealized,
         utiliseddebits}
    """

    try:
        response = breeze_request(auth, "GET", "funds")

        if response_status(response) != 200:
            logger.warning(
                "ICICI funds failed: %s", response.get("Error") if isinstance(
                    response, dict
                ) else response
            )

            return {}

        return map_fund_data(response)

    except Exception:
        logger.exception("Error fetching ICICI funds")

        return {}
