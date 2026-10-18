# broker/icici/api/baseurl.py
# ICICI Direct Breeze API base URLs.
#
# The v1 endpoint family (api.icicidirect.com/breezeapi/api/v1) is the one the
# official Breeze SDK still drives for trading (order, funds, positions,
# quotes, historical charts). Every v1 request carries a JSON body even for
# GET/DELETE, and is authenticated with X-AppKey / X-SessionToken headers plus
# an X-Checksum = SHA256(timestamp + body + api_secret), so the body bytes sent
# must match the body bytes hashed exactly.

API_URL = "https://api.icicidirect.com/breezeapi/api/v1/"

# Browser login URL. After the user authenticates, ICICI redirects to the
# redirect URI registered against the app key with ?api_session=<token>.
LOGIN_URL = "https://api.icicidirect.com/apiuser/login"

# Downloadable instrument master (zip of per-segment .txt files) used by the
# master contract loader instead of the paginated namecodes API.
SECURITY_MASTER_URL = (
    "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
)

# python-socket.io endpoint for live market data ("stock" events).
LIVE_STREAM_URL = "https://livestream.icicidirect.com"

# python-socket.io endpoint for order notifications ("order" events).
LIVE_FEEDS_URL = "https://livefeeds.icicidirect.com"


def get_url(endpoint):
    """Build a full v1 API URL from an endpoint fragment."""
    return f"{API_URL}{endpoint.lstrip('/')}"


def get_login_url(api_key):
    """Build the Breeze browser login URL for an app key."""
    import urllib.parse

    return f"{LOGIN_URL}?api_key={urllib.parse.quote(api_key, safe='')}"
