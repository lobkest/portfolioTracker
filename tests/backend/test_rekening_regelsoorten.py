"""Regelsoorten van het rekeningoverzicht: wat er met elke soort gebeurt (kassaldo, stortingen, dividend, Diagnostiek).
Verzonnen rekeningoverzicht met elk bekend regeltype. Offline, geen database."""
import os
import sys
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from diagnostiek import INFO, LET_OP
from diagnostiek_checks import check_rekening_regelsoorten
import rekening_regelsoorten as rs
from dividend import bereken_kassaldo, bouw_rekening_regels, promotie_bedragen, verwerk_rekeningoverzicht_df
from rekening_regelsoorten import BEHANDELING, GEBRUIK_DIAGNOSTIEK, behandeling, regelsoort

SWEEP = "Degiro Cash Sweep Transfer"
OVERBOEKING_VAN = "Overboeking van uw geldrekening bij flatexDEGIRO Bank 400 EUR"
OVERBOEKING_NAAR = "Overboeking naar uw geldrekening bij flatexDEGIRO Bank: 7 EUR"
ONBEKEND = "Onbekende regel"

# (datum, tijd, omschrijving, valuta, mutatie, isin, order_id), oudste eerst.
REGELS = [
    ("2024-01-02", "09:00", "Reservation iDEAL", "EUR", 500.0, None, None),
    ("2024-01-03", "09:00", "iDEAL Deposit", "EUR", 500.0, None, None),
    ("2024-01-03", "09:00", "Reservation iDEAL", "EUR", -500.0, None, None),
    ("2024-01-03", "10:00", SWEEP, "EUR", 400.0, None, None),
    ("2024-01-03", "10:00", OVERBOEKING_VAN, None, None, None, None),
    ("2024-01-03", "11:00", "iDEAL storting", "EUR", 100.0, None, None),
    ("2024-01-05", "10:00", "Koop 2 @ 50 EUR", "EUR", -100.0, "NL0000000001", "O1"),
    ("2024-01-05", "10:00", "DEGIRO Transactiekosten en/of kosten van derden", "EUR", -2.0, "NL0000000001", "O1"),
    ("2024-01-05", "15:00", "Koop 1 @ 100 USD", "USD", -100.0, "US0000000001", "O2"),
    ("2024-01-05", "15:00", "Valuta Creditering", "USD", 100.0, None, None),
    ("2024-01-05", "15:00", "Valuta Debitering", "EUR", -92.0, None, None),
    ("2024-01-05", "15:00", "DEGIRO Transactiekosten en/of kosten van derden", "EUR", -1.0, "US0000000001", "O2"),
    ("2024-02-01", "08:00", "Dividend", "USD", 10.0, "US0000000001", None),
    ("2024-02-01", "08:00", "Dividendbelasting", "USD", -1.5, "US0000000001", None),
    ("2024-02-02", "07:00", "Valuta Debitering", "USD", -8.5, None, None),
    ("2024-02-02", "07:00", "Valuta Creditering", "EUR", 7.8, None, None),
    ("2024-03-04", "08:00", "Dividend", "USD", 4.0, "US0000000002", None),
    ("2024-03-04", "08:00", "Dividend", "USD", -4.0, "US0000000002", None),
    ("2024-03-04", "08:00", "Dividend", "USD", 4.4, "US0000000002", None),
    ("2024-03-04", "08:00", "Dividendbelasting", "USD", -0.66, "US0000000002", None),
    ("2024-03-05", "07:00", "Valuta Debitering", "USD", -3.74, None, None),
    ("2024-03-05", "07:00", "Valuta Creditering", "EUR", 3.4, None, None),
    ("2024-04-01", "08:00", "Dividend", "EUR", 5.0, "NL0000000002", None),
    ("2024-04-01", "08:00", "Dividendbelasting", "EUR", -0.75, "NL0000000002", None),
    ("2024-04-06", "10:00", "Flatex Interest Income", "EUR", 0.37, None, None),
    ("2024-04-10", "09:00", "DEGIRO Verrekening Promotie", "EUR", 7.0, None, None),
    ("2024-04-10", "12:00", OVERBOEKING_NAAR, None, None, None, None),
    ("2024-04-10", "12:00", SWEEP, "EUR", -7.0, None, None),
    ("2024-04-11", "09:00", "DEGIRO Aansluitingskosten 2024 (Xetra - XET)", "EUR", -2.5, None, None),
    ("2024-04-12", "09:00", "CLAIMEMISSIE: Koop 2 @ 1,1 EUR", "EUR", -2.2, "NL0000000003", None),
    ("2024-04-12", "09:00", "PRODUCTWIJZIGING : Verkoop 5 @ 10 EUR", "EUR", 50.0, "NL0000000004", None),
    ("2024-04-12", "09:00", "PRODUCTWIJZIGING : Koop 5 @ 10 EUR", "EUR", -50.0, "NL0000000005", None),
    ("2024-04-13", "09:00", "Verrekening van Aandelen", "EUR", 3.1, "NL0000000004", None),
    ("2024-04-15", "10:00", "Koop 3 stuks", "EUR", -30.0, "NL0000000006", "O3"),
    ("2024-04-16", "10:00", "Verkoop 1 stuks", "EUR", 12.0, "NL0000000006", "O4"),
    ("2024-04-17", "10:00", "Conversie geldmarktfonds: Koop 0,000001 @ 9.899,3674 EUR", None, None, "LU0000000001", None),
    ("2024-04-18", "10:00", "Conversie geldmarktfonds: Verkoop 0,000001 @ 9.844,0842 EUR", None, None, "LU0000000001", None),
    ("2024-04-20", "10:00", "flatex Storting", "EUR", 200.0, None, None),
    ("2024-04-21", "10:00", "Processed Flatex Withdrawal", "EUR", -50.0, None, None),
    ("2024-04-22", "10:00", "Processed Flatex Withdrawal", "EUR", 20.0, None, None),
    ("2024-04-22", "10:00", "flatex terugstorting", "EUR", -20.0, None, None),
    ("2024-04-23", "10:00", ONBEKEND, "EUR", 1.0, None, None),
    ("2024-04-24", "10:00", "Reservation iDEAL", "EUR", 75.0, None, None),
]


