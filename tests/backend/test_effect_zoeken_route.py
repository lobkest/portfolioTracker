"""/api/zoek en /api/isin-kandidaten via de Flask-testclient, zonder database en zonder netwerk."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import effect_zoeken
from backend.toegangslimiet import REGELS_ZOEKEN


class TestZoekRoutes(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        effect_zoeken.wis_zoek_cache()
        self.addCleanup(app_module.toegangslimiet.wis)
        self.addCleanup(effect_zoeken.wis_zoek_cache)
        patcher = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def yahoo(self, quotes):
        p = patch.object(effect_zoeken, "_yahoo_search", return_value=quotes)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def test_te_korte_en_te_lange_query_geven_400(self):
        yahoo = self.yahoo([])
        for q in ("", " a ", "x" * 61):
            with self.subTest(q=q):
                res = self.client.get("/api/zoek", query_string={"q": q})
                self.assertEqual(res.status_code, 400)
                self.assertIn("error", res.get_json())
        yahoo.assert_not_called()

    def test_query_op_de_grenzen_mag(self):
        self.yahoo([])
        for q in ("ab", "x" * 60):
            self.assertEqual(self.client.get("/api/zoek", query_string={"q": q}).status_code, 200)

    def test_lege_yahoo_uitslag_geeft_200_met_lege_lijst(self):
        self.yahoo([])
        res = self.client.get("/api/zoek", query_string={"q": "onbestaand"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"resultaten": [], "isin_uit_zoekopdracht": None})

    def test_resultaat_met_voorkeur_en_isin_uit_zoekopdracht(self):
        self.yahoo([{"symbol": "IWDA.L", "exchange": "LSE", "exchDisp": "London", "quoteType": "ETF", "longname": "Wereld"}])
        data = self.client.get("/api/zoek", query_string={"q": "ie00b4l5y983"}).get_json()
        self.assertEqual(data["isin_uit_zoekopdracht"], "IE00B4L5Y983")
        self.assertEqual(data["resultaten"][0]["voorkeur_ticker"], "IWDA.L")

    def test_dubbele_zoekopdracht_gebruikt_de_cache(self):
        yahoo = self.yahoo([{"symbol": "AAPL", "exchange": "NMS", "quoteType": "EQUITY", "longname": "Apple Inc."}])
        for q in ("AAPL", "aapl"):
            self.client.get("/api/zoek", query_string={"q": q})
        self.assertEqual(yahoo.call_count, 1)

    def test_isin_kandidaten_geeft_het_resultaat_door(self):
        with patch.object(self.m, "bepaal_isin_kandidaten", return_value={"zekerheid": "geen", "bron": None, "kandidaten": []}) as b:
            res = self.client.get("/api/isin-kandidaten", query_string={"ticker": "IWDA.AS", "naam": "Wereld", "isin": ""})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["zekerheid"], "geen")
        b.assert_called_once_with("IWDA.AS", "Wereld", "")

    def test_isin_kandidaten_zonder_of_met_te_lange_ticker_geeft_400(self):
        for ticker in ("", "T" * 21):
            res = self.client.get("/api/isin-kandidaten", query_string={"ticker": ticker})
            self.assertEqual(res.status_code, 400)

    def test_na_het_maximum_komt_een_429_en_andere_ip_blijft_vrij(self):
        self.yahoo([])
        maximum = REGELS_ZOEKEN[0].maximum
        for _ in range(maximum):
            res = self.client.get("/api/zoek", query_string={"q": "abc"}, environ_base={"REMOTE_ADDR": "1.1.1.1"})
            self.assertEqual(res.status_code, 200)
        res = self.client.get("/api/zoek", query_string={"q": "abc"}, environ_base={"REMOTE_ADDR": "1.1.1.1"})
        self.assertEqual(res.status_code, 429)
        self.assertIn("Retry-After", res.headers)
        res = self.client.get("/api/isin-kandidaten", query_string={"ticker": "T"}, environ_base={"REMOTE_ADDR": "1.1.1.1"})
        self.assertEqual(res.status_code, 429)
        res = self.client.get("/api/zoek", query_string={"q": "abc"}, environ_base={"REMOTE_ADDR": "2.2.2.2"})
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
