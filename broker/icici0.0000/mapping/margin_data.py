def transform_margin_data(data):
    success = data.get("Success") if isinstance(data, dict) else data

    if isinstance(success, list):
        success = success[0] if success else {}

    success = success or {}

    return {
        "availablecash": str(
            float(
                success.get("availablecash")
                or success.get("available_cash")
                or success.get("cash")
                or 0
            )
        ),
        "collateral": str(
            float(
                success.get("collateral")
                or success.get("collateral_amount")
                or 0
            )
        ),
        "m2munrealized": str(
            float(success.get("m2munrealized") or 0)
        ),
        "m2mrealized": str(
            float(success.get("m2mrealized") or 0)
        ),
        "utiliseddebits": str(
            float(
                success.get("utiliseddebits")
                or success.get("used_margin")
                or 0
            )
        ),
    }
