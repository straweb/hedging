def test_order_api(
    data,
    auth,
):
    """
    Diagnostic test.

    Does NOT place an order.

    Verifies that:
      1. Breeze authentication works
      2. Symbol is recognized
      3. Quote API works
      4. Order payload can be constructed
    """

    breeze = get_breeze_client(
        auth
    )

    payload = _place_payload(
        data
    )

    logger.info(
        "ICICI order API diagnostic payload: %s",
        payload,
    )

    quote_response = breeze.get_quotes(
        stock_code=payload["stock_code"],
        exchange_code=payload["exchange_code"],
        product_type=payload["product"],
    )

    logger.info(
        "ICICI quote diagnostic response: %s",
        quote_response,
    )

    quote_status = (
        quote_response.get(
            "Status"
        )
        if isinstance(
            quote_response,
            dict,
        )
        else None
    )

    quote_rows = (
        quote_response.get(
            "Success"
        )
        if isinstance(
            quote_response,
            dict,
        )
        else []
    )

    return {
        "status": 200 if quote_rows else 500,
        "authenticated": True,
        "symbol": payload["stock_code"],
        "exchange": payload["exchange_code"],
        "quote_available": bool(
            quote_rows
        ),
        "quote_response": quote_response,
        "order_payload": payload,
    }
