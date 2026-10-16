"""Privacypagina, footer, disclaimers en zelfgehoste scripts, via de Flask-testclient zonder database."""
import os
import re
import sys
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import db

PAGINAS = ("/", "/p/ABC", "/analyse", "/privacy")
DISCLAIMER = "Indicatie, geen financieel advies."
VENDOR_VOLGORDE = [
    "chart.umd.min.js", "hammer.min.js", "chartjs-plugin-zoom.min.js", "chartjs-plugin-datalabels.min.js",
    "chartjs-plugin-annotation.min.js", "luxon.min.js", "chartjs-adapter-luxon.umd.min.js",
]
# Tags waarmee een browser zelf iets bij een andere partij ophaalt (een <a href> is alleen een link).
LAAD_ATTRIBUTEN = {"script": "src", "link": "href", "img": "src", "iframe": "src", "source": "src", "video": "src",
                   "audio": "src", "embed": "src", "object": "data"}
# Bestanden in static/js die browseropslag noemen (menu.js alleen in een comment); de rest gaat via sessieOpslag()
# en lokaleOpslag() uit gedeeld.js.
BROWSEROPSLAG_BESTANDEN = {"gedeeld.js", "overdracht.js", "menu.js"}
BROWSEROPSLAG_GEBRUIKERS = {"sessieOpslag(": {"start.js", "app.js"}, "lokaleOpslag(": {"instellingen.js"}}
# <link rel=...> die zelf iets ophaalt (canonical is alleen een verwijzing).
LAAD_REL = ("stylesheet", "icon", "manifest", "preload", "prefetch")


class _LaadAdressen(HTMLParser):
    def __init__(self):
        super().__init__()
        self.adressen = []

    def handle_starttag(self, tag, attrs):
        attribuut = LAAD_ATTRIBUTEN.get(tag)
        waarde = dict(attrs).get(attribuut) if attribuut else None
        if tag == "link" and not any(rel in (dict(attrs).get("rel") or "") for rel in LAAD_REL):
            return
        if waarde:
            self.adressen.append((tag, waarde))


def laad_adressen(html):
    parser = _LaadAdressen()
    parser.feed(html)
    return parser.adressen


def is_extern(adres):
    return adres.lower().startswith(("http://", "https://", "//"))


class _MetApp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.client = app_module.app.test_client()
        patcher = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def html(self, pad):
        res = self.client.get(pad)
        self.assertEqual(res.status_code, 200, pad)
        return res.get_data(as_text=True)


class TestPrivacyPagina(_MetApp):
    def test_pagina_heeft_datum_beschrijving_en_eigen_canonical(self):
        res = self.client.get("/privacy", base_url="https://voorbeeld.test")
        html = res.get_data(as_text=True)
        self.assertRegex(html, r"Laatst bijgewerkt: \d{1,2} \w+ \d{4}")
        self.assertIn('<meta name="description" content="', html)
        self.assertIn('<link rel="canonical" href="https://voorbeeld.test/privacy">', html)
        self.assertNotIn("noindex", html)

    def test_bewaartermijn_komt_uit_de_constante(self):
        import bewaartermijn
        self.assertIn(f"Tot {bewaartermijn.BEWAARTERMIJN_MAANDEN} maanden na je laatste gebruik", self.html("/privacy"))

    def test_noemt_de_onderwerpen_uit_de_opdracht(self):
        html = self.html("/privacy")
        for tekst in ("Frankfurt", "6 uur", "7 dagen", "Niet opslaan", "Yahoo Finance", "OpenFIGI", "iShares", "VanEck",
                      "https://autoriteitpersoonsgegevens.nl", "Portfolio verwijderen", "Geen financieel advies",
                      "geen cookies"):
            self.assertIn(tekst, html)

    def test_geen_contactgegevens(self):
        html = self.html("/privacy")
        self.assertNotIn("mailto:", html)
        self.assertNotIn("@", html)

    def test_genoemde_browsersleutels_zijn_de_echte_sleutels(self):
        html = self.html("/privacy")
        with open(os.path.join(ROOT, "static", "js", "overdracht.js"), encoding="utf-8") as f:
            overdracht = re.search(r'OVERDRACHT_SLEUTEL = "([^"]+)"', f.read()).group(1)
        with open(os.path.join(ROOT, "static", "js", "menu.js"), encoding="utf-8") as f:
            ontwikkel = re.search(r'ONTWIKKEL_OPSLAG_SLEUTEL = "([^"]+)"', f.read()).group(1)
        self.assertIn(f"<code>{overdracht}</code>", html)
        self.assertIn(f"<code>{ontwikkel}</code>", html)

    def test_browseropslag_en_cookies_komen_alleen_voor_waar_de_privacypagina_ze_noemt(self):
        gevonden = set()
        js_map = os.path.join(ROOT, "static", "js")
        for map_pad, _mappen, namen in os.walk(js_map):
            for naam in namen:
                with open(os.path.join(map_pad, naam), encoding="utf-8") as f:
                    tekst = f.read()
                self.assertNotIn("document.cookie", tekst, naam)
                if re.search(r"localStorage|sessionStorage|indexedDB", tekst):
                    gevonden.add(os.path.relpath(os.path.join(map_pad, naam), js_map))
        self.assertEqual(gevonden, BROWSEROPSLAG_BESTANDEN)

    def test_de_opslaghulpen_worden_alleen_door_de_bekende_bestanden_gebruikt(self):
        for hulp, verwacht in BROWSEROPSLAG_GEBRUIKERS.items():
            gebruikers = set()
            for map_pad, _mappen, namen in os.walk(os.path.join(ROOT, "static", "js")):
                for naam in namen:
                    with open(os.path.join(map_pad, naam), encoding="utf-8") as f:
                        tekst = f.read()
                    if hulp in tekst and naam != "gedeeld.js":
                        gebruikers.add(naam)
            self.assertEqual(gebruikers, verwacht, hulp)


