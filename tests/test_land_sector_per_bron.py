"""
Unit tests voor de land_per_bron/sector_per_bron-uitbreiding van
analysis.compute_land_sector_verdeling() -- de databron voor de gestapelde-
staafgrafiek-weergave op het Land/Sector-tabblad (toggle in
static/js/app.js, renderGestapeldeStaafgrafiek).

Draait geheel offline: classify_tickers, get_etf_sector_verdeling,
get_etf_holdings en get_land_sector worden gemockt, dus geen echte
yfinance/database-calls nodig.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import compute_land_sector_verdeling


def _price_data(tickers, waarde=100.0, datum="2024-01-02"):
    return pd.DataFrame({t: [waarde] for t in tickers}, index=[pd.Timestamp(datum)])


class TestLandSectorPerBron(unittest.TestCase):
    def test_land_per_bron_optelt_naar_totaal(self):
        # Een ETF (60% VS/40% Japan) + een los VS-aandeel -- de som van alle
        # bronnen per land in land_per_bron moet gelijk zijn aan de waarde in
        # "land" (consistentiecheck taart- vs. staaf-data). Bewust geen
        # landen onder de 0.5%-Overig-drempel, zodat "land" hier niet
        # gegroepeerd wordt en de vergelijking direct klopt.
        transacties_df = pd.DataFrame({
            "ticker": ["ETF_A", "AAPL"],
            "aantal": [1.0, 1.0],
        })
        price_data = _price_data(["ETF_A", "AAPL"], waarde=100.0)

        with patch.object(analysis, "classify_tickers", return_value={"ETF_A": True, "AAPL": False}), \
             patch.object(analysis, "get_etf_sector_verdeling", return_value={}), \
             patch.object(analysis, "get_etf_holdings", return_value=[
                 {"holding_naam": "X", "holding_ticker": "X", "gewicht": 0.6,
                  "land": "United States", "bron": "provider_csv"},
                 {"holding_naam": "Y", "holding_ticker": "Y", "gewicht": 0.4,
                  "land": "Japan", "bron": "provider_csv"},
             ]), \
             patch.object(analysis, "get_land_sector", return_value=("United States", "Technology")):
            resultaat = compute_land_sector_verdeling(transacties_df, price_data)

        land_per_bron = resultaat["land_per_bron"]
        for landnaam, per_bron in land_per_bron.items():
            self.assertAlmostEqual(sum(per_bron.values()), resultaat["land"][landnaam])

        # Expliciet: VS komt uit zowel ETF_A (60) als AAPL (100) -> 160.
        self.assertAlmostEqual(land_per_bron["United States"]["ETF_A"], 60.0)
        self.assertAlmostEqual(land_per_bron["United States"]["AAPL"], 100.0)
        self.assertAlmostEqual(resultaat["land"]["United States"], 160.0)

    def test_sector_per_bron_los_aandeel_komt_terug_als_eigen_bron(self):
        # Regressietest voor de los-aandeel-tak (de get_land_sector-tak):
        # die telde altijd al op bij "sector", maar bij het toevoegen van de
        # per-bron-registratie is dat pad makkelijk te vergeten -- dit checkt
        # expliciet dat een los aandeel als eigen bron-sleutel in
        # sector_per_bron verschijnt.
        transacties_df = pd.DataFrame({"ticker": ["AAPL"], "aantal": [1.0]})
        price_data = _price_data(["AAPL"], waarde=100.0)

        with patch.object(analysis, "classify_tickers", return_value={"AAPL": False}), \
             patch.object(analysis, "get_land_sector", return_value=("United States", "Technology")):
            resultaat = compute_land_sector_verdeling(transacties_df, price_data)

        self.assertIn("Technology", resultaat["sector_per_bron"])
        self.assertAlmostEqual(resultaat["sector_per_bron"]["Technology"]["AAPL"], 100.0)
        self.assertAlmostEqual(resultaat["sector"]["Technology"], 100.0)


if __name__ == "__main__":
    unittest.main()
