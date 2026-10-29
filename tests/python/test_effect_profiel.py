"""Profiel van één effect: refactor-helpers, ISIN-sleutel voor holdings, profielopbouw en route. Alles gemockt."""
import math
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import effect_dividend
from backend import effect_profiel
from backend import ticker_classificatie
from backend.effect_profiel import bouw_effect_profiel, verifieer_isin_bij_ticker, OnbekendEffect
from backend.portfolio_verdeling import land_pct_uit_holdings, sector_pct_met_restant, holdings_lijst_uit

ISIN = "IE00B5BMR087"
DIST_ISIN = "IE0031442068"
PROVIDER = [
    {"holding_naam": "Apple Inc", "holding_ticker": None, "gewicht": 0.6, "land": "United States", "bron": "provider_csv"},
    {"holding_naam": "Nestle SA", "holding_ticker": None, "gewicht": 0.3, "land": "Switzerland", "bron": "provider_csv"},
]
TOP10 = [
    {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.2, "land": "United States", "bron": "yfinance_top10"},
]


class TestHelpers(unittest.TestCase):
    def test_land_restant_wordt_unknown(self):
        land = land_pct_uit_holdings(PROVIDER)
        self.assertEqual((land["United States"], land["Switzerland"]), (0.6, 0.3))
        self.assertAlmostEqual(land["Unknown"], 0.1)

    def test_land_volledige_dekking_heeft_geen_unknown(self):
        volledig = [{"gewicht": 0.5, "land": "NL"}, {"gewicht": 0.5, "land": None}]
        self.assertEqual(land_pct_uit_holdings(volledig), {"NL": 0.5, "Unknown": 0.5})

    def test_sector_restant_wordt_unknown_en_volledig_niet(self):
        self.assertAlmostEqual(sector_pct_met_restant({"Tech": 0.7})["Unknown"], 0.3)
        self.assertEqual(sector_pct_met_restant({"Tech": 0.6, "Health": 0.4}), {"Tech": 0.6, "Health": 0.4})

    def test_holdings_lijst_uit_sorteert_en_slaat_nan_over(self):
        holdings = [{"holding_naam": "A", "gewicht": 0.1, "bron": "x"}, {"holding_naam": "B", "gewicht": 0.3, "bron": "x"},
                    {"holding_naam": "C", "gewicht": float("nan"), "bron": "x"}]
        r = holdings_lijst_uit(holdings, positie_waarde=100)
        self.assertEqual([h["naam"] for h in r["holdings"]], ["B", "A"])
        self.assertEqual(r["holdings"][0]["bedrag_eur"], 30.0)
        self.assertAlmostEqual(r["gedekt_gewicht"], 0.4)
        self.assertEqual((r["bron"], r["aantal"]), ("x", 2))


FONDSEN = [{"isin": ISIN, "naam": "iShares Core S&P 500 UCITS ETF", "product_url": "/nl/producten/x"}]
PAGINA = [{"naam": "Apple Inc", "gewicht": 7.0, "land": "United States", "sector": "IT", "ticker": "AAPL"}]


