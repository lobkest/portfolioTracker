"""
Tests voor GET/POST /api/portfolio/<code>/korte-namen, reset-bijnaam en bepaal_korte_naam_voorstellen().
Database en Yahoo zijn gemockt; app.py wordt wel geimporteerd (zonder DATABASE_URL draait db_init() niet).
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie as po

VOORSTELLEN = [
    {"ticker": "ASML.AS", "huidig": "ASML HOLDING NV", "voorstel": "ASML Holding"},
    {"ticker": "ONB.AS", "huidig": "ONBEKEND", "voorstel": None},
    {"ticker": "TDT.AS", "huidig": "VANECK AEX", "voorstel": "AEX"},
]


class TestBepaalKorteNaamVoorstellen(unittest.TestCase):
    def test_voorstellen_met_family_uit_ticker_info(self):
        long_names = {"TDT.AS": "VanEck AEX UCITS ETF", "ASML.AS": "ASML Holding N.V.", "ONB.AS": None}
        details = {"TDT.AS": {"fund_family": "VanEck Asset Management B.V."}}
        with patch.object(po, "db_laad_product_per_ticker",
                          return_value={"TDT.AS": "VANECK AEX", "ASML.AS": "ASML HOLDING NV", "ONB.AS": "ONBEKEND"}), \
             patch.object(po, "haal_long_names", return_value=long_names) as mock_yahoo, \
             patch.object(po, "db_get_ticker_details", return_value=details):
            uit = po.bepaal_korte_naam_voorstellen("ABC")
        self.assertEqual(uit, [
            {"ticker": "ASML.AS", "huidig": "ASML HOLDING NV", "long_name": "ASML Holding N.V.", "voorstel": "ASML Holding"},
            {"ticker": "ONB.AS", "huidig": "ONBEKEND", "long_name": None, "voorstel": None},
            {"ticker": "TDT.AS", "huidig": "VANECK AEX", "long_name": "VanEck AEX UCITS ETF", "voorstel": "AEX"},
        ])
        mock_yahoo.assert_called_once_with(["ASML.AS", "ONB.AS", "TDT.AS"])

    def test_geen_enkele_naam_van_yahoo_is_een_fout(self):
        with patch.object(po, "db_laad_product_per_ticker", return_value={"A.AS": "A"}), \
             patch.object(po, "haal_long_names", return_value={"A.AS": None}), \
             patch.object(po, "db_get_ticker_details", return_value={}):
            with self.assertRaises(po.YahooNamenOnbeschikbaar):
                po.bepaal_korte_naam_voorstellen("ABC")

    def test_portfolio_zonder_tickers_geeft_lege_lijst_zonder_yahoo(self):
        with patch.object(po, "db_laad_product_per_ticker", return_value={}), \
             patch.object(po, "haal_long_names") as mock_yahoo:
            self.assertEqual(po.bepaal_korte_naam_voorstellen("ABC"), [])
        mock_yahoo.assert_not_called()


class TestKorteNamenRoutes(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def _patches(self, bestaat=True, voorstellen=VOORSTELLEN, voorstel_fout=None):
        m = self.app_module
        return {
            "bestaat": patch.object(m, "db_portfolio_bestaat", return_value=bestaat),
            "voorstellen": patch.object(m, "bepaal_korte_naam_voorstellen", return_value=voorstellen,
                                        side_effect=voorstel_fout),
            "wijzig": patch.object(m, "db_wijzig_bijnamen"),
            "wis": patch.object(m, "wis_portfolio_basis_cache"),
            "bouw": patch.object(m, "build_portfolio_response", return_value={"code": "ABC", "tickers": []}),
        }

    def _met(self, patches):
        mocks = {naam: p.start() for naam, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)
        return mocks

    def test_get_onbekende_code_404(self):
        self._met(self._patches(bestaat=False))
        self.assertEqual(self.client.get("/api/portfolio/ABC/korte-namen").status_code, 404)

    def test_post_onbekende_code_404_en_schrijft_niets(self):
        mocks = self._met(self._patches(bestaat=False))
        self.assertEqual(self.client.post("/api/portfolio/ABC/korte-namen").status_code, 404)
        mocks["wijzig"].assert_not_called()

    def test_get_geeft_voorstellen_en_schrijft_niets(self):
        mocks = self._met(self._patches())
        res = self.client.get("/api/portfolio/abc/korte-namen")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"namen": VOORSTELLEN})
        mocks["voorstellen"].assert_called_once_with("ABC")
        mocks["wijzig"].assert_not_called()
        mocks["wis"].assert_not_called()

    def test_get_yahoo_fout_geeft_nette_json_fout(self):
        self._met(self._patches(voorstel_fout=po.YahooNamenOnbeschikbaar()))
        res = self.client.get("/api/portfolio/ABC/korte-namen")
        self.assertEqual(res.status_code, 502)
        self.assertIn("error", res.get_json())

    def test_post_schrijft_alle_voorstellen_weg_en_slaat_none_over(self):
        mocks = self._met(self._patches())
        res = self.client.post("/api/portfolio/ABC/korte-namen", json={"namen": {"HACK.AS": "Hack"}})
        self.assertEqual(res.status_code, 200)
        mocks["wijzig"].assert_called_once_with("ABC", {"ASML.AS": "ASML Holding", "TDT.AS": "AEX"})
        mocks["wis"].assert_called_once_with("ABC")
        self.assertEqual(res.get_json(), {"code": "ABC", "tickers": []})

    def test_post_yahoo_fout_schrijft_niets(self):
        mocks = self._met(self._patches(voorstel_fout=po.YahooNamenOnbeschikbaar()))
        self.assertEqual(self.client.post("/api/portfolio/ABC/korte-namen").status_code, 502)
        mocks["wijzig"].assert_not_called()


class TestResetBijnaam(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def _reset(self, long_names):
        m = self.app_module
        with patch.object(m, "haal_long_names", return_value=long_names), \
             patch.object(m, "db_wijzig_bijnaam") as wijzig, \
             patch.object(m, "db_herstel_echte_naam") as herstel, \
             patch.object(m, "wis_portfolio_basis_cache"), \
             patch.object(m, "build_portfolio_response", return_value={"code": "ABC"}):
            res = self.client.post("/api/portfolio/ABC/reset-bijnaam", json={"ticker": "ASML.AS"})
        return res, wijzig, herstel

    def test_reset_zet_live_long_name(self):
        res, wijzig, herstel = self._reset({"ASML.AS": "ASML Holding N.V."})
        self.assertEqual(res.status_code, 200)
        wijzig.assert_called_once_with("ABC", "ASML.AS", "ASML Holding N.V.")
        herstel.assert_not_called()

    def test_reset_zonder_long_name_valt_terug_op_echte_naam(self):
        res, wijzig, herstel = self._reset({"ASML.AS": None})
        self.assertEqual(res.status_code, 200)
        wijzig.assert_not_called()
        herstel.assert_called_once_with("ABC", "ASML.AS")


if __name__ == "__main__":
    unittest.main()
