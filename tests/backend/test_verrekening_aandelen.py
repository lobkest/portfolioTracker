"""'Verrekening van Aandelen' telt als opbrengst van de positie (rendement, Totaal W/V, XIRR, TWR), zonder aantal,
GAK of waarde te veranderen. Verzonnen data, offline: database en Yahoo zijn gemockt."""
import os
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie as po
from db import TRANSACTIE_KOLOMMEN
from diagnostiek_checks import check_verrekening_aandelen
from portfolio_calc import compute_value_over_time
from rekening_regelsoorten import OMSCHRIJVING_VERREKENING_AANDELEN, behandeling
from statistieken import _bouw_xirr_cashflows, bereken_holdings_en_gesloten, bereken_overige_kassabewegingen
from verrekening import bouw_verrekeningen, zet_verrekeningen

TEST_CODE = "test_verrekening"
AAA, BBB = "XX0000000001", "XX0000000002"
KASSALDO = {"saldo_eur": 10.0, "netto_gestort_eur": 1000.0, "per_datum": date(2024, 6, 1)}


def _regel(bedrag, isin=AAA, product="AAA", datum=date(2024, 5, 2), valuta="EUR"):
    return {"datum": datum, "tijd": None, "omschrijving": OMSCHRIJVING_VERREKENING_AANDELEN,
            "mutatie_valuta": valuta, "mutatie": bedrag, "product": product, "isin": isin}


def _transacties_df():
    def rij(datum, tijd, ticker, isin, aantal, koers, kosten):
        waarde = -aantal * koers
        return {"datum": pd.Timestamp(datum), "product": ticker, "isin": isin, "beurs": "EAM", "ticker": ticker,
                "aantal": aantal, "koers": koers, "totaal_eur": waarde + kosten, "echte_naam": ticker,
                "transactiekosten": kosten, "waarde_eur": waarde, "tijd": tijd, "wisselkoers": None,
                "autofx_kosten": None}
    return pd.DataFrame([
        rij("2024-01-02", "10:00", "AAA", AAA, 10.0, 50.0, -2.0),
        rij("2024-02-01", "10:00", "BBB", BBB, 5.0, 100.0, -3.0),
        rij("2024-04-02", "10:00", "AAA", AAA, 2.0, 55.0, -2.0),
    ], columns=TRANSACTIE_KOLOMMEN)


def _price_data():
    dagen = pd.bdate_range("2024-01-02", "2024-06-03")
    return pd.DataFrame({
        "AAA": [50.0 + 0.1 * i for i in range(len(dagen))],
        "BBB": [100.0 - 0.05 * i for i in range(len(dagen))],
    }, index=dagen)


def _verwerkt(regels):
    df = _transacties_df()
    toegekend, _niet = bouw_verrekeningen(regels, df)
    return zet_verrekeningen(df, toegekend)


class _Basis(unittest.TestCase):
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
        rijen = list(df.itertuples(index=False, name=None))
        with ExitStack() as stack:
            for naam, waarde in [
                ("db_get_portfolio_naam_en_transacties", ("Test", rijen)),
                ("db_get_rekening_regels_eur", regels),
                ("db_get_rekening_koop_verkoop", []),
                ("db_get_dividenden", []),
                ("db_get_isin_ticker_product", po.isin_ticker_product_rijen(df)),
                ("db_get_kassaldo", KASSALDO),
                ("db_get_order_id_rijen", []),
            ]:
                stack.enter_context(patch.object(po, naam, return_value=waarde))
            po.wis_portfolio_basis_cache(TEST_CODE)
            return po.build_portfolio_response(TEST_CODE)

    def niet_opslaan(self, regels):
        return po.analyze_transacties(_transacties_df(), code=None, naam="Test", kassaldo=KASSALDO,
                                      promoties=[], rekening_regels=regels)


