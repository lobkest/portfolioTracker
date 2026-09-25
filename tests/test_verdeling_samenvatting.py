"""
Unit tests voor portfolio_verdeling.bereken_verdeling_samenvatting() -- de
ETF-vs-aandeel-verhouding bovenaan het Verdeling-tabblad, voorheen in
static/js/app.js (toonVerdeling) zelf opgeteld.

Pure functie, geen DB/netwerk nodig.
"""
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio_verdeling import bereken_verdeling_samenvatting


class TestVerdelingSamenvatting(unittest.TestCase):
    def test_600_etf_en_400_aandeel_geeft_60_en_40_procent(self):
        verdeling = [
            {"ticker": "A", "waarde": 250.0, "is_etf": True},
            {"ticker": "B", "waarde": 400.0, "is_etf": False},
            {"ticker": "C", "waarde": 350.0, "is_etf": True},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertEqual(s["totaal"], 1000.0)
        self.assertEqual(s["etf_waarde"], 600.0)
        self.assertEqual(s["aandeel_waarde"], 400.0)
        self.assertEqual(s["etf_pct"], 60.0)
        self.assertEqual(s["aandeel_pct"], 40.0)

    def test_lege_lijst_geeft_nullen_geen_deling_door_nul(self):
        s = bereken_verdeling_samenvatting([])
        self.assertEqual(s, {
            "totaal": 0.0, "etf_waarde": 0.0, "aandeel_waarde": 0.0,
            "etf_pct": 0.0, "aandeel_pct": 0.0,
        })

    def test_alleen_etfs_geeft_100_procent_etf(self):
        verdeling = [
            {"ticker": "A", "waarde": 300.0, "is_etf": True},
            {"ticker": "B", "waarde": 700.0, "is_etf": True},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertEqual(s["etf_pct"], 100.0)
        self.assertEqual(s["aandeel_pct"], 0.0)
        self.assertEqual(s["aandeel_waarde"], 0.0)

    def test_waarde_nul_telt_niet_mee_en_crasht_niet(self):
        verdeling = [
            {"ticker": "A", "waarde": 0.0, "is_etf": True},
            {"ticker": "B", "waarde": 500.0, "is_etf": False},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertEqual(s["totaal"], 500.0)
        self.assertEqual(s["aandeel_pct"], 100.0)

    def test_nan_waarde_wordt_overgeslagen(self):
        # NaN in een som maakt het hele totaal NaN, en NaN in JSON breekt
        # res.json() in de frontend.
        verdeling = [
            {"ticker": "A", "waarde": float("nan"), "is_etf": True},
            {"ticker": "B", "waarde": 300.0, "is_etf": True},
            {"ticker": "C", "waarde": 100.0, "is_etf": False},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertEqual(s["totaal"], 400.0)
        self.assertEqual(s["etf_pct"], 75.0)
        self.assertEqual(s["aandeel_pct"], 25.0)

    def test_none_waarde_wordt_overgeslagen(self):
        verdeling = [
            {"ticker": "A", "waarde": None, "is_etf": False},
            {"ticker": "B", "waarde": 200.0, "is_etf": True},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertEqual(s["totaal"], 200.0)
        self.assertEqual(s["etf_pct"], 100.0)

    def test_decimal_waarde_wordt_float(self):
        verdeling = [
            {"ticker": "A", "waarde": Decimal("150.50"), "is_etf": True},
            {"ticker": "B", "waarde": Decimal("49.50"), "is_etf": False},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertIsInstance(s["totaal"], float)
        self.assertEqual(s["totaal"], 200.0)
        self.assertEqual(s["etf_pct"], 75.25)

    def test_percentages_tellen_op_tot_100(self):
        verdeling = [
            {"ticker": "A", "waarde": 1.0, "is_etf": True},
            {"ticker": "B", "waarde": 2.0, "is_etf": False},
        ]
        s = bereken_verdeling_samenvatting(verdeling)
        self.assertAlmostEqual(s["etf_pct"] + s["aandeel_pct"], 100.0, places=1)


if __name__ == "__main__":
    unittest.main()
