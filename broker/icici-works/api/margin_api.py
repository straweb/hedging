# api/margin_api.py
# Margin calculation for the ICICI Direct Breeze API.
#
# Breeze's basket calculator lives at POST "margincalculator" and is
# exchange-scoped: the request carries one list of legs plus a single
# exchange_code. A multi-exchange OpenAlgo basket is therefore transformed
# once (mapping/margin_data.py), grouped per exchange, and priced with one
# call per F&O group; equity legs are valued at order value because the
# endpoint does not price cash orders.

import json

from broker.icici.api.breeze_client import extract_error, is_success, request_raw
from broker.icici.mapping.margin_data import parse_margin_response, transform_margin_positions
from utils.logging import get_logger

logger = get_logger(__name__)


def calculate_margin_api(positions, auth):
    """Calculate the margin for a basket of positions. Returns (response, response_data).

    ``positions`` is the OpenAlgo margin request format:
    {symbol, exchange, action, quantity, pricetype, product, price,
    trigger_price}. The response_data follows the shared OpenAlgo contract:
    {"status": "success", "data": {"total_margin_required", "span_margin",
    "exposure_margin"}}.
    """
    if not positions:
        return _MarginResponse(400), {"status": "error", "message": "positions array cannot be empty"}

    try:
        groups, cash_values, skipped = transform_margin_positions(positions, auth)

        if skipped:
            logger.warning(f"Margin: skipped legs: {', '.join(skipped)}")

        if not groups and not cash_values:
            return _MarginResponse(400), {
                "status": "error",
                "message": "No valid positions to calculate margin. Check if symbols are valid.",
            }

        responses = {}
        for exchange_code, legs in groups.items():
            payload = json.dumps(
                {"lists": legs, "exchange_code": exchange_code}, separators=(",", ":")
            )
            logger.debug(f"Margin calculator {exchange_code}: {len(legs)} leg(s)")

            _, data = request_raw("margincalculator", auth, "POST", payload)
            responses[exchange_code] = data

            if not is_success(data):
                logger.error(
                    f"Margin calculator failed for {exchange_code}: {extract_error(data)}"
                )

        # Only exchange groups that actually answered are aggregated; a group
        # that failed makes the whole calculation an error (never a silent 0).
        answered = {
            code: data
            for code, data in responses.items()
            if isinstance(data, dict) and data.get("Success") is not None
        }
        if not answered and groups:
            failed_code = next(iter(responses))
            return _MarginResponse(502), {
                "status": "error",
                "message": f"Margin calculation failed for {failed_code}: "
                f"{extract_error(responses[failed_code]) or 'API error'}",
            }

        response_data = parse_margin_response(answered, cash_values)
        if response_data.get("status") != "success":
            return _MarginResponse(502), response_data

        logger.info(f"Margin calculation result: {response_data['data']}")
        return _MarginResponse(200), response_data

    except Exception as e:
        logger.exception("Error calculating Breeze margin")
        return _MarginResponse(500), {"status": "error", "message": f"Failed to calculate margin: {str(e)}"}


class _MarginResponse:
    """Response stand-in with the .status/.status_code pair the service reads."""

    def __init__(self, status_code):
        self.status = status_code
        self.status_code = status_code
