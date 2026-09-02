"""
Unit tests voor bereken_rendement_over_tijd() (analysis.py) — het "XIRR &
rendement"-tabblad: rendement% en XIRR% op meerdere momenten in de tijd
i.p.v. alleen het eindcijfer zoals op Statistieken.

Draait geheel offline: geen database, geen yfinance-calls — pure functie op
transacties_df + resultaat (zelfde patroon als bereken_jaren_overzicht in
test_rendement.py).
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bereken_rendement_over_tijd, bereken_totaal_rendement


class TestRendementOverTijd(unittest.TestCase):
    def _rij(self, datum, aantal, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "koers": 0.0, "totaal_eur": totaal_eur, "beurs": beurs, "product": ticker,
        }

    def test_leeg_resultaat_geeft_lege_lijsten(self):
        r = bereken_rendement_over_tijd(pd.DataFrame(), pd.DataFrame())
        self.assertEqual(r, {"labels": [], "rendement_pct": [], "xirr_pct": []})

    def test_maandeinden_plus_laatste_datum_als_stappen(self):
        # Loopt van 15 jan t/m 10 maart -- geen van beide is een maandeinde,
        # dus de stappen moeten zijn: 31 jan, 28 feb, en de laatste
        # beschikbare datum (10 maart) apart toegevoegd.
        index = pd.date_range("2023-01-15", "2023-03-10", freq="D")
        resultaat = pd.DataFrame(
            {"waarde": [1000.0] * len(index), "geinvesteerd": [1000.0] * len(index)},
            index=index,
        )
        transacties_df = pd.DataFrame([self._rij("2023-01-15", 10.0, -1000.0)])
        r = bereken_rendement_over_tijd(transacties_df, resultaat)
        self.assertEqual(r["labels"], ["2023-01-31", "2023-02-28", "2023-03-10"])

    def test_rendement_pct_matcht_bereken_totaal_rendement(self):
        index = pd.date_range("2023-01-01", "2023-02-28", freq="D")
        resultaat = pd.DataFrame(
            {"waarde": [1200.0] * len(index), "geinvesteerd": [1000.0] * len(index)},
            index=index,
        )
        transacties_df = pd.DataFrame([self._rij("2023-01-01", 10.0, -1000.0)])
        r = bereken_rendement_over_tijd(transacties_df, resultaat)
        verwacht = bereken_totaal_rendement(1000.0, 1200.0)["rendement_pct"]
        self.assertAlmostEqual(r["rendement_pct"][0], round(verwacht, 2))

    def test_periode_zonder_investering_geeft_none_niet_nul(self):
        # Eerste maand nog niets ingelegd (geinvesteerd=0) -- rendement_pct
        # en xirr_pct moeten None zijn, geen verzonnen 0%.
        index = pd.date_range("2023-01-01", "2023-01-31", freq="D")
        resultaat = pd.DataFrame(
            {"waarde": [0.0] * len(index), "geinvesteerd": [0.0] * len(index)},
            index=index,
        )
        transacties_df = pd.DataFrame([])
        transacties_df = pd.DataFrame(columns=["ticker", "datum", "aantal", "koers", "totaal_eur", "beurs", "product"])
        r = bereken_rendement_over_tijd(transacties_df, resultaat)
        self.assertIsNone(r["rendement_pct"][0])
        self.assertIsNone(r["xirr_pct"][0])

    def test_xirr_pct_is_percentage_niet_fractie(self):
        # Zelfde 10%-voorbeeld als TestXirr in test_rendement.py: €1000 op 1
        # jan 2023, waarde €1100 op 1 jan 2024 (exact 365 dagen) -> XIRR moet
        # 10.0 zijn (percentage), niet 0.10 (fractie).
        index = pd.date_range("2023-01-01", "2024-01-01", freq="D")
        waarde = [1000.0] * (len(index) - 1) + [1100.0]
        resultaat = pd.DataFrame(
            {"waarde": waarde, "geinvesteerd": [1000.0] * len(index)},
            index=index,
        )
        transacties_df = pd.DataFrame([self._rij("2023-01-01", 10.0, -1000.0)])
        r = bereken_rendement_over_tijd(transacties_df, resultaat)
        self.assertEqual(r["labels"][-1], "2024-01-01")
        self.assertAlmostEqual(r["xirr_pct"][-1], 10.0, places=2)


if __name__ == "__main__":
    unittest.main()