def _overzicht(regels=REGELS, beginsaldo=0.0):
    """Nieuwste regel bovenaan, zoals lees_rekeningoverzicht(). EUR-saldo loopt mee; sweeps krijgen een onzinnig
    tussensaldo, zoals in echte exports."""
    rijen = []
    saldo = beginsaldo
    for datum, tijd, tekst, valuta, mutatie, isin, order_id in regels:
        if valuta == "EUR" and tekst != SWEEP:
            saldo += mutatie
            rij_saldo = round(saldo, 2)
        else:
            rij_saldo = -999.0
        rijen.append({"Datum": pd.Timestamp(datum), "Tijd": tijd, "Valutadatum": pd.Timestamp(datum),
                      "Product": None, "ISIN": isin, "Omschrijving": tekst, "FX": None, "valuta_mutatie": valuta,
                      "mutatie": mutatie, "valuta_saldo": valuta, "saldo": rij_saldo, "Order Id": order_id})
    return pd.DataFrame(rijen[::-1]).reset_index(drop=True)


def _zonder(*teksten):
    return [r for r in REGELS if r[2] not in teksten]


class TestKassaldoKarakterisering(unittest.TestCase):
    def test_saldo_en_netto_gestort_vanaf_opening(self):
        kassaldo = bereken_kassaldo(_overzicht())
        self.assertEqual(kassaldo["saldo_eur"], 634.22)
        # 500 + 500 - 500 + 100 + 200 - 50 + 20 - 20 + 75
        self.assertEqual(kassaldo["netto_gestort_eur"], 825.0)
        self.assertTrue(kassaldo["vanaf_opening"])
        self.assertEqual((str(kassaldo["eerste_datum"]), str(kassaldo["per_datum"])), ("2024-01-02", "2024-04-24"))

    def test_met_beginsaldo(self):
        kassaldo = bereken_kassaldo(_overzicht(beginsaldo=50.0))
        self.assertEqual(kassaldo["saldo_eur"], 684.22)
        self.assertEqual(kassaldo["netto_gestort_eur"], 825.0)
        self.assertFalse(kassaldo["vanaf_opening"])


