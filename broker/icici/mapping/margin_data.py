# broker/icici/mapping/margin_data.py
# Margin payload helpers for the ICICI Direct plugin.
#
# The Breeze API does not expose a pre-trade basket margin calculator - its
# "margin" endpoint only reports account-level limits after allocation. The
# api/margin_api.py module therefore reports the gap explicitly; these
# helpers exist to keep the mapping layer's shape aligned with the other
# plugins that do implement the calculation.

from utils.logging import get_logger

logger = get_logger(__name__)


def map_product_type_for_margin(product):
    """OpenAlgo product -> the product label used in margin payloads."""
    mapping = {"CNC": "cash", "MIS": "margin", "NRML": "margin"}
    return mapping.get((product or "").upper(), "margin")


def transform_margin_position(position, client_id):
    """Identity passthrough - ICICI offers no per-position margin API."""
    return None


def parse_margin_response(response_data):
    """Normalize an upstream margin payload into the shared result shape."""
    if isinstance(response_data, dict) and response_data.get("status") == "success":
        return response_data
    return {
        "status": "error",
        "message": (response_data or {}).get("message", "Margin calculation failed")
        if isinstance(response_data, dict)
        else "Margin calculation failed",
    }
