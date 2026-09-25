"""
Unit tests voor portfolio_verdeling._beperk_tot_top_n_per_bron() en de
land_per_bron_top/land_per_bron_europa_top-velden van
compute_land_sector_verdeling() -- de "top 10 + Overig"-staaf op het
Land-tabblad, voorheen in static/js/app.js (renderGestapeldeStaafgrafiek,
opts.maxCategorieen) berekend.

Bewust een ANDERE "Overig"-definitie dan de taart (_voeg_kleine_landen_samen,
landen < 0.5%): een staaf per land vanaf 0.5% wordt bij veel landen
onleesbaar, een taart met maar 10 punten verstopt juist relevante landen.

Draait geheel offline: pure functie + gemockte holdings/land-lookups.
"""
import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio_verdeling
from portfolio_verdeling import (
    _beperk_tot_top_n_per_bron, _voeg_kleine_landen_samen, compute_land_sector_verdeling,
    LAND_STAAF_TOP_N,
)


def _per_bron_met_aflopende_landen(aantal):
    # Land01 = 1200, Land02 = 1100, ... één bron per land, allemaal "A".
    return {f"Land{i:02d}": {"A": float(1300 - i * 100)} for i in range(1, aantal + 1)}


class TestBeperkTotTopNPerBron(unittest.TestCase):
    def test_standaard_top_n_is_10(self):
        self.assertEqual(LAND_STAAF_TOP_N, 10)

    def test_niet_meer_dan_n_landen_blijft_ongewijzigd_zonder_overig(self):
        per_bron = _per_bron_met_aflopende_landen(10)
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 10)
        self.assertEqual(resultaat, per_bron)
        self.assertNotIn("Overig", resultaat)

    def test_rest_na_top_n_wordt_overig_per_bron_opgeteld(self):
        # 12 landen: Land11 (200) en Land12 (100) vallen buiten de top 10.
        per_bron = _per_bron_met_aflopende_landen(12)
        per_bron["Land11"]["B"] = 50.0
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 10)

        self.assertEqual(len(resultaat), 11)
        self.assertNotIn("Land11", resultaat)
        self.assertNotIn("Land12", resultaat)
        self.assertEqual(resultaat["Overig"], {"A": 300.0, "B": 50.0})

    def test_top_n_wordt_bepaald_op_totaal_over_alle_bronnen(self):
        # Z heeft per bron kleine bedragen maar samen het grootste totaal.
        per_bron = {
            "X": {"A": 100.0},
            "Y": {"A": 90.0},
            "Z": {"A": 60.0, "B": 60.0},
        }
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 2)
        self.assertEqual(set(resultaat), {"Z", "X", "Overig"})
        self.assertEqual(resultaat["Overig"], {"A": 90.0})

    def test_totaal_per_bron_blijft_behouden(self):
        per_bron = _per_bron_met_aflopende_landen(15)
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 10)
        voor = sum(b for rij in per_bron.values() for b in rij.values())
        na = sum(b for rij in resultaat.values() for b in rij.values())
        self.assertAlmostEqual(voor, na)

    def test_nan_bedrag_wordt_overgeslagen(self):
        per_bron = _per_bron_met_aflopende_landen(11)
        per_bron["Land11"]["B"] = float("nan")
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 10)
        self.assertEqual(resultaat["Overig"], {"A": 200.0})

    def test_decimal_bedrag_wordt_float(self):
        per_bron = {"X": {"A": Decimal("10.5")}, "Y": {"A": Decimal("1.5")}}
        resultaat = _beperk_tot_top_n_per_bron(per_bron, 1)
        self.assertIsInstance(resultaat["Overig"]["A"], float)
        self.assertEqual(resultaat["Overig"]["A"], 1.5)

    def test_lege_invoer(self):
        self.assertEqual(_beperk_tot_top_n_per_bron({}, 10), {})

    def test_invoer_wordt_niet_gemuteerd(self):
        per_bron = _per_bron_met_aflopende_landen(12)
        kopie = {k: dict(v) for k, v in per_bron.items()}
        _beperk_tot_top_n_per_bron(per_bron, 10)
        self.assertEqual(per_bron, kopie)