class TestVerrekeningTelMee(_Basis):
    def setUp(self):
        super().setUp()
        self.basis = self.opgeslagen([])["statistieken"]
        self.met = self.opgeslagen([_regel(5.18), _regel(7.78)])["statistieken"]

    def test_totaal_rendement_stijgt_met_het_bedrag(self):
        self.assertAlmostEqual(self.met["totalen"]["rendement_eur"] - self.basis["totalen"]["rendement_eur"], 12.96, 2)
        self.assertAlmostEqual(self.basis["totalen"]["geinvesteerd"] - self.met["totalen"]["geinvesteerd"], 12.96, 2)
        self.assertEqual(self.met["totalen"]["waarde"], self.basis["totalen"]["waarde"])

    def test_aantallen_en_gak_blijven_gelijk(self):
        def zonder_rendement(posities):
            return [{k: v for k, v in p.items() if k not in ("rendement_eur", "rendement_pct", "verrekening_eur")}
                    for p in posities]
        self.assertEqual(zonder_rendement(self.met["posities"]), zonder_rendement(self.basis["posities"]))

    def test_rendement_van_de_positie_stijgt(self):
        aaa = {p["ticker"]: p for p in self.met["posities"]}["AAA"]
        aaa_basis = {p["ticker"]: p for p in self.basis["posities"]}["AAA"]
        self.assertEqual(aaa["verrekening_eur"], 12.96)
        self.assertAlmostEqual(aaa["rendement_eur"] - aaa_basis["rendement_eur"], 12.96, 2)
        bbb = {p["ticker"]: p for p in self.met["posities"]}["BBB"]
        self.assertEqual(bbb["verrekening_eur"], 0.0)

    def test_xirr_en_twr_stijgen(self):
        self.assertGreater(self.met["geavanceerd"]["xirr_pct"], self.basis["geavanceerd"]["xirr_pct"])
        self.assertGreater(self.met["geavanceerd"]["twr_pct"], self.basis["geavanceerd"]["twr_pct"])

    def test_niet_opslaan_geeft_dezelfde_getallen_als_opgeslagen(self):
        regels = [_regel(5.18), _regel(7.78)]
        niet, opgeslagen = self.niet_opslaan(regels), self.opgeslagen(regels)
        self.assertEqual(niet["statistieken"], opgeslagen["statistieken"])
        self.assertEqual(niet["chart_data"], opgeslagen["chart_data"])

    def test_regel_zonder_isin_of_positie_heeft_geen_effect(self):
        for regel in (_regel(12.96, isin=None), _regel(12.96, isin="XX9999999999")):
            met = self.opgeslagen([regel])["statistieken"]
            self.assertEqual(met["totalen"]["rendement_eur"], self.basis["totalen"]["rendement_eur"])
            self.assertEqual(met["totalen"]["geinvesteerd"], self.basis["totalen"]["geinvesteerd"])
            self.assertEqual(met["posities"], self.basis["posities"])
            self.assertEqual(met["geavanceerd"], self.basis["geavanceerd"])


class TestRekenregels(unittest.TestCase):
    def test_xirr_cashflow_aanwezig_en_positief(self):
        df = _verwerkt([_regel(5.18), _regel(7.78)])
        resultaat = compute_value_over_time(df, _price_data())
        cashflows = _bouw_xirr_cashflows(df, resultaat)
        self.assertIn((date(2024, 5, 2), 5.18), cashflows)
        self.assertIn((date(2024, 5, 2), 7.78), cashflows)
        zonder = _bouw_xirr_cashflows(_transacties_df(), resultaat)
        self.assertEqual(len(cashflows), len(zonder) + 2)

    def test_geinvesteerd_daalt_vanaf_de_datum_van_de_regel(self):
        df = _verwerkt([_regel(12.96)])
        met = compute_value_over_time(df, _price_data())
        zonder = compute_value_over_time(_transacties_df(), _price_data())
        voor, na = pd.Timestamp("2024-05-01"), pd.Timestamp("2024-05-02")
        self.assertEqual(met.loc[voor, "geinvesteerd"], zonder.loc[voor, "geinvesteerd"])
        self.assertAlmostEqual(zonder.loc[na, "geinvesteerd"] - met.loc[na, "geinvesteerd"], 12.96, 6)
        self.assertEqual(met["waarde"].tolist(), zonder["waarde"].tolist())

    def test_gesloten_positie_krijgt_het_in_gerealiseerd_resultaat(self):
        df = _transacties_df()
        verkoop = df.iloc[[0]].assign(aantal=-12.0, totaal_eur=660.0, waarde_eur=660.0,
                                      datum=pd.Timestamp("2024-04-10"))
        df = pd.concat([df, verkoop], ignore_index=True)
        zonder = bereken_holdings_en_gesloten(df)[1]["AAA"]["gerealiseerd_eur"]
        zet_verrekeningen(df, bouw_verrekeningen([_regel(12.96)], df)[0])
        met = bereken_holdings_en_gesloten(df)[1]["AAA"]["gerealiseerd_eur"]
        self.assertAlmostEqual(met - zonder, 12.96, 6)

    def test_gak_en_aantal_van_open_positie_gelijk(self):
        zonder = bereken_holdings_en_gesloten(_transacties_df())[0]
        met = bereken_holdings_en_gesloten(_verwerkt([_regel(12.96)]))[0]
        self.assertEqual(met["AAA"]["aantal"], zonder["AAA"]["aantal"])
        self.assertEqual(met["AAA"]["gak"], zonder["AAA"]["gak"])
        self.assertEqual(met["AAA"]["verrekening_eur"], 12.96)

    def test_regel_in_vreemde_valuta_of_zonder_bedrag_telt_niet(self):
        toegekend, niet = bouw_verrekeningen([_regel(5.0, valuta="USD"), _regel(None)], _transacties_df())
        self.assertEqual((toegekend, niet), ([], []))

    def test_isin_van_een_keten_wordt_de_positie(self):
        df = _transacties_df()
        oud = df.iloc[[0]].assign(isin="XX0000000009", aantal=-10.0, koers=50.0, transactiekosten=0.0,
                                  tijd="00:00", datum=pd.Timestamp("2024-03-01"))
        nieuw = df.iloc[[0]].assign(aantal=40.0, koers=12.5, transactiekosten=0.0, tijd="00:00",
                                    datum=pd.Timestamp("2024-03-01"))
        df = pd.concat([df, oud, nieuw], ignore_index=True)
        toegekend, niet = bouw_verrekeningen([_regel(3.0, isin="XX0000000009")], df)
        self.assertEqual([(v["ticker"], v["bedrag"]) for v in toegekend], [("AAA", 3.0)])
        self.assertEqual(niet, [])


