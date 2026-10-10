"""Diagnostiek: Order ID's tegen het rekeningoverzicht, prijscheck zonder FX, regelsoorten van het rekeningoverzicht,
export-indeling en ETF-aanbieder. Offline, geen database."""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie as po
import ticker_prijscheck as tp
from diagnostiek import (
    CATEGORIE_ETF_HOLDINGS, CATEGORIE_ORDER_IDS, CATEGORIE_REKENINGOVERZICHT, CATEGORIE_WISSELKOERSEN,
    INFO, LET_OP, haal_meldingen,
)
from diagnostiek_checks import (
    aantal_tekst, check_order_ids_rekening, check_prijscheck_zonder_fx, check_rekening_regelsoorten, kale_order_id,
)
from dividend import (
    REGELSOORT_CORPORATE_ACTION, REGELSOORT_DIVIDEND, REGELSOORT_KOOP_VERKOOP, REGELSOORT_KOSTEN, REGELSOORT_RENTE,
    REGELSOORT_STORTING_OPNAME, REGELSOORT_SWEEP, REGELSOORT_VALUTA, REGELSOORT_VERREKENING_AANDELEN,
    lees_rekeningoverzicht, regelsoort,
)
from etf_holdings_provider import aanbieder_naam
from upload_verwerking import lees_transacties_excel, _meld_rekening_regelsoorten

TEST_FILES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_files")
U1 = "dcdc8696-243a-43d1-ad68-138b293638f0"
U2 = "11111111-2222-3333-4444-555555555555"
U3 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _d(tekst):
    return pd.Timestamp(tekst)


def _in_request(functie, *args):
    with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()):
        functie(*args)
        return haal_meldingen()


class TestAantalTekst(unittest.TestCase):
    def test_enkelvoud_en_meervoud(self):
        self.assertEqual(aantal_tekst(1, "order", "orders"), "1 order")
        self.assertEqual(aantal_tekst(3, "order", "orders"), "3 orders")
        self.assertEqual(aantal_tekst(0, "uitkering", "uitkeringen"), "0 uitkeringen")


class TestKaleOrderId(unittest.TestCase):
    def test_deelorder_achtervoegsel_en_hoofdletters(self):
        self.assertEqual(kale_order_id(U1 + "-2"), U1)
        self.assertEqual(kale_order_id(U1.upper()), U1)

    def test_syn_en_leeg_worden_none(self):
        self.assertIsNone(kale_order_id("SYN-abcdef0123456789-0"))
        self.assertIsNone(kale_order_id(None))
        self.assertIsNone(kale_order_id(float("nan")))

    def test_achtervoegsel_alleen_achter_een_uuid(self):
        self.assertEqual(kale_order_id("order-12"), "order-12")


