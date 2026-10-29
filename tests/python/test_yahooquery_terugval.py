"""Tests voor de yahooquery-terugval van sector, top 10 en categorie (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import ticker_classificatie as tc


def _yf_faalt():
    mock = MagicMock()
    type(mock.return_value.funds_data).sector_weightings = property(lambda s: (_ for _ in ()).throw(Exception("401")))
    type(mock.return_value.funds_data).top_holdings = property(lambda s: (_ for _ in ()).throw(Exception("401")))
    return mock


def _yq(**eigenschappen):
    mock = MagicMock()
    for naam, waarde in eigenschappen.items():
        setattr(mock.return_value, naam, waarde)
    return mock


SECTOREN = pd.DataFrame({"VWCE.DE": [0.25, 0.75]}, index=["realestate", "consumer_cyclical"])
TOP10 = pd.DataFrame({"symbol": ["AAPL"], "holdingName": ["Apple Inc"], "holdingPercent": [0.05]})


class TestSectorTerugval(unittest.TestCase):
    def setUp(self):
        p = patch.object(tc, "_sector_uit_provider", return_value={})
        p.start()
        self.addCleanup(p.stop)

    def test_yfinance_faalt_yahooquery_geeft_data_en_wordt_gecachet(self):
        with patch.object(tc, "db_get_cached_etf_sector_verdeling", return_value=None), \
             patch.object(tc, "db_save_etf_sector_verdeling") as opslaan, \
             patch.object(tc.yf, "Ticker", _yf_faalt()), \
             patch.object(tc, "YahooqueryTicker", _yq(fund_sector_weightings=SECTOREN)):
            uit = tc.get_etf_sector_verdeling("VWCE.DE")
        self.assertEqual(uit, {"Real Estate": 0.25, "Consumer Cyclical": 0.75})
        opslaan.assert_called_once_with("VWCE.DE", uit)

    def test_yahooquery_foutstring_geeft_leeg_niet_gecachet_en_logregel(self):
        with patch.object(tc, "db_get_cached_etf_sector_verdeling", return_value=None), \
             patch.object(tc, "db_save_etf_sector_verdeling") as opslaan, \
             patch.object(tc.yf, "Ticker", _yf_faalt()), \
             patch.object(tc, "YahooqueryTicker", _yq(fund_sector_weightings="Invalid Crumb")), \
             patch.object(tc, "dprint") as log:
            uit = tc.get_etf_sector_verdeling("VWCE.DE")
        self.assertEqual(uit, {})
        opslaan.assert_not_called()
        self.assertTrue(any("VWCE.DE" in str(c) for c in log.call_args_list))

    def test_beide_falen_geen_crash(self):
        with patch.object(tc, "db_get_cached_etf_sector_verdeling", return_value=None), \
             patch.object(tc.yf, "Ticker", _yf_faalt()), \
             patch.object(tc, "YahooqueryTicker", side_effect=Exception("netwerk")):
            self.assertEqual(tc.get_etf_sector_verdeling("VWCE.DE"), {})


class TestHoldingsTerugval(unittest.TestCase):
    def _draai(self, yq):
        with patch.object(tc, "db_get_cached_etf_holdings", return_value=None), \
             patch.object(tc, "get_ticker_details", return_value={}), \
             patch.object(tc, "db_save_etf_holdings") as opslaan, \
             patch.object(tc, "get_land_sector", return_value=("United States", "Tech")), \
             patch.object(tc.yf, "Ticker", _yf_faalt()), \
             patch.object(tc, "YahooqueryTicker", yq), \
             patch.object(tc, "dprint") as log:
            return tc.get_etf_holdings("VWCE.DE"), opslaan, log

    def test_yfinance_faalt_yahooquery_geeft_data_en_wordt_gecachet(self):
        uit, opslaan, _ = self._draai(_yq(fund_top_holdings=TOP10))
        self.assertEqual(uit, [{"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.05,
                                "land": "United States", "bron": "yfinance_top10"}])
        opslaan.assert_called_once_with("VWCE.DE", uit)

    def test_yahooquery_foutstring_geeft_leeg_niet_gecachet_en_logregel(self):
        uit, opslaan, log = self._draai(_yq(fund_top_holdings={"VWCE.DE": "Invalid Crumb"}))
        self.assertEqual(uit, [])
        opslaan.assert_not_called()
        self.assertTrue(any("VWCE.DE" in str(c) for c in log.call_args_list))

    def test_beide_falen_geen_crash(self):
        uit, opslaan, _ = self._draai(MagicMock(side_effect=Exception("netwerk")))
        self.assertEqual(uit, [])
        opslaan.assert_not_called()


class TestCategorieTerugval(unittest.TestCase):
    def test_categoriename_uit_fund_profile(self):
        with patch.object(tc, "YahooqueryTicker", _yq(fund_profile={"X": {"categoryName": "Large Blend"}})):
            self.assertEqual(tc._yq_categorie("X"), "Large Blend")

    def test_foutstring_geeft_none(self):
        with patch.object(tc, "YahooqueryTicker", _yq(fund_profile={"X": "No fundamentals data"})):
            self.assertIsNone(tc._yq_categorie("X"))


if __name__ == "__main__":
    unittest.main()
