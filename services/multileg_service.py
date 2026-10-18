"""
Multi-Leg Order Service

Places a batch of independent legs — exact symbols, per-leg action/quantity/
pricetype — in one call and reports per-leg success/failure. This is the
execution backend for the /hedging terminal's Deploy/Close/Swap/Exit actions:
the terminal composes the leg list (close legs first, then entries, for a
swap), the service preserves that order and fans the legs out in batches.

Analyzer (sandbox) mode is honoured the same way basket orders honour it: the
whole batch routes to the sandbox with a single multiquotes pre-fetch, so a
paper-trading session exercises the same partial-failure reporting as a live
one.

Request (simplified from the REST layer):
{
    "apikey": "...",
    "strategy": "HEDGING",
    "legs": [
        {"legid": "ce-26000", "symbol": "NIFTY28OCT2626000CE", "exchange": "NFO",
         "action": "BUY", "quantity": 75, "pricetype": "MARKET", "product": "MIS",
         "price": 0.0, "trigger_price": 0.0},
        ...
    ]
}

Response:
{
    "status": "success",
    "results": [
        {"legid": "ce-26000", "symbol": "...", "exchange": "NFO", "action": "BUY",
         "status": "success", "orderid": "240000000123"},
        {"legid": "ce-26100", "symbol": "...", "status": "error", "message": "..."}
    ],
    "successful": 1,
    "total": 2
}
"""

import copy
import importlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from database.auth_db import get_auth_token_broker
from database.settings_db import get_analyze_mode
from events import AnalyzerErrorEvent, BasketCompletedEvent, OrderFailedEvent
from utils.broker_backpressure import BrokerBusyError
from utils.constants import (
    REQUIRED_ORDER_FIELDS,
    VALID_ACTIONS,
    VALID_EXCHANGES,
    VALID_PRICE_TYPES,
    VALID_PRODUCT_TYPES,
)
from utils.event_bus import bus
from utils.logging import get_logger

logger = get_logger(__name__)


def emit_analyzer_error(request_data: dict[str, Any], error_message: str) -> dict[str, Any]:
    """Publish an analyzer error event and return the error response dict."""
    error_response = {"mode": "analyze", "status": "error", "message": error_message}

    analyzer_request = request_data.copy()
    if "apikey" in analyzer_request:
        del analyzer_request["apikey"]
    analyzer_request["api_type"] = "multileg"

    bus.publish(
        AnalyzerErrorEvent(
            mode="analyze",
            api_type="multileg",
            request_data=analyzer_request,
            response_data=error_response,
            error_message=error_message,
            api_key=request_data.get("apikey", ""),
        )
    )
    return error_response


def import_broker_module(broker_name: str) -> Any | None:
    """Dynamically import the broker-specific order API module."""
    try:
        module_path = f"broker.{broker_name}.api.order_api"
        return importlib.import_module(module_path)
    except ImportError as error:
        logger.error(f"Error importing broker module '{module_path}': {error}")
        return None


