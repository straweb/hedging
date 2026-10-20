"""
ICICI Direct Breeze API base URLs (mirrors breeze_connect 1.0.69 config).
"""

API_URL = "https://api.icicidirect.com/breezeapi/api/v1/"

# Historical Data V2 (query-string params, apikey + X-SessionToken headers,
# no checksum body).
HIST_V2_URL = "https://breezeapi.icicidirect.com/api/v2/"

# Session exchange (login api_session -> user_id / session_key /
# base64 session token).
CUSTOMER_DETAILS_URL = API_URL + "customerdetails"

# Interactive login page. The browser redirect carries ?apisession=...
LOGIN_URL = "https://api.icicidirect.com/apiissue/login"

# Security master archive (parsed by database/master_contract_db.py).
SECURITY_MASTER_URL = (
    "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
)