class TestTaartEnStaafDefinitieBewustVerschillend(unittest.TestCase):
    def test_land_op_plek_11_boven_drempel_taart_eigen_punt_staaf_overig(self):
        # 11 landen, Land11 = 200 van 7800 = 2,6% -> ruim boven de 0,5%-
        # drempel. Taart: eigen punt. Staaf: plek 11, dus in Overig.
        per_bron = _per_bron_met_aflopende_landen(11)
        land = {k: sum(v.values()) for k, v in per_bron.items()}

        taart = _voeg_kleine_landen_samen(land)
        staaf = _beperk_tot_top_n_per_bron(per_bron, 10)

        self.assertIn("Land11", taart)
        self.assertNotIn("Overig", taart)
        self.assertNotIn("Land11", staaf)
        self.assertEqual(staaf["Overig"], {"A": 200.0})

    def test_klein_land_binnen_top_10_taart_overig_staaf_eigen_staaf(self):
        # 3 landen, Klein = 1 van 1001 (<0,5%). Taart: in Overig. Staaf:
        # minder dan 10 landen, dus gewoon een eigen staaf.
        per_bron = {"Groot": {"A": 900.0}, "Middel": {"A": 100.0}, "Klein": {"A": 1.0}}
        land = {k: sum(v.values()) for k, v in per_bron.items()}

        taart = _voeg_kleine_landen_samen(land)
        staaf = _beperk_tot_top_n_per_bron(per_bron, 10)

        self.assertNotIn("Klein", taart)
        self.assertEqual(taart["Overig"], 1.0)
        self.assertIn("Klein", staaf)
        self.assertNotIn("Overig", staaf)


class TestComputeLandSectorVerdelingTopVelden(unittest.TestCase):
    def test_top_velden_beperken_land_per_bron_en_europa_variant(self):
        # Eén ETF met 12 landen (8 niet-Europees + 4 Europees): zonder
        # Europa-groepering 12 landen -> top 10 + Overig; met groepering 9
        # posten (8 + "Europe") -> ongewijzigd.
        # Gewichten tellen op tot 1.0 (geen "Unknown"-restant) en zijn uniek,
        # zodat de top 10 niet van de sorteervolgorde bij gelijkspel afhangt.
        landen = [
            ("United States", 0.29), ("Japan", 0.15), ("China", 0.10), ("Canada", 0.08),
            ("Australia", 0.07), ("India", 0.06), ("Brazil", 0.045), ("Mexico", 0.035),
            ("Germany", 0.065), ("France", 0.05), ("Netherlands", 0.03), ("Spain", 0.025),
        ]
        holdings = [
            {"holding_naam": n, "holding_ticker": n, "gewicht": g, "land": n, "bron": "provider_csv"}
            for n, g in landen
        ]
        transacties_df = pd.DataFrame({"ticker": ["ETF_A"], "aantal": [1.0]})
        price_data = pd.DataFrame({"ETF_A": [1000.0]}, index=[pd.Timestamp("2024-01-02")])

        with patch.object(portfolio_verdeling, "get_etf_sector_verdeling", return_value={}), \
             patch.object(portfolio_verdeling, "get_etf_holdings", return_value=holdings):
            resultaat = compute_land_sector_verdeling(transacties_df, price_data, {"ETF_A": True})

        top = resultaat["land_per_bron_top"]
        self.assertEqual(len(top), 11)
        self.assertIn("Mexico", top)  # plek 10 (35)
        self.assertNotIn("Netherlands", top)  # plek 11 (30)
        self.assertNotIn("Spain", top)  # plek 12 (25)
        self.assertAlmostEqual(top["Overig"]["ETF_A"], 55.0)

        europa_top = resultaat["land_per_bron_europa_top"]
        self.assertEqual(europa_top, resultaat["land_per_bron_europa"])
        self.assertNotIn("Overig", europa_top)

        # De ruwe velden blijven onbeperkt (building block, eigen tests).
        self.assertEqual(len(resultaat["land_per_bron"]), 12)


if __name__ == "__main__":
    unittest.main()
