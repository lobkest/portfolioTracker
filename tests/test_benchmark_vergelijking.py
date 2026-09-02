"""
Unit tests voor bereken_benchmark_vergelijking() (analysis.py) — de
"Vergelijk met..."-optie op het Rendement-tabblad (zie CLAUDE.md,
BENCHMARK_TICKERS). Simuleert dezelfde cashflows als de echte portfolio in
een benchmark-ticker in plaats van de eigen posities.

Draait geheel offline: geen database, geen yfinance-calls — de benchmark-
koersen worden als kant-en-klare pd.Series aangeleverd (zie de docstring van
de functie: dat ophalen is de taak van de aanroeper, app.py).
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bereken_benchmark_vergelijking


class TestBenchmarkVergelijking(unittest.TestCase):
    def _transacties(self, rijen):
        return pd.DataFrame(rijen)

    def _rij(self, datum, aantal, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "koers": 0.0, "totaal_eur": totaal_eur, "beurs": beurs, "product": ticker,
        }

    def test_enkele_investering_volgt_de_benchmarkkoers(self):
        # €100 ingelegd op dag 1 tegen benchmarkkoers 10 -> 10 hypothetische
        # stuks; waarde volgt vanaf dan gewoon aantal * koers.
        transacties_df = self._transacties([self._rij("2023-01-01", 10.0, -100.0)])
        resultaat = pd.DataFrame(
            {"waarde": [100.0, 110.0, 120.0], "geinvesteerd": [100.0, 100.0, 100.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        benchmark_koersen = pd.Series(
            [10.0, 11.0, 12.0],
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        r = bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen)
        self.assertEqual(r["labels"], ["2023-01-01", "2023-01-02", "2023-01-03"])
        self.assertEqual(r["waarde"], [100.0, 110.0, 120.0])
        self.assertEqual(r["rendement"], [0.0, 10.0, 20.0])
        self.assertEqual(r["vanaf_datum"], "2023-01-01")
        self.assertFalse(r["onvolledige_dekking"])

    def test_onttrekking_verkleint_de_hypothetische_positie(self):
        # €100 in (10 stuks @10), daarna €55 onttrokken (verkoop-cashflow,
        # positief bedrag) op dag waar de koers 11 is -> -5 stuks -> 5 stuks
        # resteren, waarde op dag 3 (koers 12) moet 5*12=60 zijn.
        transacties_df = self._transacties([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-02", -5.0, 55.0),
        ])
        resultaat = pd.DataFrame(
            {"waarde": [100.0, 55.0, 60.0], "geinvesteerd": [100.0, 45.0, 45.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        benchmark_koersen = pd.Series(
            [10.0, 11.0, 12.0],
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        r = bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen)
        self.assertAlmostEqual(r["waarde"][-1], 60.0)

    def test_benchmark_koersdata_begint_later_dan_eerste_cashflow(self):
        # Cashflow op 1 jan, maar de benchmark heeft pas vanaf 2 jan
        # koersdata (bv. AEX/IAEA.AS, sinds 2020) -> reeks start pas op 2
        # jan, met een expliciete "onvolledige_dekking"-vlag i.p.v.
        # stilzwijgend een te lage waarde te tonen.
        transacties_df = self._transacties([self._rij("2023-01-01", 10.0, -100.0)])
        resultaat = pd.DataFrame(
            {"waarde": [100.0, 100.0], "geinvesteerd": [100.0, 100.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02"]),
        )
        benchmark_koersen = pd.Series([11.0], index=pd.to_datetime(["2023-01-02"]))
        r = bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen)
        self.assertEqual(r["labels"], ["2023-01-02"])
        self.assertEqual(r["vanaf_datum"], "2023-01-02")
        self.assertTrue(r["onvolledige_dekking"])

    def test_geen_cashflows_geeft_none(self):
        transacties_df = self._transacties([self._rij("2023-01-01", 10.0, 0.0)])
        resultaat = pd.DataFrame(
            {"waarde": [0.0], "geinvesteerd": [0.0]},
            index=pd.to_datetime(["2023-01-01"]),
        )
        benchmark_koersen = pd.Series([10.0], index=pd.to_datetime(["2023-01-01"]))
        r = bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen)
        self.assertIsNone(r)

    def test_leeg_resultaat_geeft_none(self):
        transacties_df = self._transacties([self._rij("2023-01-01", 10.0, -100.0)])
        r = bereken_benchmark_vergelijking(transacties_df, pd.DataFrame(), pd.Series(dtype=float))
        self.assertIsNone(r)


if __name__ == "__main__":
    unittest.main()
