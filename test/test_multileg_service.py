"""Unit tests for the /api/v1/multileg batch order service and schema.

The hedging terminal's Deploy / Close / Swap / Exit actions fan out one leg
per enabled strike through this service, so the contract it must hold is:

- per-leg results echo the caller's ``legid`` (failures map back to strikes)
- leg order is preserved in the report regardless of completion order
- analyzer mode routes every leg to the sandbox with one multiquotes
  pre-fetch and reports partial success exactly like live mode
- a rejected leg never aborts the batch
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marshmallow import ValidationError

from restx_api.schemas import MultilegSchema
from services import multileg_service


def _leg(legid="ce-1", symbol="NIFTY28OCT2626000CE", exchange="NFO", action="BUY",
         quantity=75, pricetype="MARKET", product="MIS"):
    return {
        "legid": legid,
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": quantity,
        "pricetype": pricetype,
        "product": product,
        "price": 0.0,
        "trigger_price": 0.0,
    }


class TestMultilegSchema(unittest.TestCase):
    def test_valid_batch_loads_with_defaults(self):
        data = MultilegSchema().load({"apikey": "k" * 8, "legs": [_leg()]})
        self.assertEqual(data["strategy"], "HEDGING")
        self.assertEqual(data["legs"][0]["pricetype"], "MARKET")
        self.assertEqual(data["legs"][0]["legid"], "ce-1")

    def test_empty_legs_rejected(self):
        with self.assertRaises(ValidationError):
            MultilegSchema().load({"apikey": "k" * 8, "legs": []})

    def test_too_many_legs_rejected(self):
        with self.assertRaises(ValidationError):
            MultilegSchema().load({"apikey": "k" * 8, "legs": [_leg(legid=str(i)) for i in range(41)]})

    def test_invalid_action_rejected(self):
        with self.assertRaises(ValidationError):
            MultilegSchema().load({"apikey": "k" * 8, "legs": [_leg(action="HOLD")]})

    def test_invalid_exchange_rejected(self):
        with self.assertRaises(ValidationError):
            MultilegSchema().load({"apikey": "k" * 8, "legs": [_leg(exchange="NYSE")]})


class TestValidateLeg(unittest.TestCase):
    def test_missing_symbol_reported(self):
        leg = _leg()
        del leg["symbol"]
        ok, message = multileg_service.validate_leg({**leg, "apikey": "k", "strategy": "H"})
        self.assertFalse(ok)
        self.assertIn("symbol", message)

    def test_action_normalised_case_insensitively(self):
        ok, _ = multileg_service.validate_leg(
            {**_leg(action="sell"), "apikey": "k", "strategy": "H"}
        )
        self.assertTrue(ok)

    def test_unknown_pricetype_reported(self):
        ok, message = multileg_service.validate_leg(
            {**_leg(pricetype="LIMIT-ICE"), "apikey": "k", "strategy": "H"}
        )
        self.assertFalse(ok)
        self.assertIn("price type", message)


class TestAnalyzeMode(unittest.TestCase):
    def _run(self, sandbox_results):
        """sandbox_results: ordered list of (success, response, status)."""
        schema = MultilegSchema()
        data = schema.load({"apikey": "k" * 8, "legs": [_leg("ce-1"), _leg("ce-2", symbol="X")]})
        with (
            mock.patch.object(multileg_service, "get_analyze_mode", return_value=True),
            mock.patch("services.sandbox_service.sandbox_place_order", side_effect=sandbox_results),
            mock.patch(
                "services.quotes_service.get_multiquotes",
                return_value=(True, {"results": []}, 200),
            ),
            mock.patch.object(multileg_service.bus, "publish"),
        ):
            return multileg_service.place_multileg_order(data, "k" * 8)

    def test_partial_success_reported_with_legids(self):
        success, response, status = self._run(
            [(True, {"orderid": "S1"}, 200), (False, {"message": "Rejected by risk"}, 400)]
        )
        self.assertTrue(success)
        self.assertEqual(status, 200)
        self.assertEqual(response["mode"], "analyze")
        self.assertEqual((response["successful"], response["total"]), (1, 2))
        self.assertEqual(response["results"][0]["legid"], "ce-1")
        self.assertEqual(response["results"][0]["orderid"], "S1")
        self.assertEqual(response["results"][1]["legid"], "ce-2")
        self.assertIn("risk", response["results"][1]["message"])

    def test_all_legs_failing_marks_batch_failed(self):
        success, response, _ = self._run(
            [(False, {"message": "no funds"}, 400), (False, {"message": "no funds"}, 400)]
        )
        self.assertTrue(success)  # the call itself succeeded; every leg failed
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["successful"], 0)


class TestLiveMode(unittest.TestCase):
    @staticmethod
    def _broker(results_by_symbol):
        """place_order_api stub that dispatches on the leg's symbol."""
        broker = SimpleNamespace()

        def place(leg, token):
            outcome = results_by_symbol[leg["symbol"]]
            return outcome

        broker.place_order_api = mock.Mock(side_effect=place)
        return broker

    def _run(self, results_by_symbol, legs=None):
        schema = MultilegSchema()
        data = schema.load(
            {"apikey": "k" * 8, "legs": legs or [_leg("ce-1"), _leg("ce-2", symbol="X")]}
        )
        broker = self._broker(results_by_symbol)

        with (
            mock.patch.object(multileg_service, "get_analyze_mode", return_value=False),
            mock.patch(
                "services.order_router_service.should_route_to_pending", return_value=False
            ),
            mock.patch(
                "services.order_router_service.queue_order", return_value=(False, {}, 500)
            ),
            mock.patch.object(
                multileg_service, "get_auth_token_broker", return_value=("token", "zerodha")
            ),
            mock.patch.object(multileg_service, "import_broker_module", return_value=broker),
            mock.patch.object(multileg_service.bus, "publish"),
        ):
            return multileg_service.place_multileg_order(data, "k" * 8), broker

    def test_legids_echoed_and_order_preserved(self):
        (success, response, status), broker = self._run(
            {
                "NIFTY28OCT2626000CE": (True, {"orderid": "L1"}, 200),
                "X": (True, {"orderid": "L2"}, 200),
            }
        )
        self.assertTrue(success)
        self.assertEqual(status, 200)
        self.assertEqual(response["mode"], "live")
        self.assertEqual([r["legid"] for r in response["results"]], ["ce-1", "ce-2"])
        self.assertEqual(response["results"][0]["orderid"], "L1")
        self.assertEqual(broker.place_order_api.call_count, 2)

    def test_one_bad_leg_does_not_abort_the_batch(self):
        (success, response, _), _broker = self._run(
            {
                "NIFTY28OCT2626000CE": (False, {"message": "Insufficient funds"}, 400),
                "X": (True, {"orderid": "L2"}, 200),
            }
        )
        self.assertTrue(success)
        self.assertEqual((response["successful"], response["total"]), (1, 2))
        self.assertEqual(response["results"][0]["status"], "error")
        self.assertIn("Insufficient", response["results"][0]["message"])

    def test_action_uppercased_before_broker_call(self):
        legs = [_leg("eq-1", symbol="RELIANCE", exchange="NSE", action="buy")]
        (success, response, _), broker = self._run(
            {"RELIANCE": (True, {"orderid": "L1"}, 200)}, legs=legs
        )
        self.assertTrue(success)
        sent = broker.place_order_api.call_args[0][0]
        self.assertEqual(sent["action"], "BUY")
        self.assertEqual(sent["strategy"], "HEDGING")

    def test_invalid_apikey_rejected(self):
        schema = MultilegSchema()
        data = schema.load({"apikey": "k" * 8, "legs": [_leg()]})
        with (
            mock.patch.object(multileg_service, "get_analyze_mode", return_value=False),
            mock.patch(
                "services.order_router_service.should_route_to_pending", return_value=False
            ),
            mock.patch.object(multileg_service, "get_auth_token_broker", return_value=(None, None)),
        ):
            success, response, status = multileg_service.place_multileg_order(data, "k" * 8)
        self.assertFalse(success)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
