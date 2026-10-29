"""Yahoo-volgorde (yahooquery eerst) en de geheugencache voor 'geen fondsdata' (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import ticker_classificatie as tc

SECTOREN = pd.DataFrame({"X": [1.0]}, index=["technology"])
LEGE_SECTOREN = pd.DataFrame()
TOP10 = pd.DataFrame({"symbol": ["AAPL"], "holdingName": ["Apple Inc"], "holdingPercent": [0.05]})


def _yq(**eigenschappen):
    mock = MagicMock()
    for naam, waarde in eigenschappen.items():
        setattr(mock.return_value, naam, waarde)
    return mock


def _yf(sectoren=None):
    mock = MagicMock()
    mock.return_value.funds_data.sector_weightings = sectoren
    mock.return_value.funds_data.top_holdings = None
    return mock


class _MetLegeCache(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)

    def sector(self, yq, yf):
        with patch.object(tc, "db_get_cached_etf_sector_verdeling", return_value=None), \
                patch.object(tc, "db_save_etf_sector_verdeling"), patch.object(tc, "_sector_uit_provider", return_value={}), \
                patch.object(tc, "YahooqueryTicker", yq), patch.object(tc.yf, "Ticker", yf), \
                patch.object(tc, "dprint"):
            return tc.get_etf_sector_verdeling("X")


class TestVolgorde(_MetLegeCache):
    def test_yahooquery_heeft_data_dus_geen_yfinance(self):
        yf = _yf()
        uit = self.sector(_yq(fund_sector_weightings=SECTOREN), yf)
        self.assertEqual(uit, {"Technology": 1.0})
        yf.assert_not_called()

    def test_yahooquery_leeg_dus_yfinance_als_terugval(self):
        yf = _yf({"technology": 0.9})
        uit = self.sector(_yq(fund_sector_weightings=LEGE_SECTOREN), yf)
        self.assertEqual(uit, {"Technology": 0.9})
        yf.assert_called_once_with("X")

    def test_holdings_yahooquery_eerst(self):
        yf = _yf()
        with patch.object(tc, "db_get_cached_etf_holdings", return_value=None), \
                patch.object(tc, "get_ticker_details", return_value={}), \
                patch.object(tc, "db_save_etf_holdings"), patch.object(tc, "get_land_sector", return_value=("US", "IT")), \
                patch.object(tc, "YahooqueryTicker", _yq(fund_top_holdings=TOP10)), patch.object(tc.yf, "Ticker", yf), \
                patch.object(tc, "dprint"):
            uit = tc.get_etf_holdings("X")
        self.assertEqual(uit[0]["holding_naam"], "Apple Inc")
        yf.assert_not_called()


class TestGeenFondsdataCache(_MetLegeCache):
    def test_tweede_aanroep_doet_geen_yahoo_call(self):
        yq, yf = _yq(fund_sector_weightings=LEGE_SECTOREN), _yf(None)
        self.assertEqual(self.sector(yq, yf), {})
        yq.reset_mock()
        yf.reset_mock()
        self.assertEqual(self.sector(yq, yf), {})
        yq.assert_not_called()
        yf.assert_not_called()

    def test_na_een_uur_wordt_yahoo_weer_geprobeerd(self):
        yq, yf = _yq(fund_sector_weightings=LEGE_SECTOREN), _yf(None)
        with patch.object(tc.time, "monotonic", return_value=1000.0):
            self.sector(yq, yf)
        yq.reset_mock()
        with patch.object(tc.time, "monotonic", return_value=1000.0 + tc.GEEN_FONDSDATA_CACHE_SECONDEN - 1):
            self.sector(yq, yf)
            yq.assert_not_called()
        with patch.object(tc.time, "monotonic", return_value=1000.0 + tc.GEEN_FONDSDATA_CACHE_SECONDEN):
            self.sector(yq, yf)
        yq.assert_called_once_with("X")

    def test_exceptie_of_foutstring_wordt_niet_onthouden(self):
        for naam, yq in (("exceptie", MagicMock(side_effect=Exception("netwerk"))),
                         ("foutstring", _yq(fund_sector_weightings="Invalid Crumb"))):
            with self.subTest(naam):
                self.sector(yq, _yf(None))
                yq.reset_mock()
                self.sector(yq, _yf(None))
                yq.assert_called_once_with("X")

    def test_yfinance_met_data_na_lege_yahooquery_wordt_niet_onthouden(self):
        self.sector(_yq(fund_sector_weightings=LEGE_SECTOREN), _yf({"technology": 1.0}))
        self.assertFalse(tc._geen_fondsdata_bekend("X", tc.ONDERDEEL_SECTOR))

    def test_holdings_lege_uitkomst_wordt_onthouden(self):
        yq = _yq(fund_top_holdings=pd.DataFrame())
        with patch.object(tc, "db_get_cached_etf_holdings", return_value=None), \
                patch.object(tc, "get_ticker_details", return_value={}), \
                patch.object(tc, "YahooqueryTicker", yq), patch.object(tc.yf, "Ticker", _yf()), patch.object(tc, "dprint"):
            self.assertEqual(tc.get_etf_holdings("X"), [])
            yq.reset_mock()
            self.assertEqual(tc.get_etf_holdings("X"), [])
        yq.assert_not_called()

    def test_maximum_aantal_items_gooit_het_oudste_weg(self):
        with patch.object(tc, "GEEN_FONDSDATA_CACHE_MAX_ITEMS", 3):
            for tijd, ticker in enumerate(["A", "B", "C", "D"]):
                with patch.object(tc.time, "monotonic", return_value=float(tijd)):
                    tc._onthoud_geen_fondsdata(ticker, "sector")
        self.assertEqual(set(tc._geen_fondsdata_cache), {("B", "sector"), ("C", "sector"), ("D", "sector")})

    def test_wissen_leegt_de_cache(self):
        tc._onthoud_geen_fondsdata("A", "sector")
        tc.wis_geen_fondsdata_cache()
        self.assertFalse(tc._geen_fondsdata_bekend("A", "sector"))


if __name__ == "__main__":
    unittest.main()
