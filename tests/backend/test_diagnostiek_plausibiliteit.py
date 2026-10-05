"""Plausibiliteit-checks (diagnostiek_checks.py) op de reeksen die het dashboard berekent. Offline, geen database.

De split-gevallen lopen door de echte keten (get_prices met gemockte Yahoo, effectieve datums, compute_per_ticker).
"""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diagnostiek import GOED, LET_OP
from diagnostiek_checks import (
    MAX_BEVINDINGEN_PER_CHECK, check_dagsprong, check_transactiekoers_vs_rekenkoers, check_waarde_vs_inleg,
    _naam_per_ticker,
)
from portfolio_calc import compute_per_ticker
from test_waarde_latere_splits import TICKER, _Omgeving, _rij

D = datetime.date


def _alle_checks(transacties_df, prijs_data):
    per_ticker = compute_per_ticker(transacties_df, prijs_data)
    return (check_transactiekoers_vs_rekenkoers(transacties_df, prijs_data)
            + check_waarde_vs_inleg(per_ticker, _naam_per_ticker(transacties_df))
            + check_dagsprong(transacties_df, per_ticker))


def _via_keten(transacties, close, splits, dagen):
    with _Omgeving(transacties, {TICKER: close}, {TICKER: splits}, dagen) as omg:
        _resultaat, transacties_df, prijs_data = omg.waarde()
    return _alle_checks(transacties_df, prijs_data)


def _df(rijen):
    kolommen = ["datum", "product", "echte_naam", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur",
                "waarde_eur"]
    df = pd.DataFrame(rijen, columns=kolommen)
    df["datum"] = pd.to_datetime(df["datum"])
    return df


def _niveaus(bevindingen):
    return [(b["niveau"], b["sleutel"]) for b in bevindingen]


class TestXelaSpikeOudeSituatie(unittest.TestCase):
    """Vóór de splitfix: Yahoo's koers gecorrigeerd voor latere reverse splits (1:3, 1:20, 1:200 = x12.000), aantal ruw."""

    FACTOR = 3 * 20 * 200

    def _invoer(self):
        dagen = pd.bdate_range("2021-01-04", "2021-01-29")
        koers = pd.Series(0.5, index=dagen)
        koers["2021-01-20":] = 0.6
        prijs_data = pd.DataFrame({"XELA": koers * self.FACTOR})
        df = _df([
            ("2021-01-08", "EXELA", "EXELA TECHNOLOGIES", "US1", "NSQ", "XELA", 100, 0.5, -51.0, -50.0),
            ("2021-01-25", "EXELA", "EXELA TECHNOLOGIES", "US1", "NSQ", "XELA", 10, 0.6, -7.0, -6.0),
        ])
        return df, prijs_data

    def test_transactiekoers_wordt_gevangen(self):
        df, prijs_data = self._invoer()
        [b] = check_transactiekoers_vs_rekenkoers(df, prijs_data)
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "plausibel:koers:XELA"))
        for stuk in ("EXELA TECHNOLOGIES", "(XELA)", "2 van 2", "08-01-2021", "DeGiro EUR 0,50", "dashboard EUR 6.000,00"):
            self.assertIn(stuk, b["tekst"])

    def test_waarde_ten_opzichte_van_inleg_wordt_gevangen(self):
        df, prijs_data = self._invoer()
        per_ticker = compute_per_ticker(df, prijs_data)
        [b] = check_waarde_vs_inleg(per_ticker, _naam_per_ticker(df))
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "plausibel:waarde_inleg:XELA"))
        # 100 x 7.200 = 720.000 tegen 50 inleg: 14.400x, op de eerste dag met de hogere koers.
        self.assertIn("20-01-2021", b["tekst"])
        self.assertIn("14400x", b["tekst"])
        self.assertIn("EUR 720.000,00", b["tekst"])


