"""Wat-als: basis (met code en 'niet opslaan'), aankoop en de twee routes. Alles gemockt; geen database, geen netwerk."""
import json
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import effect_profiel
from backend import portfolio_orchestratie as po
from backend import portfolio_verdeling
from backend.effect_profiel import GeenKoers, OnbekendEffect, bouw_aankoop
from backend.toegangslimiet import REGELS_EXTERNE_BRON
from backend.wat_als import MAX_AANTAL_WAT_ALS, MAX_POSITIES_WAT_ALS, valideer_aankoop_invoer, valideer_posities, simuleer_aankoop

ISIN = "IE00B5BMR087"
ETF_ISIN = "IE00BK5BQT80"
TOP10 = [{"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.2, "land": "United States",
          "bron": "yfinance_top10"}]
PROXY = {"VWCE.DE": {"proxy_isin": "IE00B6R52259", "proxy_naam": "iShares MSCI ACWI", "max_afwijking_pp": 0.47,
                     "proxy_land": {"United States": 0.7, "Japan": 0.3}}}


def transacties_df():
    return pd.DataFrame({
        "datum": [pd.Timestamp("2024-01-01")] * 2, "product": ["All-World", "Apple"], "echte_naam": [None, "Apple Inc."],
        "isin": [ETF_ISIN, "US0378331005"], "ticker": ["VWCE.DE", "AAPL"], "aantal": [10.0, 2.0],
    })


def price_data():
    return pd.DataFrame({"VWCE.DE": [100.0], "AAPL": [200.0]}, index=[pd.Timestamp("2024-01-02")])


def aankoop_msft(waarde=100.0):
    return {"ticker": "MSFT", "naam": "Microsoft Corp", "is_etf": False, "waarde_eur": waarde,
            "fracties": {"land_pct": {"United States": 1.0}, "sector_pct": {"Technology": 1.0}, "land_bron": None,
                         "land_proxy": None}, "holdings": None}, {"ticker": "MSFT", "aantal": 1}


