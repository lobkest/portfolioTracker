"""
Unit tests voor het "nog_in_bezit"-veld op compute_per_ticker() (analysis.py),
dat de frontend gebruikt om " (oud)" achter een niet meer aangehouden positie
te tonen in de dropdown van het Per-aandeel-tabblad (zie static/js/app.js,
ververAandeelSelect()).

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import compute_per_ticker


class TestNogInBezit(unittest.TestCase):
    def _rij(self, datum, aantal, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "adj_aantal": aantal, "koers": 0.0, "totaal_eur": totaal_eur,
            "beurs": beurs, "product": ticker,
        }

    def test_nog_aangehouden_positie_is_nog_in_bezit(self):
        df = pd.DataFrame([self._rij("2023-01-01", 10.0, -100.0)])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0]},
            index=[pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        )
        result = compute_per_ticker(df, price_data)
        self.assertTrue(result["X"]["nog_in_bezit"])

    def test_volledig_verkochte_positie_is_niet_meer_in_bezit(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-02", -10.0, 100.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 10.0, 10.0]},
            index=[
                pd.Timestamp("2023-01-01"),
                pd.Timestamp("2023-01-02"),
                pd.Timestamp("2023-01-03"),
            ],
        )
        result = compute_per_ticker(df, price_data)
        self.assertFalse(result["X"]["nog_in_bezit"])

    def test_compute_per_ticker_gestopt_na_volledige_verkoop(self):
        # Regressie: "geinvesteerd" is een CUMULATIEVE netto cashflow
        # (aankopen min verkopen) -- bij een volledige verkoop MET VERLIES
        # (koop €100, verkoop €80) blijft geinvesteerd permanent op €20
        # staan (het gerealiseerde verlies), ook al is holdings dan allang
        # 0. Met geinvesteerd als "nog in bezit"-signaal (de oude bug) liep
        # de grafiek dus onterecht door tot de laatste koersdatum i.p.v. te
        # stoppen kort na de verkoop. holdings (aandelenaantal) is het
        # juiste signaal.
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-02", -10.0, 80.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 8.0, 8.0, 8.0, 8.0]},
            index=[
                pd.Timestamp("2023-01-01"),
                pd.Timestamp("2023-01-02"),
                pd.Timestamp("2023-01-03"),
                pd.Timestamp("2023-01-04"),
                pd.Timestamp("2023-01-05"),
            ],
        )
        result = compute_per_ticker(df, price_data)
        self.assertFalse(result["X"]["nog_in_bezit"])
        # Grafiek stopt kort na de verkoopdatum (2 jan + 1 dag eraan
        # toegevoegd, zie compute_per_ticker), niet doorlopend tot de
        # laatste koersdatum (5 jan).
        self.assertNotIn("2023-01-05", result["X"]["labels"])
        self.assertIn("2023-01-02", result["X"]["labels"])


if __name__ == "__main__":
    unittest.main()
