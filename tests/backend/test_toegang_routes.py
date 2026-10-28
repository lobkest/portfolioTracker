"""Limieten, 404's, uploadgrenzen, headers, robots/sitemap en meta-tags via de Flask-testclient, zonder database."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
from portfolio_admin import (
    GeenVrijeCode, UploadHoortBijBestaandePortfolio, MELDING_UPLOAD_HOORT_BIJ_BESTAANDE, MAX_BIJNAAM_LENGTE,
)
from toegangslimiet import REGELS_FOUTE_CODE, REGELS_FOUTE_CODE_GLOBAAL, REGELS_GEVOELIGE_ACTIE, REGELS_UPLOAD


def XFF_PER_BEZOEKER(adres):
    return f"nep, {adres}, 104.22.109.68, 10.0.0.1"


def ip(adres):
    return {"environ_base": {"REMOTE_ADDR": adres}}


class _MetApp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        patcher = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_app(self, naam, **kwargs):
        p = patch.object(self.m, naam, **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock


class TestFouteCodeLimiet(_MetApp):
    def setUp(self):
        super().setUp()
        self.patch_app("laad_split_gecorrigeerde_transacties", return_value=None)
        self.patch_app("build_portfolio_response", return_value=None)
        self.patch_app("db_portfolio_bestaat", return_value=False)

    def test_onbekende_code_geeft_404(self):
        self.assertEqual(self.client.get("/api/portfolio/ABC", **ip("1.1.1.1")).status_code, 404)

    def test_na_het_maximum_komt_een_429_met_minuten_en_retry_after(self):
        maximum = REGELS_FOUTE_CODE[0].maximum
        for _ in range(maximum):
            self.assertEqual(self.client.get("/api/portfolio/ABC", **ip("1.1.1.1")).status_code, 404)
        res = self.client.get("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.assertEqual(res.status_code, 429)
        self.assertIn("over 10 minuten", res.get_json()["error"])
        self.assertEqual(res.headers["Retry-After"], str(REGELS_FOUTE_CODE[0].venster_seconden))

    def test_andere_ip_is_niet_geblokkeerd(self):
        for _ in range(REGELS_FOUTE_CODE[0].maximum):
            self.client.get("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.assertEqual(self.client.get("/api/portfolio/ABC", **ip("2.2.2.2")).status_code, 404)

    def test_geblokkeerd_ip_kan_ook_geen_geldige_code_ophalen(self):
        for _ in range(REGELS_FOUTE_CODE[0].maximum):
            self.client.get("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.patch_app("build_portfolio_response", return_value={"code": "XYZ"})
        self.assertEqual(self.client.get("/api/portfolio/XYZ", **ip("1.1.1.1")).status_code, 429)

    def test_geblokkeerde_pogingen_verlengen_de_blokkade_niet(self):
        for _ in range(REGELS_FOUTE_CODE[0].maximum + 5):
            self.client.get("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.assertEqual(len(self.m.toegangslimiet._gebeurtenissen[("foute_code", "1.1.1.1")]),
                         REGELS_FOUTE_CODE[0].maximum)

    def test_globale_limiet_blokkeert_ook_nieuwe_ips(self):
        for i in range(REGELS_FOUTE_CODE_GLOBAAL[0].maximum):
            self.client.get("/api/portfolio/ABC", **ip(f"10.0.{i // 250}.{i % 250 + 1}"))
        res = self.client.get("/api/portfolio/ABC", **ip("9.9.9.9"))
        self.assertEqual(res.status_code, 429)
        self.assertIn("tijdelijk niet mogelijk", res.get_json()["error"])

    def test_geldige_code_telt_niet_als_fout(self):
        self.patch_app("build_portfolio_response", return_value={"code": "XYZ"})
        self.patch_app("_markeer_gebruikt")
        for _ in range(REGELS_FOUTE_CODE[0].maximum + 5):
            self.assertEqual(self.client.get("/api/portfolio/XYZ", **ip("1.1.1.1")).status_code, 200)

    def test_onbekende_positie_bij_geldige_code_telt_niet_als_foute_code(self):
        self.patch_app("ticker_zekerheid_groepen", return_value=[])
        for _ in range(REGELS_FOUTE_CODE[0].maximum + 2):
            res = self.client.get("/api/portfolio/XYZ/ticker-zekerheid/positie?isin=X&beurs=Y", **ip("1.1.1.1"))
            self.assertEqual(res.status_code, 404)

    def test_pagina_route_telt_en_blokkeert_niet(self):
        for _ in range(REGELS_FOUTE_CODE[0].maximum):
            self.client.get("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.assertEqual(self.client.get("/p/ABC", **ip("1.1.1.1")).status_code, 200)

    def test_alle_code_routes_geven_404_bij_onbekende_code(self):
        self.patch_app("haal_portfolio_basis", return_value=(None, None, None))
        self.patch_app("bouw_verdeling_over_tijd", return_value=None)
        self.patch_app("laad_transacties_en_resultaat", return_value=(None, None))
        self.patch_app("ticker_zekerheid_groepen", return_value=None)
        self.patch_app("bouw_transacties_lijst", return_value=None)
        routes = [
            ("get", "/api/portfolio/ABC"),
            ("get", "/api/portfolio/ABC/verrijking"),
            ("get", "/api/portfolio/ABC/verdeling-over-tijd?dimensie=land"),
            ("get", "/api/portfolio/ABC/benchmark-vergelijking"),
            ("get", "/api/portfolio/ABC/rendement-over-tijd"),
            ("get", "/api/portfolio/ABC/koersoverzicht"),
            ("get", "/api/portfolio/ABC/box3"),
            ("get", "/api/portfolio/ABC/ticker-koers-bereik"),
            ("get", "/api/portfolio/ABC/ticker-zekerheid/lijst"),
            ("get", "/api/portfolio/ABC/ticker-zekerheid/positie"),
            ("post", "/api/portfolio/ABC/ticker-zekerheid/wijzig"),
            ("get", "/api/portfolio/ABC/ticker-zekerheid/alle-prijzen"),
            ("get", "/api/portfolio/ABC/dividend"),
            ("get", "/api/portfolio/ABC/upload-meldingen"),
            ("get", "/api/portfolio/ABC/dividend-verwachting"),
            ("get", "/api/portfolio/ABC/historisch-rendement"),
            ("get", "/api/portfolio/ABC/transacties"),
            ("post", "/api/portfolio/ABC/bijnaam"),
            ("post", "/api/portfolio/ABC/bijnamen"),
            ("post", "/api/portfolio/ABC/reset-bijnaam"),
            ("get", "/api/portfolio/ABC/korte-namen"),
            ("post", "/api/portfolio/ABC/korte-namen"),
            ("delete", "/api/portfolio/ABC"),
            ("post", "/api/portfolio/ABC/wijzig-code"),
            ("post", "/api/portfolio/ABC/bijwerken"),
        ]
        for methode, pad in routes:
            with self.subTest(pad=pad, methode=methode):
                self.m.toegangslimiet.wis()
                kwargs = {"json": {"ticker": "X", "bijnaam": "Y", "namen": {}, "nieuwe_code": "XYZ", "isin": "I"}} \
                    if methode == "post" else {}
                res = getattr(self.client, methode)(pad, **kwargs)
                self.assertEqual(res.status_code, 404)
                self.assertIn("Geen portfolio gevonden", res.get_json()["error"])


class TestGevoeligeActies(_MetApp):
    def test_verwijderen_van_onbekende_code_is_404_en_verwijdert_niets(self):
        self.patch_app("db_portfolio_bestaat", return_value=False)
        verwijder = self.patch_app("db_delete_portfolio")
        self.assertEqual(self.client.delete("/api/portfolio/ABC").status_code, 404)
        verwijder.assert_not_called()

    def test_verwijderen_van_bestaande_code_lukt(self):
        self.patch_app("db_portfolio_bestaat", return_value=True)
        verwijder = self.patch_app("db_delete_portfolio")
        self.patch_app("wis_portfolio_basis_cache")
        self.assertEqual(self.client.delete("/api/portfolio/ABC").get_json(), {"success": True})
        verwijder.assert_called_once_with("ABC")

    def test_na_het_maximum_per_uur_komt_een_429_ongeacht_de_uitkomst(self):
        self.patch_app("db_portfolio_bestaat", return_value=False)
        for _ in range(REGELS_GEVOELIGE_ACTIE[0].maximum):
            self.client.delete("/api/portfolio/ABC", **ip("1.1.1.1"))
        res = self.client.delete("/api/portfolio/ABC", **ip("1.1.1.1"))
        self.assertEqual(res.status_code, 429)
        self.assertIn("over 60 minuten", res.get_json()["error"])

    def test_wijzig_code_en_verwijderen_delen_de_teller(self):
        self.patch_app("db_portfolio_bestaat", return_value=True)
        self.patch_app("db_delete_portfolio")
        self.patch_app("wis_portfolio_basis_cache")
        self.patch_app("db_wijzig_portfolio_code", return_value=(False, "Code 'XYZ' is al in gebruik."))
        for _ in range(REGELS_GEVOELIGE_ACTIE[0].maximum - 1):
            self.client.delete("/api/portfolio/ABC")
        res = self.client.post("/api/portfolio/ABC/wijzig-code", json={"nieuwe_code": "XYZ"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Code 'XYZ' is al in gebruik."))
        self.assertEqual(self.client.post("/api/portfolio/ABC/wijzig-code", json={"nieuwe_code": "XYZ"}).status_code,
                         429)

    def test_gewone_routes_vallen_niet_onder_de_gevoelige_limiet(self):
        self.patch_app("build_portfolio_response", return_value={"code": "ABC"})
        self.patch_app("_markeer_gebruikt")
        for _ in range(REGELS_GEVOELIGE_ACTIE[0].maximum + 3):
            self.assertEqual(self.client.get("/api/portfolio/ABC").status_code, 200)


class TestBijnaamValidatie(_MetApp):
    def setUp(self):
        super().setUp()
        self.patch_app("db_portfolio_bestaat", return_value=True)
        self.wijzig = self.patch_app("db_wijzig_bijnaam")
        self.wijzig_alle = self.patch_app("db_wijzig_bijnamen")
        self.patch_app("wis_portfolio_basis_cache")
        self.patch_app("build_portfolio_response", return_value={"code": "ABC"})

    def test_te_lange_bijnaam_geeft_400(self):
        res = self.client.post("/api/portfolio/ABC/bijnaam", json={"ticker": "X", "bijnaam": "a" * (MAX_BIJNAAM_LENGTE + 1)})
        self.assertEqual(res.status_code, 400)
        self.wijzig.assert_not_called()

    def test_bijnaam_op_het_maximum_mag(self):
        res = self.client.post("/api/portfolio/ABC/bijnaam", json={"ticker": "X", "bijnaam": "a" * MAX_BIJNAAM_LENGTE})
        self.assertEqual(res.status_code, 200)

    def test_te_lange_bijnaam_in_bulk_geeft_400_en_schrijft_niets(self):
        res = self.client.post("/api/portfolio/ABC/bijnamen",
                               json={"namen": {"A": "ok", "B": "b" * (MAX_BIJNAAM_LENGTE + 1)}})
        self.assertEqual(res.status_code, 400)
        self.wijzig_alle.assert_not_called()

    def test_zonder_json_body_geeft_400_in_plaats_van_crash(self):
        self.assertEqual(self.client.post("/api/portfolio/ABC/bijnaam").status_code, 400)
        self.assertEqual(self.client.post("/api/portfolio/ABC/reset-bijnaam").status_code, 400)


class TestUploadGrenzen(_MetApp):
    def _upload(self, adres="1.1.1.1", inhoud=b"x", naam="a.xlsx"):
        return self.client.post("/upload", data={"bestand1": (io.BytesIO(inhoud), naam)},
                                content_type="multipart/form-data", **ip(adres))

    def test_upload_is_beperkt_per_ip(self):
        self.patch_app("_upload_impl", side_effect=lambda *_: self.m.jsonify({"ok": True}))
        for _ in range(REGELS_UPLOAD[0].maximum):
            self.assertEqual(self._upload().status_code, 200)
        res = self._upload()
        self.assertEqual(res.status_code, 429)
        self.assertIn("te veel bestanden", res.get_json()["error"])
        self.assertEqual(self._upload("2.2.2.2").status_code, 200)

    def test_te_groot_bestand_geeft_json_413_in_het_nederlands(self):
        res = self._upload(inhoud=b"x" * (self.m.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(res.status_code, 413)
        self.assertIn("te groot", res.get_json()["error"])

    def test_bijwerken_geeft_ook_json_413(self):
        self.patch_app("db_portfolio_bestaat", return_value=True)
        res = self.client.post("/api/portfolio/ABC/bijwerken",
                               data={"bestand1": (io.BytesIO(b"x" * (self.m.MAX_UPLOAD_BYTES + 1)), "a.xlsx")},
                               content_type="multipart/form-data")
        self.assertEqual(res.status_code, 413)
        self.assertIn("te groot", res.get_json()["error"])

    def test_geen_excel_geeft_400_met_melding(self):
        res = self._upload(inhoud=b"geen zip", naam="a.xlsx")
        self.assertEqual(res.status_code, 400)
        self.assertIn("geen .xlsx", res.get_json()["error"])

    def test_deelverzameling_geeft_400_zonder_code_en_telt_als_foute_poging(self):
        self.patch_app("lees_transacties_excel", return_value=pd.DataFrame())
        self.patch_app("voeg_koers_eur_toe", side_effect=lambda df: df)
        self.patch_app("_upload_opslaan", side_effect=UploadHoortBijBestaandePortfolio(MELDING_UPLOAD_HOORT_BIJ_BESTAANDE))
        res = self._upload()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], MELDING_UPLOAD_HOORT_BIJ_BESTAANDE)
        self.assertEqual(len(self.m.toegangslimiet._gebeurtenissen[("foute_code", "1.1.1.1")]), 1)

    def test_geen_vrije_code_geeft_503(self):
        self.patch_app("lees_transacties_excel", return_value=pd.DataFrame())
        self.patch_app("voeg_koers_eur_toe", side_effect=lambda df: df)
        self.patch_app("_upload_opslaan", side_effect=GeenVrijeCode("x"))
        self.assertEqual(self._upload().status_code, 503)

    def test_zware_codeloze_routes_hebben_een_limiet(self):
        self.patch_app("bereken_etf_overlap_detail", return_value=[])
        for pad, methode in (("/api/etf-overlap-detail?a=A&b=B", "get"), ("/api/ticker-zekerheid-check", "post"),
                             ("/api/box3/bereken", "post")):
            with self.subTest(pad=pad):
                self.m.toegangslimiet.wis()
                antwoorden = {getattr(self.client, methode)(pad, **ip("1.1.1.1")).status_code for _ in range(700)}
                self.assertIn(429, antwoorden)


class TestHeadersEnProxy(_MetApp):
    def test_beveiligingsheaders_op_pagina_en_api(self):
        for pad in ("/", "/api/etf-overlap-detail"):
            with self.subTest(pad=pad):
                h = self.client.get(pad).headers
                self.assertEqual(h["X-Content-Type-Options"], "nosniff")
                self.assertEqual(h["X-Frame-Options"], "DENY")
                self.assertEqual(h["Referrer-Policy"], "same-origin")

    def _client_ip_bij_aanvraag(self, headers):
        gezien = []
        self.patch_app("_markeer_gebruikt")
        self.patch_app("build_portfolio_response", side_effect=lambda code: gezien.append(self.m.client_ip()) or {})
        self.client.get("/api/portfolio/ABC", headers=headers)
        return gezien[0]

    def test_ip_komt_uit_x_forwarded_for_en_niet_uit_true_client_ip(self):
        ip_gezien = self._client_ip_bij_aanvraag(
            {"X-Forwarded-For": XFF_PER_BEZOEKER("5.6.7.8"), "True-Client-IP": "9.9.9.9"})
        self.assertEqual(ip_gezien, "5.6.7.8")

    def test_ip_komt_niet_in_de_logs(self):
        uitvoer = io.StringIO()
        with redirect_stdout(uitvoer):
            self._client_ip_bij_aanvraag({"X-Forwarded-For": XFF_PER_BEZOEKER("5.6.7.8")})
        self.assertNotIn("5.6.7.8", uitvoer.getvalue())

    def test_limiet_telt_per_client_ip_achter_de_proxy(self):
        self.patch_app("db_portfolio_bestaat", return_value=False)
        self.patch_app("build_portfolio_response", return_value=None)
        for _ in range(REGELS_FOUTE_CODE[0].maximum):
            self.client.get("/api/portfolio/ABC", headers={"X-Forwarded-For": XFF_PER_BEZOEKER("5.6.7.8")})
        geblokkeerd = self.client.get("/api/portfolio/ABC", headers={"X-Forwarded-For": XFF_PER_BEZOEKER("5.6.7.8")})
        self.assertEqual(geblokkeerd.status_code, 429)
        andere = self.client.get("/api/portfolio/ABC", headers={"X-Forwarded-For": XFF_PER_BEZOEKER("6.6.6.6")})
        self.assertEqual(andere.status_code, 404)

    def test_client_ip_is_de_bezoeker_ook_als_die_adressen_vooraan_meestuurt(self):
        for header, verwacht in (
            ("nep, 1.2.3.4, 5.6.7.8, 10.0.0.1", "1.2.3.4"),
            ("1.2.3.4, 104.22.109.68, 10.0.0.1", "1.2.3.4"),
        ):
            with self.subTest(header=header):
                self.assertEqual(self._client_ip_bij_aanvraag({"X-Forwarded-For": header}), verwacht)


class TestFavicon(_MetApp):
    def test_favicon_op_de_root_geeft_het_icoon(self):
        res = self.client.get("/favicon.ico")
        self.assertEqual(res.status_code, 200)
        with open(os.path.join(self.m.app.static_folder, "favicon", "favicon.ico"), "rb") as f:
            self.assertEqual(res.data, f.read())
        self.assertIn("icon", res.mimetype)
        res.close()


class TestRobotsEnSitemap(_MetApp):
    def test_robots_laat_startpagina_en_privacy_toe_met_sitemap_op_eigen_domein(self):
        res = self.client.get("/robots.txt", base_url="https://voorbeeld.test")
        tekst = res.get_data(as_text=True)
        self.assertEqual(res.mimetype, "text/plain")
        for regel in ("Allow: /", "Allow: /privacy", "Disallow: /p/", "Disallow: /api/", "Disallow: /analyse", "Disallow: /upload",
                      "Sitemap: https://voorbeeld.test/sitemap.xml"):
            self.assertIn(regel + "\n", tekst)

    def test_sitemap_bevat_de_startpagina_en_privacy_en_volgt_https_van_de_proxy(self):
        res = self.client.get("/sitemap.xml", base_url="http://voorbeeld.test",
                              headers={"X-Forwarded-Proto": "https"})
        xml = res.get_data(as_text=True)
        self.assertEqual(res.mimetype, "application/xml")
        self.assertEqual(xml.count("<loc>"), 2)
        self.assertIn("<loc>https://voorbeeld.test/</loc>", xml)
        self.assertIn("<loc>https://voorbeeld.test/privacy</loc>", xml)


class TestMetaTags(_MetApp):
    def test_startpagina_heeft_beschrijving_open_graph_canonical_en_titel(self):
        html = self.client.get("/", base_url="https://voorbeeld.test").get_data(as_text=True)
        self.assertIn("<title>Portfolio Dashboard – inzicht in je DeGiro-portfolio</title>", html)
        self.assertIn('<meta name="description" content="', html)
        for sleutel in ("og:title", "og:description", "og:type", "og:url", "og:image"):
            self.assertIn(f'property="{sleutel}"', html)
        self.assertIn('content="https://voorbeeld.test/static/favicon/web-app-manifest-512x512.png', html)
        self.assertIn('<link rel="canonical" href="https://voorbeeld.test/">', html)
        self.assertNotIn("noindex", html)

    def test_uitleg_is_standaard_ingeklapt_maar_staat_in_de_html(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('<details class="startUitleg">', html)
        self.assertNotIn("<details class=\"startUitleg\" open", html)
        self.assertIn("<summary>Wat doet deze app?</summary>", html)
        self.assertIn("worden je transacties niet opgeslagen", html)
        self.assertIn("eenmalig, je transacties worden niet opgeslagen", html)
        self.assertNotIn("niets bewaard", html)

    def test_startpagina_legt_uit_wat_de_app_doet(self):
        html = self.client.get("/").get_data(as_text=True)
        for woord in ("DeGiro-export", "XIRR", "TWR", "ETF-overlap", "dividend", "prognose", "Niet opslaan"):
            self.assertIn(woord, html)

    def test_portfolio_paginas_zijn_noindex_en_sturen_geen_referrer(self):
        for pad in ("/p/ABC", "/analyse"):
            with self.subTest(pad=pad):
                html = self.client.get(pad).get_data(as_text=True)
                self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
                self.assertIn('<meta name="referrer" content="no-referrer">', html)
                self.assertNotIn("canonical", html)
                self.assertNotIn("og:url", html)


if __name__ == "__main__":
    unittest.main()
