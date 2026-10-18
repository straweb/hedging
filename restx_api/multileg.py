"""
Multi-Leg Order API Endpoint

POST /api/v1/multileg

Places a batch of independent legs — exact symbols, per-leg action/quantity/
pricetype — and reports a per-leg result so the caller can see partial
success. The execution backend for the /hedging terminal's Deploy / Close /
Swap / Exit actions; also usable from the REST API for any symbol batch
(options legs, equity legs, futures).

Request Body:
{
    "apikey": "your_api_key",
    "strategy": "HEDGING",
    "legs": [
        {
            "legid": "ce-26000",            // optional, echoed back on the result
            "symbol": "NIFTY28OCT2626000CE",
            "exchange": "NFO",
            "action": "BUY",                 // BUY | SELL (case insensitive)
            "quantity": 75,
            "pricetype": "MARKET",           // MARKET | LIMIT | SL | SL-M
            "product": "MIS",                // MIS | NRML | CNC
            "price": 0.0,                    // LIMIT/SL price
            "trigger_price": 0.0             // SL/SL-M trigger
        }
    ]
}

Response (per-leg partial success):
{
    "status": "success",
    "mode": "live",
    "results": [
        {"legid": "ce-26000", "symbol": "NIFTY28OCT2626000CE", "exchange": "NFO",
         "action": "BUY", "status": "success", "orderid": "240000000123"},
        {"legid": "ce-26100", "symbol": "NIFTY28OCT2626100CE", "exchange": "NFO",
         "action": "BUY", "status": "error", "message": "Insufficient funds"}
    ],
    "successful": 1,
    "total": 2
}

Notes:
- Leg order is preserved: the caller sequences close-before-open legs for a
  swap by passing them in that order.
- Analyzer (sandbox) mode routes every leg to the sandbox and returns
  "mode": "analyze" — the same partial-failure report.
- Legs are placed in batches of 10 with a 1s delay between batches (same
  pacing as basket orders).
"""

import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from restx_api.schemas import MultilegSchema
from services.multileg_service import place_multileg_order
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

# Create namespace
api = Namespace("multileg", description="Multi-Leg Order API")

# Get rate limit from environment
ORDER_RATE_LIMIT = os.getenv("ORDER_RATE_LIMIT", "10 per second")


@api.route("/", strict_slashes=False)
class MultilegOrder(Resource):
    @limiter.limit(ORDER_RATE_LIMIT)
    def post(self):
        """
        Place a batch of legs with per-leg results (partial success allowed).
        """
        try:
            schema = MultilegSchema()
            data = schema.load(request.json)

            api_key = data.get("apikey")

            logger.info(
                f"Multileg API request: strategy={data.get('strategy')}, "
                f"legs={len(data.get('legs', []))}"
            )

            success, response_data, status_code = place_multileg_order(
                multileg_data=data, api_key=api_key
            )

            return make_response(jsonify(response_data), status_code)

        except ValidationError as err:
            logger.warning(f"Validation error in multileg request: {err.messages}")
            return make_response(
                jsonify({"status": "error", "message": "Validation error", "errors": err.messages}),
                400,
            )
        except Exception:
            logger.exception("An unexpected error occurred in the Multileg endpoint.")
            error_response = {
                "status": "error",
                "message": "An unexpected error occurred in the API endpoint",
            }
            return make_response(jsonify(error_response), 500)
