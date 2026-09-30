"""
Route-level tests voor /api/etf-overlap-detail -- de holdings-detailtabel
achter een geklikte percentage-cel op het ETF-overlap-tabblad. Los van een portfolio-code (werkt ticker-op-
ticker, ook voor de 'niet opslaan'-analyse), dus geen setUp/tearDown-rijen
in de database nodig -- alleen bereken_etf_overlap_detail() wordt gemockt.

Geen echte database nodig: app.py draait init_db() alleen als DATABASE_URL
is ingesteld.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEtfOverlapDetailRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_geeft_holdings_van_bereken_etf_overlap_detail_door(self):
        rijen = [
            {"holding_naam": "Apple Inc", "gewicht_a": 0.6, "gewicht_b": 0.3},
            {"holding_naam": "Nestle SA", "gewicht_a": None, "gewicht_b": 0.7},
        ]
        with patch.object(self.app_module, "bereken_etf_overlap_detail", return_value=rijen) as mock_detail:
            res = self.client.get("/api/etf-overlap-detail", query_string={"a": "ETF_A", "b": "ETF_B"})

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["holdings"], rijen)
        mock_detail.assert_called_once_with("ETF_A", "ETF_B")

    def test_ontbrekende_a_of_b_geeft_400(self):
        res = self.client.get("/api/etf-overlap-detail", query_string={"b": "ETF_B"})
        self.assertEqual(res.status_code, 400)

        res = self.client.get("/api/etf-overlap-detail", query_string={"a": "ETF_A"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
