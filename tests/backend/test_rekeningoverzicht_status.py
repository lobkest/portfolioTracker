"""beoordeel_rekeningoverzicht(): volledig of onvolledig rekeningoverzicht. Verzonnen data, offline."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dividend import ORDER_ID_KOLOM_REKENING, bereken_kassaldo, bouw_rekening_regels
from rekeningoverzicht_status import (
    REDEN_BEGINSALDO, REDEN_BEGINT_TE_LAAT, REDEN_GESTORT_NUL, REDEN_GESTORT_TE_LAAG, REDEN_SALDO_NEGATIEF,
    beoordeel_rekeningoverzicht,
)

D = datetime.date


def _transacties(*rijen):
    return pd.DataFrame([{"datum": pd.Timestamp(d), "beurs": b, "product": "X"} for d, b in rijen])


def _kassaldo(eerste=D(2024, 1, 2), saldo=1.29, gestort=13440.0, vanaf_opening=True):
    return {"saldo_eur": saldo, "netto_gestort_eur": gestort, "eerste_datum": eerste,
            "per_datum": D(2024, 6, 1), "vanaf_opening": vanaf_opening}


def _regels(eerste=D(2024, 1, 2)):
    return [{"datum": eerste + datetime.timedelta(days=5), "mutatie": 1.0}, {"datum": eerste, "mutatie": 5.0}]


def _beoordeel(transacties=None, regels=None, kassaldo=None, geinvesteerd=13438.71, dividend=0.0, overig=0.0):
    transacties = transacties if transacties is not None else _transacties(("2024-01-03", "EAM"))
    return beoordeel_rekeningoverzicht(
        transacties, regels if regels is not None else _regels(), kassaldo or _kassaldo(),
        geinvesteerd, geinvesteerd, dividend, overig)


class TestBeoordeelRekeningoverzicht(unittest.TestCase):
    def test_zonder_rekeningoverzicht_geen_veld(self):
        self.assertIsNone(beoordeel_rekeningoverzicht(_transacties(("2024-01-03", "EAM")), [], None, 100, 100, None, None))

    def test_volledig_overzicht(self):
        status = _beoordeel()
        self.assertTrue(status["volledig"])
        self.assertEqual(status["redenen"], [])
        self.assertEqual(status["eerste_regel_datum"], "2024-01-02")
        self.assertEqual(status["eerste_transactie_datum"], "2024-01-03")

    def test_overzicht_van_een_week_bij_oudere_transacties(self):
        status = _beoordeel(
            transacties=_transacties(("2021-01-27", "EAM"), ("2026-10-01", "EAM")),
            regels=_regels(eerste=D(2026, 10, 1)), kassaldo=_kassaldo(eerste=D(2026, 10, 1), gestort=0.0, saldo=-3389.75))
        self.assertFalse(status["volledig"])
        self.assertEqual(status["redenen"], [REDEN_BEGINT_TE_LAAT, REDEN_GESTORT_NUL, REDEN_SALDO_NEGATIEF])
        self.assertIn("01-10-2026", status["redenen_tekst"][0])
        self.assertIn("27-01-2021", status["redenen_tekst"][0])

    def test_marge_van_enkele_dagen(self):
        transacties = _transacties(("2024-01-03", "EAM"))
        self.assertTrue(_beoordeel(transacties=transacties, regels=_regels(eerste=D(2024, 1, 6)))["volledig"])
        status = _beoordeel(transacties=transacties, regels=_regels(eerste=D(2024, 1, 7)))
        self.assertEqual(status["redenen"], [REDEN_BEGINT_TE_LAAT])

    def test_corporate_action_telt_niet_als_eerste_transactie(self):
        transacties = _transacties(("2020-01-01", "DEG"), ("2024-01-03", "EAM"))
        status = _beoordeel(transacties=transacties)
        self.assertTrue(status["volledig"])
        self.assertEqual(status["eerste_transactie_datum"], "2024-01-03")

    def test_beginsaldo_niet_nul(self):
        status = _beoordeel(kassaldo=_kassaldo(vanaf_opening=False))
        self.assertEqual(status["redenen"], [REDEN_BEGINSALDO])

    def test_netto_gestort_nul_met_transacties(self):
        status = _beoordeel(kassaldo=_kassaldo(gestort=0.0))
        self.assertEqual(status["redenen"], [REDEN_GESTORT_NUL])

    def test_negatief_kassaldo(self):
        status = _beoordeel(kassaldo=_kassaldo(saldo=-0.01))
        self.assertEqual(status["redenen"], [REDEN_SALDO_NEGATIEF])

    def test_gestort_kleiner_dan_geinvesteerd(self):
        status = _beoordeel(kassaldo=_kassaldo(gestort=10000.0, saldo=0.0))
        self.assertEqual(status["redenen"], [REDEN_GESTORT_TE_LAAG])

    def test_dividend_en_overig_tellen_mee_in_de_dekking(self):
        kassaldo = _kassaldo(gestort=13400.0, saldo=3.0)
        self.assertEqual(_beoordeel(kassaldo=kassaldo)["redenen"], [REDEN_GESTORT_TE_LAAG])
        self.assertTrue(_beoordeel(kassaldo=kassaldo, dividend=45.0, overig=-5.0)["volledig"])

    def test_kleine_afwijking_binnen_marge(self):
        self.assertTrue(_beoordeel(kassaldo=_kassaldo(gestort=13438.0, saldo=0.0))["volledig"])

    def test_zonder_transacties_geen_gestort_redenen(self):
        status = _beoordeel(kassaldo=_kassaldo(gestort=0.0), geinvesteerd=0.0)
        self.assertTrue(status["volledig"])


class TestOpslaanGelijkAanNietOpslaan(unittest.TestCase):
    def test_zelfde_uitkomst_voor_dicts_uit_db_en_uit_het_bestand(self):
        regels = [("2024-01-02", "iDEAL storting", 13440.0), ("2024-01-03", "Koop 10 @ 1.343,871 EUR", -13438.71)]
        df = pd.DataFrame([{
            "Datum": pd.Timestamp(d), "Tijd": "09:00", "Valutadatum": pd.Timestamp(d), "Product": None, "ISIN": None,
            "Omschrijving": o, "FX": None, "valuta_mutatie": "EUR", "mutatie": m, "valuta_saldo": "EUR", "saldo": None,
            ORDER_ID_KOLOM_REKENING: None,
        } for d, o, m in regels])
        kassaldo = bereken_kassaldo(df)
        uit_bestand = bouw_rekening_regels(df)
        uit_db = [{"datum": r["datum"], "tijd": r["tijd"], "omschrijving": r["omschrijving"],
                   "mutatie_valuta": r["mutatie_valuta"], "mutatie": r["mutatie"]} for r in uit_bestand]
        transacties = _transacties(("2024-01-03", "EAM"))
        args = (13438.71, 13438.71, None, 0.0)
        a = beoordeel_rekeningoverzicht(transacties, uit_bestand, kassaldo, *args)
        b = beoordeel_rekeningoverzicht(transacties, uit_db, kassaldo, *args)
        self.assertEqual(a, b)
        self.assertTrue(a["volledig"])


if __name__ == "__main__":
    unittest.main()
