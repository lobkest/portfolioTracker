"""Routes /api/portfolio/<code>/box3 en /api/box3/bereken, en box3_basis bij 'niet opslaan'.
Zonder database: app.py draait db_init() alleen met DATABASE_URL, en de DB-functies worden gemockt."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _transacties_df():
    return pd.DataFrame([{
        "datum": pd.Timestamp("2024-03-01"), "tijd": "10:00", "product": "Test", "isin": "NL0000000001",
        "beurs": "EAM", "ticker": "AAA", "aantal": 10.0, "koers": 100.0, "totaal_eur": -1000.0,
        "transactiekosten": -2.0, "autofx_kosten": None, "waarde_eur": -998.0,
    }])


def _resultaat():
    dagen = pd.date_range("2024-03-01", "2024-12-31", freq="D")
    return pd.DataFrame({"waarde": 1100.0, "geinvesteerd": 1000.0}, index=dagen)


class TestBox3Routes(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_onbekende_code_geeft_404(self):
        with patch.object(self.app_module, "laad_transacties_en_resultaat", return_value=(None, None)):
            res = self.client.get("/api/portfolio/ZZTEST/box3")
        self.assertEqual(res.status_code, 404)

    def test_zonder_koersdata_400(self):
        with patch.object(self.app_module, "laad_transacties_en_resultaat", return_value=(_transacties_df(), None)):
            res = self.client.get("/api/portfolio/ZZTEST/box3")
        self.assertEqual(res.status_code, 400)

    def test_basis_zonder_rekeningoverzicht(self):
        with patch.object(self.app_module, "laad_transacties_en_resultaat",
                          return_value=(_transacties_df(), _resultaat())), \
             patch.object(self.app_module, "db_get_dividenden", return_value=[]) as mock_div:
            res = self.client.get("/api/portfolio/zztest/box3")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        mock_div.assert_called_once_with("ZZTEST")
        self.assertFalse(data["dividend_beschikbaar"])
        self.assertEqual(data["jaren"][0]["jaar"], 2024)

    def test_bereken_zonder_database(self):
        basis = {"jaren": [{"jaar": 2025, "lopend": True, "waarde_begin": 10000, "waarde_eind": 15000,
                            "netto_inleg": 0, "kosten": 0, "kosten_onvolledig": False, "dividend_bruto": None,
                            "dividendbelasting": None, "gerealiseerd": 0}],
                 "verkopen": [], "latente_winst": 0, "dividend_beschikbaar": False}
        import db
        with patch.object(db, "db_connect", side_effect=AssertionError("database geraakt")):
            res = self.client.post("/api/box3/bereken", json={"basis": basis, "invoer": {"fiscale_partner": False}})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["jaren"][0]["aanwas"]["belasting"], 1152.0)
        self.assertIn("parameters_stand", data)

    def test_negatieve_invoer_geeft_400(self):
        res = self.client.post("/api/box3/bereken", json={"basis": {"jaren": []}, "invoer": {"banktegoeden": -5}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("negatief", res.get_json()["error"])

    def test_spaarrente_boven_20_procent_geeft_400(self):
        res = self.client.post("/api/box3/bereken", json={"basis": {"jaren": []}, "invoer": {"spaarrente_pct": 25}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("spaarrente", res.get_json()["error"])

    def test_partner_geen_boolean_geeft_400(self):
        res = self.client.post("/api/box3/bereken", json={"basis": {"jaren": []}, "invoer": {"fiscale_partner": "ja"}})
        self.assertEqual(res.status_code, 400)

    def test_kapotte_basis_geeft_400(self):
        res = self.client.post("/api/box3/bereken", json={"basis": {"jaren": [{"jaar": 2025}]}, "invoer": {}})
        self.assertEqual(res.status_code, 400)

    def test_niet_opslaan_vraagt_box3_basis_met_dividend(self):
        dividenden = [{"datum": "2024-05-01", "bruto_eur": 10.0, "belasting_eur": -1.5, "netto_eur": 8.5}]
        am = self.app_module
        with patch.object(am, "ticker_resolutie_niet_opslaan", return_value=({}, [], [])), \
             patch.object(am, "bepaal_product_per_ticker", return_value={}), \
             patch.object(am, "bouw_transacties_df_niet_opslaan", return_value=_transacties_df()), \
             patch.object(am, "bereken_kassaldo", return_value=None), \
             patch.object(am, "verwerk_dividend_zonder_opslaan", return_value=dividenden) as mock_div, \
             patch.object(am, "analyze_transacties", return_value={"box3_basis": {"jaren": []}}) as mock_analyse, \
             patch.object(am, "meld_valuta_consistentie"), \
             patch.object(am, "transacties_overzicht_uit_df", return_value=[]), \
             patch.object(am, "bouw_dividend_samenvatting", return_value=None):
            result = am._analyseer_zonder_opslaan(pd.DataFrame(), pd.DataFrame({"x": [1]}), "")
        mock_div.assert_called_once()  # één keer voor dividend én box 3
        self.assertTrue(mock_analyse.call_args.kwargs["box3"])
        self.assertEqual(mock_analyse.call_args.kwargs["box3_dividenden"], dividenden)
        self.assertIn("box3_basis", result)


if __name__ == "__main__":
    unittest.main()
