"""
ICICI Direct streaming package.
"""

from .icici_adapter import (
    ICICIAdapter,
    IciciWebSocketAdapter,
)

from .icici_websocket import (
    IciciWebSocket,
)


__all__ = [
    "ICICIAdapter",
    "IciciWebSocketAdapter",
    "IciciWebSocket",
]

# """
# ICICI Direct Breeze streaming integration for OpenAlgo.
# """
# from .icici_adapter import ICICIAdapter
# from .icici_websocket import IciciWebSocket

# __all__ = [
#     "ICICIAdapter",
#     "IciciWebSocket",
# ]

# from .icici_adapter import ICICIAdapter
# from .icici_order_adapter import ICICIOrderAdapter
# from .icici_websocket import ICICIWebSocket

# __all__ = [
#     "ICICIAdapter",
#     "ICICIOrderAdapter",
#     "ICICIWebSocket",
# ]