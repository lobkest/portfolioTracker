"""Aansluitingskosten per beurs en jaar: parsen, samenvatting, beide routes gelijk, en niets in kosten of rendement.
Verzonnen data, offline: database en Yahoo zijn gemockt."""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
import portfolio_orchestratie as po
from aansluitingskosten import BEURS_ONBEKEND, bouw_aansluitingskosten_samenvatting, parse_aansluitingskosten
from db import TRANSACTIE_KOLOMMEN
from dividend import ORDER_ID_KOLOM_REKENING, bereken_kassaldo, bouw_rekening_regels
from rekening_regelsoorten import REGELSOORT_AANSLUITINGSKOSTEN, regelsoort

TEST_CODE = "test_aansluitingskosten"


def _tekst(jaar, beurs, code):
    return f"DEGIRO Aansluitingskosten {jaar} ({beurs} - {code})"


ONLEESBAAR = "DEGIRO Aansluitingskosten Voorbeeldbeurs"
# Twee beurzen, twee jaren; AAA staat alleen op EAM, dus ZZZ heeft kosten zonder positie.
KOSTEN_REGELS = [
    ("2024-02-03", "09:00", _tekst(2024, "Voorbeeldbeurs AG", "ZZZ"), "EUR", -2.5),
    ("2024-02-03", "09:00", _tekst(2024, "Xetra", "XET"), "EUR", -2.5),
    ("2025-02-03", "09:00", _tekst(2025, "Voorbeeldbeurs AG", "ZZZ"), "EUR", -2.5),
    ("2025-02-03", "09:00", _tekst(2025, "Xetra", "XET"), "EUR", -2.5),
]
OVERIGE_REGELS = [
    ("2024-01-02", "09:00", "iDEAL storting", "EUR", 1000.0),
    ("2024-03-01", "09:00", "DEGIRO Transactiekosten en/of kosten van derden", "EUR", -2.0),
]


def _rekening_df(regels):
    return pd.DataFrame([{
        "Datum": pd.Timestamp(d), "Tijd": t, "Valutadatum": pd.Timestamp(d), "Product": None, "ISIN": None,
        "Omschrijving": o, "FX": None, "valuta_mutatie": v, "mutatie": m, "valuta_saldo": "EUR", "saldo": None,
        ORDER_ID_KOLOM_REKENING: None,
    } for d, t, o, v, m in regels])


def _regels(regels):
    return bouw_rekening_regels(_rekening_df(regels))


def _transacties_df():
    def rij(datum, ticker, isin, aantal, koers, kosten):
        waarde = -aantal * koers
        return {"datum": pd.Timestamp(datum), "product": ticker, "isin": isin, "beurs": "XET", "ticker": ticker,
                "aantal": aantal, "koers": koers, "totaal_eur": waarde + kosten, "echte_naam": ticker,
                "transactiekosten": kosten, "waarde_eur": waarde, "tijd": "10:00", "wisselkoers": None,
                "autofx_kosten": None}
    return pd.DataFrame([
        rij("2024-01-02", "AAA", "XX0000000001", 10.0, 50.0, -2.0),
        rij("2024-03-01", "AAA", "XX0000000001", 2.0, 55.0, -2.0),
    ], columns=TRANSACTIE_KOLOMMEN)


def _price_data():
    dagen = pd.bdate_range("2024-01-02", "2025-06-02")
    return pd.DataFrame({"AAA": [50.0 + 0.01 * i for i in range(len(dagen))]}, index=dagen)


class TestParsen(unittest.TestCase):
    def test_jaar_naam_en_code(self):
        self.assertEqual(parse_aansluitingskosten("DEGIRO Aansluitingskosten 2026 (Voorbeeldbeurs AG - ZZZ)"),
                         (2026, "Voorbeeldbeurs AG", "ZZZ"))

    def test_naam_met_streepje_en_extra_spaties(self):
        self.assertEqual(parse_aansluitingskosten("DEGIRO Aansluitingskosten  2025 ( Beurs - Noord - nqq ) "),
                         (2025, "Beurs - Noord", "NQQ"))

    def test_onbekend_formaat(self):
        for tekst in (ONLEESBAAR, "DEGIRO Aansluitingskosten 2024", "DEGIRO Aansluitingskosten 2024 (Xetra)", None):
            self.assertIsNone(parse_aansluitingskosten(tekst), tekst)

    def test_onbekend_formaat_blijft_aansluitingskosten(self):
        self.assertEqual(regelsoort(ONLEESBAAR), REGELSOORT_AANSLUITINGSKOSTEN)


