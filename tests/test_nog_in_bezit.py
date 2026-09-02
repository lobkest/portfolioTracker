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


if __name__ == "__main__":
    unittest.main()
