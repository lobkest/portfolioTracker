"""
Unit tests voor portfolio_verdeling.bereken_land_dekking(): hoeveel van een ETF
een bekend land heeft, plus de holdingstabel voor Diagnostiek > ETF-holdings.

Draait geheel offline: pure functie, geen database of Yahoo.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_verdeling import bereken_land_dekking


def _h(naam, gewicht, land, sector=None):
    return {"naam": naam, "gewicht": gewicht, "land": land, "sector": sector}


class TestBerekenLandDekking(unittest.TestCase):
    def test_alleen_top10_lage_dekking(self):
        # 10 holdings van 2% = 20% gedekt, alle met land: 80% onbekend, restrij 80%.
        holdings = [_h(f"Bedrijf {i}", 0.02, "United States", "Technology") for i in range(10)]
        uit = bereken_land_dekking(holdings)
        self.assertAlmostEqual(uit["onbekend_pct"], 80.0)
        self.assertAlmostEqual(uit["dekking_pct"], 20.0)
        self.assertEqual(len(uit["rijen"]), 11)
        self.assertEqual(uit["rijen"][-1][0], "Niet in holdingsdata")
        self.assertAlmostEqual(uit["rijen"][-1][1], 80.0)
        self.assertEqual(uit["rijen"][-1][2:], ["–", "–"])

    def test_volledige_dekking_geen_restrij(self):
        holdings = [_h("Klein", 0.4, "Japan", "Industrials"), _h("Groot", 0.6, "United States", "Technology")]
        uit = bereken_land_dekking(holdings)
        self.assertAlmostEqual(uit["onbekend_pct"], 0.0)
        self.assertEqual(uit["rijen"], [["Groot", 60.0, "United States", "Technology"],
                                        ["Klein", 40.0, "Japan", "Industrials"]])

    def test_holdings_zonder_land(self):
        # 30% zonder land (None en "Unknown") + 70% met land = 30% onbekend; ontbrekende sector -> Unknown.
        holdings = [_h("A", 0.7, "Germany", "Financial Services"), _h("B", 0.2, None), _h("C", 0.1, "Unknown")]
        uit = bereken_land_dekking(holdings)
        self.assertAlmostEqual(uit["onbekend_pct"], 30.0)
        self.assertEqual([r[0] for r in uit["rijen"]], ["A", "B", "C"])
        self.assertEqual(uit["rijen"][1][2:], ["Unknown", "Unknown"])
        self.assertEqual(uit["rijen"][2][2:], ["Unknown", "Unknown"])

    def test_lege_invoer(self):
        uit = bereken_land_dekking([])
        self.assertEqual(uit["onbekend_pct"], 100.0)
        self.assertEqual(uit["rijen"], [["Niet in holdingsdata", 100.0, "–", "–"]])


if __name__ == "__main__":
    unittest.main()