class TestSamenvatting(unittest.TestCase):
    def test_per_beurs_per_jaar_en_totaal(self):
        s = bouw_aansluitingskosten_samenvatting(_regels(KOSTEN_REGELS + OVERIGE_REGELS))
        self.assertEqual((s["totaal_eur"], s["aantal_regels"]), (10.0, 4))
        self.assertEqual(s["per_jaar"], [{"jaar": 2024, "bedrag_eur": 5.0}, {"jaar": 2025, "bedrag_eur": 5.0}])
        self.assertEqual([b["code"] for b in s["per_beurs"]], ["XET", "ZZZ"])
        xet = s["per_beurs"][0]
        self.assertEqual((xet["naam"], xet["totaal_eur"], xet["aantal_regels"]), ("Xetra", 5.0, 2))
        self.assertEqual(xet["per_jaar"], [{"jaar": 2024, "bedrag_eur": 2.5}, {"jaar": 2025, "bedrag_eur": 2.5}])
        self.assertEqual(s["onbekend_formaat"], [])

    def test_sleutel_gelijk_aan_verdeling_beurs(self):
        s = bouw_aansluitingskosten_samenvatting(_regels(KOSTEN_REGELS + [
            ("2024-02-03", "09:00", _tekst(2024, "Euronext Amsterdam", "EAM"), "EUR", -2.5)]))
        per_code = {b["code"]: b for b in s["per_beurs"]}
        self.assertEqual((per_code["XET"]["verdeling_naam"], per_code["XET"]["verdeling_naam_euronext"]),
                         ("Xetra", "Xetra"))
        self.assertEqual((per_code["EAM"]["verdeling_naam"], per_code["EAM"]["verdeling_naam_euronext"]),
                         ("Euronext Amsterdam", "Euronext"))
        # Onbekende code: zoals Verdeling, rauw.
        self.assertEqual(per_code["ZZZ"]["verdeling_naam"], "ZZZ")

    def test_onbekend_formaat_onder_beurs_onbekend(self):
        s = bouw_aansluitingskosten_samenvatting(_regels(
            KOSTEN_REGELS[:1] + [("2024-02-04", "09:00", ONLEESBAAR, "EUR", -2.5)]))
        self.assertEqual(s["totaal_eur"], 5.0)
        onbekend = s["per_beurs"][-1]
        self.assertEqual((onbekend["code"], onbekend["naam"], onbekend["verdeling_naam"]),
                         (BEURS_ONBEKEND, BEURS_ONBEKEND, None))
        self.assertEqual(onbekend["per_jaar"], [{"jaar": None, "bedrag_eur": 2.5}])
        self.assertEqual(s["onbekend_formaat"], [ONLEESBAAR])

    def test_geen_kostenregels_lege_samenvatting(self):
        s = bouw_aansluitingskosten_samenvatting(_regels(OVERIGE_REGELS))
        self.assertEqual((s["totaal_eur"], s["aantal_regels"], s["per_beurs"]), (0.0, 0, []))

    def test_kassaldo_telt_de_kosten_nog_steeds(self):
        met = bereken_kassaldo(_rekening_df(OVERIGE_REGELS + KOSTEN_REGELS))
        zonder = bereken_kassaldo(_rekening_df(OVERIGE_REGELS))
        self.assertEqual(round(zonder["saldo_eur"] - met["saldo_eur"], 2), 10.0)
        self.assertEqual(met["netto_gestort_eur"], zonder["netto_gestort_eur"])