class TestHoldingsVoorIsin(unittest.TestCase):
    def draai(self, cached=None, fondsen=FONDSEN, pagina=PAGINA):
        with patch.object(ticker_classificatie, "db_get_cached_etf_holdings", return_value=cached) as lees, \
                patch.object(ticker_classificatie, "db_save_etf_holdings") as bewaar, \
                patch.object(ticker_classificatie, "ishares_fondsen", return_value=fondsen), \
                patch.object(ticker_classificatie, "fetch_ishares_holdings_via_productpagina", return_value=pagina) as haal:
            return ticker_classificatie.get_etf_holdings_voor_isin(ISIN), lees, bewaar, haal

    def test_cachetreffer_haalt_niets_op(self):
        holdings, lees, bewaar, haal = self.draai(cached=PROVIDER)
        self.assertEqual(holdings, PROVIDER)
        lees.assert_called_once_with(f"isin:{ISIN}")
        haal.assert_not_called()
        bewaar.assert_not_called()

    def test_cachemiss_slaat_alleen_onder_de_isin_sleutel_op(self):
        holdings, _lees, bewaar, _haal = self.draai()
        bewaar.assert_called_once_with(f"isin:{ISIN}", holdings)
        self.assertEqual(holdings[0]["bron"], "provider_csv")
        for aanroep in bewaar.call_args_list:
            self.assertTrue(aanroep.args[0].startswith("isin:"))

    def test_yfinance_cache_onder_de_sleutel_wordt_niet_vertrouwd(self):
        holdings, _lees, bewaar, _haal = self.draai(cached=TOP10)
        self.assertEqual(holdings[0]["bron"], "provider_csv")
        bewaar.assert_called_once()

    def test_geen_ishares_fonds_of_geen_holdings_geeft_none_zonder_opslaan(self):
        for naam, kwargs in (("geen fonds", {"fondsen": []}), ("fondsenlijst weg", {"fondsen": None}),
                             ("geen holdings", {"pagina": None})):
            with self.subTest(naam):
                holdings, _lees, bewaar, _haal = self.draai(**kwargs)
                self.assertIsNone(holdings)
                bewaar.assert_not_called()


def koersen_voor(tickers, _start, waarde=100.0):
    return pd.DataFrame({tickers[0]: [waarde, float("nan")]}, index=pd.to_datetime(["2026-10-26", "2026-10-27"]))


class _Profiel(unittest.TestCase):
    """Alle externe bronnen van effect_profiel staan gemockt; per test aan te passen via self.m."""

    def setUp(self):
        self.m = {}
        standaard = {
            "get_prices": dict(side_effect=koersen_voor),
            "get_ticker_details": dict(return_value={"is_etf": True, "long_name": "Fonds", "valuta": "EUR"}),
            "get_land_sector": dict(return_value=("Netherlands", "Technology")),
            "get_etf_sector_verdeling": dict(return_value={"Technology": 0.7}),
            "get_etf_holdings": dict(return_value=TOP10),
            "get_etf_holdings_voor_isin": dict(return_value=None),
            "land_proxies_voor_etfs": dict(return_value={}),
            "db_get_isins_voor_ticker": dict(return_value=[]),
            "zoek_gecachet": dict(return_value=[]),
            "ishares_fondsen": dict(return_value=[]),
        }
        for naam, kwargs in standaard.items():
            p = patch.object(effect_profiel, naam, **kwargs)
            self.m[naam] = p.start()
            self.addCleanup(p.stop)
        for naam, kwargs in {"haal_yahoo_dividenden": dict(return_value={"dividenden": {}, "dividend_rate": None, "trailing_rate": None}),
                             "_haal_splits_op": dict(return_value={}),
                             "_fx_actueel": dict(return_value=1.0)}.items():
            p = patch.object(effect_dividend, naam, **kwargs)
            self.m[naam] = p.start()
            self.addCleanup(p.stop)

    def profiel(self, ticker="SXR8.DE", isin=None):
        return bouw_effect_profiel(ticker, isin)


class TestVerificatie(_Profiel):
    def test_isin_uit_eigen_data_is_geverifieerd(self):
        self.m["db_get_isins_voor_ticker"].return_value = [ISIN]
        self.assertTrue(verifieer_isin_bij_ticker("SXR8.DE", ISIN))

    def test_ticker_in_de_noteringen_van_de_isin_is_geverifieerd(self):
        quote = {"symbol": "SXR8.DE", "quoteType": "ETF", "longname": "Fonds"}
        self.m["zoek_gecachet"].return_value = [quote]
        self.assertTrue(verifieer_isin_bij_ticker("sxr8.de", ISIN))

    def test_geen_van_beide_is_niet_geverifieerd(self):
        self.m["zoek_gecachet"].return_value = [{"symbol": "IDUS.L", "quoteType": "ETF", "longname": "Dist"}]
        self.assertFalse(verifieer_isin_bij_ticker("SXR8.DE", DIST_ISIN))

    def test_bronfouten_geven_niet_geverifieerd(self):
        self.m["db_get_isins_voor_ticker"].side_effect = RuntimeError("db")
        self.m["zoek_gecachet"].side_effect = RuntimeError("yahoo")
        self.assertFalse(verifieer_isin_bij_ticker("SXR8.DE", ISIN))


