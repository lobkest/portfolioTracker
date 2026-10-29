"""Aansluitingskosten per beurs en jaar: parsen, samenvatting, beide routes gelijk, en niets in kosten of rendement.
Verzonnen data, offline: database en Yahoo zijn gemockt."""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import portfolio_orchestratie as po
from backend.aansluitingskosten import (
    BEURS_ONBEKEND, bouw_aansluitingskosten_samenvatting, parse_aansluitingskosten, voeg_beurs_resultaat_toe)
from backend.db import TRANSACTIE_KOLOMMEN
from backend.dividend import ORDER_ID_KOLOM_REKENING, bereken_kassaldo, bouw_rekening_regels
from backend.rekening_regelsoorten import REGELSOORT_AANSLUITINGSKOSTEN, regelsoort
from backend.statistieken import bereken_totale_transactiekosten

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
        self.assertEqual(per_code["XET"]["verdeling_naam"], "Xetra")
        self.assertEqual(per_code["EAM"]["verdeling_naam"], "Euronext Amsterdam")
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


def _tx(datum, beurs, ticker, aantal, kosten=-2.0, product=None, korting=None):
    rij = {"datum": pd.Timestamp(datum), "beurs": beurs, "ticker": ticker, "aantal": aantal,
           "product": product or ticker, "transactiekosten": kosten}
    if korting is not None:
        rij["kostenkorting_eur"] = korting
    return rij


def _stat(posities=(), gesloten=(), periode=("2023-01-05", "2026-06-01")):
    status = {"eerste_regel_datum": periode[0], "laatste_regel_datum": periode[1]} if periode else None
    return {"posities": [{"ticker": t, "rendement_eur": w} for t, w in posities],
            "gesloten_posities": [{"ticker": t, "rendement_eur": w} for t, w in gesloten],
            "totalen": {"rekeningoverzicht_status": status}}


def _samenvatting(*regels):
    return bouw_aansluitingskosten_samenvatting(_regels(list(regels)))


def _kosten(jaar, code, naam="Beurs"):
    return (f"{jaar}-02-03", "09:00", _tekst(jaar, naam, code), "EUR", -2.5)


def _resultaat(samenvatting, rijen, stat, huidig_jaar=2026):
    voeg_beurs_resultaat_toe(samenvatting, pd.DataFrame(rijen), stat, huidig_jaar)
    return {b["code"]: b for b in samenvatting["per_beurs"]}


