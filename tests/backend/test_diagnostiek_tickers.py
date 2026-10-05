"""Tickers-checks (diagnostiek_checks.py) en de verwachte Diagnostiek voor het echte portfolio. Offline, geen database:
de caches (ticker_info, openfigi_cache) worden als dicts meegegeven of gemockt."""
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
from diagnostiek import CATEGORIE_DATA, CATEGORIE_SPLITS, CATEGORIE_TICKERS, GOED, INFO, LET_OP, haal_meldingen, meld
from diagnostiek_checks import (
    beurs_oordeel, check_corporate_action_rijen, check_isin_wissels, check_ontbrekende_kolommen,
    check_openfigi_leeg, check_openfigi_root, check_synthetische_order_ids, check_ticker_info_onvolledig,
    check_ticker_samenvatting, check_valuta_consistentie, dis_acc_strijdigheden, openfigi_root_mismatches,
    ticker_bevindingen,
)
from portfolio_calc import bepaal_split_boekingen, compute_split_adjusted_shares, meld_split_koppeling
from split_correctie import bepaal_effectieve_datums

VWCE_ISIN = "IE00B3RBWM25"
VWCE_DEGIRO = "VANGUARD FTSE ALL-WORLD UCITS ETF USD DIS"
VWCE_YAHOO = "Vanguard FTSE All-World UCITS ETF USD Accumulation"
XELA_OUD, XELA_NIEUW = "US30162V1026", "US30162V4095"
OPENFIGI_VWRL = [{"ticker": "VWRL"}, {"ticker": "VWRD"}, {"ticker": "VGWL"}]
M = datetime.time(0, 0)
NAN = float("nan")


class TestDisAcc(unittest.TestCase):
    def test_vwce_dis_tegen_accumulation(self):
        strijdig = dis_acc_strijdigheden({"VWCE.DE": [VWCE_DEGIRO]}, {"VWCE.DE": VWCE_YAHOO})
        self.assertEqual(strijdig, {"VWCE.DE": (VWCE_DEGIRO, VWCE_YAHOO)})

    def test_woordgrens(self):
        namen = {"DISC": ["WARNER BROS DISCOVERY"], "ACX": ["ACCESS BANK ACC"]}
        yahoo = {"DISC": "Warner Bros. Discovery Accumulating", "ACX": "Access Bank Distributing"}
        # DISCOVERY is geen DIS; "ACC" als los woord wel.
        self.assertEqual(set(dis_acc_strijdigheden(namen, yahoo)), {"ACX"})

    def test_zelfde_vorm_of_onbekend_geeft_niets(self):
        self.assertEqual(dis_acc_strijdigheden({"A": ["X UCITS ETF ACC"]}, {"A": "X UCITS ETF Accumulating"}), {})
        self.assertEqual(dis_acc_strijdigheden({"A": ["X UCITS ETF DIS"]}, {"A": None}), {})
        self.assertEqual(dis_acc_strijdigheden({"A": ["ASML HOLDING"]}, {"A": "ASML Holding N.V."}), {})


class TestOpenfigi(unittest.TestCase):
    def test_root_ontbreekt_toont_bekende_roots(self):
        mismatch = openfigi_root_mismatches({"VWCE.DE": [VWCE_ISIN]}, {VWCE_ISIN: OPENFIGI_VWRL})
        self.assertEqual(mismatch, {"VWCE.DE": (VWCE_ISIN, ["VGWL", "VWRD", "VWRL"])})
        [b] = check_openfigi_root(mismatch, {"VWCE.DE": "VWCE"})
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "tickers:openfigi_root:VWCE.DE"))
        self.assertIn("'VWCE' staat niet bij OpenFIGI voor IE00B3RBWM25; OpenFIGI kent: VGWL, VWRD, VWRL", b["tekst"])

    def test_geen_resultaten_of_root_klopt_geeft_niets(self):
        cache = {"I1": [], "I2": [{"ticker": "VWRL"}]}
        self.assertEqual(openfigi_root_mismatches({"X.DE": ["I1"], "VWRL.AS": ["I2"], "Y": ["NIET_GECACHET"]}, cache), {})

    def test_lege_cache_is_info_nooit_opgevraagd_is_niets(self):
        uit = check_openfigi_leeg({"I1": ("XELA", "XELA"), "I2": ("ANDER", "AND")}, {"I1": []})
        self.assertEqual([(b["niveau"], b["sleutel"]) for b in uit], [(INFO, "tickers:openfigi_leeg:I1")])
        self.assertIn("OpenFIGI kent deze ISIN niet; geen extra controle mogelijk", uit[0]["tekst"])


