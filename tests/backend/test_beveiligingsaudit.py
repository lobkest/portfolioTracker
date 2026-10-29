"""Invoercontroles uit de beveiligingsaudit, via de Flask-testclient zonder database en zonder netwerk."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
class TestInvoerGrenzen(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        for naam, kwargs in (("db_connect", {"side_effect": AssertionError("geen database")}),):
            p = patch.object(db, naam, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def _patch_app(self, naam, **kwargs):
        p = patch.object(self.m, naam, **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def test_overlap_detail_weigert_te_lange_ticker(self):
        bereken = self._patch_app("bereken_etf_overlap_detail", return_value=[])
        antwoord = self.client.get("/api/etf-overlap-detail", query_string={"a": "X" * 21, "b": "VWRL.AS"})
        self.assertEqual(antwoord.status_code, 400)
        bereken.assert_not_called()

    def test_overlap_detail_accepteert_normale_tickers(self):
        self._patch_app("bereken_etf_overlap_detail", return_value=[])
        antwoord = self.client.get("/api/etf-overlap-detail", query_string={"a": "IWDA.AS", "b": "VWRL.AS"})
        self.assertEqual(antwoord.status_code, 200)

    def test_etf_holdings_weigert_te_lange_ticker(self):
        bereken = self._patch_app("bereken_etf_holdings_lijst", return_value=[])
        antwoord = self.client.get("/api/etf-holdings", query_string={"ticker": "X" * 21})
        self.assertEqual(antwoord.status_code, 400)
        bereken.assert_not_called()

    def _koers_bereik(self, **params):
        self._patch_app("laad_split_gecorrigeerde_transacties", return_value=pd.DataFrame({"ticker": ["IWDA.AS"]}))
        prijzen = self._patch_app("get_prices", return_value=pd.DataFrame())
        antwoord = self.client.get("/api/portfolio/ABC/ticker-koers-bereik", query_string=params)
        return antwoord, prijzen

    def test_koers_bereik_weigert_ongeldige_datum_met_400_zonder_koersen_op_te_halen(self):
        for params in ({"ticker": "IWDA.AS", "vanaf": "morgen"}, {"ticker": "IWDA.AS", "vanaf": "2024-01-01", "tot": "x"}):
            antwoord, prijzen = self._koers_bereik(**params)
            self.assertEqual(antwoord.status_code, 400)
            prijzen.assert_not_called()

    def test_koers_bereik_weigert_te_lange_ticker(self):
        antwoord, prijzen = self._koers_bereik(ticker="X" * 21, vanaf="2024-01-01")
        self.assertEqual(antwoord.status_code, 400)
        prijzen.assert_not_called()

    def test_koers_bereik_met_geldige_invoer_haalt_koersen_op(self):
        antwoord, prijzen = self._koers_bereik(ticker="IWDA.AS", vanaf="2024-01-01", tot="2024-02-01")
        self.assertEqual(antwoord.status_code, 200)
        prijzen.assert_called_once()


class TestWorkflowRechten(unittest.TestCase):
    def test_workflows_vragen_alleen_leesrechten(self):
        map_ = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".github", "workflows")
        for naam in os.listdir(map_):
            with open(os.path.join(map_, naam), encoding="utf-8") as f:
                self.assertIn("permissions:\n  contents: read", f.read(), naam)


if __name__ == "__main__":
    unittest.main()
