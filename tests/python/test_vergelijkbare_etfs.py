"""Vergelijkbare ETF's (fase B van de alternatieven op een thuisbeurs): kandidaten kiezen, vergelijken, selecteren en de
twee endpoints. Verzonnen data, offline: Yahoo, de fondsenlijst en de database zijn gemockt."""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import vergelijkbare_etfs as ve
from backend.vergelijkbare_etfs import (
    MAX_KANDIDATEN, MIN_OVERLAP_PCT, OnbekendeTicker, bron_uit_portfolio, fonds_profiel, gelijkenis_pct,
    kies_kandidaten_voor_bron, onderzoek_kandidaat, overlap_pct, selecteer_alternatieven, selecteer_onder_drempel, thuisnotering_van_vaneck,
    valideer_vergelijk_invoer, vaneck_kandidaten, zoek_vergelijkbare_etfs)

ISIN_BRON = "IE00B4L5Y983"


def isin_van(n):
    return f"IE{n:09d}0"


def fonds(n, naam, grootte, regio="WERELD", markt="ONTWIKKELD", asset="AANDELEN", sub=None):
    return {"portfolio_id": n, "isin": isin_van(n), "naam": naam, "asset_class": asset, "regio": regio, "markt_type": markt,
            "sub_asset_class": sub, "strategie_codes": [], "fondsgrootte": grootte}


BRONFONDS = dict(fonds(1, "iShares Core MSCI World UCITS ETF USD (Acc)", 100.0), isin=ISIN_BRON)


def holding(naam, gewicht, land="United States", bron="provider_csv"):
    return {"holding_naam": naam, "gewicht": gewicht, "land": land, "bron": bron}


def notering(ticker="X.AS", code="EAM", beurs="AMS", via_naam=False):
    return {"ticker": ticker, "beurs": beurs, "beurs_code": code, "beurs_naam": "Euronext Amsterdam", "via_naam": via_naam}


class TestKandidaten(unittest.TestCase):
    def test_ishares_bron_zelfde_codes_en_niet_het_bronfonds(self):
        lijst = [BRONFONDS, fonds(2, "iShares Edge MSCI World Quality", 50.0), fonds(3, "iShares MSCI Europe", 80.0, regio="EUROPA"),
                 fonds(4, "iShares MSCI EM", 70.0, markt="OPKOMEND"), fonds(5, "iShares Obligaties", 90.0, asset="OBLIGATIES"),
                 dict(fonds(6, "iShares Dubbel", 10.0), isin=ISIN_BRON)]
        kandidaten, via = kies_kandidaten_voor_bron(ISIN_BRON, "Wereld", "IWDA.DE", lijst, vaneck=[])
        self.assertEqual(via, "codes")
        self.assertEqual([k["naam"] for k in kandidaten], ["iShares Edge MSCI World Quality"])

    def test_maximum_tien_op_fondsgrootte_aflopend(self):
        lijst = [BRONFONDS] + [fonds(10 + i, f"Wereld Fonds {chr(65 + i)}", float(i)) for i in range(12)]
        kandidaten, _via = kies_kandidaten_voor_bron(ISIN_BRON, "x", "T", lijst, vaneck=[])
        self.assertEqual(len(kandidaten), MAX_KANDIDATEN)
        groottes = [k["fondsgrootte"] for k in kandidaten]
        self.assertEqual(groottes, sorted(groottes, reverse=True))
        self.assertEqual(groottes[0], 11.0)

    def test_zelfde_sub_asset_class_krijgt_voorrang_als_er_niet_genoeg_plek_is(self):
        lijst = [dict(BRONFONDS, sub_asset_class="LARGE")]
        lijst += [fonds(10 + i, f"Groot Fonds {chr(65 + i)}", 1000.0 + i, sub="SMALL") for i in range(10)]
        lijst += [fonds(50, "Klein Maar Zelfde Soort", 1.0, sub="LARGE")]
        kandidaten, _via = kies_kandidaten_voor_bron(ISIN_BRON, "x", "T", lijst, vaneck=[])
        self.assertIn("Klein Maar Zelfde Soort", [k["naam"] for k in kandidaten])
        self.assertEqual(len(kandidaten), MAX_KANDIDATEN)

    def test_share_classes_van_een_fonds_tellen_een_keer(self):
        lijst = [BRONFONDS, fonds(2, "iShares MSCI World Quality UCITS ETF USD (Acc)", 50.0),
                 fonds(3, "iShares MSCI World Quality UCITS ETF USD (Dist)", 50.0)]
        kandidaten, _via = kies_kandidaten_voor_bron(ISIN_BRON, "x", "T", lijst, vaneck=[])
        self.assertEqual(len(kandidaten), 1)

    def test_naam_bron_aanbiedernamen_tellen_niet_mee(self):
        lijst = [fonds(2, "iShares Gold Producers UCITS ETF", 30.0), fonds(3, "iShares Digital Security UCITS ETF", 99.0),
                 fonds(4, "iShares MSCI World", 80.0)]
        kandidaten, via = kies_kandidaten_voor_bron(None, "VanEck Gold Miners UCITS ETF", "GDX.L", lijst, vaneck=[])
        self.assertEqual(via, "naam")
        self.assertEqual([k["naam"] for k in kandidaten], ["iShares Gold Producers UCITS ETF"])

    def test_naam_bron_telt_vaneck_fondsen_mee_behalve_het_eigen_fonds(self):
        vaneck = [{"bron": "vaneck", "isin": None, "naam": None, "tickers": ["GDX.L", "G2X.DE"], "fondsgrootte": None},
                  {"bron": "vaneck", "isin": None, "naam": None, "tickers": ["TDT.AS"], "fondsgrootte": None}]
        kandidaten, _via = kies_kandidaten_voor_bron(None, "VanEck Gold Miners UCITS ETF", "GDX.L", [], vaneck=vaneck)
        self.assertEqual([k["tickers"] for k in kandidaten], [["TDT.AS"]])

    def test_vaneck_groepeert_op_url(self):
        groepen = vaneck_kandidaten()
        tickers = [t for g in groepen for t in g["tickers"]]
        self.assertIn("GDX.L", tickers)
        self.assertNotIn("IWDA.AS", tickers)
        self.assertEqual(next(g["tickers"] for g in groepen if "GDX.L" in g["tickers"]), ["GDX.L", "G2X.DE"])

    def test_geen_kandidaten_bij_lege_lijst(self):
        self.assertEqual(kies_kandidaten_voor_bron(ISIN_BRON, "x", "T", [BRONFONDS], vaneck=[])[0], [])
        self.assertEqual(kies_kandidaten_voor_bron(None, "Iets", "T", None, vaneck=[])[0], [])


