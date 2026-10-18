# broker/icici/streaming/icici_mapping.py
# Channel-prefix and capability maps for the ICICI Direct live feed.
#
# The livestream socket.io endpoint rooms are "<prefix>.<type>!<token>":
#   type 1 = exchange quotes, type 2 = market depth.
# Prefixes follow the official Breeze SDK's get_stock_token_value map (BFO
# rides prefix 8 on the rate-refresh stream, matching its depth layout), and
# index spot quotes stream on the dedicated prefix 3 room.

# OpenAlgo exchange -> livestream room prefix.
EXCHANGE_PREFIX = {
    "NSE": "4",
    "NFO": "4",
    "BSE": "1",
    "BFO": "8",
    "MCX": "6",
    "NDX": "13",
    "CDS": "13",
    "NSE_INDEX": "3",
    "BSE_INDEX": "3",
}

# brexchange values stored in the symtoken table -> room prefix.
BREXCHANGE_PREFIX = {
    "NSE": "4",
    "NFO": "4",
    "BSE": "1",
    "BFO": "8",
    "MCX": "6",
    "NDX": "13",
}

QUOTES_CHANNEL_TYPE = "1"
DEPTH_CHANNEL_TYPE = "2"

# The proxy canonical mode strings (websocket_proxy/mode_utils.py).
MODE_TO_TYPE = {1: "1", 2: "1", 3: "2"}
MODE_TO_NAME = {1: "LTP", 2: "QUOTE", 3: "DEPTH"}


class IciciExchangeMapper:
    """Build livestream room identifiers for an instrument."""

    @staticmethod
    def get_channel(brexchange, token, mode):
        """Room id for a (brexchange, token, mode) subscription."""
        prefix = BREXCHANGE_PREFIX.get(brexchange, "4")
        channel_type = MODE_TO_TYPE.get(mode, "1")
        return f"{prefix}.{channel_type}!{token}"

    @staticmethod
    def get_prefix(brexchange):
        return BREXCHANGE_PREFIX.get(brexchange, "4")

    @staticmethod
    def is_index_exchange(exchange):
        return exchange in ("NSE_INDEX", "BSE_INDEX")


class IciciCapabilityRegistry:
    """Capability surface advertised to the websocket proxy."""

    SUPPORTED_EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "MCX", "NSE_INDEX", "BSE_INDEX"]
    SUPPORTED_MODES = [1, 2, 3]
    SUPPORTED_DEPTH_LEVELS = [5]

    @classmethod
    def is_exchange_supported(cls, exchange):
        return exchange in cls.SUPPORTED_EXCHANGES

    @classmethod
    def is_mode_supported(cls, mode):
        return mode in cls.SUPPORTED_MODES

    @classmethod
    def is_depth_level_supported(cls, depth_level):
        return depth_level in cls.SUPPORTED_DEPTH_LEVELS