class _MetPortfolioMocks(unittest.TestCase):
    """Dezelfde mocks als test_proxy_niet_opslaan, plus echte bedrijven- en land/sector-berekeningen."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for doel, naam, waarde in [
            (po, "classify_tickers", {"VWCE.DE": True, "AAPL": False}),
            (po, "vul_ontbrekende_long_names", None),
            (po, "_verwarm_land_sector_cache_parallel", None),
            (po, "compute_valuta_verdeling", {"valuta": {}}),
            (po, "compute_beurs_verdeling", {"beurs": {}}),
            (po, "get_etf_holdings_uit_cache", []),
            (portfolio_verdeling, "get_etf_holdings", TOP10),
            (portfolio_verdeling, "get_etf_sector_verdeling", {"Technology": 1.0}),
            (portfolio_verdeling, "get_land_sector", ("United States", "Technology")),
        ]:
            stack.enter_context(patch.object(doel, naam, return_value=waarde))
        stack.enter_context(patch.object(po, "haal_portfolio_basis", return_value=("N", transacties_df(), price_data())))
        self.mock_proxy = stack.enter_context(patch.object(po, "land_proxies_voor_etfs", return_value=PROXY))


class TestBasisMetCode(_MetPortfolioMocks):
    def test_voor_is_gelijk_aan_wat_de_verrijking_toont_inclusief_proxy(self):
        verrijking = po.analyze_transacties_verrijking(transacties_df(), "ZZTESTWATALS", prijs_data_al_klaar=price_data())
        self.assertEqual(verrijking["land_sector_verdeling"]["per_etf"]["VWCE.DE"]["land_bron"], "proxy")

        basis = po.bouw_wat_als_basis("ZZTESTWATALS")
        aankoop, _ = aankoop_msft()
        r = simuleer_aankoop(basis, aankoop, po.WAT_ALS_TOP_N)

        land = verrijking["land_sector_verdeling"]["land"]
        sector = verrijking["land_sector_verdeling"]["sector"]
        for onderdeel, tab in (("land", land), ("sector", sector)):
            for rij in r[onderdeel]:
                self.assertAlmostEqual(rij["voor_pct"], tab.get(rij["naam"], 0.0) / sum(tab.values()) * 100, places=4)

        top = verrijking["bedrijven_verdeling"]["top"]
        totaal = verrijking["bedrijven_verdeling"]["totaal_waarde"]
        voor = {b["bedrijf"]: b["waarde"] / totaal * 100 for b in top}
        gevonden = 0
        for rij in r["bedrijven"]:
            if rij["naam"] in voor:
                gevonden += 1
                self.assertAlmostEqual(rij["voor_pct"], voor[rij["naam"]], places=2)
        self.assertGreater(gevonden, 0)

    def test_onbekende_code_geeft_none(self):
        with patch.object(po, "haal_portfolio_basis", return_value=(None, None, None)):
            self.assertIsNone(po.bouw_wat_als_basis("ZZTESTONBEKEND"))

    def test_zonder_koersen_een_lege_basis_zonder_fouten(self):
        with patch.object(po, "haal_portfolio_basis", return_value=("N", transacties_df(), pd.DataFrame())):
            basis = po.bouw_wat_als_basis("ZZTESTLEEG")
        r = simuleer_aankoop(basis, aankoop_msft()[0], 5)
        self.assertEqual(r["totaal"]["voor"], 0.0)

    def test_verrijking_blijft_dezelfde_stappen_doen(self):
        verrijking = po.analyze_transacties_verrijking(transacties_df(), "ZZTESTWATALS", prijs_data_al_klaar=price_data())
        self.mock_proxy.assert_called_once_with({"VWCE.DE": ETF_ISIN})
        self.assertEqual(verrijking["verdeling"][0]["ticker"], "VWCE.DE")


class TestBasisNietOpslaan(_MetPortfolioMocks):
    POSITIES = [{"ticker": "VWCE.DE", "naam": "All-World", "waarde": 1000.0},
                {"ticker": "AAPL", "naam": "Apple Inc.", "waarde": 400.0}]

    def test_is_etf_komt_uit_de_classificatie_en_er_is_geen_proxy(self):
        posities = [dict(p, is_etf=(p["ticker"] == "AAPL")) for p in self.POSITIES]
        schoon, fout = valideer_posities(posities)
        self.assertIsNone(fout)
        basis = po.bouw_wat_als_basis_uit_posities(schoon)
        self.assertEqual(basis["is_etf_map"], {"VWCE.DE": True, "AAPL": False})
        self.mock_proxy.assert_not_called()
        self.assertEqual(basis["fracties_per_ticker"]["VWCE.DE"]["land_bron"], "yfinance_top10")
        self.assertEqual(basis["waarde_per_ticker"], {"AAPL": 400.0, "VWCE.DE": 1000.0})

    def test_voor_is_gelijk_aan_de_route_met_code_zonder_proxy(self):
        schoon, _ = valideer_posities(self.POSITIES)
        zonder_opslaan = po.bouw_wat_als_basis_uit_posities(schoon)
        with patch.object(po, "_bepaal_land_proxies", return_value={}):
            met_code = po.bouw_wat_als_basis("ZZTESTWATALS")
        for sleutel in ("waarde_per_ticker", "is_etf_map", "fracties_per_ticker", "holdings_per_etf"):
            self.assertEqual(zonder_opslaan[sleutel], met_code[sleutel], sleutel)
        # Het enige bekende verschil: de meegestuurde naam in plaats van echte_naam.
        self.assertEqual(zonder_opslaan["aandeel_namen"], {"AAPL": "Apple Inc."})

    def test_met_proxy_verschilt_alleen_het_land_van_de_etf(self):
        schoon, _ = valideer_posities(self.POSITIES)
        zonder_opslaan = po.bouw_wat_als_basis_uit_posities(schoon)
        met_code = po.bouw_wat_als_basis("ZZTESTWATALS")
        verschil = {t for t in met_code["fracties_per_ticker"]
                    if met_code["fracties_per_ticker"][t] != zonder_opslaan["fracties_per_ticker"][t]}
        self.assertEqual(verschil, {"VWCE.DE"})


class TestValidatie(unittest.TestCase):
    def test_aantal_moet_een_geheel_getal_binnen_de_grenzen_zijn(self):
        for slecht in (0, -1, "5", 5.0, 2.5, True, None, MAX_AANTAL_WAT_ALS + 1):
            with self.subTest(aantal=slecht):
                self.assertIsNotNone(valideer_aankoop_invoer({"ticker": "A", "aantal": slecht})[3])
        for goed in (1, 10, MAX_AANTAL_WAT_ALS):
            self.assertEqual(valideer_aankoop_invoer({"ticker": " A ", "aantal": goed}), ("A", None, goed, None))

    def test_ticker_en_isin(self):
        for body in (None, [], {"aantal": 1}, {"ticker": "", "aantal": 1}, {"ticker": "T" * 21, "aantal": 1},
                     {"ticker": 5, "aantal": 1}, {"ticker": "A", "aantal": 1, "isin": "onzin"},
                     {"ticker": "A", "aantal": 1, "isin": 12}):
            with self.subTest(body=body):
                self.assertIsNotNone(valideer_aankoop_invoer(body)[3])
        self.assertEqual(valideer_aankoop_invoer({"ticker": "A", "aantal": 1, "isin": ISIN.lower()}), ("A", ISIN, 1, None))
        self.assertEqual(valideer_aankoop_invoer({"ticker": "A", "aantal": 1, "isin": ""})[1], None)

    def test_posities(self):
        goed = {"ticker": "A", "naam": "Een", "waarde": 10}
        self.assertEqual(valideer_posities([goed, {"ticker": "B", "waarde": 2.5}])[0],
                         [{"ticker": "A", "naam": "Een", "waarde": 10.0}, {"ticker": "B", "naam": "B", "waarde": 2.5}])
        for slecht in (None, [], "x", [goed] * 2, [dict(goed, waarde=-1)], [dict(goed, waarde=0)],
                       [dict(goed, waarde=float("nan"))], [dict(goed, waarde=float("inf"))], [dict(goed, waarde="5")],
                       [dict(goed, waarde=True)], [dict(goed, ticker="T" * 21)], [dict(goed, naam="n" * 201)], [5],
                       [{"ticker": f"T{i}", "waarde": 1} for i in range(MAX_POSITIES_WAT_ALS + 1)]):
            with self.subTest(slecht=str(slecht)[:40]):
                self.assertIsNotNone(valideer_posities(slecht)[1])


def profiel(koers=12.5, type_="ETF"):
    return {"ticker": "EMIM.AS", "naam": "EM IMI", "type": type_, "koers_eur": koers, "land": {"Taiwan": 0.6, "Unknown": 0.4},
            "sector": {"Technology": 1.0}, "land_bron": "provider_csv", "land_proxy": None, "isin_geverifieerd": True,
            "holdings_route": "provider_ticker", "holdings_fonds": None, "waarschuwingen": [],
            "dividend": {"status": "keert_uit", "per_stuk_bruto_eur": 0.5, "per_stuk_netto_eur": 0.425, "bron": "yahoo_reeks",
                         "uitkeringen_12m": 4, "belasting_fractie": 0.15, "belasting_bron": "standaard"}}


class TestBouwAankoop(unittest.TestCase):
    def test_waarde_is_aantal_maal_koers_en_holdings_zijn_volledig(self):
        holdings = [{"holding_naam": f"H{i}", "gewicht": 0.01} for i in range(40)]
        with patch.object(effect_profiel, "_profiel_en_holdings", return_value=(profiel(), holdings)):
            aankoop, samenvatting = bouw_aankoop("EMIM.AS", None, 4)
        self.assertEqual(aankoop["waarde_eur"], 50.0)
        self.assertEqual(len(aankoop["holdings"]), 40)
        self.assertEqual(aankoop["fracties"]["land_pct"], {"Taiwan": 0.6, "Unknown": 0.4})
        self.assertEqual((samenvatting["aantal"], samenvatting["koers_eur"], samenvatting["waarde_eur"]), (4, 12.5, 50.0))
        self.assertEqual(set(samenvatting), {"ticker", "naam", "aantal", "koers_eur", "waarde_eur", "isin_geverifieerd",
                                             "holdings_route", "holdings_fonds", "waarschuwingen",
                                             "dividend_jaar_bruto_eur", "dividend_jaar_netto_eur", "dividend_status",
                                             "dividend_per_stuk_bruto_eur", "dividend_per_stuk_netto_eur", "dividend_bron",
                                             "dividend_uitkeringen_12m", "dividend_belasting_fractie",
                                             "dividend_belasting_bron"})
        self.assertEqual((samenvatting["dividend_jaar_bruto_eur"], samenvatting["dividend_jaar_netto_eur"]), (2.0, 1.7))

    def test_dividenduitleg_velden_komen_uit_het_profiel(self):
        with patch.object(effect_profiel, "_profiel_en_holdings", return_value=(profiel(), [])):
            _, samenvatting = bouw_aankoop("EMIM.AS", None, 4)
        self.assertEqual(
            (samenvatting["dividend_per_stuk_bruto_eur"], samenvatting["dividend_per_stuk_netto_eur"], samenvatting["dividend_bron"],
             samenvatting["dividend_uitkeringen_12m"], samenvatting["dividend_belasting_fractie"],
             samenvatting["dividend_belasting_bron"]),
            (0.5, 0.425, "yahoo_reeks", 4, 0.15, "standaard"))

    def test_dividenduitleg_velden_zijn_none_bij_onbekend(self):
        onbekend = dict(profiel()["dividend"], status="onbekend", per_stuk_bruto_eur=None, per_stuk_netto_eur=None)
        with patch.object(effect_profiel, "_profiel_en_holdings", return_value=(dict(profiel(), dividend=onbekend), [])):
            _, samenvatting = bouw_aankoop("EMIM.AS", None, 4)
        velden = [k for k in samenvatting if k.startswith("dividend_") and k not in ("dividend_jaar_bruto_eur", "dividend_jaar_netto_eur", "dividend_status")]
        self.assertEqual(len(velden), 6)
        self.assertTrue(all(samenvatting[k] is None for k in velden))

    def test_aandeel_heeft_geen_holdings_en_ontbrekende_sector_wordt_unknown(self):
        p = dict(profiel(type_="EQUITY"), sector={})
        with patch.object(effect_profiel, "_profiel_en_holdings", return_value=(p, None)):
            aankoop, _ = bouw_aankoop("ASML.AS", None, 1)
        self.assertEqual((aankoop["is_etf"], aankoop["holdings"], aankoop["fracties"]["sector_pct"]),
                         (False, None, {"Unknown": 1.0}))

    def test_geen_koers_geeft_geen_koers(self):
        with patch.object(effect_profiel, "_profiel_en_holdings", return_value=(profiel(koers=None), [])):
            with self.assertRaises(GeenKoers):
                bouw_aankoop("EMIM.AS", None, 1)

    def test_onbekende_ticker_gaat_door(self):
        with patch.object(effect_profiel, "_profiel_en_holdings", side_effect=OnbekendEffect("X")):
            with self.assertRaises(OnbekendEffect):
                bouw_aankoop("X", None, 1)


def basis_met_isins():
    etf = {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": "provider_csv", "land_proxy": None}
    return {
        "waarde_per_ticker": {"CSPX.AS": 1000.0, "IWDA.AS": 500.0}, "is_etf_map": {"CSPX.AS": True, "IWDA.AS": True},
        "fracties_per_ticker": {"CSPX.AS": etf, "IWDA.AS": etf},
        "holdings_per_etf": {"CSPX.AS": [{"holding_naam": "Apple Inc", "gewicht": 0.5}],
                             "IWDA.AS": [{"holding_naam": "Apple Inc", "gewicht": 0.4}]},
        "aandeel_namen": {}, "bron_namen": {}, "extra_aandelen": {},
        # OUD.AS is een gesloten positie: wel een ISIN bekend, niet in waarde_per_ticker.
        "isin_per_ticker": {"CSPX.AS": ISIN, "IWDA.AS": "IE00B4L5Y983", "OUD.AS": ISIN},
    }


def etf_aankoop(ticker, geverifieerd):
    aankoop = {"ticker": ticker, "naam": "Fonds", "is_etf": True, "waarde_eur": 100.0,
               "fracties": {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None, "land_proxy": None},
               "holdings": [{"holding_naam": "Apple Inc", "gewicht": 0.5}]}
    return aankoop, {"ticker": ticker, "isin_geverifieerd": geverifieerd, "waarschuwingen": []}


class TestZelfdeFondsAls(unittest.TestCase):
    def draai(self, basis, ticker, isin, geverifieerd):
        with patch.object(po, "bouw_aankoop", return_value=etf_aankoop(ticker, geverifieerd)):
            return po.bereken_wat_als(basis, ticker, isin, 1)["aankoop"]["zelfde_fonds_als"]

    def test_geverifieerde_isin_van_een_open_positie_met_een_andere_ticker(self):
        self.assertEqual(self.draai(basis_met_isins(), "SXR8.DE", ISIN, True), ["CSPX.AS"])

    def test_bijkopen_van_dezelfde_ticker_staat_niet_in_de_lijst(self):
        self.assertEqual(self.draai(basis_met_isins(), "CSPX.AS", ISIN, True), [])

    def test_bijkopen_zonder_isin_gebruikt_de_isin_van_de_eigen_positie(self):
        basis = basis_met_isins()
        basis["waarde_per_ticker"]["SXR8.DE"] = 50.0
        basis["isin_per_ticker"]["SXR8.DE"] = ISIN
        basis["is_etf_map"]["SXR8.DE"] = True
        basis["fracties_per_ticker"]["SXR8.DE"] = basis["fracties_per_ticker"]["CSPX.AS"]
        basis["holdings_per_etf"]["SXR8.DE"] = basis["holdings_per_etf"]["CSPX.AS"]
        self.assertEqual(self.draai(basis, "CSPX.AS", None, None), ["SXR8.DE"])

    def test_niet_geverifieerde_isin_geeft_een_lege_lijst(self):
        self.assertEqual(self.draai(basis_met_isins(), "SXR8.DE", ISIN, False), [])

    def test_gesloten_positie_met_dezelfde_isin_telt_niet(self):
        basis = basis_met_isins()
        del basis["waarde_per_ticker"]["CSPX.AS"]
        self.assertEqual(self.draai(basis, "SXR8.DE", ISIN, True), [])

    def test_zonder_isin_en_zonder_eigen_positie_een_lege_lijst(self):
        self.assertEqual(self.draai(basis_met_isins(), "SXR8.DE", None, None), [])

    def test_niet_opslaan_geeft_none(self):
        basis = basis_met_isins()
        del basis["isin_per_ticker"]
        self.assertIsNone(self.draai(basis, "SXR8.DE", ISIN, True))

    def test_het_signaal_verandert_de_berekening_niet(self):
        basis = basis_met_isins()
        with patch.object(po, "bouw_aankoop", return_value=etf_aankoop("SXR8.DE", True)):
            met = po.bereken_wat_als(basis, "SXR8.DE", ISIN, 1)["wat_als"]
        zonder = dict(basis, isin_per_ticker={})
        with patch.object(po, "bouw_aankoop", return_value=etf_aankoop("SXR8.DE", True)):
            zonder_signaal = po.bereken_wat_als(zonder, "SXR8.DE", ISIN, 1)["wat_als"]
        self.assertEqual(met, zonder_signaal)
        self.assertIn("SXR8.DE", [p["ticker"] for p in met["posities"]])

    def test_basis_met_code_bevat_de_isins_van_de_transacties(self):
        po_basis = po._lege_wat_als_basis()
        self.assertEqual(po_basis["isin_per_ticker"], {})


class _MetApp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)

    def patch_app(self, naam, **kwargs):
        p = patch.object(self.m, naam, **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def post(self, pad, body, **kwargs):
        return self.client.post(pad, json=body, **kwargs)


class TestRoutes(_MetApp):
    GELDIG = {"ticker": "EMIM.AS", "aantal": 10}
    POSITIES = [{"ticker": "AAPL", "naam": "Apple", "waarde": 100.0}]

    def test_ongeldige_invoer_geeft_400_zonder_iets_aan_te_roepen(self):
        basis = self.patch_app("bouw_wat_als_basis")
        zonder = self.patch_app("bouw_wat_als_basis_uit_posities")
        for slecht in ({"ticker": "A", "aantal": 0}, {"ticker": "A", "aantal": "5"}, {"ticker": "A", "aantal": 5.0},
                       {"ticker": "A", "aantal": 1, "isin": "onzin"}, {"aantal": 1}):
            self.assertEqual(self.post("/api/portfolio/ABC/wat-als", slecht).status_code, 400)
            self.assertEqual(self.post("/api/wat-als", dict(slecht, posities=self.POSITIES)).status_code, 400)
        basis.assert_not_called()
        zonder.assert_not_called()

    def test_niet_opslaan_valideert_de_posities(self):
        zonder = self.patch_app("bouw_wat_als_basis_uit_posities")
        te_veel = [{"ticker": f"T{i}", "waarde": 1} for i in range(MAX_POSITIES_WAT_ALS + 1)]
        for posities in (None, [], te_veel, [{"ticker": "A", "waarde": -5}], [{"ticker": "T" * 21, "waarde": 5}]):
            res = self.post("/api/wat-als", dict(self.GELDIG, posities=posities))
            self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/wat-als", content_type="application/json",
                               data='{"ticker":"A","aantal":1,"posities":[{"ticker":"X","waarde":NaN}]}')
        self.assertEqual(res.status_code, 400)
        zonder.assert_not_called()

    def test_te_lange_naam_geeft_400_zonder_iets_aan_te_roepen(self):
        basis = self.patch_app("bouw_wat_als_basis")
        profiel = self.patch_app("bouw_effect_profiel")
        lang = "n" * (self.m.NAAM_MAX_TEKENS + 1)
        self.assertEqual(self.post("/api/portfolio/ABC/wat-als", dict(self.GELDIG, naam=lang)).status_code, 400)
        self.assertEqual(self.post("/api/wat-als", dict(self.GELDIG, naam=lang, posities=self.POSITIES)).status_code, 400)
        self.assertEqual(self.post("/api/portfolio/ABC/wat-als", dict(self.GELDIG, naam=5)).status_code, 400)
        self.assertEqual(self.client.get("/api/effect-profiel", query_string={"ticker": "A", "naam": lang}).status_code, 400)
        basis.assert_not_called()
        profiel.assert_not_called()

    def test_naam_gaat_door_naar_het_profiel_en_de_berekening(self):
        profiel = self.patch_app("bouw_effect_profiel", return_value={"ticker": "A"})
        self.client.get("/api/effect-profiel", query_string={"ticker": "A", "naam": " Fonds X "})
        profiel.assert_called_once_with("A", None, "Fonds X")
        bereken = self.patch_app("bereken_wat_als", return_value={"wat_als": {}, "aankoop": {}})
        self.patch_app("bouw_wat_als_basis", return_value={"x": 1})
        self.post("/api/portfolio/ABC/wat-als", dict(self.GELDIG, naam="Fonds X"))
        self.assertEqual(bereken.call_args.args[-1], "Fonds X")

    def test_geen_json_body_geeft_400(self):
        self.assertEqual(self.client.post("/api/portfolio/ABC/wat-als", data="geen json").status_code, 400)

    def test_onbekende_code_is_404_en_telt_als_foute_poging(self):
        self.patch_app("bouw_wat_als_basis", return_value=None)
        res = self.post("/api/portfolio/ABC/wat-als", self.GELDIG)
        self.assertEqual(res.status_code, 404)
        self.assertIn("Geen portfolio", res.get_json()["error"])

    def test_antwoord_heeft_wat_als_en_aankoop_zonder_nan(self):
        aankoop, _ = aankoop_msft()
        samenvatting = {"ticker": "MSFT", "naam": "Microsoft Corp", "aantal": 1, "koers_eur": 100.0, "waarde_eur": 100.0,
                        "isin_geverifieerd": None, "holdings_route": None, "holdings_fonds": None, "waarschuwingen": []}
        basis = {"waarde_per_ticker": {"AAPL": 100.0}, "is_etf_map": {"AAPL": False},
                 "fracties_per_ticker": {"AAPL": {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None,
                                                  "land_proxy": None}},
                 "holdings_per_etf": {}, "aandeel_namen": {"AAPL": "Apple"}, "bron_namen": {}, "extra_aandelen": {}}
        self.patch_app("bouw_wat_als_basis", return_value=basis)
        with patch.object(po, "bouw_aankoop", return_value=(aankoop, samenvatting)):
            res = self.post("/api/portfolio/ABC/wat-als", {"ticker": "MSFT", "aantal": 1})
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data, parse_constant=lambda c: self.fail(f"{c} in de JSON"))
        self.assertEqual(set(data), {"wat_als", "aankoop", "orderkosten"})
        self.assertEqual(data["wat_als"]["totaal"], {"voor": 100.0, "na": 200.0})
        self.assertEqual(data["aankoop"]["waarde_eur"], 100.0)

    def test_foutcodes_van_de_aankoop(self):
        self.patch_app("bouw_wat_als_basis", return_value={"x": 1})
        for fout, status in ((OnbekendEffect("X"), 404), (GeenKoers("X"), 422), (RuntimeError("boem"), 500)):
            with self.subTest(fout=type(fout).__name__):
                with patch.object(po, "bouw_aankoop", side_effect=fout), patch("builtins.print"):
                    res = self.post("/api/portfolio/ABC/wat-als", self.GELDIG)
                self.assertEqual(res.status_code, status)
                self.assertIn("error", res.get_json())

    def test_niet_opslaan_route_geeft_hetzelfde_antwoord_type(self):
        aankoop, samenvatting = aankoop_msft()
        samenvatting.update({"naam": "Microsoft Corp", "koers_eur": 100.0, "waarde_eur": 100.0})
        self.patch_app("bouw_wat_als_basis_uit_posities", return_value={
            "waarde_per_ticker": {"AAPL": 100.0}, "is_etf_map": {"AAPL": False},
            "fracties_per_ticker": {"AAPL": {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None,
                                             "land_proxy": None}},
            "holdings_per_etf": {}, "aandeel_namen": {}, "bron_namen": {}, "extra_aandelen": {}})
        with patch.object(po, "bouw_aankoop", return_value=(aankoop, samenvatting)):
            res = self.post("/api/wat-als", {"ticker": "MSFT", "aantal": 1, "posities": self.POSITIES})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.get_json()), {"wat_als", "aankoop", "orderkosten"})
        self.assertEqual(res.get_json()["orderkosten"], {"status": "geen_transacties"})
        self.assertIsNone(res.get_json()["aankoop"]["zelfde_fonds_als"])

    def test_code_route_bevat_orderkosten_uit_de_eigen_transacties(self):
        aankoop, samenvatting = aankoop_msft()
        samenvatting.update({"naam": "Microsoft Corp", "koers_eur": 100.0, "waarde_eur": 100.0})
        transacties = pd.DataFrame([{"datum": pd.Timestamp.today(), "beurs": "EAM", "transactiekosten": -2.0, "aantal": 1.0,
                                     "order_id": None, "ticker": "AAPL", "product": "Apple"}])
        self.patch_app("bouw_wat_als_basis", return_value={
            "waarde_per_ticker": {"AAPL": 100.0}, "is_etf_map": {"AAPL": False},
            "fracties_per_ticker": {"AAPL": {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None,
                                             "land_proxy": None}},
            "holdings_per_etf": {}, "aandeel_namen": {}, "bron_namen": {}, "extra_aandelen": {},
            "orderkosten_invoer": {"code": "ABC", "transacties_df": transacties}})
        with patch.object(po, "bouw_aankoop", return_value=(aankoop, samenvatting)), \
                patch.object(po, "db_get_ticker_details", return_value={"MSFT": {"yahoo_beurs": "AMS"}}):
            res = self.post("/api/portfolio/ABC/wat-als", {"ticker": "MSFT", "aantal": 1})
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.get_json()["orderkosten"]["status"], res.get_json()["orderkosten"]["mediaan_eur"]),
                         ("geschat", 2.0))

    def test_beide_routes_vallen_onder_de_externe_bron_limiet(self):
        self.patch_app("bouw_wat_als_basis", return_value=None)
        for pad in ("/api/portfolio/ABC/wat-als", "/api/wat-als"):
            with self.subTest(pad=pad):
                self.m.toegangslimiet.wis()
                statussen = [self.post(pad, {"ticker": "A", "aantal": 0}, environ_base={"REMOTE_ADDR": "4.4.4.4"}).status_code
                             for _ in range(REGELS_EXTERNE_BRON[0].maximum + 1)]
                self.assertEqual(statussen[-1], 429)
                self.assertEqual(set(statussen[:-1]), {400})


if __name__ == "__main__":
    unittest.main()
