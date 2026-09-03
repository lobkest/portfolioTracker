"""
Unit tests voor analysis.bereken_etf_overlap() -- de portfolio-overlap-
matrix tussen aangehouden ETF's (zie CLAUDE.md "ETF-overlap").

Draait geheel offline: is_etf_map wordt direct meegegeven en get_etf_holdings
wordt gemockt, dus geen echte yfinance/database-calls nodig.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import bereken_etf_overlap


def _price_data(tickers, waarde=100.0, datum="2024-01-02"):
    return pd.DataFrame({t: [waarde] for t in tickers}, index=[pd.Timestamp(datum)])


class TestBerekenEtfOverlap(unittest.TestCase):
    def test_identieke_holdings_honderd_procent_overlap(self):
        transacties_df = pd.DataFrame({"ticker": ["ETF_A", "ETF_B"], "aantal": [1.0, 1.0]})
        price_data = _price_data(["ETF_A", "ETF_B"])
        holdings = [
            {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.6,
             "land": "United States", "bron": "provider_csv"},
            {"holding_naam": "Microsoft Corp", "holding_ticker": "MSFT", "gewicht": 0.4,
             "land": "United States", "bron": "provider_csv"},
        ]

        with patch.object(analysis, "get_etf_holdings", return_value=holdings):
            matrix = bereken_etf_overlap(transacties_df, price_data, {"ETF_A": True, "ETF_B": True})

        self.assertAlmostEqual(matrix["ETF_A"]["ETF_B"], 1.0)
        self.assertAlmostEqual(matrix["ETF_B"]["ETF_A"], 1.0)

    def test_geen_gedeelde_holdings_nul_procent_geen_crash(self):
        transacties_df = pd.DataFrame({"ticker": ["ETF_A", "ETF_B"], "aantal": [1.0, 1.0]})
        price_data = _price_data(["ETF_A", "ETF_B"])

        def fake_holdings(ticker):
            if ticker == "ETF_A":
                return [{"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 1.0,
                         "land": "United States", "bron": "provider_csv"}]
            return [{"holding_naam": "Nestle SA", "holding_ticker": "NESN.SW", "gewicht": 1.0,
                     "land": "Switzerland", "bron": "provider_csv"}]

        with patch.object(analysis, "get_etf_holdings", side_effect=fake_holdings):
            matrix = bereken_etf_overlap(transacties_df, price_data, {"ETF_A": True, "ETF_B": True})

        self.assertAlmostEqual(matrix["ETF_A"]["ETF_B"], 0.0)
        self.assertAlmostEqual(matrix["ETF_B"]["ETF_A"], 0.0)

    def test_minder_dan_twee_etfs_geeft_lege_matrix(self):
        transacties_df = pd.DataFrame({"ticker": ["ETF_A", "AAPL"], "aantal": [1.0, 1.0]})
        price_data = _price_data(["ETF_A", "AAPL"])

        matrix = bereken_etf_overlap(transacties_df, price_data, {"ETF_A": True, "AAPL": False})

        self.assertEqual(matrix, {})

    def test_meegegeven_is_etf_map_is_leidend_geen_eigen_classify_tickers(self):
        # Beide tickers ZOUDEN ETF's kunnen zijn, maar de meegegeven
        # is_etf_map zegt voor allebei False -- laat get_etf_holdings een
        # AssertionError geven zodra 'ie aangeroepen wordt, zodat een eigen
        # classify_tickers-herberekening (de oude situatie) meteen zou
        # opvallen. Met <2 "echte" ETF's volgens de meegegeven map hoort dit
        # een lege matrix te geven, zonder ooit get_etf_holdings aan te roepen.
        transacties_df = pd.DataFrame({"ticker": ["ETF_A", "ETF_B"], "aantal": [1.0, 1.0]})
        price_data = _price_data(["ETF_A", "ETF_B"])

        with patch.object(analysis, "get_etf_holdings", side_effect=AssertionError(
                "get_etf_holdings mag niet aangeroepen worden -- is_etf_map zegt overal False")):
            matrix = bereken_etf_overlap(transacties_df, price_data, {"ETF_A": False, "ETF_B": False})

        self.assertEqual(matrix, {})


if __name__ == "__main__":
    unittest.main()
