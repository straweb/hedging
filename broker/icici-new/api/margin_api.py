"""
ICICI Direct Breeze margin calculation for OpenAlgo.

Breeze exposes no basket-margin calculator, so ``calculate_margin_api``
reports the account's available segment margin (GET /margin per exchange)
alongside zeroed span/exposure components rather than inventing numbers.
The OpenAlgo margin view renders ``total_margin_required`` /
``span_margin`` / ``exposure_margin`` — the available balance is surfaced
through ``total_margin_required`` only when the broker returns usable
figures, otherwise an explicit error message is returned.
"""

from broker.icici.api.breeze_client import breeze_request, response_error, response_status
from utils.logging import get_logger


logger = get_logger(__name__)


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_margin(response: dict) -> dict:
    """
    Tolerant extraction of margin figures from a Breeze /margin response.
    """

    success = response.get("Success") if isinstance(response, dict) else None

    rows = []

    if isinstance(success, list):
        rows = [row for row in success if isinstance(row, dict)]
    elif isinstance(success, dict):
        rows = [success]

    total = span = exposure = 0.0

    for row in rows:
        total += _to_float(
            row.get("total_margin")
            or row.get("available_margin")
            or row.get("total_balance")
            or 0
        )

        span += _to_float(row.get("span_margin") or row.get("span") or 0)

        exposure += _to_float(
            row.get("exposure_margin") or row.get("exposure") or 0
        )

    return {"total": total, "span": span, "exposure": exposure}


def calculate_margin_api(positions, auth):
    """
    Return (response_object, data) where ``data.data`` carries
    {total_margin_required, span_margin, exposure_margin[, total_charges]}.
    """

    class MarginResponse:
        def __init__(self, status):
            self.status = status

    try:
        exchanges = set()

        if isinstance(positions, dict):
            positions = [positions]

        for position in positions or []:
            if isinstance(position, dict):
                exchange = str(
                    position.get("exchange")
                    or position.get("exchange_code")
                    or ""
                ).strip().upper()

                if exchange:
                    exchanges.add(exchange)

        if not exchanges:
            exchanges = {"NSE"}

        totals = {"total": 0.0, "span": 0.0, "exposure": 0.0}

        errors = []

        from broker.icici.mapping.transform_data import get_breeze_exchange

        for exchange in exchanges:
            response = breeze_request(
                auth,
                "GET",
                "margin",
                {"exchange_code": get_breeze_exchange(exchange)},
            )

            if response_status(response) != 200:
                errors.append(response_error(response))

                continue

            figures = _extract_margin(response)

            for key in totals:
                totals[key] += figures[key]

        if not totals["total"] and errors:
            message = "; ".join(error for error in errors if error)

            return MarginResponse(502), {
                "status": "error",
                "message": message
                or "ICICI Breeze did not return margin figures",
            }

        data = {
            "data": {
                "total_margin_required": f"{totals['total']:.2f}",
                "span_margin": f"{totals['span']:.2f}",
                "exposure_margin": f"{totals['exposure']:.2f}",
                "total_charges": "0.00",
            }
        }

        return MarginResponse(200), data

    except Exception as exc:
        logger.exception("ICICI margin calculation failed")

        return MarginResponse(502), {
            "status": "error",
            "message": f"ICICI margin calculation failed: {exc}",
        }