class TestBeursResultaat(unittest.TestCase):
    def test_open_en_gesloten_positie_op_een_beurs(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2025, "XET"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-04-01", "XET", "BBB", 5),
                 _tx("2025-01-10", "XET", "BBB", -5)]
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 100.0)], gesloten=[("BBB", 40.5)]))["XET"]
        self.assertEqual((xet["wv_eur"], xet["wv_na_aansluitingskosten_eur"], xet["wv_volledig"]), (140.5, 135.5, True))
        self.assertEqual(xet["transacties_per_jaar"], [{"jaar": 2024, "aantal": 2}, {"jaar": 2025, "aantal": 1}])
        self.assertEqual((s["totaal_wv_eur"], s["totaal_wv_na_aansluitingskosten_eur"]), (140.5, 135.5))

    def test_deeluitvoeringen_van_een_order_tellen_als_een_transactie(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [dict(_tx("2024-03-01", "XET", "AAA", 6), order_id="abc-0001-def"),
                 dict(_tx("2024-03-01", "XET", "AAA", 4), order_id="abc-0001-def-1"),
                 dict(_tx("2024-03-02", "XET", "AAA", 1), order_id="abc-0002-def"),
                 dict(_tx("2024-03-03", "XET", "AAA", 1), order_id=None),
                 dict(_tx("2024-03-03", "XET", "AAA", 1), order_id=float("nan")),
                 dict(_tx("2024-03-04", "XET", "AAA", 1), order_id="SYN-123"),
                 dict(_tx("2024-03-04", "XET", "AAA", 1), order_id="SYN-124")]
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 1.0)]))["XET"]
        self.assertEqual(xet["transacties_per_jaar"], [{"jaar": 2024, "aantal": 6}])

    def test_positie_zonder_transacties_betaalt_wel_kosten(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2025, "XET"), _kosten(2026, "XET"))
        xet = _resultaat(s, [_tx("2024-03-01", "XET", "AAA", 10)], _stat(posities=[("AAA", 5.0)]))["XET"]
        self.assertEqual(xet["jaren_met_positie"], [2024, 2025, 2026])
        self.assertEqual(xet["kosten_zonder_positie_of_transacties"], [])
        self.assertTrue(xet["heeft_open_positie"])

    def test_alles_verkocht_daarna_nog_kosten(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2025, "XET"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-06-01", "XET", "AAA", -10)]
        xet = _resultaat(s, rijen, _stat(gesloten=[("AAA", 5.0)]))["XET"]
        self.assertEqual(xet["jaren_met_positie"], [2024])
        self.assertEqual(xet["kosten_zonder_positie_of_transacties"], [2025])
        self.assertFalse(xet["heeft_open_positie"])

    def test_positie_over_twee_beurzen_met_gewicht_en_wv(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2024, "TDG"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-03-02", "TDG", "AAA", 30)]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 80.0)]))
        self.assertEqual(per["XET"]["posities"], [
            {"ticker": "AAA", "naam": "AAA", "nog_in_bezit": True, "wv_eur": 20.0, "gewicht": 0.25}])
        self.assertEqual(per["TDG"]["posities"][0]["gewicht"], 0.75)
        self.assertEqual(per["TDG"]["posities"][0]["wv_eur"], 60.0)

    def test_positie_verkocht_op_een_beurs_telt_niet_op_de_andere(self):
        s = _samenvatting(_kosten(2024, "TDG"), _kosten(2025, "XET"))
        rijen = [_tx("2022-03-01", "TDG", "AAA", 10), _tx("2022-06-01", "TDG", "AAA", -10),
                 _tx("2023-03-01", "XET", "AAA", 5)]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 1.0)]))
        self.assertEqual(per["TDG"]["jaren_met_positie"], [2022])
        self.assertEqual(per["XET"]["jaren_met_positie"], [2023, 2024, 2025, 2026])

    def test_posities_volgorde_open_eerst_dan_naam(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [_tx("2024-01-01", "XET", "ZZ", 1), _tx("2024-01-02", "XET", "AA", 1), _tx("2024-01-03", "XET", "MM", 1),
                 _tx("2024-02-01", "XET", "AA", -1)]
        xet = _resultaat(s, rijen, _stat(posities=[("ZZ", 1.0), ("MM", 2.0)], gesloten=[("AA", 3.0)]))["XET"]
        self.assertEqual([(p["naam"], p["nog_in_bezit"]) for p in xet["posities"]],
                         
                         [("MM", True), ("ZZ", True), ("AA", False)])

    def test_posities_gebruiken_bijnamen(self):
        s = _samenvatting(_kosten(2024, "XET"))
        voeg_beurs_resultaat_toe(s, pd.DataFrame([_tx("2024-01-01", "XET", "AAA", 1)]), _stat(posities=[("AAA", 1.0)]),
                                 2026, ticker_namen={"AAA": "Mijn fonds"})
        self.assertEqual(s["per_beurs"][0]["posities"][0]["naam"], "Mijn fonds")

    def test_activiteit_zonder_kosten_met_alleen_een_positie(self):
        s = _samenvatting(_kosten(2024, "XET"))
        xet = _resultaat(s, [_tx("2024-03-01", "XET", "AAA", 1)], _stat(posities=[("AAA", 1.0)]))["XET"]
        # 2025 gedekt zonder kosten en zonder transacties, maar met positie; 2026 is het huidige jaar.
        self.assertEqual(xet["activiteit_zonder_kosten"], [2025])

    def test_deels_verkocht_geen_dubbeltelling(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-05-01", "XET", "AAA", -4)]
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 100.0)], gesloten=[("AAA", 30.0)]))["XET"]
        self.assertEqual(xet["wv_eur"], 130.0)

    def test_ticker_op_twee_beurzen_naar_gekochte_stuks(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2024, "TDG"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-03-02", "TDG", "AAA", 30)]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 80.0)]))
        self.assertEqual((per["XET"]["wv_eur"], per["TDG"]["wv_eur"]), (20.0, 60.0))
        self.assertEqual(s["totaal_wv_eur"], 80.0)

    def test_corporate_action_telt_niet(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2024, "DEG"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-03-02", "DEG", "AAA", 40, kosten=None),
                 _tx("2024-03-03", "XET", "BBB", 1, product="BBB NON TRADEABLE")]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 50.0)]))
        self.assertEqual(per["XET"]["transacties_per_jaar"], [{"jaar": 2024, "aantal": 1}])
        self.assertEqual(per["XET"]["wv_eur"], 50.0)
        self.assertIsNone(per["DEG"]["wv_eur"])
        self.assertEqual(per["DEG"]["transacties_per_jaar"], [])

    def test_open_ticker_zonder_koers_is_onvolledig(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10), _tx("2024-03-02", "XET", "BBB", 5)]
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 100.0)]))["XET"]
        self.assertFalse(xet["wv_volledig"])
        self.assertEqual(xet["wv_eur"], 100.0)
        self.assertFalse(s["totaal_wv_volledig"])

    def test_beurs_met_kosten_zonder_transacties(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2024, "TDG"))
        per = _resultaat(s, [_tx("2024-03-01", "XET", "AAA", 10)], _stat(posities=[("AAA", 10.0)]))
        tdg = per["TDG"]
        self.assertEqual((tdg["wv_eur"], tdg["wv_na_aansluitingskosten_eur"], tdg["transactiekosten_eur"]),
                         (None, None, None))
        self.assertEqual((tdg["kosten_zonder_positie_of_transacties"], tdg["transacties_per_jaar"]), ([2024], []))
        self.assertEqual(per["XET"]["kosten_zonder_positie_of_transacties"], [])

    def test_transacties_zonder_kosten_alleen_in_gedekte_jaren(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [_tx(f"{j}-06-01", "XET", "AAA", 1) for j in (2022, 2023, 2024, 2025, 2026)]
        # Periode 05-01-2023 .. 01-06-2026: 2022 en 2023 (beginjaar) buiten, 2026 is het huidige jaar.
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 1.0)]))["XET"]
        self.assertEqual(xet["activiteit_zonder_kosten"], [2025])

    def test_zonder_periode_geen_transacties_zonder_kosten(self):
        s = _samenvatting(_kosten(2024, "XET"))
        stat = _stat(posities=[("AAA", 1.0)], periode=None)
        xet = _resultaat(s, [_tx("2025-06-01", "XET", "AAA", 1)], stat)["XET"]
        self.assertEqual(xet["activiteit_zonder_kosten"], [])

    def test_betaald_dit_jaar(self):
        s = _samenvatting(_kosten(2025, "XET"), _kosten(2026, "TDG"))
        rijen = [_tx("2025-03-01", "XET", "AAA", 1), _tx("2026-03-01", "TDG", "BBB", 1)]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 1.0), ("BBB", 1.0)]))
        self.assertEqual((per["XET"]["betaald_dit_jaar"], per["TDG"]["betaald_dit_jaar"]), (False, True))

    def test_transactiekosten_per_beurs_en_niet_van_wv_af(self):
        s = _samenvatting(_kosten(2024, "XET"), _kosten(2024, "TDG"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10, kosten=-2.0, korting=1.0),
                 _tx("2024-03-02", "XET", "AAA", 5, kosten=-2.0, korting=0.0),
                 _tx("2024-03-03", "TDG", "BBB", 5, kosten=-0.5, korting=0.0)]
        per = _resultaat(s, rijen, _stat(posities=[("AAA", 50.0), ("BBB", 20.0)]))
        verwacht = bereken_totale_transactiekosten(pd.DataFrame(rijen[:2]))["totaal"]
        self.assertEqual(per["XET"]["transactiekosten_eur"], verwacht)
        self.assertEqual((per["XET"]["transactiekosten_eur"], per["TDG"]["transactiekosten_eur"]), (4.0, 0.5))
        self.assertEqual(per["XET"]["wv_na_aansluitingskosten_eur"], 47.5)
        self.assertEqual(s["totaal_transactiekosten_eur"], 4.5)

    def test_transactiekosten_niet_beschikbaar_is_none(self):
        s = _samenvatting(_kosten(2024, "XET"))
        rijen = [_tx("2024-03-01", "XET", "AAA", 10, kosten=None)]
        xet = _resultaat(s, rijen, _stat(posities=[("AAA", 5.0)]))["XET"]
        self.assertIsNone(xet["transactiekosten_eur"])
        self.assertIsNone(s["totaal_transactiekosten_eur"])

    def test_onbekend_formaat_zonder_wv(self):
        s = _samenvatting(("2024-02-04", "09:00", ONLEESBAAR, "EUR", -2.5))
        onbekend = _resultaat(s, [_tx("2024-03-01", "XET", "AAA", 1)], _stat(posities=[("AAA", 5.0)]))[BEURS_ONBEKEND]
        self.assertEqual((onbekend["wv_eur"], onbekend["wv_na_aansluitingskosten_eur"], onbekend["transactiekosten_eur"]),
                         (None, None, None))
        self.assertEqual((onbekend["transacties_per_jaar"], onbekend["kosten_zonder_positie_of_transacties"],
                          onbekend["activiteit_zonder_kosten"], onbekend["betaald_dit_jaar"]), ([], [], [], False))
        self.assertIsNone(s["totaal_wv_eur"])

    def test_kleine_letters_en_spaties_in_beurscode(self):
        s = _samenvatting(_kosten(2024, "XET"))
        xet = _resultaat(s, [_tx("2024-03-01", " xet ", "AAA", 1)], _stat(posities=[("AAA", 5.0)]))["XET"]
        self.assertEqual(xet["wv_eur"], 5.0)


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

    def test_wv_per_beurs_sluit_aan_op_statistieken(self):
        antwoord = self.niet_opslaan(OVERIGE_REGELS + KOSTEN_REGELS)
        xet = next(b for b in antwoord["aansluitingskosten"]["per_beurs"] if b["code"] == "XET")
        totalen = antwoord["statistieken"]["totalen"]
        # Het totaalrendement is na transactiekosten, de positie-W/V (GAK op kale waarde) ervoor.
        self.assertEqual(xet["wv_eur"], round(totalen["rendement_eur"] + totalen["totale_transactiekosten"], 2))
        self.assertEqual(xet["wv_na_aansluitingskosten_eur"], round(xet["wv_eur"] - 5.0, 2))
        self.assertEqual(xet["transacties_per_jaar"], [{"jaar": 2024, "aantal": 2}])
        zzz = next(b for b in antwoord["aansluitingskosten"]["per_beurs"] if b["code"] == "ZZZ")
        self.assertIsNone(zzz["wv_eur"])

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