class TestProfielDividend(_Profiel):
    def test_dividend_staat_onder_de_sleutel_dividend(self):
        self.m["haal_yahoo_dividenden"].return_value = {
            "dividenden": {"2026-09-01": 1.0}, "dividend_rate": None, "trailing_rate": None}
        p = self.profiel("ASML.AS", ISIN)
        self.assertEqual((p["dividend"]["status"], p["dividend"]["per_stuk_bruto_eur"]), ("keert_uit", 1.0))
        self.assertEqual(p["waarschuwingen"].count("geen_dividend_data"), 0)

    def test_geen_dividenddata_geeft_waarschuwing_en_het_profiel_blijft(self):
        self.m["haal_yahoo_dividenden"].return_value = None
        p = self.profiel()
        self.assertEqual(p["dividend"]["status"], "onbekend")
        self.assertIn("geen_dividend_data", p["waarschuwingen"])
        self.assertEqual(p["koers_eur"], 100.0)


class TestProfielAandeel(_Profiel):
    def test_aandeel_heeft_land_en_sector_1_en_geen_holdings(self):
        self.m["get_ticker_details"].return_value = {"is_etf": False, "long_name": "ASML", "valuta": "EUR"}
        p = self.profiel("ASML.AS")
        self.assertEqual((p["type"], p["land"], p["sector"]), ("EQUITY", {"Netherlands": 1.0}, {"Technology": 1.0}))
        self.assertEqual((p["land_bron"], p["holdings"], p["isin_geverifieerd"]), ("aandeel", None, None))
        self.assertEqual(p["koers_eur"], 100.0)
        self.assertEqual(p["koers_datum"], "2026-10-26")
        self.assertEqual(p["waarschuwingen"], [])
        self.m["get_etf_holdings"].assert_not_called()


