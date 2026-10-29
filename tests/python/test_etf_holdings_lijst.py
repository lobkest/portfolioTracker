"""bereken_etf_holdings_lijst(): holdings van één ETF met jouw deel in euro. get_etf_holdings is gemockt, geen database."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.portfolio_verdeling import bereken_etf_holdings_lijst


def holding(naam, gewicht, bron="provider_csv"):
    return {"holding_naam": naam, "gewicht": gewicht, "land": "United States", "bron": bron}


def lijst(holdings, positie_waarde=None):
    with patch("backend.portfolio_verdeling.get_etf_holdings", return_value=holdings) as mock:
        resultaat = bereken_etf_holdings_lijst("ETF", positie_waarde)
    mock.assert_called_once_with("ETF")
    return resultaat


class TestEtfHoldingsLijst(unittest.TestCase):
    def test_sorteert_aflopend_op_gewicht(self):
        r = lijst([holding("Klein", 0.1), holding("Groot", 0.5), holding("Midden", 0.3)])
        self.assertEqual([h["naam"] for h in r["holdings"]], ["Groot", "Midden", "Klein"])
        self.assertEqual(r["bron"], "provider_csv")
        self.assertEqual(r["aantal"], 3)

    def test_voegt_zelfde_bedrijf_samen(self):
        r = lijst([holding("Nestle S.A.", 0.04), holding("NESTLE SA", 0.03), holding("Apple Inc", 0.05)])
        self.assertEqual(r["aantal"], 2)
        nestle = r["holdings"][0]
        self.assertEqual(nestle["naam"], "Nestle S.A.")
        self.assertAlmostEqual(nestle["gewicht"], 0.07)

    def test_bedrag_met_positiewaarde(self):
        r = lijst([holding("A", 0.25), holding("B", 0.1)], positie_waarde=1000.0)
        self.assertEqual([h["bedrag_eur"] for h in r["holdings"]], [250.0, 100.0])

    def test_bedrag_none_zonder_of_met_nul_positiewaarde(self):
        for waarde in (None, 0, 0.0):
            with self.subTest(waarde=waarde):
                r = lijst([holding("A", 0.25)], positie_waarde=waarde)
                self.assertIsNone(r["holdings"][0]["bedrag_eur"])

    def test_bedrag_op_twee_decimalen(self):
        r = lijst([holding("A", 0.12345)], positie_waarde=100.0)
        self.assertEqual(r["holdings"][0]["bedrag_eur"], 12.35)

    def test_gedekt_gewicht_slaat_nan_over(self):
        r = lijst([holding("A", 0.5), holding("B", float("nan")), holding("C", 0.25)])
        self.assertAlmostEqual(r["gedekt_gewicht"], 0.75)
        self.assertEqual(r["aantal"], 2)

    def test_top10_bron_wordt_doorgegeven(self):
        r = lijst([holding("A", 0.1, bron="yfinance_top10")])
        self.assertEqual(r["bron"], "yfinance_top10")

    def test_lege_holdings(self):
        self.assertEqual(lijst([]), {"bron": None, "aantal": 0, "gedekt_gewicht": 0, "holdings": []})


if __name__ == "__main__":
    unittest.main()