class TestVergelijken(unittest.TestCase):
    def test_overlap_identiek_is_100_en_zonder_gedeelde_holdings_0(self):
        a = {"apple": 0.6, "shell": 0.4}
        self.assertEqual(overlap_pct(a, dict(a)), 100.0)
        self.assertEqual(overlap_pct(a, {"nvidia": 1.0}), 0.0)

    def test_overlap_deels_is_som_van_de_kleinste_gewichten(self):
        self.assertEqual(overlap_pct({"x": 0.5, "y": 0.5}, {"x": 0.3, "z": 0.7}), 30.0)

    def test_gelijkenis_handberekend(self):
        # |0,6-0,4| + |0,4-0,4| + |0-0,2| = 0,4 -> 40 pp verschil -> 100 - 20
        self.assertEqual(gelijkenis_pct({"NL": 0.6, "US": 0.4}, {"NL": 0.4, "US": 0.4, "DE": 0.2}), 80.0)
        self.assertEqual(gelijkenis_pct({"NL": 1.0}, {"NL": 1.0}), 100.0)
        self.assertEqual(gelijkenis_pct({"NL": 1.0}, {"US": 1.0}), 0.0)

    def test_profiel_gebruikt_dezelfde_bedrijfsgroepering_en_top10(self):
        holdings = [holding("Apple Inc", 0.3), holding("Apple Inc.", 0.1), holding("Shell", 0.2, "Netherlands")]
        p = fonds_profiel(holdings, {"Technology": 0.5})
        self.assertAlmostEqual(sum(p["gewichten"].values()), 0.6)
        self.assertEqual(p["aantal_holdings"], 2)
        self.assertEqual(p["top10"][0]["gewicht_pct"], 40.0)
        self.assertAlmostEqual(p["land"]["Unknown"], 0.4)
        self.assertAlmostEqual(p["sector"]["Unknown"], 0.5)

    def test_selectie_drempel_sortering_en_maximum_vijf(self):
        uitslag = [{"overlap_pct": x, "naam": str(x)} for x in (95, 60, 70, 80, 69.9, 85, 90, 72, 71)]
        gekozen = selecteer_alternatieven(uitslag)
        self.assertEqual([v["overlap_pct"] for v in gekozen], [95, 90, 85, 80, 72])
        self.assertEqual([v["overlap_pct"] for v in selecteer_alternatieven(uitslag, max_n=10)][-2:], [71, 70])
        self.assertEqual(MIN_OVERLAP_PCT, 70.0)