class TestOrderIdsRekening(unittest.TestCase):
    def test_alles_in_beide_alleen_periode(self):
        uit = check_order_ids_rekening([(U1, _d("2024-01-02"), "ASML"), (U2, _d("2024-06-03"), "AAPL")],
                                       [(U1, _d("2024-01-02"), "ASML"), (U2, _d("2024-06-03"), "AAPL")])
        self.assertEqual([(b["niveau"], b["sleutel"]) for b in uit], [(INFO, "order_ids:rekening:periode")])
        self.assertEqual(uit[0]["tekst"], "Transacties: 02-01-2024 t/m 03-06-2024; rekeningoverzicht: 02-01-2024 t/m "
                                          "03-06-2024. 2 orders uit de transacties vergeleken binnen 02-01-2024 t/m "
                                          "03-06-2024.")

    def test_deelorder_achtervoegsel_telt_als_dezelfde_order(self):
        uit = check_order_ids_rekening([(U1, _d("2024-01-02"), "ASML"), (U1 + "-1", _d("2024-01-02"), "ASML")],
                                       [(U1.upper(), _d("2024-01-02"), "ASML")])
        self.assertEqual([b["sleutel"] for b in uit], ["order_ids:rekening:periode"])

    def test_syn_id_wordt_overgeslagen(self):
        uit = check_order_ids_rekening([(U1, _d("2024-01-02"), "ASML"), ("SYN-abc-0", _d("2024-03-01"), "XELA")],
                                       [(U1, _d("2024-01-02"), "ASML"), (None, _d("2024-03-31"), None)])
        self.assertEqual([b["sleutel"] for b in uit], ["order_ids:rekening:periode"])

    def test_kortere_export_alleen_periode(self):
        transacties = [(U2, _d("2019-03-12"), "AAPL"), (U1, _d("2024-01-02"), "ASML")]
        rekening = [(None, _d("2023-01-01"), None), (U1, _d("2024-01-02"), "ASML")]
        [b] = check_order_ids_rekening(transacties, rekening)
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "order_ids:rekening:periode"))
        self.assertIn("Transacties: 12-03-2019 t/m 02-01-2024; rekeningoverzicht: 01-01-2023 t/m 02-01-2024. "
                      "1 order uit de transacties vergeleken binnen 01-01-2023 t/m 02-01-2024.", b["tekst"])

    def test_beide_richtingen(self):
        transacties = [(U1, _d("2024-01-02"), "ASML"), (U2, _d("2024-05-02"), "Apple")]
        rekening = [(U1, _d("2024-01-02"), "ASML"), (U3, _d("2024-04-01"), "Nvidia"), (None, _d("2024-06-30"), None)]
        uit = {b["sleutel"]: b for b in check_order_ids_rekening(transacties, rekening)}
        alleen_t = uit["order_ids:rekening:alleen_transacties"]
        alleen_r = uit["order_ids:rekening:alleen_rekening"]
        self.assertEqual((alleen_t["niveau"], alleen_r["niveau"]), (LET_OP, LET_OP))
        self.assertEqual(alleen_t["tekst"], "1 order staat in de transacties maar niet in het rekeningoverzicht "
                                            "(binnen 02-01-2024 t/m 02-05-2024), bv. Apple op 02-05-2024.")
        self.assertEqual(alleen_r["tabel"], {"kolommen": ["Datum", "Product", "Order ID"],
                                             "rijen": [["01-04-2024", "Nvidia", U3]]})

    def test_geen_overlap(self):
        [b] = check_order_ids_rekening([(U1, _d("2020-01-02"), "ASML")], [(U2, _d("2024-01-02"), "AAPL")])
        self.assertIn("De periodes overlappen niet; Order ID's niet vergeleken.", b["tekst"])

    def test_zonder_rekeningoverzicht_geen_melding(self):
        self.assertEqual(check_order_ids_rekening([(U1, _d("2024-01-02"), "ASML")], []), [])

    def test_meld_na_opslaan_uit_een_query(self):
        rijen = [("transacties", U1, datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), "ASML"),
                 ("transacties", U2, datetime.date(2024, 5, 2), datetime.date(2024, 5, 2), "Apple"),
                 ("rekening", U1, datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), "ASML"),
                 ("rekening", None, datetime.date(2024, 1, 1), datetime.date(2024, 6, 30), None)]
        with patch.object(po, "db_get_order_id_periodes", return_value=rijen) as query:
            meldingen = _in_request(po.meld_order_ids_rekening, "ZZTEST")
        query.assert_called_once_with("ZZTEST")
        self.assertEqual({(m["categorie"], m["sleutel"]) for m in meldingen},
                         {(CATEGORIE_ORDER_IDS, "order_ids:rekening:periode"),
                          (CATEGORIE_ORDER_IDS, "order_ids:rekening:alleen_transacties")})
        self.assertIn("tabel", [m for m in meldingen if m["niveau"] == LET_OP][0])

    def test_meld_na_opslaan_zonder_rekeningregels_stil_en_fout_breekt_niet(self):
        with patch.object(po, "db_get_order_id_periodes",
                          return_value=[("transacties", U1, datetime.date(2024, 1, 2), datetime.date(2024, 1, 2), "A")]):
            self.assertEqual(_in_request(po.meld_order_ids_rekening, "ZZTEST"), [])
        with patch.object(po, "db_get_order_id_periodes", side_effect=RuntimeError("db weg")):
            [m] = _in_request(po.meld_order_ids_rekening, "ZZTEST")
        self.assertTrue(m["sleutel"].startswith("check_mislukt"))

    def test_niet_opslaan_uit_de_bestanden(self):
        excel = pd.DataFrame({"Order ID": [U1, None], "Datum": [_d("2024-01-02"), _d("2024-02-01")],
                              "Product": ["ASML", "XELA"]})
        rekening = pd.DataFrame({"Order Id": [U1, U3], "Datum": [_d("2024-01-02"), _d("2024-01-15")],
                                 "Product": ["ASML", "Nvidia"]})
        meldingen = _in_request(po.meld_order_ids_rekening_uit_bestanden, excel, rekening)
        self.assertEqual({m["sleutel"] for m in meldingen},
                         {"order_ids:rekening:periode", "order_ids:rekening:alleen_rekening"})