class TestDividendKarakterisering(unittest.TestCase):
    def test_uitkeringen(self):
        records = {r["isin"]: r for r in verwerk_rekeningoverzicht_df(_overzicht())}
        self.assertEqual(set(records), {"US0000000001", "US0000000002", "NL0000000002"})
        usd = records["US0000000001"]
        self.assertEqual((usd["valuta"], usd["netto_eur"], usd["herinvesteerd"]), ("USD", 7.8, False))
        self.assertAlmostEqual(usd["bruto_eur"], 7.8 * 10 / 8.5)
        self.assertAlmostEqual(usd["belasting_eur"], -7.8 * 1.5 / 8.5)
        storno = records["US0000000002"]
        self.assertEqual(storno["netto_eur"], 3.4)
        self.assertAlmostEqual(storno["bruto_eur"], 4.0)
        self.assertAlmostEqual(storno["belasting_eur"], -0.6)
        eur = records["NL0000000002"]
        self.assertEqual((eur["valuta"], eur["bruto_eur"], eur["belasting_eur"], eur["netto_eur"]),
                         ("EUR", 5.0, -0.75, 4.25))


class TestDiagnostiekKarakterisering(unittest.TestCase):
    def test_verrekening_en_onbekende_soorten(self):
        verrekening, onbekend = check_rekening_regelsoorten(_overzicht())
        self.assertEqual((verrekening["niveau"], verrekening["sleutel"]), (LET_OP, "rekening:verrekening_aandelen"))
        self.assertEqual((onbekend["niveau"], onbekend["sleutel"]), (INFO, "rekening:onbekende_soorten"))
        # Voor de regelsoortentabel stonden hier ook "Koop/Verkoop # stuks" en de conversie geldmarktfonds.
        self.assertEqual([rij[0] for rij in onbekend["tabel"]["rijen"]], [ONBEKEND])


class TestRegelsoortTabel(unittest.TestCase):
    def test_elke_soort_heeft_een_behandeling(self):
        soorten = {waarde for naam, waarde in vars(rs).items() if naam.startswith("REGELSOORT_")}
        self.assertEqual(soorten, set(BEHANDELING))
        for regel in REGELS:
            self.assertIn(regelsoort(regel[2]), BEHANDELING, regel[2])
        self.assertTrue(all(b.reden for b in BEHANDELING.values()))

    def test_nieuw_herkende_soorten(self):
        self.assertEqual(regelsoort("Koop 20 stuks"), rs.REGELSOORT_KOOP_VERKOOP)
        self.assertEqual(regelsoort("Verkoop 1.250 stuks"), rs.REGELSOORT_KOOP_VERKOOP)
        self.assertEqual(regelsoort("Conversie geldmarktfonds: Koop 0,000001 @ 9.899,3674 EUR"),
                         rs.REGELSOORT_CONVERSIE_GELDMARKTFONDS)
        self.assertEqual(regelsoort("Reservation iDEAL"), rs.REGELSOORT_RESERVERING)
        self.assertEqual(regelsoort("DEGIRO Aansluitingskosten 2024 (Xetra - XET)"), rs.REGELSOORT_AANSLUITINGSKOSTEN)
        self.assertEqual(regelsoort("DEGIRO Transactiekosten en/of kosten van derden"), rs.REGELSOORT_KOSTEN)
        self.assertIsNone(regelsoort("Koop 20 stuks extra"))

    def test_onbekend_is_bewuste_terugval(self):
        self.assertEqual(behandeling(ONBEKEND), BEHANDELING[rs.REGELSOORT_ONBEKEND])
        self.assertEqual((behandeling(ONBEKEND).kassaldo, behandeling(ONBEKEND).storting), (True, False))
        self.assertEqual(behandeling(ONBEKEND).gebruik, GEBRUIK_DIAGNOSTIEK)