class TestOnderDrempel(unittest.TestCase):
    def test_max_drie_gesorteerd_en_minstens_dertig_procent(self):
        uitslag = [{"overlap_pct": x} for x in (10, 29.9, 30, 55, 69.9, 40, 62, 70, 85)]
        self.assertEqual([v["overlap_pct"] for v in selecteer_onder_drempel(uitslag)], [69.9, 62, 55])
        self.assertEqual([v["overlap_pct"] for v in selecteer_onder_drempel(uitslag, max_n=10)][-2:], [40, 30])

    def test_niets_tussen_dertig_en_zeventig_geeft_een_lege_lijst(self):
        self.assertEqual(selecteer_onder_drempel([{"overlap_pct": 20}, {"overlap_pct": 90}]), [])


class TestOnderzoekKandidaat(unittest.TestCase):
    def setUp(self):
        self.bron = fonds_profiel([holding("Apple", 0.5), holding("Shell", 0.5, "Netherlands")], {"Technology": 1.0})
        self.kandidaat = {"bron": "ishares", "isin": isin_van(2), "naam": "iShares X UCITS ETF (Acc)", "fondsgrootte": 5.0}
        self.sector = []

    def sector_ophaler(self, ticker):
        self.sector.append(ticker)
        return {"Technology": 0.8}

    def run_kandidaat(self, kandidaat=None, noteringen=None, holdings=None):
        return onderzoek_kandidaat(
            kandidaat or self.kandidaat, self.bron,
            onderzoeker=lambda isin, naam=None: {"noteringen": [notering()] if noteringen is None else noteringen},
            holdings_ophaler=lambda k, n: [holding("Apple", 0.5), holding("Shell", 0.4, "Netherlands")] if holdings is None else holdings,
            sector_ophaler=self.sector_ophaler)

    def test_volledige_uitkomst(self):
        r = self.run_kandidaat()
        self.assertEqual((r["overlap_pct"], r["ticker"], r["beurs_code"], r["uitkeringsvorm"]), (90.0, "X.AS", "EAM", "ACC"))
        self.assertEqual(r["land_gelijkenis_pct"], 90.0)
        self.assertEqual(r["sector_gelijkenis_pct"], 80.0)
        self.assertEqual(self.sector, ["X.AS"])

    def test_sector_wordt_alleen_opgehaald_boven_de_drempel(self):
        r = self.run_kandidaat(holdings=[holding("Nvidia", 1.0)])
        self.assertEqual(r["overlap_pct"], 0.0)
        self.assertIsNone(r["sector_gelijkenis_pct"])
        self.assertEqual(self.sector, [])

    def test_zonder_thuisnotering_vervalt_de_kandidaat(self):
        self.assertIsNone(self.run_kandidaat(noteringen=[]))

    def test_notering_alleen_via_naam_vervalt(self):
        self.assertIsNone(self.run_kandidaat(noteringen=[notering(via_naam=True)]))

    def test_zonder_volledige_holdings_vervalt_de_kandidaat(self):
        self.assertIsNone(self.run_kandidaat(holdings=[]))

    def test_fout_in_de_netwerklaag_laat_de_kandidaat_vervallen(self):
        def kapot(isin, naam=None):
            raise RuntimeError("Yahoo down")
        self.assertIsNone(onderzoek_kandidaat(self.kandidaat, self.bron, onderzoeker=kapot))

    def test_vaneck_gebruikt_thuisnotering_uit_de_eigen_tickers(self):
        kandidaat = {"bron": "vaneck", "isin": None, "naam": None, "tickers": ["TDT.L", "TDT.AS"], "fondsgrootte": None}
        self.assertEqual(thuisnotering_van_vaneck(kandidaat)["beurs_code"], "EAM")
        self.assertIsNone(thuisnotering_van_vaneck(dict(kandidaat, tickers=["GDX.L", "G2X.DE"])))
        with patch.object(ve, "_naam_van_vaneck", return_value="VanEck AEX UCITS ETF"):
            r = self.run_kandidaat(kandidaat)
        self.assertEqual((r["ticker"], r["naam"], r["isin"]), ("TDT.AS", "VanEck AEX UCITS ETF", None))


