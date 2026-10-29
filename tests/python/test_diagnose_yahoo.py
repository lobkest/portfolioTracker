"""Tijdelijke diagnose-route voor Yahoo op Render; alles gemockt, zonder netwerk of database."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db, diagnose_yahoo
from backend.accesslog import accesslog_regel

TOKEN = "geheim-token-123"
NEP_COOKIE_WAARDE = "cookiewaarde-ABCDEF"
NEP_CRUMB = "crumbwaarde123"


def nep_sessie():
    cookie = SimpleNamespace(name="A3", value=NEP_COOKIE_WAARDE)
    sessie = MagicMock()
    sessie.cookies.jar = [cookie]

    def get(url):
        if "getcrumb" in url:
            return SimpleNamespace(status_code=200, url=url, text=NEP_CRUMB)
        return SimpleNamespace(status_code=404, url="https://consent.yahoo.com/x",
                               text=f"<html>sessie {NEP_COOKIE_WAARDE} " + "x" * 400)
    sessie.get.side_effect = get
    return sessie


def nep_yf_ticker(*args):
    t = MagicMock()
    t.info = {"currency": "USD", "quoteType": "ETF", **{f"k{i}": i for i in range(30)}}
    t.funds_data.top_holdings = pd.DataFrame({"a": [1, 2, 3]})
    t.history.return_value = pd.DataFrame({"Close": [1, 2]})
    t.history_metadata = {"symbol": "SPY", "currency": "USD"}
    return t


def nep_yq_ticker(*args):
    t = MagicMock()
    t.price = {"SPY": "Unauthorized: Invalid Crumb"}
    t.fund_top_holdings = pd.DataFrame({"a": [1, 2]})
    return t


class _MetApp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)

    def met_token(self, waarde=TOKEN):
        p = patch.dict(os.environ, {"DIAGNOSE_TOKEN": waarde})
        p.start()
        self.addCleanup(p.stop)

    def mock_yahoo(self):
        for doel, waarde in (("yf.Ticker", nep_yf_ticker), ("yahooquery.Ticker", nep_yq_ticker),
                             ("initialize_session", nep_sessie)):
            p = patch.object(*self._doel(doel), side_effect=waarde)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _doel(naam):
        if naam == "yf.Ticker":
            return diagnose_yahoo.yf, "Ticker"
        if naam == "yahooquery.Ticker":
            return diagnose_yahoo.yahooquery, "Ticker"
        return diagnose_yahoo, naam


class TestAfscherming(_MetApp):
    def test_zonder_token_in_omgeving_is_404(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIAGNOSE_TOKEN", None)
            self.assertEqual(self.client.get(f"/api/diagnose/yahoo?token={TOKEN}").status_code, 404)

    def test_verkeerd_token_is_404(self):
        self.met_token()
        self.assertEqual(self.client.get("/api/diagnose/yahoo?token=fout").status_code, 404)
        self.assertEqual(self.client.get("/api/diagnose/yahoo").status_code, 404)

    def test_te_veel_of_ongeldige_tickers_is_400(self):
        self.met_token()
        self.assertEqual(self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=A,B,C,D").status_code, 400)
        self.assertEqual(self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=A,,B").status_code, 400)
        lang = "X" * 21
        self.assertEqual(self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers={lang}").status_code, 400)


class TestDiagnose(_MetApp):
    def setUp(self):
        super().setUp()
        self.met_token()
        self.mock_yahoo()

    def test_structuur_per_ticker_en_stap(self):
        res = self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=SPY")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIs(data["tijdelijk"], True)
        stappen = data["tickers"]["SPY"]
        self.assertEqual(set(stappen), {"info", "funds_data", "history", "yahooquery_price",
                                        "yahooquery_fund_top_holdings"})
        for stap in stappen.values():
            self.assertEqual(set(stap), {"ok", "duur_s", "type_fout", "fout", "samenvatting"})
            self.assertTrue(stap["ok"])
        info = stappen["info"]["samenvatting"]
        self.assertEqual((info["aantal_sleutels"], len(info["eerste_sleutels"])), (32, 15))
        self.assertTrue(info["currency"] and info["quoteType"])
        self.assertEqual(stappen["funds_data"]["samenvatting"]["aantal_rijen"], 3)
        self.assertEqual(stappen["history"]["samenvatting"],
                         {"aantal_rijen": 2, "metadata_sleutels": ["symbol", "currency"]})
        self.assertEqual(stappen["yahooquery_price"]["samenvatting"],
                         {"type": "dict", "ruwe_tekst": "Unauthorized: Invalid Crumb"})
        self.assertEqual(stappen["yahooquery_fund_top_holdings"]["samenvatting"],
                         {"type": "DataFrame", "aantal_rijen": 2})

    def test_standaard_zijn_twee_tickers(self):
        data = self.client.get(f"/api/diagnose/yahoo?token={TOKEN}").get_json()
        self.assertEqual(list(data["tickers"]), ["SPY", "TDIV.AS"])

    def test_een_falende_stap_laat_de_rest_doorlopen(self):
        def kapot(*args):
            t = nep_yf_ticker()
            type(t).funds_data = property(lambda s: (_ for _ in ()).throw(RuntimeError("401 " + "z" * 500)))
            return t
        with patch.object(diagnose_yahoo.yf, "Ticker", side_effect=kapot):
            stappen = self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=SPY").get_json()["tickers"]["SPY"]
        self.assertFalse(stappen["funds_data"]["ok"])
        self.assertEqual(stappen["funds_data"]["type_fout"], "RuntimeError")
        self.assertEqual(len(stappen["funds_data"]["fout"]), 300)
        self.assertTrue(stappen["info"]["ok"] and stappen["history"]["ok"])

    def test_crumb_stap_bevat_geen_cookie_of_crumbwaarden(self):
        res = self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=SPY")
        crumb = res.get_json()["crumb"]
        self.assertTrue(crumb["ok"])
        fc, getcrumb = crumb["samenvatting"]
        self.assertEqual(fc["status"], 404)
        self.assertIn("consent", fc["eindlocatie"])
        self.assertEqual(fc["cookie_namen"], ["A3"])
        self.assertLessEqual(len(fc["body"]), 200)
        tekst = res.get_data(as_text=True)
        for geheim in (NEP_COOKIE_WAARDE, NEP_CRUMB, TOKEN):
            self.assertNotIn(geheim, tekst)

    def test_tellers_en_omgeving(self):
        data = self.client.get(f"/api/diagnose/yahoo?token={TOKEN}&tickers=SPY").get_json()
        self.assertEqual(set(data["omgeving"]) - {"RENDER_REGION"}, {"yfinance", "yahooquery", "curl_cffi"})
        with patch.dict(os.environ, {"RENDER_REGION": "frankfurt"}):
            self.assertEqual(diagnose_yahoo.omgeving()["RENDER_REGION"], "frankfurt")


class TestTokenNietInLogs(unittest.TestCase):
    def test_accesslog_bevat_geen_query(self):
        self.assertNotIn(TOKEN, accesslog_regel("GET", "/api/diagnose/yahoo", 200, 0.1))

    def test_maskeer_pad_maskeert_het_token(self):
        from backend.debug_utils import maskeer_pad
        tekst = maskeer_pad(f"/api/diagnose/yahoo?tickers=SPY&token={TOKEN}&x=1")
        self.assertNotIn(TOKEN, tekst)
        self.assertIn("token=***&x=1", tekst)


if __name__ == "__main__":
    unittest.main()