class TestRoutes(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for naam, waarde in [
            ("get_prices", _price_data()),
            ("db_get_koers_splits", {}),
            ("db_get_laatste_koers_update", (None, None)),
            ("ticker_waarschuwingen_voor_transacties", ([], {})),
            ("analyze_transacties_verrijking", {}),
            ("db_get_ticker_details", {}),
            ("db_get_cached_openfigi_voor_isins", {}),
        ]:
            stack.enter_context(patch.object(po, naam, return_value=waarde))
        po.wis_portfolio_basis_cache(TEST_CODE)
        self.addCleanup(po.wis_portfolio_basis_cache, TEST_CODE)

    def opgeslagen(self, regels):
        df = _transacties_df()
        eur = [r for r in _regels(regels) if r["mutatie_valuta"] == "EUR" and r["mutatie"] is not None]
        with ExitStack() as stack:
            for naam, waarde in [
                ("db_get_portfolio_naam_en_transacties", ("Test", list(df.itertuples(index=False, name=None)))),
                ("db_get_rekening_regels_eur", eur),
                ("db_get_rekening_koop_verkoop", []),
                ("db_get_dividenden", []),
                ("db_get_isin_ticker_product", po.isin_ticker_product_rijen(df)),
                ("db_get_kassaldo", None),
                ("db_get_order_id_rijen", []),
            ]:
                stack.enter_context(patch.object(po, naam, return_value=waarde))
            return po.build_portfolio_response(TEST_CODE)

    def niet_opslaan(self, regels):
        with patch.object(db, "db_connect", side_effect=AssertionError("database bij 'niet opslaan'")), \
                patch.object(po, "db_get_rekening_regels_eur", side_effect=AssertionError("database")):
            return po.analyze_transacties(_transacties_df(), code=None, naam="Test",
                                          rekening_regels=_regels(regels) if regels is not None else None)

    def test_zelfde_samenvatting_in_beide_routes(self):
        opgeslagen, niet = self.opgeslagen(KOSTEN_REGELS), self.niet_opslaan(KOSTEN_REGELS)
        self.assertEqual(opgeslagen["aansluitingskosten"], niet["aansluitingskosten"])
        self.assertEqual(niet["aansluitingskosten"]["totaal_eur"], 10.0)
        self.assertNotIn("onbekend_formaat", niet["aansluitingskosten"])

    def test_zonder_rekeningoverzicht_geen_veld(self):
        self.assertNotIn("aansluitingskosten", self.niet_opslaan(None))
        self.assertNotIn("aansluitingskosten", self.opgeslagen([]))

    def test_rekeningoverzicht_zonder_kosten_lege_samenvatting(self):
        self.assertEqual(self.niet_opslaan(OVERIGE_REGELS)["aansluitingskosten"]["per_beurs"], [])

    def test_kosten_en_rendement_ongewijzigd(self):
        met, zonder = self.niet_opslaan(OVERIGE_REGELS + KOSTEN_REGELS), self.niet_opslaan(OVERIGE_REGELS)
        self.assertEqual(met["statistieken"]["totalen"]["totale_transactiekosten"], 4.0)
        for veld in ("statistieken", "chart_data", "per_ticker", "box3_basis", "transacties_lijst"):
            self.assertEqual(met.get(veld), zonder.get(veld), veld)
        met_box3 = po.analyze_transacties(_transacties_df(), code=None, naam="Test", box3=True,
                                          rekening_regels=_regels(OVERIGE_REGELS + KOSTEN_REGELS))
        zonder_box3 = po.analyze_transacties(_transacties_df(), code=None, naam="Test", box3=True,
                                             rekening_regels=_regels(OVERIGE_REGELS))
        self.assertEqual(met_box3["box3_basis"], zonder_box3["box3_basis"])

    def test_onbekend_formaat_in_diagnostiek(self):
        with patch.object(po, "meld") as meld:
            self.niet_opslaan([("2024-02-04", "09:00", ONLEESBAAR, "EUR", -2.5)])
        sleutels = [c.kwargs.get("sleutel") for c in meld.call_args_list]
        self.assertIn("aansluitingskosten_onbekend_formaat", sleutels)


if __name__ == "__main__":
    unittest.main()