class TestDagsprong(unittest.TestCase):
    def _invoer(self, sprongdag):
        dagen = pd.bdate_range("2024-03-01", "2024-03-29")
        koers = pd.Series(10.0, index=dagen)
        koers[sprongdag:] = 25.0
        df = _df([("2024-03-01", "ACME", "ACME CORP", "US1", "NSY", "ACM", 10, 10.0, -100.0, -100.0)])
        return df, pd.DataFrame({"ACM": koers})

    def test_sprong_zonder_transactie(self):
        df, prijs_data = self._invoer("2024-03-12")
        [b] = check_dagsprong(df, compute_per_ticker(df, prijs_data))
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "plausibel:dagsprong:ACM"))
        for stuk in ("ACME CORP", "12-03-2024", "EUR 100,00", "EUR 250,00", "+150,0%"):
            self.assertIn(stuk, b["tekst"])

    def test_sprong_op_transactiedag_telt_niet(self):
        df, prijs_data = self._invoer("2024-03-12")
        df = pd.concat([df, _df([("2024-03-12", "ACME", "ACME CORP", "US1", "NSY", "ACM", 1, 25.0, -25.0, -25.0)])])
        self.assertEqual(check_dagsprong(df, compute_per_ticker(df, prijs_data)), [])

    def test_weekendboeking_telt_op_de_volgende_koersdag(self):
        df, prijs_data = self._invoer("2024-03-11")  # maandag
        df = pd.concat([df, _df([("2024-03-09", "ACME", "ACME CORP", "US1", "NSY", "ACM", 1, 25.0, -25.0, -25.0)])])
        self.assertEqual(check_dagsprong(df, compute_per_ticker(df, prijs_data)), [])

    def test_beperkt_tot_max_en_en_x_meer(self):
        dagen = pd.bdate_range("2024-03-01", "2024-03-29")
        aantal = MAX_BEVINDINGEN_PER_CHECK + 2
        koersen, rijen = {}, []
        for i in range(aantal):
            koers = pd.Series(10.0, index=dagen)
            koers["2024-03-12":] = 20.0 + i
            koersen[f"T{i}"] = koers
            rijen.append(("2024-03-01", f"P{i}", f"P{i}", f"X{i}", "NSY", f"T{i}", 1, 10.0, -10.0, -10.0))
        df = _df(rijen)
        uit = check_dagsprong(df, compute_per_ticker(df, pd.DataFrame(koersen)))
        self.assertEqual(len(uit), MAX_BEVINDINGEN_PER_CHECK + 1)
        self.assertEqual(uit[0]["sleutel"], f"plausibel:dagsprong:T{aantal - 1}")  # grootste sprong eerst
        self.assertIn("en 2 meer", uit[-1]["tekst"])


class TestGeenMelding(unittest.TestCase):
    def test_normale_positie_alleen_goed(self):
        dagen = pd.bdate_range("2024-01-02", "2024-03-29")
        koers = pd.Series([100.0 + i * 0.5 for i in range(len(dagen))], index=dagen)
        df = _df([
            ("2024-01-02", "ACME", "ACME CORP", "US1", "NSY", "ACM", 10, 101.0, -1011.0, -1010.0),
            ("2024-02-01", "ACME", "ACME CORP", "US1", "NSY", "ACM", 5, 110.0, -551.0, -550.0),
            ("2024-03-01", "ACME", "ACME CORP", "US1", "NSY", "ACM", -5, 120.0, 599.0, 600.0),
        ])
        [b] = _alle_checks(df, pd.DataFrame({"ACM": koers}))
        self.assertEqual((b["niveau"], b["sleutel"]), (GOED, "plausibel:koers"))
        self.assertIn("1 posities (3 transacties)", b["tekst"])

    def test_gesloten_positie_met_latere_forward_split(self):
        dagen = pd.bdate_range("2022-01-03", "2022-08-31")
        transacties = [_rij(D(2022, 1, 3), 10, 100.0, -1000.0), _rij(D(2022, 1, 14), -10, 100.0, 1000.0)]
        uit = _via_keten(transacties, pd.Series(25.0, index=dagen), {"2022-07-22": 4.0}, dagen)
        self.assertEqual(_niveaus(uit), [(GOED, "plausibel:koers")])

    def test_gesloten_positie_met_latere_reverse_split(self):
        dagen = pd.bdate_range("2022-01-03", "2023-12-29")
        transacties = [_rij(D(2022, 1, 3), 100, 5.0, -500.0), _rij(D(2022, 1, 14), -100, 5.0, 500.0)]
        uit = _via_keten(transacties, pd.Series(50.0, index=dagen), {"2023-12-01": 0.1}, dagen)
        self.assertEqual(_niveaus(uit), [(GOED, "plausibel:koers")])

    def test_geboekte_split_byd_vorm(self):
        ratio = 37 / 14
        dagen = pd.bdate_range("2025-05-02", "2025-08-29")
        transacties = [
            _rij(D(2025, 5, 2), 14, 10.0, -140.0, beurs="TDG"),
            _rij(D(2025, 6, 10), 14, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 6, 10), 9, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 7, 31), -23, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 7, 31), 23, 0.0, 0.0, beurs="TDG"),
        ]
        uit = _via_keten(transacties, pd.Series(10 / ratio, index=dagen), {"2025-06-10": ratio}, dagen)
        self.assertEqual(_niveaus(uit), [(GOED, "plausibel:koers")])

    def test_lege_invoer(self):
        self.assertEqual(_alle_checks(_df([]), pd.DataFrame()), [])


if __name__ == "__main__":
    unittest.main()