class TestPrijscheckZonderFx(unittest.TestCase):
    def test_een_melding_per_ticker(self):
        checks = {"NESN.SW": [{"reden": "geen_fx"}, {"reden": "geen_fx"}], "ASML.AS": [{"reden": None}]}
        [b] = check_prijscheck_zonder_fx(checks, {"NESN.SW": "CHF"}, {"NESN.SW": "Nestle"})
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "prijscheck_geen_fx:NESN.SW"))
        self.assertEqual(b["tekst"], "Nestle (NESN.SW): geen wisselkoers CHF → EUR; de prijscheck tegen Yahoo is "
                                     "overgeslagen.")

    def test_andere_reden_of_geen_checks_geeft_niets(self):
        self.assertEqual(check_prijscheck_zonder_fx({"A": [{"reden": "geen_yahoo_koers"}], "B": []}, {}, {}), [])

    def test_reden_uit_beoordeel_prijs(self):
        with patch.object(tp, "_fx_koers_op_datum", return_value=None), redirect_stdout(io.StringIO()):
            geen_fx = tp._beoordeel_prijs("NESN.SW", "2024-01-02", 90.0, 100.0, "CHF", 101.0, 99.0, 1.0)
        self.assertEqual((geen_fx["reden"], geen_fx["match"]), ("geen_fx", None))
        self.assertEqual(tp._beoordeel_prijs("A", "2024-01-02", 90.0, None, "EUR", None, None, 1.0)["reden"],
                         "geen_yahoo_koers")
        self.assertEqual(tp._beoordeel_prijs("A", "2024-01-02", None, 90.0, "EUR", None, None, 1.0)["reden"],
                         "geen_eigen_koers")
        gelukt = tp._beoordeel_prijs("A", "2024-01-02", 90.0, 90.5, "EUR", 91.0, 89.0, 1.0)
        self.assertEqual((gelukt["reden"], gelukt["match"]), (None, True))

    def test_meld_tickers_meldt_in_wisselkoersen(self):
        df = pd.DataFrame({"datum": [_d("2024-01-02")], "tijd": [datetime.time(10, 0)], "product": ["Nestle"],
                           "echte_naam": ["NESTLE"], "isin": ["CH0038863350"], "beurs": ["SWX"], "ticker": ["NESN.SW"],
                           "aantal": [1.0], "koers": [90.0], "transactiekosten": [-1.0], "waarde_eur": [-90.0]})
        details = {"NESN.SW": {"valuta": "CHF", "quote_type": "EQUITY", "yahoo_beurs": "EBS"}}
        with patch.object(po, "db_get_ticker_details", return_value=details), \
                patch.object(po, "db_get_cached_openfigi_voor_isins", return_value={}):
            meldingen = _in_request(po._meld_tickers, df, [], {"NESN.SW": [{"reden": "geen_fx", "match": None}]})
        fx = [m for m in meldingen if m["categorie"] == CATEGORIE_WISSELKOERSEN]
        self.assertEqual([m["sleutel"] for m in fx], ["prijscheck_geen_fx:NESN.SW"])
        self.assertIn("NESTLE (NESN.SW): geen wisselkoers CHF → EUR", fx[0]["tekst"])