class TestFooterEnTeksten(_MetApp):
    def test_elke_pagina_heeft_de_footer(self):
        for pad in PAGINAS:
            html = self.html(pad)
            self.assertIn('<footer class="sitevoet">', html, pad)
            self.assertIn('<a href="/privacy">Privacy</a>', html, pad)
            self.assertIn("Geen financieel advies", html, pad)

    def test_uploadknop_heeft_de_bewaartermijn_en_privacylink(self):
        html = self.html("/")
        self.assertIn('na 6 maanden zonder gebruik verwijderd. <a href="/privacy">Privacy</a>', html)

    def test_verwijderblok_noemt_wat_er_wordt_gewist(self):
        html = self.html("/p/ABC")
        self.assertIn("Dit verwijdert direct en permanent al je transacties, dividenden, rekeningregels en "
                      "upload-meldingen.", html)

    def test_instellingen_heeft_de_privacylink_voor_het_mobiele_dashboard(self):
        html = self.html("/p/ABC")
        blok = html[html.index('id="tab-instellingen"'):html.index('id="tab-instellingen-bestanden"')]
        self.assertIn('<a href="/privacy">Privacy</a>', blok)

    def test_disclaimer_staat_in_de_juiste_tabbladen(self):
        html = self.html("/p/ABC")
        blokken = re.split(r'(?=<div id="tab-)', html)
        per_tab = {re.match(r'<div id="tab-([\w-]+)"', b).group(1): b for b in blokken if b.startswith('<div id="tab-')}
        for tab in ("rendement", "statistieken", "prognose", "prognose-huidig", "box3"):
            self.assertIn(DISCLAIMER, per_tab[tab], tab)
        self.assertEqual(html.count(DISCLAIMER), 5)


class TestZelfgehosteScripts(_MetApp):
    def test_geen_enkele_pagina_laadt_iets_van_een_ander_domein(self):
        for pad in PAGINAS:
            for tag, adres in laad_adressen(self.html(pad)):
                self.assertFalse(is_extern(adres), f"{pad}: <{tag}> laadt {adres}")

    def test_waakhond_ziet_een_extern_script(self):
        adressen = laad_adressen('<script src="https://cdnjs.cloudflare.com/x.js"></script><a href="https://a.nl">')
        self.assertEqual([a for a in adressen if is_extern(a[1])], [("script", "https://cdnjs.cloudflare.com/x.js")])

    def test_geen_extern_adres_in_de_eigen_css_en_js(self):
        for submap, patroon in (("css", r"url\(\s*['\"]?(https?:)?//|@import"), ("js", r"https?://(?!www\.w3\.org)")):
            for map_pad, _mappen, namen in os.walk(os.path.join(ROOT, "static", submap)):
                for naam in namen:
                    with open(os.path.join(map_pad, naam), encoding="utf-8") as f:
                        for nummer, regel in enumerate(f, 1):
                            if re.search(patroon, regel) and not regel.lstrip().startswith(("//", "/*", "*")):
                                self.fail(f"{naam}:{nummer} verwijst naar een extern adres: {regel.strip()[:80]}")

    def test_vendor_scripts_worden_in_dezelfde_volgorde_geladen_en_bestaan(self):
        scripts = [a for t, a in laad_adressen(self.html("/p/ABC")) if t == "script" and "/static/vendor/" in a]
        self.assertEqual([re.search(r"/static/vendor/([^?]+)", a).group(1) for a in scripts], VENDOR_VOLGORDE)
        for naam in VENDOR_VOLGORDE:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "vendor", naam)), naam)

    def test_vendor_scripts_krijgen_een_inhoudsversie(self):
        scripts = [a for t, a in laad_adressen(self.html("/p/ABC")) if "/static/vendor/" in a]
        self.assertTrue(scripts)
        for adres in scripts:
            self.assertRegex(adres, r"\?v=[0-9a-f]{10}$")

    def test_elke_vendor_library_heeft_een_licentiebestand(self):
        licenties = [n for n in os.listdir(os.path.join(ROOT, "static", "vendor")) if n.startswith("LICENSE")]
        self.assertEqual(len(licenties), len(VENDOR_VOLGORDE))

    def test_startpagina_laadt_geen_grafiekscripts(self):
        self.assertNotIn("/static/vendor/", self.html("/"))


if __name__ == "__main__":
    unittest.main()
