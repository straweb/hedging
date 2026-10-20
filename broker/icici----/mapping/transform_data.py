from database.token_db import (
    get_br_symbol,
)


def map_order_type(
    pricetype,
):
    mapping = {
        "MARKET": "market",
        "LIMIT": "limit",
        "SL": "stoploss",
        "SL-M": "stoploss",
    }

    return mapping.get(
        str(
            pricetype
        ).upper(),
        "limit",
    )


def map_product_type(
    product,
):
    mapping = {
        "CNC": "cash",
        "NRML": "futures",
        "MIS": "margin",
    }

    return mapping.get(
        str(
            product
        ).upper(),
        "margin",
    )


def reverse_map_product_type(
    exchange,
    product,
):
    mapping = {
        "cash": "CNC",
        "futures": "NRML",
        "options": "NRML",
        "margin": "MIS",
    }

    return mapping.get(
        str(
            product
        ).lower()
    )


def transform_data(
    data,
):
    symbol = (
        get_br_symbol(
            data