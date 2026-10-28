"""Route-level tests voor /api/etf-holdings; bereken_etf_holdings_lijst() is gemockt, geen database."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestEtfHoldingsRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_geeft_resultaat_door_met_waarde(self):
        resultaat = {"bron": "provider_csv", "aantal": 1, "gedekt_gewicht": 0.5,
                     "holdings": [{"naam": "Apple Inc", "gewicht": 0.5, "bedrag_eur": 250.0}]}
        with patch.object(self.app_module, "bereken_etf_holdings_lijst", return_value=resultaat) as mock:
            res = self.client.get("/api/etf-holdings", query_string={"ticker": "VWCE.DE", "waarde": "500.5"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), resultaat)
        mock.assert_called_once_with("VWCE.DE", 500.5)

    def test_zonder_waarde_geeft_none_door(self):
        with patch.object(self.app_module, "bereken_etf_holdings_lijst", return_value={}) as mock:
            res = self.client.get("/api/etf-holdings", query_string={"ticker": "VWCE.DE"})
        self.assertEqual(res.status_code, 200)
        mock.assert_called_once_with("VWCE.DE", None)

    def test_ontbrekende_ticker_geeft_400(self):
        res = self.client.get("/api/etf-holdings")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_ongeldige_waarde_geeft_400(self):
        for waarde in ("abc", "nan", "inf"):
            with self.subTest(waarde=waarde):
                res = self.client.get("/api/etf-holdings", query_string={"ticker": "X", "waarde": waarde})
                self.assertEqual(res.status_code, 400)
                self.assertIn("error", res.get_json())


if __name__ == "__main__":
    unittest.main()
