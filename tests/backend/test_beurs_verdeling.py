"""compute_beurs_verdeling(): huidige posities per DeGiro-beurs, met een Euronext-samengevoegde variant."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_verdeling import compute_beurs_verdeling


def _transacties(rijen):
    return pd.DataFrame(rijen, columns=["ticker", "beurs", "aantal"])


def _koersen(laatste):
    return pd.DataFrame([laatste], index=pd.to_datetime(["2026-01-02"]))


class TestBeursVerdeling(unittest.TestCase):
    def test_positie_op_twee_beurzen_wordt_gesplitst(self):
        df = _transacties([("A", "EAM", 10), ("B", "TDG", 5)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0, "B": 20.0}))
        self.assertEqual(res["beurs"], {"Euronext Amsterdam": 100.0, "Tradegate": 100.0})
        self.assertEqual(res["beurs_per_bron"], {"Euronext Amsterdam": {"A": 100.0}, "Tradegate": {"B": 100.0}})
        self.assertEqual(res["aantal_beurzen"], 2)

    def test_eam_en_xams_vallen_samen(self):
        df = _transacties([("A", "EAM", 1), ("B", "XAMS", 2)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0, "B": 10.0}))
        self.assertEqual(res["beurs"], {"Euronext Amsterdam": 30.0})
        self.assertEqual(res["aantal_beurzen"], 1)

    def test_euronext_variant_voegt_samen_tradegate_blijft_apart(self):
        df = _transacties([("A", "EAM", 1), ("B", "EPA", 2), ("C", "TDG", 3)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0, "B": 10.0, "C": 10.0}))
        self.assertEqual(res["aantal_beurzen"], 3)
        self.assertEqual(res["beurs_euronext"], {"Euronext": 30.0, "Tradegate": 30.0})
        self.assertEqual(res["beurs_euronext_per_bron"]["Euronext"], {"A": 10.0, "B": 20.0})
        self.assertEqual(res["aantal_beurzen_euronext"], 2)

    def test_volledig_verkochte_positie_telt_niet_mee(self):
        df = _transacties([("A", "EAM", 10), ("A", "EAM", -10), ("B", "NDQ", 1)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0, "B": 50.0}))
        self.assertEqual(res["beurs"], {"Nasdaq": 50.0})
        self.assertEqual(res["aantal_beurzen"], 1)

    def test_rij_zonder_ticker_telt_niet_mee(self):
        df = _transacties([("A", "EAM", 1), (None, "DEG", 100)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0}))
        self.assertEqual(res["beurs"], {"Euronext Amsterdam": 10.0})

    def test_onbekende_code_wordt_rauw_getoond(self):
        df = _transacties([("A", "XYZ", 2)])
        res = compute_beurs_verdeling(df, _koersen({"A": 5.0}))
        self.assertEqual(res["beurs"], {"XYZ": 10.0})
        self.assertEqual(res["beurs_euronext"], {"XYZ": 10.0})

    def test_totaal_gelijk_aan_waarde_posities(self):
        # 3*10 + 2*25 + 4*5 = 100
        df = _transacties([("A", "EAM", 3), ("B", "NSY", 2), ("C", "LSE", 4)])
        res = compute_beurs_verdeling(df, _koersen({"A": 10.0, "B": 25.0, "C": 5.0}))
        self.assertAlmostEqual(sum(res["beurs"].values()), 100.0)
        self.assertAlmostEqual(sum(res["beurs_euronext"].values()), 100.0)


if __name__ == "__main__":
    unittest.main()