def _rekening(*regels):
    return pd.DataFrame([{"Datum": _d(datum), "Omschrijving": tekst, "valuta_mutatie": valuta, "mutatie": bedrag}
                         for datum, tekst, valuta, bedrag in regels])


class TestRegelsoort(unittest.TestCase):
    def test_nederlandse_degiro_teksten(self):
        verwacht = {
            "Koop 11 @ 57,33 EUR": REGELSOORT_KOOP_VERKOOP,
            "Verkoop 1.250 @ 3,5 USD": REGELSOORT_KOOP_VERKOOP,
            "DEGIRO Transactiekosten en/of kosten van derden": REGELSOORT_KOSTEN,
            "Dividend": REGELSOORT_DIVIDEND,
            "Dividendbelasting": REGELSOORT_DIVIDEND,
            "Dividend Herinvestering": REGELSOORT_DIVIDEND,
            "Valuta Creditering": REGELSOORT_VALUTA,
            "Valuta Debitering": REGELSOORT_VALUTA,
            "CLAIMEMISSIE: Koop 2 @ 1,1 EUR": REGELSOORT_CORPORATE_ACTION,
            "PRODUCTWIJZIGING : Verkoop 5 @ 10 EUR": REGELSOORT_CORPORATE_ACTION,
            "Verrekening van Aandelen": REGELSOORT_VERREKENING_AANDELEN,
            "iDEAL storting": REGELSOORT_STORTING_OPNAME,
            "Overboeking van uw geldrekening bij flatexDEGIRO Bank 150 EUR": REGELSOORT_SWEEP,
            "Overboeking naar uw geldrekening bij flatexDEGIRO Bank 150 EUR": REGELSOORT_SWEEP,
        }
        self.assertEqual({tekst: regelsoort(tekst) for tekst in verwacht}, verwacht)

    def test_engelse_teksten_uit_de_bestaande_parsers(self):
        self.assertEqual(regelsoort("iDEAL Deposit"), REGELSOORT_STORTING_OPNAME)
        self.assertEqual(regelsoort("Withdrawal"), REGELSOORT_STORTING_OPNAME)
        self.assertEqual(regelsoort("Degiro Cash Sweep Transfer"), REGELSOORT_SWEEP)

    def test_rente_uit_een_echte_export(self):
        self.assertEqual(regelsoort("Flatex Interest Income"), REGELSOORT_RENTE)

    def test_onzekere_engelse_teksten_blijven_onbekend(self):
        for tekst in ("Buy 3 @ 100 EUR", "Sell 3 @ 100 EUR", "FX Credit", None):
            self.assertIsNone(regelsoort(tekst), tekst)


