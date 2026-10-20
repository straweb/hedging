"""
ICICI Direct order-notification adapter.

Converts Breeze order notifications into the
OpenAlgo normalized order-update format.
"""

from __future__ import annotations

from typing import Callable, Optional

from utils.logging import get_logger

from .icici_mapping import normalize_order_update

logger = get_logger(__name__)


class ICICIOrderAdapter:
    def __init__(
        self,
        callback: Optional[
            Callable
        ] = None,
    ):
        self.callback = callback

    def set_callback(
        self,
        callback: Callable,
    ):
        self.callback = callback

    def process(
        self,
        order,
    ):
        """
        Process one Breeze order event.

        Breeze may deliver:
            dict
            list[dict]
        """

        if isinstance(
            order,
            list,
        ):
            for item in order:
                self.process(item)

            return

        if not isinstance(
            order,
            dict,
        ):
            logger.warning(
                "Ignoring invalid ICICI order event: %r",
                order,
            )

            return

        normalized = normalize_order_update(
            order
        )

        if not normalized:
            return

        logger.debug(
            "ICICI normalized order update: "
            "orderid=%s status=%s",
            normalized.get("orderid"),
            normalized.get("status"),
        )

        if self.callback:
            try:
                self.callback(
                    normalized
                )

            except Exception:
                logger.exception(
                    "ICICI order update consumer failed"
                )

    def normalize(
        self,
        order,
    ):
        return normalize_order_update(
            order
        )
