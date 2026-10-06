"""
Route-level tests voor de pagina-routes: / (start), /p/<code> (portfolio)
en /analyse ('niet opslaan'). Ze geven alleen een template of een redirect
terug; db_connect is gepatcht om te falen als bewijs dat ze de
database niet raken.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestPaginaRoutes(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import db
        self.app_module = app_module
        self.client = app_module.app.test_client()
        patcher = patch.object(
            db, "db_connect",
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
        self.assertNotIn('id="dashboardSection"', html)

    def test_start_laadt_geen_dashboard_scripts(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('src="/static/js/start.js"', html)
        self.assertNotIn("app.js", html)
        self.assertNotIn("chart.umd", html)

    def test_geldige_code_geeft_pagina_met_data_code(self):
        res = self.client.get("/p/ABC")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('data-code="ABC"', html)
        self.assertIn('id="dashboardSection"', html)
        self.assertNotIn('id="uploadForm"', html)
        self.assertNotIn("start.js", html)

    def test_maxlength_van_nieuwe_code_komt_uit_code_length(self):
        for pad in ("/p/ABC", "/analyse"):
            html = self.client.get(pad).get_data(as_text=True)
            self.assertIn(f'id="nieuweCodeInput" placeholder="Nieuwe code" maxlength="{self.app_module.CODE_LENGTH}"', html, pad)

    def test_beide_paginas_hebben_de_gedeelde_onderdelen(self):
        for pad in ("/", "/p/ABC", "/analyse"):
            html = self.client.get(pad).get_data(as_text=True)
            self.assertIn('id="laadOverlay"', html, pad)
            self.assertIn('href="/static/css/style.css"', html, pad)
            for script in ("navigatie.js", "overdracht.js", "gedeeld.js", "infotip.js"):
                self.assertIn(f'src="/static/js/{script}"', html, pad)

    def test_gedeelde_scripts_staan_voor_het_paginascript(self):
        for pad, paginascript in (("/", "start.js"), ("/p/ABC", "app.js")):
            html = self.client.get(pad).get_data(as_text=True)
            self.assertIn(f'src="/static/js/{paginascript}"', html, pad)
            self.assertLess(html.index("gedeeld.js"), html.index(paginascript), pad)
            self.assertLess(html.index("overdracht.js"), html.index(paginascript), pad)

    def test_elk_element_id_uit_de_js_bestaat_op_de_pagina(self):
        # De top-level listeners crashen op een ontbrekend element (getElementById geeft dan null).
        import re
        basis = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for pad, paginascript in (("/", "start.js"), ("/p/ABC", "app.js")):
            html = self.client.get(pad).get_data(as_text=True)
            ids_html = set(re.findall(r'id="([^"]+)"', html))
            scripts = re.findall(r'src="/static/js/([^"]+)"', html)
            self.assertIn(paginascript, scripts, pad)
            bronnen = {}
            for script in scripts:
                with open(os.path.join(basis, "static", "js", *script.split("/")), encoding="utf-8") as f:
                    bronnen[script] = f.read()
            # Elementen die de JS zelf aanmaakt staan niet in de template.
            alle_js = "\n".join(bronnen.values())
            for script, js in bronnen.items():
                ids_js = set(re.findall(r'getElementById\("([^"]+)"\)', js))
                zelf_gemaakt = {i for i in ids_js if re.search(rf'\.id = "{re.escape(i)}"', alle_js)}
                self.assertEqual(ids_js - ids_html - zelf_gemaakt, set(), f"{script} op {pad}")

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
        html = res.get_data(as_text=True)
        self.assertIn('data-code=""', html)
        self.assertIn('id="dashboardSection"', html)


if __name__ == "__main__":
    unittest.main()
