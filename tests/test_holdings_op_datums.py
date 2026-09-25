"""
Unit tests voor portfolio_calc.holdings_op_datums() -- het aantal
aangehouden aandelen per koersdatum voor /api/portfolio/<code>/ticker-koers-
bereik (de "meer historie laden"-knoppen op 'Per aandeel aankoop'). Voorheen
vulde de frontend (mergePrependHistorie/mergeAppendHistorie) die reeks zelf
met nullen aan.

Pure functie, geen DB/netwerk nodig.
"""
import os
import sys
import unittest
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio_calc import holdings_op_datums, compute_per_ticker_koers_en_aankopen


def _datums(*isos):
    return pd.DatetimeIndex([pd.Timestamp(d) for d in isos])


class TestHoldingsOpDatums(unittest.TestCase):
    def _trades(self, *rijen):
        return pd.DataFrame(
            [{"ticker": "X", "datum": pd.Timestamp(d), "adj_aantal": a} for d, a in rijen]
        )

    def test_nul_voor_de_eerste_aankoop(self):
        trades = self._trades(("2023-01-03", 10.0))
        datums = _datums("2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04")
        self.assertEqual(holdings_op_datums(trades, datums), [0.0, 0.0, 10.0, 10.0])

    def test_nul_na_volledige_verkoop(self):
        trades = self._trades(("2023-01-01", 10.0), ("2023-01-03", -10.0))
        datums = _datums("2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05")
        self.assertEqual(holdings_op_datums(trades, datums), [10.0, 10.0, 0.0, 0.0, 0.0])

    def test_tussentijdse_nulperiode_blijft_staan(self):
        # Verkocht en later teruggekocht: de nul-periode ertussen is echte
        # data en mag niet worden weggeknipt of overgeslagen.
        trades = self._trades(("2023-01-01", 5.0), ("2023-01-02", -5.0), ("2023-01-04", 3.0))
        datums = _datums("2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05")
        self.assertEqual(holdings_op_datums(trades, datums), [5.0, 0.0, 0.0, 3.0, 3.0])

    def test_alle_datums_voor_eerste_transactie_geven_alleen_nullen(self):
        # Typisch "+6 maanden terug": het hele opgehaalde stuk ligt vóór de
        # eerste aankoop.
        trades = self._trades(("2023-06-01", 10.0))
        datums = _datums("2023-01-01", "2023-02-01", "2023-03-01")
        self.assertEqual(holdings_op_datums(trades, datums), [0.0, 0.0, 0.0])

    def test_transactie_tussen_twee_koersdatums_telt_vanaf_de_volgende(self):
        # Aankoop op een zaterdag: telt pas mee op de eerstvolgende handelsdag.
        trades = self._trades(("2023-01-07", 4.0))
        datums = _datums("2023-01-06", "2023-01-09")
        self.assertEqual(holdings_op_datums(trades, datums), [0.0, 4.0])

    def test_transacties_voor_de_reeks_tellen_mee_als_beginstand(self):
        trades = self._trades(("2022-12-01", 7.0))
        datums = _datums("2023-01-01", "2023-01-02")
        self.assertEqual(holdings_op_datums(trades, datums), [7.0, 7.0])

    def test_onafgeronde_restpositie_wordt_nul(self):
        # Float-ruis na een volledige verkoop (bv. fractionele stukken na een
        # split) moet als 0 terugkomen, niet als 1e-12.
        trades = self._trades(("2023-01-01", 0.1), ("2023-01-01", 0.2), ("2023-01-02", -0.3))
        datums = _datums("2023-01-01", "2023-01-02")
        self.assertEqual(holdings_op_datums(trades, datums), [0.3, 0.0])

    def test_decimal_en_nan_aantal(self):
        trades = pd.DataFrame([
            {"ticker": "X", "datum": pd.Timestamp("2023-01-01"), "adj_aantal": Decimal("2.5")},
            {"ticker": "X", "datum": pd.Timestamp("2023-01-02"), "adj_aantal": float("nan")},
        ])
        datums = _datums("2023-01-01", "2023-01-02")
        self.assertEqual(holdings_op_datums(trades, datums), [2.5, 2.5])

    def test_geen_transacties_geeft_nullen(self):
        trades = pd.DataFrame(columns=["ticker", "datum", "adj_aantal"])
        datums = _datums("2023-01-01", "2023-01-02")
        self.assertEqual(holdings_op_datums(trades, datums), [0.0, 0.0])

    def test_lege_datumreeks(self):
        trades = self._trades(("2023-01-01", 1.0))
        self.assertEqual(holdings_op_datums(trades, _datums()), [])

    def test_gelijk_aan_per_ticker_aankoop_binnen_de_crop(self):
        # Binnen de standaard-crop moeten beide bronnen exact dezelfde
        # holdings geven, anders springt de lijn na "meer historie laden".
        trades = pd.DataFrame([
            {"ticker": "X", "datum": pd.Timestamp("2023-01-02"), "aantal": 10.0, "adj_aantal": 10.0,
             "koers": 0.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "X"},
            {"ticker": "X", "datum": pd.Timestamp("2023-01-04"), "aantal": -4.0, "adj_aantal": -4.0,
             "koers": 0.0, "totaal_eur": 40.0, "beurs": "EAM", "product": "X"},
        ])
        index = _datums("2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05")
        price_data = pd.DataFrame({"X": [10.0] * 5}, index=index)
        aankoop = compute_per_ticker_koers_en_aankopen(trades, price_data)["X"]
        crop_index = pd.DatetimeIndex(pd.to_datetime(aankoop["labels"]))
        self.assertEqual(holdings_op_datums(trades, crop_index), aankoop["holdings"])


if __name__ == "__main__":
    unittest.main()
