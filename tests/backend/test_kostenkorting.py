"""Promotie van DeGiro als korting op transactiekosten: toewijzing, doorrekening en regelsoort. Zonder database."""
import os
import sys
import unittest
from datetime import date
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dividend import REGELSOORT_PROMOTIE, OMSCHRIJVINGEN_PROMOTIE, bereken_kassaldo, regelsoort
from portfolio_calc import compute_value_over_time
from statistieken import bereken_holdings_en_gesloten, bereken_totale_transactiekosten, bereken_totaal_rendement
from transactie_utils import (
    KOSTENKORTING_KOLOM, pas_kostenkorting_toe, transacties_overzicht_uit_df, verdeel_kostenkorting,
)


def _rij(datum, tijd, kosten, aantal=1.0, koers=100.0, ticker="TST.AS"):
    waarde = -aantal * koers
    return {"datum": pd.Timestamp(datum), "tijd": tijd, "ticker": ticker, "product": "TEST", "echte_naam": "TEST",
            "isin": "XX0000000001", "beurs": "EAM", "wisselkoers": None, "aantal": aantal, "adj_aantal": aantal,
            "koers": koers, "waarde_eur": waarde, "transactiekosten": kosten, "totaal_eur": waarde + kosten}


def _vijf_plus_een():
    """Kosten 1, 1, 1, 1, 3 vóór de promodatum en nog een transactie erna."""
    return pd.DataFrame([
        _rij("2024-01-10", "10:00", -1.0),
        _rij("2024-02-10", "11:00", -1.0),
        _rij("2024-03-10", "12:00", -1.0),
        _rij("2024-03-20", "09:00", -1.0),
        _rij("2024-03-20", "09:05", -3.0),
        _rij("2024-05-01", "10:00", -3.0),
    ])


PROMO = [(date(2024, 4, 10), 7.0)]