class TestZoekVergelijkbareEtfs(unittest.TestCase):
    def zoek(self, bron_holdings=None, bron="provider_csv", lijst=None, per_kandidaat=None, vaneck=()):
        holdings = [holding("Apple", 0.5), holding("Shell", 0.5, "Netherlands")] if bron_holdings is None else bron_holdings
        per_kandidaat = per_kandidaat or {}
        with patch.object(ve, "holdings_voor_effect", return_value={"holdings": holdings, "bron": bron}), \
                patch.object(ve, "vaneck_kandidaten", return_value=list(vaneck)):
            return zoek_vergelijkbare_etfs(
                "IWDA.DE", ISIN_BRON, "iShares Core MSCI World UCITS ETF",
                onderzoeker=lambda isin, naam=None: {"noteringen": [notering(f"T{isin[-3:]}.AS")]},
                holdings_ophaler=lambda k, n: per_kandidaat.get(k["isin"], [holding("Apple", 0.5), holding("Shell", 0.5, "Netherlands")]),
                sector_ophaler=lambda t: {"Technology": 1.0},
                fondsen_ophaler=lambda: [BRONFONDS, fonds(2, "Wereld Fonds A", 20.0), fonds(3, "Wereld Fonds B", 10.0)]
                if lijst is None else lijst)

    def test_alternatieven_boven_de_drempel_gesorteerd(self):
        antwoord = self.zoek(per_kandidaat={isin_van(3): [holding("Apple", 0.5), holding("Nvidia", 0.5)]})
        self.assertEqual(antwoord["status"], "ok")
        self.assertEqual(antwoord["kandidaten_via"], "codes")
        self.assertEqual([a["overlap_pct"] for a in antwoord["alternatieven"]], [100.0])
        self.assertEqual((antwoord["aantal_kandidaten"], antwoord["aantal_onderzocht"]), (2, 2))
        self.assertIsNone(antwoord["waarschuwing"])
        self.assertEqual(antwoord["bron"]["aantal_holdings"], 2)

    def test_onder_drempel_gevuld_zonder_alternatieven_met_sector_aangevuld(self):
        stukje = lambda w: [holding("Apple", w), holding("Nvidia", 1 - w)]
        antwoord = self.zoek(per_kandidaat={isin_van(2): stukje(0.6), isin_van(3): stukje(0.4)})
        self.assertEqual((antwoord["status"], antwoord["alternatieven"]), ("geen_alternatieven", []))
        self.assertEqual([v["overlap_pct"] for v in antwoord["onder_drempel"]], [50.0, 40.0])
        self.assertTrue(all(v["sector_gelijkenis_pct"] == 100.0 for v in antwoord["onder_drempel"]))
        self.assertTrue(all(v["land_gelijkenis_pct"] is not None for v in antwoord["onder_drempel"]))

    def test_onder_drempel_leeg_als_er_wel_alternatieven_zijn(self):
        antwoord = self.zoek(per_kandidaat={isin_van(3): [holding("Apple", 0.5), holding("Nvidia", 0.5)]})
        self.assertEqual(antwoord["status"], "ok")
        self.assertEqual(antwoord["onder_drempel"], [])

    def test_onder_drempel_max_drie_en_niet_onder_dertig(self):
        lijst = [BRONFONDS] + [fonds(n, f"Wereld Fonds {chr(65 + n)}", float(n)) for n in range(2, 8)]
        gewicht = {isin_van(2): 0.65, isin_van(3): 0.6, isin_van(4): 0.5, isin_van(5): 0.45, isin_van(6): 0.2, isin_van(7): 0.1}
        per = {i: [holding("Apple", w), holding("Nvidia", 1 - w)] for i, w in gewicht.items()}
        antwoord = self.zoek(lijst=lijst, per_kandidaat=per)
        # Bron is 50% Apple: overlap = min(gewicht, 0,5) -> 50, 50, 50, 45, 20, 10; de 45 valt buiten de top 3, 20 en 10 onder de 30.
        self.assertEqual([v["overlap_pct"] for v in antwoord["onder_drempel"]], [50.0, 50.0, 50.0])

    def test_geen_alternatief_boven_de_drempel(self):
        antwoord = self.zoek(per_kandidaat={isin_van(2): [holding("Nvidia", 1.0)], isin_van(3): [holding("Nvidia", 1.0)]})
        self.assertEqual((antwoord["status"], antwoord["alternatieven"]), ("geen_alternatieven", []))

    def test_bron_met_alleen_top10_geeft_waarschuwing_maar_rekent_door(self):
        antwoord = self.zoek(bron="yfinance_top10")
        self.assertEqual(antwoord["waarschuwing"], "bron_alleen_top10")
        self.assertEqual(antwoord["status"], "ok")

    def test_geen_kandidaten(self):
        antwoord = self.zoek(lijst=[BRONFONDS])
        self.assertEqual((antwoord["status"], antwoord["alternatieven"], antwoord["aantal_kandidaten"]), ("geen_kandidaten", [], 0))

    def test_bron_zonder_holdings_is_een_nette_fout(self):
        antwoord = self.zoek(bron_holdings=[], bron=None)
        self.assertEqual(antwoord["status"], "fout")
        self.assertTrue(antwoord["fout"])

    def test_fondsenlijst_faalt_dan_valt_het_terug_op_naam_en_vaneck(self):
        def kapot():
            raise RuntimeError("screener down")
        with patch.object(ve, "holdings_voor_effect", return_value={"holdings": [holding("Apple", 1.0)], "bron": "provider_csv"}), \
                patch.object(ve, "vaneck_kandidaten", return_value=[]):
            antwoord = zoek_vergelijkbare_etfs("IWDA.DE", ISIN_BRON, "Wereld", fondsen_ophaler=kapot,
                                                sector_ophaler=lambda t: None)
        self.assertEqual((antwoord["status"], antwoord["kandidaten_via"]), ("geen_kandidaten", "naam"))