class TestRekeningRegelsoorten(unittest.TestCase):
    def test_alleen_bekende_soorten_geeft_niets(self):
        df = _rekening(("2024-01-02", "Koop 1 @ 10 EUR", "EUR", -10.0), ("2024-01-02", "Dividend", "USD", 1.0))
        self.assertEqual(check_rekening_regelsoorten(df), [])

    def test_verrekening_van_aandelen_let_op(self):
        df = _rekening(("2023-02-01", "Verrekening van Aandelen", "EUR", 5.0),
                       ("2023-03-05", "Verrekening van Aandelen", "EUR", 7.34))
        [b] = check_rekening_regelsoorten(df)
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "rekening:verrekening_aandelen"))
        self.assertEqual(b["tekst"], "Rekeningoverzicht: 2x 'Verrekening van Aandelen' (01-02-2023 t/m 05-03-2023, "
                                     "totaal EUR 12,34): geld uit een corporate action. Het staat niet in het "
                                     "transactiebestand en telt dus niet mee in het rendement.")

    def test_onbekende_soorten_gegroepeerd_met_aantallen(self):
        df = _rekening(*[("2024-0%d-01" % m, "FX Credit", "EUR", 0.1) for m in (1, 2, 3)],
                       ("2024-01-01", "Flatex Interest Income", "EUR", 0.1),
                       ("2024-04-01", "Buy 3 @ 100 EUR", "EUR", -300.0),
                       ("2024-05-01", "Buy 5 @ 101,5 EUR", "EUR", -507.5),
                       ("2024-05-01", "Koop 1 @ 10 EUR", "EUR", -10.0))
        [b] = check_rekening_regelsoorten(df)
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "rekening:onbekende_soorten"))
        self.assertEqual(b["tekst"], "Rekeningoverzicht: 2 regelsoorten die de app niet herkent: "
                                     "'FX Credit' (3x), 'Buy # @ # EUR' (2x).")
        self.assertEqual(b["tabel"]["rijen"][1], ["Buy # @ # EUR", 2, "01-04-2024", "01-05-2024"])

    def test_echt_rekeningoverzicht(self):
        with open(os.path.join(TEST_FILES, "Account_test.xlsx"), "rb") as f:
            df = lees_rekeningoverzicht(f)
        meldingen = _in_request(_meld_rekening_regelsoorten, df)
        self.assertEqual([(m["categorie"], m["niveau"], m["sleutel"]) for m in meldingen],
                         [(CATEGORIE_REKENINGOVERZICHT, LET_OP, "rekening:verrekening_aandelen")])

    def test_leeg_of_none(self):
        self.assertEqual(check_rekening_regelsoorten(None), [])
        self.assertEqual(check_rekening_regelsoorten(pd.DataFrame()), [])


class TestExportIndeling(unittest.TestCase):
    def _order_id_melding(self, bestand):
        meldingen = _in_request(lees_transacties_excel, bestand)
        return next(m for m in meldingen if m["sleutel"] == "order_ids")

    def test_oude_indeling_echt_bestand(self):
        with open(os.path.join(TEST_FILES, "Transactions_test.xlsx"), "rb") as f:
            melding = self._order_id_melding(f)
        self.assertTrue(melding["tekst"].endswith(" Order ID uit de naamloze kolom rechts van de kop (oude indeling)."))

    def test_nieuwe_indeling_onder_eigen_kop(self):
        df = pd.read_excel(os.path.join(TEST_FILES, "Transactions_test.xlsx"))
        df["Order ID"] = df["Unnamed: 17"]
        buffer = io.BytesIO()
        df.drop(columns=["Unnamed: 17"]).to_excel(buffer, index=False)
        melding = self._order_id_melding(buffer)
        self.assertTrue(melding["tekst"].endswith(" Order ID onder de eigen kop (nieuwe indeling)."))


class TestEtfAanbieder(unittest.TestCase):
    def test_aanbieder_naam(self):
        self.assertEqual(aanbieder_naam("CSPX.AS"), "iShares")
        self.assertEqual(aanbieder_naam("GDX.L"), "VanEck")
        self.assertEqual(aanbieder_naam("ONBEKEND.AS"), "iShares")

    def test_in_provider_melding(self):
        land_sector = {"per_etf": {"GDX.L": {"land": {"US": 0.6, "CA": 0.4}, "land_bron": "provider_csv"}}}
        meldingen = _in_request(po._meld_etf_holdings, land_sector)
        self.assertEqual([(m["categorie"], m["tekst"]) for m in meldingen],
                         [(CATEGORIE_ETF_HOLDINGS, "'GDX.L': volledige holdings van de fondsaanbieder (VanEck).")])


if __name__ == "__main__":
    unittest.main()