def validate_leg(leg: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate one leg; returns (ok, error message)."""
    missing_fields = [field for field in REQUIRED_ORDER_FIELDS if field not in leg]
    if missing_fields:
        return False, f"Missing mandatory field(s): {', '.join(missing_fields)}"

    if leg.get("exchange") not in VALID_EXCHANGES:
        return False, f"Invalid exchange. Must be one of: {', '.join(VALID_EXCHANGES)}"

    action = str(leg.get("action", "")).upper()
    if action not in VALID_ACTIONS:
        return False, f"Invalid action. Must be one of: {', '.join(VALID_ACTIONS)}"

    if leg.get("pricetype") and leg["pricetype"] not in VALID_PRICE_TYPES:
        return False, f"Invalid price type. Must be one of: {', '.join(VALID_PRICE_TYPES)}"

    if leg.get("product") and leg["product"] not in VALID_PRODUCT_TYPES:
        return False, f"Invalid product type. Must be one of: {', '.join(VALID_PRODUCT_TYPES)}"

    return True, None


def _leg_result(leg: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """A result row that always echoes what the caller needs to map it back."""
    return {
        "legid": leg.get("legid"),
        "symbol": leg.get("symbol", "Unknown"),
        "exchange": leg.get("exchange", ""),
        "action": str(leg.get("action", "")).upper(),
        **fields,
    }


def _place_live_leg(
    leg: dict[str, Any],
    broker_module: Any,
    auth_token: str,
    strategy: str,
    api_key: str,
) -> dict[str, Any]:
    """Place one leg against the live broker; never raises."""
    try:
        order_data = {
            **leg,
            "apikey": api_key,
            "action": str(leg.get("action", "")).upper(),
            "strategy": strategy,
        }
        # Validated on the merged form: REQUIRED_ORDER_FIELDS includes apikey
        # and strategy, which only exist after the merge (same as basket orders).
        is_valid, error_message = validate_leg(order_data)
        if not is_valid:
            return _leg_result(leg, status="error", message=error_message)

        res, response_data, _ = broker_module.place_order_api(order_data, auth_token)
        if res:
            return _leg_result(leg, status="success", orderid=response_data.get("orderid"))
        return _leg_result(
            leg, status="error", message=response_data.get("message", "Order placement failed")
        )
    except BrokerBusyError as e:
        return _leg_result(leg, status="error", message=str(e))
    except Exception as e:
        logger.exception(f"multileg live leg failed for {leg.get('symbol')}: {e}")
        return _leg_result(leg, status="error", message=str(e))


def _process_multileg_live(
    legs: list[dict[str, Any]],
    auth_token: str,
    broker: str,
    strategy: str,
    original_data: dict[str, Any],
    api_key: str,
) -> tuple[bool, dict[str, Any], int]:
    """Fan the legs out to the broker in batches of 10, preserving leg order."""
    broker_module = import_broker_module(broker)
    if broker_module is None:
        error_response = {"status": "error", "message": "Broker-specific module not found"}
        bus.publish(
            OrderFailedEvent(
                mode="live",
                api_type="multileg",
                request_data=original_data,
                response_data=error_response,
                error_message="Broker-specific module not found",
                api_key=original_data.get("apikey", ""),
            )
        )
        return False, error_response, 404

    BATCH_SIZE = 10
    batched: list[list[dict[str, Any]]] = [
        legs[i : i + BATCH_SIZE] for i in range(0, len(legs), BATCH_SIZE)
    ]

    results: list[dict[str, Any]] = []
    for index, batch in enumerate(batched):
        if index > 0:
            time.sleep(1.0)  # Rate limit delay between batches, as basket orders do

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = [
                executor.submit(
                    _place_live_leg, leg, broker_module, auth_token, strategy, api_key
                )
                for leg in batch
            ]
            for future in as_completed(futures):
                results.append(future.result())

        # Preserve the caller's leg order in the report regardless of completion order.
        done = {r.get("legid", r.get("symbol")) for r in results}
        batch_ids = [leg.get("legid", leg.get("symbol")) for leg in batch]
        if not all(identifier in done for identifier in batch_ids):
            # A worker raised through the executor's future.result() guard: report it.
            for leg, identifier in zip(batch, batch_ids):
                if identifier not in done:
                    results.append(
                        _leg_result(leg, status="error", message="Leg was not processed")
                    )

    successful = sum(1 for r in results if r.get("status") == "success")
    response_data = {
        "status": "success" if successful else "error",
        "mode": "live",
        "results": results,
        "successful": successful,
        "total": len(results),
    }
    bus.publish(
        BasketCompletedEvent(
            mode="live",
            api_type="multileg",
            strategy=strategy,
            results=results,
            successful=successful,
            total=len(results),
            request_data=original_data,
            response_data=response_data,
            api_key=original_data.get("apikey", ""),
        )
    )
    return True, response_data, 200


def place_multileg_order(
    multileg_data: dict[str, Any],
    api_key: str,
) -> tuple[bool, dict[str, Any], int]:
    """
    Place a batch of legs. Analyzer mode routes the batch to the sandbox;
    live mode fans out to the broker. Both return per-leg results.
    """
    original_data = copy.deepcopy(multileg_data)
    original_data["apikey"] = api_key
    legs: list[dict[str, Any]] = multileg_data["legs"]
    strategy = multileg_data.get("strategy") or "HEDGING"

    # ── Analyzer mode: sandbox each leg, one multiquotes pre-fetch for the batch ──
    if get_analyze_mode():
        from services.quotes_service import get_multiquotes
        from services.sandbox_service import sandbox_place_order

        quote_cache: dict[tuple[str, str], dict[str, Any]] = {}
        unique_symbols = {
            (leg.get("symbol"), leg.get("exchange"))
            for leg in legs
            if leg.get("symbol") and leg.get("exchange")
        }
        if unique_symbols:
            try:
                success_mq, mq_response, _ = get_multiquotes(
                    symbols=[{"symbol": s, "exchange": e} for s, e in unique_symbols],
                    api_key=api_key,
                )
                if success_mq and "results" in mq_response:
                    for result in mq_response["results"]:
                        key = (result.get("symbol"), result.get("exchange"))
                        if key[0] and key[1] and result.get("data"):
                            quote_cache[key] = result["data"]
            except Exception as e:
                logger.debug(f"multileg multiquotes pre-fetch failed: {e}")

        results: list[dict[str, Any]] = []
        for leg in legs:
            order_with_auth = {**leg, "apikey": api_key, "strategy": strategy}
            is_valid, error_message = validate_leg(order_with_auth)
            if not is_valid:
                results.append(_leg_result(leg, status="error", message=error_message))
                continue

            prefetched = quote_cache.get((leg.get("symbol"), leg.get("exchange")))
            success, response, _ = sandbox_place_order(
                order_with_auth, api_key, {"apikey": api_key, "order_type": "multileg"},
                prefetched_quote=prefetched,
            )
            if success:
                results.append(
                    _leg_result(leg, status="success", orderid=response.get("orderid"))
                )
            else:
                results.append(
                    _leg_result(
                        leg,
                        status="error",
                        message=response.get("message", "Order placement failed"),
                    )
                )

        successful = sum(1 for r in results if r.get("status") == "success")
        response_data = {
            "status": "success" if successful else "error",
            "mode": "analyze",
            "results": results,
            "successful": successful,
            "total": len(results),
        }
        bus.publish(
            BasketCompletedEvent(
                mode="analyze",
                api_type="multileg",
                strategy=strategy,
                results=results,
                successful=successful,
                total=len(results),
                request_data=original_data,
                response_data=response_data,
                api_key=api_key,
            )
        )
        return True, response_data, 200

    # ── Live mode ──
    # Action Center (semi-auto) routing first, exactly as basket orders do.
    from services.order_router_service import queue_order, should_route_to_pending

    if should_route_to_pending(api_key, "multileg"):
        return queue_order(api_key, original_data, "multileg")

    auth_token, broker_name = get_auth_token_broker(api_key)
    if auth_token is None:
        error_response = {"status": "error", "message": "Invalid openalgo apikey"}
        return False, error_response, 403

    return _process_multileg_live(
        legs, auth_token, broker_name, strategy, original_data, api_key
    )
