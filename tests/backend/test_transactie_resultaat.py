"""Resultaat en dividend per aankoop (transactie_resultaat.py): pure functies, zonder database."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from transactie_resultaat import bereken_transactie_resultaten, laatste_koersen

NAN = float("nan")
M = datetime.time(0, 0)


def _rij(datum, aantal, koers, totaal_eur=None, waarde_eur="auto", adj_aantal=None, isin="NL0000000001",
         ticker="AAA", tijd=datetime.time(10, 0), beurs="EAM", product="ACME", kosten=-1.0):
    """Aankoop: totaal_eur = -(aantal x koers) - 1, waarde_eur = -(aantal x koers). Verkoop: totaal_eur meegeven."""
    if totaal_eur is None:
        totaal_eur = -aantal * koers - 1.0
    if waarde_eur == "auto":
        waarde_eur = -aantal * koers if aantal > 0 else aantal * -koers
    return {"datum": pd.Timestamp(datum), "tijd": tijd, "product": product, "isin": isin, "beurs": beurs,
            "ticker": ticker, "aantal": float(aantal), "adj_aantal": float(adj_aantal if adj_aantal is not None else aantal),
            "koers": koers, "totaal_eur": totaal_eur, "waarde_eur": waarde_eur, "transactiekosten": kosten}


def _bereken(rijen, koersen=None, dividenden=None):
    df = pd.DataFrame(rijen)
    uitkomst = bereken_transactie_resultaten(df, koersen or {}, dividenden)
    return [uitkomst[label] for label in df.index]


def _dividend(datum, netto_eur, isin="NL0000000001"):
    return {"datum": datum, "isin": isin, "netto_eur": netto_eur, "herinvesteerd": False}


class TestResultaatPerAankoop(unittest.TestCase):
    def test_alleen_open(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"AAA": 12.0})
        self.assertAlmostEqual(r["resultaat_eur"], 20.0)
        self.assertAlmostEqual(r["resultaat_pct"], 20.0)
        self.assertEqual(r["status"], "open")

    def test_twee_aankopen_elk_eigen_aankoopkoers(self):
        a, b = _bereken([_rij("2024-01-01", 10, 10.0), _rij("2024-02-01", 10, 15.0)], {"AAA": 12.0})
        self.assertAlmostEqual(a["resultaat_eur"], 20.0)
        self.assertAlmostEqual(a["resultaat_pct"], 20.0)
        self.assertAlmostEqual(b["resultaat_eur"], -30.0)
        self.assertAlmostEqual(b["resultaat_pct"], -20.0)

    def test_volledig_verkocht_houdt_resultaat_ook_zonder_koers(self):
        aankoop, verkoop = _bereken([_rij("2024-01-01", 10, 10.0), _rij("2024-03-01", -10, 15.0, totaal_eur=150.0)])
        self.assertEqual(aankoop["status"], "gesloten")
        self.assertAlmostEqual(aankoop["resultaat_eur"], 50.0)
        self.assertAlmostEqual(aankoop["resultaat_pct"], 50.0)
        self.assertEqual(verkoop, {"resultaat_eur": None, "resultaat_pct": None, "status": None,
                                   "dividend_eur": None, "dividend_pct": None})

    def test_fifo_deels_verkocht_over_twee_aankopen(self):
        # 15 verkocht voor 450 (30 per stuk): A (10 @ 10) helemaal, B (10 @ 20) voor 5 stuks.
        a, b, _ = _bereken([
            _rij("2024-01-01", 10, 10.0), _rij("2024-02-01", 10, 20.0), _rij("2024-03-01", -15, 30.0, totaal_eur=450.0),
        ], {"AAA": 25.0})
        self.assertEqual(a["status"], "gesloten")
        self.assertAlmostEqual(a["resultaat_eur"], 200.0)
        self.assertAlmostEqual(a["resultaat_pct"], 200.0)
        self.assertEqual(b["status"], "deels_verkocht")
        self.assertAlmostEqual(b["resultaat_eur"], 5 * 10 + 5 * 5)
        self.assertAlmostEqual(b["resultaat_pct"], 37.5)

    def test_verkoop_op_zelfde_dag_volgt_tijd(self):
        verkoop, aankoop = _bereken([
            _rij("2024-01-01", -5, 12.0, totaal_eur=60.0, tijd=datetime.time(9, 0)),
            _rij("2024-01-01", 5, 10.0, tijd=datetime.time(15, 0)),
        ], {"AAA": 11.0})
        self.assertEqual(aankoop["status"], "open")
        self.assertAlmostEqual(aankoop["resultaat_eur"], 5.0)

    def test_split_gecorrigeerd_aantal(self):
        # 10 stuks @ 40 gekocht, na een 4:1-split 40 stuks: aankoopkoers per huidig stuk 10.
        [r] = _bereken([_rij("2020-01-01", 10, 40.0, adj_aantal=40)], {"AAA": 12.0})
        self.assertAlmostEqual(r["resultaat_eur"], 80.0)
        self.assertAlmostEqual(r["resultaat_pct"], 20.0)

    def test_isin_wissel_schaalt_open_stukken(self):
        # 1:3-split met ISIN-wissel: 10 OLD -> 30 NEW; aankoop 10 @ 30 wordt 30 @ 10.
        aankoop, uit, in_ = _bereken([
            _rij("2024-01-01", 10, 30.0, isin="OLD", ticker="XYZ"),
            _rij("2024-05-01", -10, 30.0, totaal_eur=300.0, isin="OLD", ticker="XYZ", tijd=M, kosten=None),
            _rij("2024-05-01", 30, 10.0, totaal_eur=-300.0, isin="NEW", ticker="XYZ", tijd=M, kosten=None),
        ], {"XYZ": 12.0})
        self.assertEqual(aankoop["status"], "open")
        self.assertAlmostEqual(aankoop["resultaat_eur"], 60.0)
        self.assertAlmostEqual(aankoop["resultaat_pct"], 20.0)
        self.assertIsNone(uit["status"])
        self.assertIsNone(in_["status"])

    def test_ontbrekende_koers(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"ANDER": 12.0})
        self.assertIsNone(r["resultaat_eur"])
        self.assertIsNone(r["resultaat_pct"])
        self.assertEqual(r["status"], "open")

    def test_koers_van_meest_recente_ticker(self):
        a, _ = _bereken([_rij("2024-01-01", 10, 10.0, ticker="OUD"), _rij("2024-02-01", 1, 10.0, ticker="NIEUW")],
                        {"OUD": 50.0, "NIEUW": 12.0})
        self.assertAlmostEqual(a["resultaat_eur"], 20.0)

    def test_corporate_action_rij_krijgt_niets(self):
        _, ca = _bereken([_rij("2024-01-01", 10, 10.0), _rij("2024-02-01", 10, 10.0, beurs="DEG")], {"AAA": 12.0})
        self.assertIsNone(ca["status"])
        self.assertIsNone(ca["resultaat_eur"])

    def test_rij_zonder_isin_en_ticker(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0, isin=None, ticker=None)], {"AAA": 12.0})
        self.assertIsNone(r["status"])

    def test_zonder_isin_terugval_op_ticker(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0, isin=None)], {"AAA": 12.0})
        self.assertAlmostEqual(r["resultaat_eur"], 20.0)

    def test_kostenbasis_nul_geeft_geen_percentage(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0, waarde_eur=0.0)], {"AAA": 12.0})
        self.assertAlmostEqual(r["resultaat_eur"], 120.0)
        self.assertIsNone(r["resultaat_pct"])

    def test_waarde_eur_null_valt_terug_op_koers(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0, waarde_eur=NAN)], {"AAA": 12.0})
        self.assertAlmostEqual(r["resultaat_eur"], 20.0)
        self.assertAlmostEqual(r["resultaat_pct"], 20.0)


class TestDividendPerAankoop(unittest.TestCase):
    def test_een_aankoop(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"AAA": 10.0}, [_dividend(datetime.date(2024, 6, 1), 5.0)])
        self.assertAlmostEqual(r["dividend_eur"], 5.0)
        self.assertAlmostEqual(r["dividend_pct"], 5.0)
        self.assertAlmostEqual(r["resultaat_eur"], 0.0)

    def test_twee_loten_pro_rata(self):
        a, b = _bereken([_rij("2024-01-01", 10, 10.0), _rij("2024-02-01", 30, 10.0)], {"AAA": 10.0},
                        [_dividend(datetime.date(2024, 6, 1), 8.0)])
        self.assertAlmostEqual(a["dividend_eur"], 2.0)
        self.assertAlmostEqual(b["dividend_eur"], 6.0)
        self.assertAlmostEqual(b["dividend_pct"], 2.0)

    def test_gesloten_lot_voor_dividend_krijgt_niets(self):
        a, verkoop, b = _bereken([
            _rij("2024-01-01", 10, 10.0), _rij("2024-03-01", -10, 10.0, totaal_eur=100.0), _rij("2024-04-01", 10, 10.0),
        ], {"AAA": 10.0}, [_dividend(datetime.date(2024, 6, 1), 4.0)])
        self.assertAlmostEqual(a["dividend_eur"], 0.0)
        self.assertIsNone(verkoop["dividend_eur"])
        self.assertAlmostEqual(b["dividend_eur"], 4.0)

    def test_aankoop_op_uitbetaaldag_telt_niet_mee(self):
        a, b = _bereken([_rij("2024-01-01", 10, 10.0), _rij("2024-06-01", 10, 10.0)], {"AAA": 10.0},
                        [_dividend(datetime.date(2024, 6, 1), 6.0)])
        self.assertAlmostEqual(a["dividend_eur"], 6.0)
        self.assertAlmostEqual(b["dividend_eur"], 0.0)

    def test_netto_none_overgeslagen(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"AAA": 10.0},
                       [_dividend(datetime.date(2024, 6, 1), None), _dividend(datetime.date(2024, 7, 1), 3.0)])
        self.assertAlmostEqual(r["dividend_eur"], 3.0)

    def test_dividend_van_andere_isin_telt_niet(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"AAA": 10.0},
                       [_dividend(datetime.date(2024, 6, 1), 3.0, isin="US0000000002")])
        self.assertAlmostEqual(r["dividend_eur"], 0.0)

    def test_zonder_dividenden_none(self):
        [r] = _bereken([_rij("2024-01-01", 10, 10.0)], {"AAA": 10.0}, None)
        self.assertIsNone(r["dividend_eur"])
        self.assertIsNone(r["dividend_pct"])
        self.assertEqual(r["status"], "open")


class TestLaatsteKoersen(unittest.TestCase):
    def test_laatste_niet_lege_koers_per_ticker(self):
        prijzen = pd.DataFrame({"AAA": [10.0, 11.0, NAN], "BBB": [NAN, NAN, NAN]},
                               index=pd.date_range("2024-01-01", periods=3))
        self.assertEqual(laatste_koersen(prijzen), {"AAA": 11.0})

    def test_leeg(self):
        self.assertEqual(laatste_koersen(pd.DataFrame()), {})


if __name__ == "__main__":
    unittest.main()