class TestProfielEtf(_Profiel):
    def test_etf_holdings_bron_route_gebruikt_de_ticker_en_negeert_de_isin(self):
        with patch.object(effect_profiel, "ETF_HOLDINGS_BRON", {"VEUR.AS": "x"}):
            self.m["get_etf_holdings"].return_value = PROVIDER
            p = self.profiel("VEUR.AS", ISIN)
        self.m["get_etf_holdings"].assert_called_once_with("VEUR.AS")
        self.m["get_etf_holdings_voor_isin"].assert_not_called()
        self.assertEqual((p["land_bron"], p["isin_geverifieerd"]), ("provider_csv", False))
        self.assertNotIn("isin_gekozen", p["waarschuwingen"])

    def test_provider_ticker_route_met_juiste_isin_is_toch_geverifieerd(self):
        with patch.object(effect_profiel, "ETF_HOLDINGS_BRON", {"EMIM.AS": "x"}):
            self.m["db_get_isins_voor_ticker"].return_value = []
            self.m["zoek_gecachet"].return_value = [{"symbol": "EMIM.AS", "quoteType": "ETF", "longname": "EM"}]
            self.m["get_etf_holdings"].return_value = PROVIDER
            p = self.profiel("EMIM.AS", "IE00BKM4GZ66")
        self.assertTrue(p["isin_geverifieerd"])
        self.assertEqual((p["holdings_route"], p["holdings_fonds"]), ("provider_ticker", None))
        self.assertNotIn("isin_gekozen", p["waarschuwingen"])

    def test_zonder_isin_is_isin_geverifieerd_none(self):
        self.assertIsNone(self.profiel("VWRL.AS")["isin_geverifieerd"])

    def test_isin_route_met_dist_isin_noemt_het_dist_fonds_en_waarschuwt(self):
        dist_naam = "iShares Core S&P 500 UCITS ETF USD (Dist)"
        self.m["ishares_fondsen"].return_value = [{"isin": ISIN, "naam": "Acc"}, {"isin": DIST_ISIN, "naam": dist_naam}]
        self.m["get_etf_holdings_voor_isin"].return_value = PROVIDER
        p = self.profiel("SXR8.DE", DIST_ISIN)
        self.assertEqual(p["holdings_route"], "isin")
        self.assertEqual(p["holdings_fonds"], {"isin": DIST_ISIN, "naam": dist_naam})
        self.assertIn("isin_gekozen", p["waarschuwingen"])
        self.assertFalse(p["isin_geverifieerd"])

    def test_isin_route_zonder_fonds_in_de_lijst_geeft_naam_none(self):
        self.m["get_etf_holdings_voor_isin"].return_value = PROVIDER
        self.assertEqual(self.profiel("SXR8.DE", ISIN)["holdings_fonds"], {"isin": ISIN, "naam": None})

    def test_top10_route_heeft_geen_holdings_fonds(self):
        p = self.profiel("VWRL.AS", ISIN)
        self.assertEqual((p["holdings_route"], p["holdings_fonds"]), ("top10", None))

    def test_ticker_cache_met_provider_holdings_is_route_provider_ticker(self):
        self.m["get_etf_holdings"].return_value = PROVIDER
        self.assertEqual(self.profiel("VWRL.AS")["holdings_route"], "provider_ticker")

    def test_aandeel_heeft_geen_route_en_geen_fonds(self):
        self.m["get_ticker_details"].return_value = {"is_etf": False, "long_name": "ASML"}
        p = self.profiel("ASML.AS")
        self.assertEqual((p["holdings_route"], p["holdings_fonds"]), (None, None))

    def test_geverifieerde_isin_route_geeft_geen_isin_gekozen(self):
        self.m["db_get_isins_voor_ticker"].return_value = [ISIN]
        self.m["get_etf_holdings_voor_isin"].return_value = PROVIDER
        p = self.profiel("SXR8.DE", ISIN)
        self.m["get_etf_holdings_voor_isin"].assert_called_once_with(ISIN)
        self.m["get_etf_holdings"].assert_not_called()
        self.assertTrue(p["isin_geverifieerd"])
        self.assertEqual(p["waarschuwingen"], [])
        self.assertEqual(p["holdings"]["bron"], "provider_csv")
        self.assertEqual([h["naam"] for h in p["holdings"]["top"]], ["Apple Inc", "Nestle SA"])
        self.assertAlmostEqual(p["land"]["Unknown"], 0.1)

    def test_niet_geverifieerde_isin_geeft_waarschuwing_en_nooit_een_proxy(self):
        self.m["get_etf_holdings_voor_isin"].return_value = PROVIDER
        p = self.profiel("SXR8.DE", DIST_ISIN)
        self.assertFalse(p["isin_geverifieerd"])
        self.assertIn("isin_gekozen", p["waarschuwingen"])
        self.m["land_proxies_voor_etfs"].assert_not_called()

    def test_niet_geverifieerde_isin_zonder_ishares_valt_terug_op_top10_zonder_proxy(self):
        p = self.profiel("VWRL.AS", ISIN)
        self.assertIn("alleen_top10", p["waarschuwingen"])
        self.assertNotIn("isin_gekozen", p["waarschuwingen"])
        self.m["land_proxies_voor_etfs"].assert_not_called()

    def test_top10_route_zonder_isin_geeft_alleen_top10(self):
        p = self.profiel("VWRL.AS")
        self.assertEqual(p["waarschuwingen"], ["alleen_top10"])
        self.assertEqual(p["land_bron"], "yfinance_top10")
        self.m["get_etf_holdings"].assert_called_once_with("VWRL.AS")
        self.m["land_proxies_voor_etfs"].assert_not_called()

    def test_proxy_bij_geverifieerde_isin_en_top10(self):
        self.m["db_get_isins_voor_ticker"].return_value = [ISIN]
        self.m["land_proxies_voor_etfs"].return_value = {"VWRL.AS": {
            "proxy_isin": "IE1", "proxy_land": {"United States": 0.6, "Japan": 0.4},
            "proxy_naam": "iShares X", "max_afwijking_pp": 0.4}}
        p = self.profiel("VWRL.AS", ISIN)
        self.m["land_proxies_voor_etfs"].assert_called_once_with({"VWRL.AS": ISIN})
        self.assertEqual((p["land_bron"], p["land"]), ("proxy", {"United States": 0.6, "Japan": 0.4}))
        self.assertEqual(p["land_proxy"], {"naam": "iShares X", "max_afwijking_pp": 0.4})
        self.assertEqual(p["waarschuwingen"], ["alleen_top10", "land_via_proxy"])

    def test_proxy_zonder_resultaat_laat_de_top10_staan(self):
        self.m["db_get_isins_voor_ticker"].return_value = [ISIN]
        self.m["land_proxies_voor_etfs"].return_value = {"VWRL.AS": {"proxy_isin": None, "proxy_land": None}}
        p = self.profiel("VWRL.AS", ISIN)
        self.assertEqual((p["land_bron"], p["land_proxy"]), ("yfinance_top10", None))

    def test_holdings_voor_effect_geeft_de_volledige_lijst_en_het_profiel_de_top_n(self):
        veel = [{"holding_naam": f"H{i}", "holding_ticker": None, "gewicht": 0.001, "land": "NL", "bron": "provider_csv"}
                for i in range(effect_profiel.MAX_TOP_HOLDINGS + 5)]
        self.m["db_get_isins_voor_ticker"].return_value = [ISIN]
        self.m["get_etf_holdings_voor_isin"].return_value = veel + [dict(veel[0], holding_naam="NaN", gewicht=float("nan"))]
        hv = effect_profiel.holdings_voor_effect("SXR8.DE", ISIN)
        self.assertEqual((len(hv["holdings"]), hv["route"], hv["isin_geverifieerd"], hv["waarschuwingen"]),
                         (len(veel), "isin", True, []))
        self.assertEqual(hv["fonds"], {"isin": ISIN, "naam": None})
        profiel = self.profiel("SXR8.DE", ISIN)
        self.assertEqual(len(profiel["holdings"]["top"]), effect_profiel.MAX_TOP_HOLDINGS)
        self.assertEqual(profiel["holdings"]["aantal"], len(veel))

    def test_top_van_holdings_is_begrensd_maar_aantal_blijft_volledig(self):
        veel = [{"holding_naam": f"H{i}", "holding_ticker": None, "gewicht": 0.001, "land": "NL", "bron": "provider_csv"}
                for i in range(effect_profiel.MAX_TOP_HOLDINGS + 5)]
        self.m["get_etf_holdings"].return_value = veel
        p = self.profiel("VWRL.AS")
        self.assertEqual(len(p["holdings"]["top"]), effect_profiel.MAX_TOP_HOLDINGS)
        self.assertEqual(p["holdings"]["aantal"], len(veel))


