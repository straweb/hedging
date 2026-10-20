# broker/icici/streaming/__init__.py
"""
ICICI Direct streaming package.

The market-data adapter (IciciWebSocketAdapter) is registered with the
websocket proxy in websocket_proxy/__init__.py. Order updates are served
by the REST-polling fallback (services/order_update_service.py), since
Breeze's order stream is Socket.IO-bound and shared with the market feed
session.
"""

from .icici_adapter import IciciWebSocketAdapter
from .icici_mapping import IciciCapabilityRegistry, IciciExchangeMapper

__all__ = [
    "IciciWebSocketAdapter",
    "IciciExchangeMapper",
    "IciciCapabilityRegistry",
]