class TestTickerInfoEnBeurs(unittest.TestCase):
    def test_onvolledige_ticker_info(self):
        details = {"A": {"valuta": "EUR", "quote_type": None}, "B": {"valuta": "EUR", "quote_type": "EQUITY"}}
        [b] = check_ticker_info_onvolledig(details, {"A": "Alfa"})
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "tickers:ticker_info:A"))
        self.assertIn("Alfa (A)", b["tekst"])
        self.assertIn("quote_type", b["tekst"])

    def test_beurs_oordeel(self):
        self.assertEqual(beurs_oordeel("XET", "GER"), "zeker")
        self.assertEqual(beurs_oordeel("EAM", "GER"), "beurs")
        self.assertEqual(beurs_oordeel("NSQ", "NMS"), "onzeker")  # NSQ staat niet in BEURS_MAP
        self.assertEqual(beurs_oordeel("XET", None), "onzeker")

    def test_samenvatting(self):
        oordeel = {"A": "zeker", "B": "zeker", "C": "onzeker", "D": "beurs"}
        [b] = check_ticker_samenvatting(oordeel, {"B": {"openfigi", "dis_acc"}})
        self.assertEqual(b["niveau"], INFO)
        self.assertEqual(b["tekst"], "4 posities: 1 zeker, 1 onzeker (beurs niet te controleren), 2 met waarschuwing "
                                     "(B: OpenFIGI, DIS/ACC; D: beurs).")
        [b] = check_ticker_samenvatting({"A": "zeker"}, {})
        self.assertEqual(b["niveau"], GOED)


def _excel(*rijen):
    return pd.DataFrame(rijen, columns=["ISIN", "Beurs", "Product", "Wisselkoers"])


class TestValutaConsistentie(unittest.TestCase):
    def test_vreemde_valuta_zonder_wisselkoers_en_andersom(self):
        excel = _excel(("US1", "TDG", "APPLE", NAN), ("NL1", "EAM", "ASML", 1.08), ("NL2", "EAM", "GOED", NAN))
        tickers = {("US1", "TDG"): "AAPL", ("NL1", "EAM"): "ASML.AS", ("NL2", "EAM"): "GOED.AS"}
        uit = check_valuta_consistentie(excel, tickers, {"AAPL": "USD", "ASML.AS": "EUR", "GOED.AS": "EUR"})
        self.assertEqual([b["sleutel"] for b in uit], ["tickers:valuta:ASML.AS", "tickers:valuta:AAPL"])
        self.assertTrue(all(b["niveau"] == LET_OP for b in uit))
        self.assertIn("Yahoo noteert AAPL in USD", uit[1]["tekst"])

    def test_onbekende_valuta_ticker_of_corporate_action_overslaan(self):
        excel = _excel(("US1", "NSQ", "X", NAN), ("US2", "DEG", "Y", NAN), ("US3", "NSQ", "Z - NON TRADEABLE", NAN))
        tickers = {("US1", "NSQ"): "X", ("US2", "DEG"): "Y", ("US3", "NSQ"): "Z"}
        self.assertEqual(check_valuta_consistentie(excel, tickers, {"Y": "USD", "Z": "USD"}), [])

    def test_zonder_excel_stil(self):
        self.assertEqual(check_valuta_consistentie(None, {}, {}), [])


def _portfolio():
    """Het echte portfolio in het klein: XELA met ISIN-wissel (NSQ), VWCE op XET als DIS gekocht, ASML."""
    kolommen = ["datum", "tijd", "product", "echte_naam", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur",
                "transactiekosten", "waarde_eur", "order_id"]
    x_oud, x_nieuw = "EXELA TECHNOLOGIES IN", "EXELA TECHNOLOGIES INC. - COMMON STOCK"
    rijen = [
        ("2021-01-08", datetime.time(17, 57), x_oud, x_oud, XELA_OUD, "NSQ", "XELA", 13, 0.41, -5.89, -0.54, -5.35, "o1"),
        ("2021-01-25", datetime.time(21, 36), x_oud, x_oud, XELA_OUD, "NSQ", "XELA", 1, 0.71, -1.21, -0.50, -0.71, "o2"),
        ("2021-01-26", M, x_oud, x_oud, XELA_OUD, "NSQ", "XELA", -14, 0.72, 10.06, NAN, 10.06, "SYN-a-0"),
        ("2021-01-26", M, x_nieuw, x_nieuw, XELA_NIEUW, "NSQ", "XELA", 4, 2.16, -8.62, NAN, -8.62, "SYN-b-0"),
        ("2021-01-27", datetime.time(16, 17), x_nieuw, x_nieuw, XELA_NIEUW, "NSQ", "XELA", -4, 1.91, 7.14, -0.51, 7.65,
         "o3"),
        ("2022-03-01", datetime.time(10, 0), VWCE_YAHOO, VWCE_DEGIRO, VWCE_ISIN, "XET", "VWCE.DE", 10, 100.0, -1001.0,
         -1.0, -1000.0, "o4"),
        ("2022-03-01", datetime.time(11, 0), "ASML", "ASML HOLDING", "NL0010273215", "EAM", "ASML.AS", 2, 500.0,
         -1001.0, -1.0, -1000.0, "o5"),
    ]
    df = pd.DataFrame(rijen, columns=kolommen)
    df["datum"] = pd.to_datetime(df["datum"])
    return df