class TestProfielFouten(_Profiel):
    def test_geen_koers_geeft_profiel_met_waarschuwing(self):
        self.m["get_prices"].side_effect = lambda *a: pd.DataFrame()
        p = self.profiel()
        self.assertIsNone(p["koers_eur"])
        self.assertIsNone(p["koers_datum"])
        self.assertIn("geen_koers", p["waarschuwingen"])

    def test_koersfout_wordt_geen_exceptie(self):
        self.m["get_prices"].side_effect = RuntimeError("yahoo")
        self.assertIn("geen_koers", self.profiel()["waarschuwingen"])

    def test_sectorfout_geeft_leeg_sectorblok_en_waarschuwing(self):
        self.m["get_etf_sector_verdeling"].side_effect = RuntimeError("yahoo")
        p = self.profiel()
        self.assertEqual(p["sector"], {})
        self.assertIn("geen_sector", p["waarschuwingen"])

    def test_holdingsfout_geeft_geen_holdings_en_waarschuwing(self):
        self.m["get_etf_holdings"].side_effect = RuntimeError("yahoo")
        p = self.profiel()
        self.assertIsNone(p["holdings"])
        self.assertIn("geen_holdings", p["waarschuwingen"])

    def test_volledig_onbekende_ticker_geeft_onbekend_effect(self):
        self.m["get_ticker_details"].return_value = {}
        self.m["get_prices"].side_effect = lambda *a: pd.DataFrame()
        with self.assertRaises(OnbekendEffect):
            self.profiel("NIETBESTAAND")

    def test_geen_nan_in_het_profiel(self):
        self.m["get_etf_holdings"].return_value = TOP10 + [dict(TOP10[0], holding_naam="B", gewicht=float("nan"))]
        self.m["get_etf_sector_verdeling"].return_value = {"Tech": float("nan")}

        def zoek_nan(waarde):
            if isinstance(waarde, float):
                return math.isnan(waarde)
            if isinstance(waarde, dict):
                return any(zoek_nan(v) for v in waarde.values())
            if isinstance(waarde, list):
                return any(zoek_nan(v) for v in waarde)
            return False
        self.assertFalse(zoek_nan(self.profiel()))


class TestRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)

    def test_ontbrekende_of_te_lange_ticker_geeft_400(self):
        for q in ({}, {"ticker": "T" * 21}):
            self.assertEqual(self.client.get("/api/effect-profiel", query_string=q).status_code, 400)

    def test_ongeldige_isin_geeft_400_en_roept_niets_aan(self):
        with patch.object(self.m, "bouw_effect_profiel") as bouw:
            res = self.client.get("/api/effect-profiel", query_string={"ticker": "SXR8.DE", "isin": "onzin"})
        self.assertEqual(res.status_code, 400)
        bouw.assert_not_called()

    def test_geldige_isin_wordt_genormaliseerd_doorgegeven(self):
        with patch.object(self.m, "bouw_effect_profiel", return_value={"ticker": "SXR8.DE"}) as bouw:
            res = self.client.get("/api/effect-profiel", query_string={"ticker": "SXR8.DE", "isin": ISIN.lower()})
        self.assertEqual(res.status_code, 200)
        bouw.assert_called_once_with("SXR8.DE", ISIN)

    def test_zonder_isin_wordt_none_doorgegeven(self):
        with patch.object(self.m, "bouw_effect_profiel", return_value={}) as bouw:
            self.client.get("/api/effect-profiel", query_string={"ticker": "ASML.AS"})
        bouw.assert_called_once_with("ASML.AS", None)

    def test_onbekende_ticker_geeft_404_json(self):
        with patch.object(self.m, "bouw_effect_profiel", side_effect=OnbekendEffect("X")):
            res = self.client.get("/api/effect-profiel", query_string={"ticker": "X"})
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.get_json())

    def test_valt_onder_de_externe_bron_limiet(self):
        from backend.toegangslimiet import REGELS_EXTERNE_BRON
        with patch.object(self.m, "bouw_effect_profiel", return_value={}):
            statussen = [self.client.get("/api/effect-profiel", query_string={"ticker": "A"},
                                         environ_base={"REMOTE_ADDR": "3.3.3.3"}).status_code
                         for _ in range(REGELS_EXTERNE_BRON[0].maximum + 1)]
        self.assertEqual(statussen[-1], 429)
        self.assertEqual(set(statussen[:-1]), {200})


if __name__ == "__main__":
    unittest.main()