class TestKassaldoGeenDubbeleTelling(unittest.TestCase):
    def test_toegekende_verrekening_zit_niet_meer_in_overig(self):
        regels = [_regel(12.96)]
        kassaldo = {"saldo_eur": 512.96, "netto_gestort_eur": 1000.0, "vanaf_opening": True}
        zonder_toekenning = bereken_overige_kassabewegingen(regels, kassaldo, 500.0, 500.0)
        toegekend, _ = bouw_verrekeningen(regels, _transacties_df())
        met_toekenning = bereken_overige_kassabewegingen(regels, kassaldo, 487.04, 500.0, toegekend)
        self.assertEqual(zonder_toekenning, 12.96)
        self.assertEqual(met_toekenning, 0.0)
        self.assertAlmostEqual(487.04, 1000.0 + met_toekenning - 512.96, 6)

    def test_niet_toegekende_verrekening_blijft_in_overig(self):
        regels = [_regel(12.96, isin=None)]
        kassaldo = {"saldo_eur": 512.96, "netto_gestort_eur": 1000.0, "vanaf_opening": True}
        toegekend, _ = bouw_verrekeningen(regels, _transacties_df())
        self.assertEqual(bereken_overige_kassabewegingen(regels, kassaldo, 500.0, 500.0, toegekend), 12.96)


class TestDiagnostiekEnBehandeling(unittest.TestCase):
    def test_toegekend_zegt_dat_het_meetelt_als_opbrengst(self):
        toegekend, niet = bouw_verrekeningen([_regel(5.18), _regel(7.78)], _transacties_df())
        [b] = check_verrekening_aandelen(toegekend, niet)
        self.assertEqual(b["sleutel"], "rekening:verrekening_aandelen")
        self.assertIn("2x 'Verrekening van Aandelen'", b["tekst"])
        self.assertIn("EUR 12,96", b["tekst"])
        self.assertIn("telt mee als opbrengst van AAA", b["tekst"])
        self.assertEqual([r[3] for r in b["rijen"]["rijen"]], ["5,18", "7,78"])

    def test_zonder_positie_is_let_op_met_rijen(self):
        toegekend, niet = bouw_verrekeningen([_regel(12.96, isin=None)], _transacties_df())
        [b] = check_verrekening_aandelen(toegekend, niet)
        self.assertEqual(b["sleutel"], "rekening:verrekening_aandelen_zonder_positie")
        self.assertEqual(b["niveau"], "LET_OP")
        self.assertEqual(b["rijen"]["rijen"][0][3], "12,96")

    def test_geen_regels_geen_bevinding(self):
        self.assertEqual(check_verrekening_aandelen([], []), [])

    def test_behandeling_noemt_opbrengst(self):
        self.assertIn("opbrengst", behandeling(OMSCHRIJVING_VERREKENING_AANDELEN).reden)


if __name__ == "__main__":
    unittest.main()