DETAILS = {
    "VWCE.DE": {"valuta": "EUR", "quote_type": "ETF", "yahoo_beurs": "GER", "long_name": VWCE_YAHOO},
    "XELA": {"valuta": "USD", "quote_type": "EQUITY", "yahoo_beurs": "NMS", "long_name": "Exela Technologies, Inc."},
    "ASML.AS": {"valuta": "EUR", "quote_type": "EQUITY", "yahoo_beurs": "AMS", "long_name": "ASML Holding N.V."},
}
OPENFIGI = {VWCE_ISIN: OPENFIGI_VWRL, XELA_OUD: [], XELA_NIEUW: [], "NL0010273215": [{"ticker": "ASML"}]}


class TestVerwachteDiagnostiekEchtPortfolio(unittest.TestCase):
    """Data zonder LET_OP, één Splits-regel voor XELA met 1:3, Tickers: LET_OP's voor VWCE en INFO's voor XELA."""

    def setUp(self):
        self.app = Flask(__name__)

    def _per_categorie(self, meldingen, categorie):
        return [(m["niveau"], m["sleutel"]) for m in meldingen if m["categorie"] == categorie]

    def test_alle_categorieen(self):
        df = _portfolio()
        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            for b in (check_ontbrekende_kolommen(df) + check_synthetische_order_ids(df)
                      + check_corporate_action_rijen(df)):
                meld(CATEGORIE_DATA, b["niveau"], b["tekst"], sleutel=b["sleutel"])
            for b in check_isin_wissels(df):
                meld(CATEGORIE_SPLITS, b["niveau"], b["tekst"], sleutel=b["sleutel"])
            aangepast = compute_split_adjusted_shares(df)
            aangepast, koppeling = bepaal_effectieve_datums(
                aangepast, bepaal_split_boekingen(aangepast),
                {"XELA": {"2021-01-26": 1 / 3, "2022-07-26": 0.05, "2023-05-15": 0.005}})
            meld_split_koppeling(koppeling)
            waarschuwingen = [{"ticker": "VWCE.DE", "naam": "VWCE", "redenen": ["openfigi"]}]
            for b in ticker_bevindingen(aangepast, DETAILS, OPENFIGI, waarschuwingen):
                meld(CATEGORIE_TICKERS, b["niveau"], b["tekst"], sleutel=b["sleutel"])
            meldingen = haal_meldingen()

        self.assertEqual(self._per_categorie(meldingen, CATEGORIE_DATA), [])
        splits = [m for m in meldingen if m["categorie"] == CATEGORIE_SPLITS]
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["niveau"], INFO)
        self.assertIn("Reverse split 1:3 van XELA", splits[0]["tekst"])
        self.assertIn("fractie 0,67 stuk contant uitbetaald", splits[0]["tekst"])
        self.assertEqual(self._per_categorie(meldingen, CATEGORIE_TICKERS), [
            (LET_OP, "tickers:dis_acc:VWCE.DE"),
            (LET_OP, "tickers:openfigi_root:VWCE.DE"),
            (INFO, f"tickers:openfigi_leeg:{XELA_OUD}"),
            (INFO, f"tickers:openfigi_leeg:{XELA_NIEUW}"),
            (INFO, "tickers:samenvatting"),
        ])
        samenvatting = [m for m in meldingen if m["sleutel"] == "tickers:samenvatting"][0]
        self.assertEqual(samenvatting["tekst"], "3 posities: 1 zeker, 1 onzeker (beurs niet te controleren), "
                                                "1 met waarschuwing (VWCE.DE: OpenFIGI, DIS/ACC).")


class TestMeldTickers(unittest.TestCase):
    def test_meldt_in_tickers_en_breekt_niet_bij_dbfout(self):
        app = Flask(__name__)
        df = compute_split_adjusted_shares(_portfolio())
        with app.test_request_context(), redirect_stdout(io.StringIO()), \
                patch.object(po, "db_get_ticker_details", return_value=DETAILS), \
                patch.object(po, "db_get_cached_openfigi_voor_isins", return_value=OPENFIGI):
            po._meld_tickers(df, [])
            categorieen = {m["categorie"] for m in haal_meldingen()}
        self.assertIn(CATEGORIE_TICKERS, categorieen)

        with app.test_request_context(), redirect_stdout(io.StringIO()), \
                patch.object(po, "db_get_ticker_details", side_effect=RuntimeError("db weg")):
            po._meld_tickers(df, [])


if __name__ == "__main__":
    unittest.main()
