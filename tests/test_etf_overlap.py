"""
Unit tests voor bereken_etf_overlap() -- de portfolio-overlap-
matrix tussen aangehouden ETF's.

Draait geheel offline: is_etf_map wordt direct meegegeven en get_etf_holdings
wordt gemockt, dus geen echte yfinance/database-calls nodig.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio_verdeling
from portfolio_verdeling import bereken_etf_overlap, bereken_etf_overlap_detail


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

        with patch.object(portfolio_verdeling, "get_etf_holdings", return_value=holdings):
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

        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=fake_holdings):
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

        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=AssertionError(
                "get_etf_holdings mag niet aangeroepen worden -- is_etf_map zegt overal False")):
            matrix = bereken_etf_overlap(transacties_df, price_data, {"ETF_A": False, "ETF_B": False})

        self.assertEqual(matrix, {})


class TestBerekenEtfOverlapDetail(unittest.TestCase):
    """Unit tests voor bereken_etf_overlap_detail() -- de holdings-lijst
    achter een geklikte percentage-cel op het ETF-overlap-tabblad. Draait geheel offline: zelfde
    get_etf_holdings-mockpatroon als TestBerekenEtfOverlap hierboven."""

    def test_gedeelde_en_eigen_holdings_beide_kanten_gevuld(self):
        def fake_holdings(ticker):
            if ticker == "ETF_A":
                return [
                    {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.6,
                     "land": "United States", "bron": "provider_csv"},
                    {"holding_naam": "Microsoft Corp", "holding_ticker": "MSFT", "gewicht": 0.4,
                     "land": "United States", "bron": "provider_csv"},
                ]
            return [
                {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.3,
                 "land": "United States", "bron": "provider_csv"},
                {"holding_naam": "Nestle SA", "holding_ticker": "NESN.SW", "gewicht": 0.7,
                 "land": "Switzerland", "bron": "provider_csv"},
            ]

        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=fake_holdings):
            rijen = bereken_etf_overlap_detail("ETF_A", "ETF_B")

        per_naam = {r["holding_naam"]: r for r in rijen}
        self.assertEqual(len(rijen), 3)
        self.assertAlmostEqual(per_naam["Apple Inc"]["gewicht_a"], 0.6)
        self.assertAlmostEqual(per_naam["Apple Inc"]["gewicht_b"], 0.3)
        self.assertAlmostEqual(per_naam["Microsoft Corp"]["gewicht_a"], 0.4)
        self.assertIsNone(per_naam["Microsoft Corp"]["gewicht_b"])
        self.assertIsNone(per_naam["Nestle SA"]["gewicht_a"])
        self.assertAlmostEqual(per_naam["Nestle SA"]["gewicht_b"], 0.7)

    def test_gesorteerd_aflopend_op_hoogste_van_beide_gewichten(self):
        def fake_holdings(ticker):
            if ticker == "ETF_A":
                return [
                    {"holding_naam": "Klein A", "holding_ticker": "KA", "gewicht": 0.05,
                     "land": "United States", "bron": "provider_csv"},
                    {"holding_naam": "Groot", "holding_ticker": "GR", "gewicht": 0.5,
                     "land": "United States", "bron": "provider_csv"},
                ]
            return [
                {"holding_naam": "Midden", "holding_ticker": "MI", "gewicht": 0.2,
                 "land": "United States", "bron": "provider_csv"},
            ]

        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=fake_holdings):
            rijen = bereken_etf_overlap_detail("ETF_A", "ETF_B")

        self.assertEqual([r["holding_naam"] for r in rijen], ["Groot", "Midden", "Klein A"])

    def test_zelfde_bedrijf_andere_naamspelling_telt_als_gedeeld(self):
        # _normaliseer_bedrijfsnaam() moet "ASML Holding NV" en "ASML
        # Holding N.V." als hetzelfde bedrijf herkennen (zelfde
        # normalisatie als bereken_etf_overlap() gebruikt).
        def fake_holdings(ticker):
            if ticker == "ETF_A":
                return [{"holding_naam": "ASML Holding NV", "holding_ticker": "ASML", "gewicht": 0.5,
                         "land": "Netherlands", "bron": "provider_csv"}]
            return [{"holding_naam": "ASML Holding N.V.", "holding_ticker": "ASML", "gewicht": 0.4,
                     "land": "Netherlands", "bron": "provider_csv"}]

        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=fake_holdings):
            rijen = bereken_etf_overlap_detail("ETF_A", "ETF_B")

        self.assertEqual(len(rijen), 1)
        self.assertAlmostEqual(rijen[0]["gewicht_a"], 0.5)
        self.assertAlmostEqual(rijen[0]["gewicht_b"], 0.4)


if __name__ == "__main__":
    unittest.main()