def _effect(*teksten, beginsaldo=0.0):
    """(verschil saldo, verschil netto gestort) door de regels met deze omschrijvingen."""
    met = bereken_kassaldo(_overzicht(beginsaldo=beginsaldo))
    zonder = bereken_kassaldo(_overzicht(_zonder(*teksten), beginsaldo=beginsaldo))
    return (round(met["saldo_eur"] - zonder["saldo_eur"], 2),
            round(met["netto_gestort_eur"] - zonder["netto_gestort_eur"], 2))


class TestBehandelingPerSoort(unittest.TestCase):
    def test_sweeps_en_overboekingen_genegeerd_met_beginsaldo(self):
        self.assertEqual(_effect(SWEEP, OVERBOEKING_VAN, OVERBOEKING_NAAR, beginsaldo=50.0), (0.0, 0.0))
        self.assertFalse(bereken_kassaldo(_overzicht(beginsaldo=50.0))["vanaf_opening"])

    def test_overboeking_op_soort_genegeerd_ook_met_eur_bedrag(self):
        regels = REGELS + [("2024-04-25", "10:00", OVERBOEKING_VAN, "EUR", 400.0, None, None)]
        self.assertEqual(bereken_kassaldo(_overzicht(regels))["saldo_eur"], 634.22)

    def test_reservering_gepaard_netto_nul(self):
        kassaldo = bereken_kassaldo(_overzicht(REGELS[:3]))
        self.assertEqual((kassaldo["saldo_eur"], kassaldo["netto_gestort_eur"]), (500.0, 500.0))

    def test_reservering_los_telt_als_storting(self):
        kassaldo = bereken_kassaldo(_overzicht([("2024-04-24", "10:00", "Reservation iDEAL", "EUR", 75.0, None, None)]))
        self.assertEqual((kassaldo["saldo_eur"], kassaldo["netto_gestort_eur"]), (75.0, 75.0))

    def test_stortingen_en_opnames(self):
        self.assertEqual(_effect("iDEAL storting"), (100.0, 100.0))
        self.assertEqual(_effect("flatex Storting"), (200.0, 200.0))
        self.assertEqual(_effect("Processed Flatex Withdrawal", "flatex terugstorting"), (-50.0, -50.0))

    def test_alleen_kassaldo(self):
        for tekst, bedrag in [("Flatex Interest Income", 0.37), ("DEGIRO Aansluitingskosten 2024 (Xetra - XET)", -2.5),
                              ("CLAIMEMISSIE: Koop 2 @ 1,1 EUR", -2.2), ("Verrekening van Aandelen", 3.1),
                              ("Koop 3 stuks", -30.0), ("Verkoop 1 stuks", 12.0), (ONBEKEND, 1.0),
                              ("DEGIRO Transactiekosten en/of kosten van derden", -3.0)]:
            self.assertEqual(_effect(tekst), (bedrag, 0.0), tekst)

    def test_vreemde_valuta_en_conversie_geldmarktfonds_niet_in_kassaldo(self):
        self.assertEqual(_effect("Koop 1 @ 100 USD"), (0.0, 0.0))
        self.assertEqual(_effect("Conversie geldmarktfonds: Koop 0,000001 @ 9.899,3674 EUR",
                                 "Conversie geldmarktfonds: Verkoop 0,000001 @ 9.844,0842 EUR"), (0.0, 0.0))

    def test_promotie_kassaldo_en_kostenkorting_niet_als_storting(self):
        self.assertEqual(_effect("DEGIRO Verrekening Promotie"), (7.0, 0.0))
        self.assertEqual(promotie_bedragen(bouw_rekening_regels(_overzicht())), [(date(2024, 4, 10), 7.0)])

    def test_rente_en_aansluitingskosten_niet_in_dividend_of_kostenkorting(self):
        records = verwerk_rekeningoverzicht_df(_overzicht())
        self.assertEqual(len(records), 3)
        self.assertEqual(len(promotie_bedragen(bouw_rekening_regels(_overzicht()))), 1)


if __name__ == "__main__":
    unittest.main()
