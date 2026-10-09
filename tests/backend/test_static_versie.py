import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, render_template_string

from static_versie import StaticVersies, bestand_hash, registreer_static_versies


def _schrijf(pad, inhoud):
    with open(pad, "w", encoding="utf-8") as f:
        f.write(inhoud)


class TestStaticVersie(unittest.TestCase):
    def setUp(self):
        self.map = tempfile.TemporaryDirectory()
        self.addCleanup(self.map.cleanup)
        os.makedirs(os.path.join(self.map.name, "js"))
        self.pad = os.path.join(self.map.name, "js", "a.js")
        _schrijf(self.pad, "console.log(1);")

    def _render(self):
        app = Flask(__name__, static_folder=self.map.name, static_url_path="/static")
        registreer_static_versies(app)
        with app.test_request_context():
            return render_template_string("<script src=\"{{ url_for('static', filename='js/a.js') }}\"></script>")

    def test_gerenderde_template_heeft_versie(self):
        html = self._render()
        self.assertIn(f'src="/static/js/a.js?v={bestand_hash(self.pad)}"', html)

    def test_versie_verandert_met_inhoud(self):
        oud = self._render()
        _schrijf(self.pad, "console.log(2);")
        nieuw = self._render()
        self.assertIn("?v=", nieuw)
        self.assertNotEqual(oud, nieuw)

    def test_versie_wordt_per_bestand_gecachet(self):
        versies = StaticVersies(self.map.name)
        eerste = versies.versie("js/a.js")
        _schrijf(self.pad, "console.log(2);")
        self.assertEqual(versies.versie("js/a.js"), eerste)

    def test_ontbrekend_bestand_krijgt_geen_versie(self):
        app = Flask(__name__, static_folder=self.map.name, static_url_path="/static")
        registreer_static_versies(app)
        with app.test_request_context():
            html = render_template_string("{{ url_for('static', filename='js/bestaat_niet.js') }}")
        self.assertEqual(html, "/static/js/bestaat_niet.js")

    def test_echte_app_zet_versie_op_static_urls(self):
        import app as app_module
        html = app_module.app.test_client().get("/").get_data(as_text=True)
        self.assertRegex(html, r'href="/static/css/style\.css\?v=[0-9a-f]{10}"')


if __name__ == "__main__":
    unittest.main()
