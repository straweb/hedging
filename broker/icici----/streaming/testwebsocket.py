from breeze_connect import BreezeConnect

breeze = BreezeConnect(
    api_key="YOUR_API_KEY"
)

breeze.generate_session(
    api_secret="YOUR_API_SECRET",
    session_token="YOUR_SESSION_TOKEN",
)

def on_ticks(ticks):
    print("TICK:", ticks)

breeze.on_ticks = on_ticks

print(
    "USER:",
    bool(breeze.user_id),
)

print(
    "SESSION:",
    bool(breeze.session_key),
)

breeze.ws_connect()

print(
    "STREAM HANDLER:",
    breeze.sio_rate_refresh_handler
)

breeze.subscribe_feeds(
    stock_token="4.1!YOUR_NSE_TOKEN"
)

input(
    "Press Enter to stop..."
)

breeze.ws_disconnect()
