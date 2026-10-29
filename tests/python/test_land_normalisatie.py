"""Een holding-land '-', leeg of None telt als 'Unknown' (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.etf_holdings_provider import _holding_rij, normaliseer_land
from backend.etf_proxy import land_uit_holdings
from backend.portfolio_verdeling import bereken_land_dekking, land_pct_uit_holdings

LEEG = ["-", "", "  ", None, " - "]


class TestNormaliseerLand(unittest.TestCase):
    def test_streepje_leeg_en_none_zijn_unknown(self):
        for waarde in LEEG:
            with self.subTest(waarde=waarde):
                self.assertEqual(normaliseer_land(waarde), "Unknown")

    def test_echte_landen_blijven_staan_ook_european_union(self):
        self.assertEqual(normaliseer_land("European Union"), "European Union")
        self.assertEqual(normaliseer_land(" Japan "), "Japan")

    def test_provider_rij_gebruikt_dezelfde_regel(self):
        self.assertEqual(_holding_rij("Cash", 1.0, "-", None)["land"], "Unknown")

    def test_land_pct_telt_alle_lege_varianten_samen(self):
        holdings = [{"land": waarde, "gewicht": 0.1} for waarde in LEEG] + [{"land": "European Union", "gewicht": 0.5}]
        land = land_pct_uit_holdings(holdings)
        self.assertAlmostEqual(land["Unknown"], 0.5)
        self.assertAlmostEqual(land["European Union"], 0.5)
        self.assertEqual(set(land), {"Unknown", "European Union"})

    def test_proxy_land_telt_ook_samen(self):
        land = land_uit_holdings([{"land": "-", "gewicht": 10.0}, {"land": None, "gewicht": 20.0}, {"land": "Japan", "gewicht": 70.0}])
        self.assertAlmostEqual(land["Unknown"], 0.3)
        self.assertAlmostEqual(land["Japan"], 0.7)

    def test_landdekking_telt_streepje_als_onbekend(self):
        dekking = bereken_land_dekking([{"naam": "Cash", "gewicht": 0.4, "land": "-"}, {"naam": "A", "gewicht": 0.6, "land": "Japan"}])
        self.assertAlmostEqual(dekking["onbekend_pct"], 40.0)
        self.assertEqual(dekking["rijen"][1][2], "Unknown")


if __name__ == "__main__":
    unittest.main()