class TestTijdbudget(unittest.TestCase):
    def test_trage_kandidaat_na_het_budget_geeft_onvolledig_en_houdt_de_rest(self):
        bron = fonds_profiel([holding("Apple", 1.0)])
        kandidaten = [{"bron": "ishares", "isin": isin_van(n), "naam": f"Fonds {n}", "fondsgrootte": 1.0} for n in (2, 3)]

        def holdings(k, n):
            if k["isin"] == isin_van(3):
                time.sleep(0.5)
            return [holding("Apple", 1.0)]
        with patch.object(ve, "TIJDBUDGET_SECONDEN", 0.1):
            resultaten, onvolledig = ve._onderzoek_parallel(
                kandidaten, bron, lambda isin, naam=None: {"noteringen": [notering()]}, holdings, lambda t: None)
        self.assertTrue(onvolledig)
        self.assertEqual([r["isin"] for r in resultaten], [isin_van(2)])

    def test_snelle_kandidaten_zijn_niet_onvolledig(self):
        bron = fonds_profiel([holding("Apple", 1.0)])
        kandidaten = [{"bron": "ishares", "isin": isin_van(2), "naam": "Fonds", "fondsgrootte": 1.0}]
        resultaten, onvolledig = ve._onderzoek_parallel(
            kandidaten, bron, lambda isin, naam=None: {"noteringen": [notering()]}, lambda k, n: [holding("Apple", 1.0)],
            lambda t: None)
        self.assertEqual((len(resultaten), onvolledig), (1, False))


class TestValidatieEnPortfolio(unittest.TestCase):
    def test_geldige_en_ongeldige_invoer(self):
        self.assertEqual(valideer_vergelijk_invoer({"ticker": " IWDA.DE ", "isin": ISIN_BRON, "naam": "World"}),
                         ("IWDA.DE", ISIN_BRON, "World", None))
        self.assertEqual(valideer_vergelijk_invoer({"ticker": "A"}), ("A", None, "A", None))
        for slecht in (None, [], {}, {"ticker": ""}, {"ticker": "T" * 21}, {"ticker": 5}, {"ticker": "A", "isin": "onzin"},
                       {"ticker": "A", "isin": 5}, {"ticker": "A", "naam": 5}, {"ticker": "A", "naam": "n" * 1000}):
            self.assertIsNotNone(valideer_vergelijk_invoer(slecht)[3], slecht)

    def test_bron_uit_portfolio(self):
        rijen = [(ISIN_BRON, "IWDA.DE", "World bijnaam"), ("US0378331005", "AAPL", "Apple")]
        with patch.object(ve, "db_get_isin_ticker_product", return_value=rijen), \
                patch.object(ve, "ishares_fondsen", return_value=[BRONFONDS]), \
                patch.object(ve, "db_get_ticker_details", return_value={"IWDA.DE": {"long_name": "iShares Core MSCI World"}}):
            self.assertEqual(bron_uit_portfolio("IWDA.DE", "ABC"), (ISIN_BRON, "iShares Core MSCI World"))
            with self.assertRaises(OnbekendeTicker):
                bron_uit_portfolio("ONBEKEND", "ABC")
        with patch.object(ve, "db_get_isin_ticker_product", return_value=[]), \
                patch.object(ve, "db_portfolio_bestaat", return_value=False):
            self.assertIsNone(bron_uit_portfolio("IWDA.DE", "ABC"))


