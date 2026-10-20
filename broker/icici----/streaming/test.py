from breeze_connect import BreezeConnect

breeze = BreezeConnect(
    api_key="YOUR_ICICI_API_KEY"
)

breeze.generate_session(
    api_secret="YOUR_ICICI_API_SECRET",
    session_token="YOUR_ICICI_SESSION_TOKEN",
)

print("BREEZE SESSION OK")

breeze.ws_connect()

print("BREEZE WEBSOCKET OK")
