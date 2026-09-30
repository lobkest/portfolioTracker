"""
Route-level tests voor de pagina-routes: / (start), /p/<code> (portfolio)
en /analyse ('niet opslaan'). Ze geven alleen een template of een redirect
terug; get_db_connection is gepatcht om te falen als bewijs dat ze de
database niet raken.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestPaginaRoutes(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        patcher = patch.object(
            app_module, "get_db_connection",
            side_effect=AssertionError("pagina-route mag de database niet raken"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_geeft_uploadformulier_zonder_code(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('id="uploadForm"', html)
        self.assertIn('data-code=""', html)

    def test_geldige_code_geeft_pagina_met_data_code(self):
        res = self.client.get("/p/ABC")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-code="ABC"', res.get_data(as_text=True))

    def test_kleine_letters_redirecten_naar_hoofdletters(self):
        for pad in ("/p/abc", "/p/aBc"):
            res = self.client.get(pad)
            self.assertEqual(res.status_code, 302, pad)
            self.assertEqual(res.headers["Location"], "/p/ABC", pad)

    def test_ongeldige_code_redirect_naar_start_met_melding(self):
        for pad in ("/p/AB", "/p/ABCD", "/p/A1C", "/p/a-c"):
            res = self.client.get(pad)
            self.assertEqual(res.status_code, 302, pad)
            self.assertEqual(res.headers["Location"], "/?melding=ongeldige-code", pad)

    def test_analyse_geeft_pagina_zonder_code(self):
        res = self.client.get("/analyse")
        self.assertEqual(res.status_code, 200)
        self.assertIn('data-code=""', res.get_data(as_text=True))

    def test_statische_verwijzingen_zijn_absoluut_op_portfolio_pagina(self):
        html = self.client.get("/p/ABC").get_data(as_text=True)
        self.assertIn('href="/static/css/style.css"', html)
        self.assertIn('src="/static/js/app.js"', html)


if __name__ == "__main__":
    unittest.main()
