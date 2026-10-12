"""Home-opbouw: geïnvesteerd = netto gestort + dividend + overige kassabewegingen − cash. Verzonnen data, offline."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db import TRANSACTIE_KOLOMMEN
from dividend import ORDER_ID_KOLOM_REKENING, bereken_kassaldo, bouw_rekening_regels
from portfolio_calc import compute_value_over_time
from statistieken import bereken_overige_kassabewegingen, bereken_statistieken

DIVIDEND = ("2024-06-01", "09:00", "Dividend", "EUR", 2.19)
STORTING_RNA = ("2024-01-02", "09:00", "iDEAL storting", "EUR", 13440.0)
KOOP_RNA = ("2024-01-03", "09:00", "Koop 10 @ 1.343,871 EUR", "EUR", -13438.71)
STORTING_RUIM = ("2024-01-02", "09:00", "iDEAL storting", "EUR", 13500.0)
AANSLUITING = [("2024-02-03", "09:00", f"DEGIRO Aansluitingskosten 2024 (Xetra - XET{i})", "EUR", -2.5) for i in range(9)]


def _rekening_df(regels):
    return pd.DataFrame([{
        "Datum": pd.Timestamp(d), "Tijd": t, "Valutadatum": pd.Timestamp(d), "Product": None, "ISIN": None,
        "Omschrijving": o, "FX": None, "valuta_mutatie": v, "mutatie": m, "valuta_saldo": "EUR", "saldo": None,
        ORDER_ID_KOLOM_REKENING: None,
    } for d, t, o, v, m in regels])


def _som(regels, geinvesteerd):
    """(overig, geïnvesteerd − (netto gestort + dividend + overig − cash))"""
    df = _rekening_df(regels)
    kassaldo = bereken_kassaldo(df)
    overig = bereken_overige_kassabewegingen(bouw_rekening_regels(df), kassaldo, geinvesteerd, geinvesteerd)
    dividend = sum(m for _, _, o, _, m in regels if o == "Dividend")
    verschil = geinvesteerd - (kassaldo["netto_gestort_eur"] + dividend + overig - kassaldo["saldo_eur"])
    return overig, round(verschil, 2)


class TestOpbouwSluit(unittest.TestCase):
    def test_rna_patroon_overig_nul(self):
        self.assertEqual(_som([STORTING_RNA, KOOP_RNA, DIVIDEND], 13438.71), (0.0, 0.0))

    def test_aansluitingskosten_en_rente(self):
        rente = ("2024-04-01", "09:00", "Flatex Interest Income", "EUR", 0.4)
        overig, verschil = _som([STORTING_RUIM, KOOP_RNA, DIVIDEND, rente] + AANSLUITING, 13438.71)
        self.assertEqual((overig, verschil), (-22.1, 0.0))

    def test_alleen_aansluitingskosten(self):
        self.assertEqual(_som([STORTING_RUIM, KOOP_RNA] + AANSLUITING, 13438.71), (-22.5, 0.0))

    def test_promotie_zit_al_in_de_kosten(self):
        kosten = ("2024-01-03", "09:00", "DEGIRO Transactiekosten en/of kosten van derden", "EUR", -2.0)
        promo = ("2024-01-04", "09:00", "DEGIRO Verrekening Promotie", "EUR", 2.0)
        koop = ("2024-01-03", "09:00", "Koop 10 @ 100 EUR", "EUR", -1000.0)
        self.assertEqual(_som([("2024-01-02", "09:00", "iDEAL storting", "EUR", 1500.0), koop, kosten, promo], 1000.0),
                         (0.0, 0.0))

    def test_verrekening_aandelen_en_onbekende_regel_tellen_mee(self):
        regels = [STORTING_RNA, KOOP_RNA, ("2024-05-01", "09:00", "Verrekening van Aandelen", "EUR", 1.0),
                  ("2024-05-02", "09:00", "Iets nieuws", "EUR", -0.5)]
        self.assertEqual(_som(regels, 13438.71), (0.5, 0.0))

    def test_vreemde_valuta_telt_niet(self):
        regels = [STORTING_RNA, KOOP_RNA, ("2024-05-01", "09:00", "Flatex Interest Income", "USD", 5.0)]
        self.assertEqual(_som(regels, 13438.71), (0.0, 0.0))


class TestVeldOntbreekt(unittest.TestCase):
    def _regels_en_kassaldo(self, regels):
        df = _rekening_df(regels)
        return bouw_rekening_regels(df), bereken_kassaldo(df)

    def test_zonder_rekeningoverzicht(self):
        self.assertIsNone(bereken_overige_kassabewegingen(None, None, 100.0, 100.0))
        self.assertIsNone(bereken_overige_kassabewegingen([], None, 100.0, 100.0))

    def test_onbetrouwbaar_negatief_saldo(self):
        regels, kassaldo = self._regels_en_kassaldo([STORTING_RNA, ("2024-01-03", "09:00", "Koop 1 @ 20000 EUR", "EUR", -20000.0)])
        self.assertIsNone(bereken_overige_kassabewegingen(regels, kassaldo, 20000.0, 20000.0))

    def test_onbetrouwbaar_geen_stortingen(self):
        regels, kassaldo = self._regels_en_kassaldo([KOOP_RNA[:4] + (5.0,)])
        self.assertIsNone(bereken_overige_kassabewegingen(regels, kassaldo, 100.0, 100.0))

    def test_overzicht_niet_vanaf_opening(self):
        regels, kassaldo = self._regels_en_kassaldo([STORTING_RNA, KOOP_RNA])
        kassaldo["vanaf_opening"] = False
        self.assertIsNone(bereken_overige_kassabewegingen(regels, kassaldo, 13438.71, 13438.71))


class TestInStatistieken(unittest.TestCase):
    def _stats(self, rekening_regels, kassaldo):
        def rij(datum, aantal, koers):
            waarde = -aantal * koers
            return {"datum": pd.Timestamp(datum), "product": "AAA", "isin": "XX0000000001", "beurs": "XET",
                    "ticker": "AAA", "aantal": aantal, "koers": koers, "totaal_eur": waarde, "echte_naam": "AAA",
                    "transactiekosten": 0.0, "waarde_eur": waarde, "tijd": "10:00", "wisselkoers": None,
                    "autofx_kosten": None}
        transacties = pd.DataFrame([rij("2024-01-03", 10.0, 100.0)], columns=TRANSACTIE_KOLOMMEN)
        dagen = pd.bdate_range("2024-01-02", "2024-03-01")
        prijzen = pd.DataFrame({"AAA": [100.0] * len(dagen)}, index=dagen)
        resultaat = compute_value_over_time(transacties, prijzen)
        return bereken_statistieken(transacties, prijzen, resultaat, kassaldo=kassaldo,
                                    rekening_regels=rekening_regels)["totalen"]

    def test_veld_aanwezig_en_gelijk_voor_beide_routes(self):
        df = _rekening_df([("2024-01-02", "09:00", "iDEAL storting", "EUR", 1100.0),
                           ("2024-01-03", "09:00", "Koop 10 @ 100 EUR", "EUR", -1000.0)] + AANSLUITING[:2])
        kassaldo = bereken_kassaldo(df)
        niet_opslaan = bouw_rekening_regels(df)
        opgeslagen = [{k: r[k] for k in ("datum", "tijd", "omschrijving", "mutatie_valuta", "mutatie")}
                      for r in niet_opslaan]
        a, b = self._stats(niet_opslaan, kassaldo), self._stats(opgeslagen, kassaldo)
        self.assertEqual(a["overige_kassabewegingen_eur"], -5.0)
        self.assertEqual(a["overige_kassabewegingen_eur"], b["overige_kassabewegingen_eur"])
        self.assertEqual(a["geinvesteerd"], 1000.0)
        self.assertEqual(a["geinvesteerd"], a["netto_gestort_eur"] + a["overige_kassabewegingen_eur"] - a["kassaldo_eur"])

    def test_veld_is_none_zonder_rekeningoverzicht(self):
        self.assertIsNone(self._stats(None, None)["overige_kassabewegingen_eur"])


if __name__ == "__main__":
    unittest.main()