class TestEndpoints(unittest.TestCase):
    ANTWOORD = {"status": "ok", "alternatieven": []}

    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        for naam, kwargs in (("is_etf_volgens_classificatie", {"return_value": True}),
                             ("zoek_vergelijkbare_etfs", {"return_value": self.ANTWOORD})):
            p = patch.object(app_module, naam, **kwargs)
            setattr(self, naam, p.start())
            self.addCleanup(p.stop)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)

    def patch_bron(self, **kwargs):
        p = patch.object(self.m, "bron_uit_portfolio", **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def test_met_code_geeft_antwoord_en_gebruikt_isin_uit_het_portfolio(self):
        self.patch_bron(return_value=(ISIN_BRON, "World"))
        res = self.client.get("/api/portfolio/abc/vergelijkbare-etfs?ticker=IWDA.DE")
        self.assertEqual((res.status_code, res.get_json()), (200, self.ANTWOORD))
        self.zoek_vergelijkbare_etfs.assert_called_once_with("IWDA.DE", ISIN_BRON, "World")

    def test_ongeldige_ticker_is_400(self):
        bron = self.patch_bron(return_value=(ISIN_BRON, "World"))
        for url in ("?ticker=", "", "?ticker=" + "T" * 21):
            self.assertEqual(self.client.get(f"/api/portfolio/ABC/vergelijkbare-etfs{url}").status_code, 400)
        bron.assert_not_called()

    def test_onbekende_code_en_onbekende_ticker_zijn_404(self):
        self.patch_bron(return_value=None)
        res = self.client.get("/api/portfolio/ABC/vergelijkbare-etfs?ticker=X")
        self.assertEqual(res.status_code, 404)
        self.assertIn("Geen portfolio", res.get_json()["error"])
        self.patch_bron(side_effect=OnbekendeTicker("X"))
        res = self.client.get("/api/portfolio/ABC/vergelijkbare-etfs?ticker=X")
        self.assertEqual(res.status_code, 404)
        self.assertIn("staat niet", res.get_json()["error"])

    def test_geen_etf_is_400(self):
        self.patch_bron(return_value=(ISIN_BRON, "World"))
        self.is_etf_volgens_classificatie.return_value = False
        res = self.client.get("/api/portfolio/ABC/vergelijkbare-etfs?ticker=AAPL")
        self.assertEqual(res.status_code, 400)
        self.zoek_vergelijkbare_etfs.assert_not_called()

    def test_onverwachte_fout_is_nette_json(self):
        self.patch_bron(return_value=(ISIN_BRON, "World"))
        self.zoek_vergelijkbare_etfs.side_effect = RuntimeError("kapot")
        res = self.client.get("/api/portfolio/ABC/vergelijkbare-etfs?ticker=IWDA.DE")
        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())

    def test_niet_opslaan_valideert_en_antwoordt(self):
        for body in (None, {}, {"ticker": ""}, {"ticker": "A", "isin": "onzin"}):
            self.assertEqual(self.client.post("/api/vergelijkbare-etfs", json=body).status_code, 400)
        self.assertEqual(self.client.post("/api/vergelijkbare-etfs", data="geen json").status_code, 400)
        res = self.client.post("/api/vergelijkbare-etfs", json={"ticker": "IWDA.DE", "isin": ISIN_BRON, "naam": "World"})
        self.assertEqual((res.status_code, res.get_json()), (200, self.ANTWOORD))
        self.zoek_vergelijkbare_etfs.assert_called_once_with("IWDA.DE", ISIN_BRON, "World")


if __name__ == "__main__":
    unittest.main()