class TestVerdeelKostenkorting(unittest.TestCase):
    def test_promo_past_exact_op_de_eerste_vijf(self):
        korting, restanten = verdeel_kostenkorting(_vijf_plus_een(), PROMO)
        self.assertEqual(korting.tolist(), [1.0, 1.0, 1.0, 1.0, 3.0, 0.0])
        self.assertEqual(restanten, [])

    def test_promo_valt_midden_in_een_transactie(self):
        korting, restanten = verdeel_kostenkorting(_vijf_plus_een(), [(date(2024, 4, 10), 5.5)])
        self.assertEqual(korting.tolist(), [1.0, 1.0, 1.0, 1.0, 1.5, 0.0])
        self.assertEqual(restanten, [])

    def test_restant_bij_te_weinig_kosten(self):
        korting, restanten = verdeel_kostenkorting(_vijf_plus_een(), [(date(2024, 4, 10), 10.0)])
        self.assertAlmostEqual(korting.sum(), 7.0)
        self.assertEqual(restanten, [(date(2024, 4, 10), 3.0)])

    def test_transacties_na_de_promodatum_krijgen_niets(self):
        korting, restanten = verdeel_kostenkorting(_vijf_plus_een(), [(date(2024, 2, 10), 5.0)])
        self.assertEqual(korting.tolist(), [1.0, 1.0, 0.0, 0.0, 0.0, 0.0])
        self.assertEqual(restanten, [(date(2024, 2, 10), 3.0)])

    def test_zelfde_dag_volgorde_op_tijd(self):
        df = pd.DataFrame([_rij("2024-03-01", "14:00", -2.0), _rij("2024-03-01", "09:00", -2.0)])
        korting, _ = verdeel_kostenkorting(df, [(date(2024, 3, 5), 3.0)])
        self.assertEqual(korting.tolist(), [1.0, 2.0])

    def test_meerdere_promoregels_op_datum(self):
        df = _vijf_plus_een()
        # Opzettelijk niet op datum aangeleverd; de eerste promo heeft alleen de eerste transactie beschikbaar.
        korting, restanten = verdeel_kostenkorting(df, [(date(2024, 4, 1), 2.0), (date(2024, 1, 15), 1.5)])
        self.assertEqual(korting.tolist(), [1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
        self.assertEqual(restanten, [(date(2024, 1, 15), 0.5)])

    def test_rijen_zonder_kosten_worden_overgeslagen(self):
        df = pd.DataFrame([_rij("2024-01-01", "10:00", float("nan")), _rij("2024-01-02", "10:00", 0.0),
                           _rij("2024-01-03", "10:00", -2.0)])
        korting, _ = verdeel_kostenkorting(df, [(date(2024, 2, 1), 1.0)])
        self.assertEqual(korting.tolist(), [0.0, 0.0, 1.0])

    def test_decimal_uit_de_database(self):
        df = _vijf_plus_een()
        df["transactiekosten"] = [Decimal(str(k)) for k in df["transactiekosten"]]
        korting, _ = verdeel_kostenkorting(df, PROMO)
        self.assertAlmostEqual(korting.sum(), 7.0)

    def test_zonder_promoties_geen_korting(self):
        korting, restanten = verdeel_kostenkorting(_vijf_plus_een(), [])
        self.assertTrue((korting == 0).all())
        self.assertEqual(restanten, [])


class TestPasKostenkortingToe(unittest.TestCase):
    def setUp(self):
        self.df = _vijf_plus_een()
        korting, _ = verdeel_kostenkorting(self.df, PROMO)
        self.met_korting = pas_kostenkorting_toe(self.df, korting)

    def test_kosten_en_totaal_omlaag_waarde_eur_gelijk(self):
        self.assertEqual(self.met_korting["transactiekosten"].tolist(), [0.0, 0.0, 0.0, 0.0, 0.0, -3.0])
        self.assertEqual(self.met_korting["totaal_eur"].tolist(), [-100.0] * 5 + [-103.0])
        self.assertEqual(self.met_korting["waarde_eur"].tolist(), self.df["waarde_eur"].tolist())

    def test_origineel_blijft_ongewijzigd(self):
        self.assertEqual(self.df["transactiekosten"].tolist(), [-1.0, -1.0, -1.0, -1.0, -3.0, -3.0])
        self.assertNotIn(KOSTENKORTING_KOLOM, self.df.columns)

    def test_totale_kosten_betaald_en_netto(self):
        r = bereken_totale_transactiekosten(self.met_korting)
        self.assertEqual((r["totaal"], r["totaal_betaald"], r["korting"]), (3.0, 10.0, 7.0))

    def test_geinvesteerd_en_rendement(self):
        koersen = pd.DataFrame({"TST.AS": [110.0]}, index=[pd.Timestamp("2024-06-03")])
        voor = compute_value_over_time(self.df, koersen).iloc[-1]
        na = compute_value_over_time(self.met_korting, koersen).iloc[-1]
        self.assertAlmostEqual(voor["geinvesteerd"], 610.0)
        self.assertAlmostEqual(na["geinvesteerd"], 603.0)
        self.assertAlmostEqual(na["waarde"], voor["waarde"])
        self.assertAlmostEqual(bereken_totaal_rendement(na["geinvesteerd"], na["waarde"])["rendement_eur"], 57.0)

    def test_gak_ongewijzigd(self):
        voor, _ = bereken_holdings_en_gesloten(self.df)
        na, _ = bereken_holdings_en_gesloten(self.met_korting)
        self.assertAlmostEqual(na["TST.AS"]["gak"], voor["TST.AS"]["gak"])
        self.assertAlmostEqual(na["TST.AS"]["gak"], 100.0)

    def test_zonder_promotie_identiek(self):
        df = _vijf_plus_een()
        korting, _ = verdeel_kostenkorting(df, [])
        zonder = pas_kostenkorting_toe(df, korting)
        pd.testing.assert_frame_equal(zonder.drop(columns=KOSTENKORTING_KOLOM), df)
        self.assertEqual(bereken_totale_transactiekosten(zonder), {**bereken_totale_transactiekosten(df), "korting": 0.0})


class TestOverzichtMetKorting(unittest.TestCase):
    def test_kosten_zoals_betaald_en_korting_apart(self):
        df = _vijf_plus_een()
        korting, _ = verdeel_kostenkorting(df, PROMO)
        lijst = transacties_overzicht_uit_df(pas_kostenkorting_toe(df, korting))
        laatste_promorij = next(r for r in lijst if r["datum"] == "2024-03-20" and r["tijd"] == "09:05")
        self.assertEqual(laatste_promorij["transactiekosten"], -3.0)
        self.assertEqual(laatste_promorij["totaal_eur"], -103.0)
        self.assertEqual(laatste_promorij[KOSTENKORTING_KOLOM], 3.0)
        self.assertEqual(lijst[0][KOSTENKORTING_KOLOM], 0.0)

    def test_zonder_kortingkolom_nul(self):
        lijst = transacties_overzicht_uit_df(_vijf_plus_een())
        self.assertTrue(all(r[KOSTENKORTING_KOLOM] == 0.0 for r in lijst))


class TestPromotieRegel(unittest.TestCase):
    def test_regelsoort_herkent_promotie(self):
        for tekst in OMSCHRIJVINGEN_PROMOTIE:
            self.assertEqual(regelsoort(tekst), REGELSOORT_PROMOTIE)

    def test_kassaldo_telt_promo_mee_maar_niet_als_storting(self):
        # Nieuwste regel bovenaan, zoals in het DeGiro-bestand.
        df = pd.DataFrame([
            {"Datum": pd.Timestamp("2024-04-10"), "Omschrijving": "DEGIRO Verrekening Promotie", "valuta_mutatie": "EUR", "mutatie": 7.0, "saldo": 8.0},
            {"Datum": pd.Timestamp("2024-03-01"), "Omschrijving": "DEGIRO Transactiekosten en/of kosten van derden", "valuta_mutatie": "EUR", "mutatie": -1.0, "saldo": 1.0},
            {"Datum": pd.Timestamp("2024-03-01"), "Omschrijving": "Koop 1 @ 98 EUR", "valuta_mutatie": "EUR", "mutatie": -98.0, "saldo": 2.0},
            {"Datum": pd.Timestamp("2024-02-28"), "Omschrijving": "iDEAL Deposit", "valuta_mutatie": "EUR", "mutatie": 100.0, "saldo": 100.0},
        ])
        kassaldo = bereken_kassaldo(df)
        self.assertEqual(kassaldo["saldo_eur"], 8.0)
        self.assertEqual(kassaldo["netto_gestort_eur"], 100.0)


if __name__ == "__main__":
    unittest.main()
