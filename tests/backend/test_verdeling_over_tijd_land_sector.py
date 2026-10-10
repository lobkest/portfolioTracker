"""Verdeling over tijd voor land en sector: land_sector_fracties(), de gewichten, het opwarmen (ook gesloten
posities) en de route. Zonder database en zonder Yahoo: de get_*- en proxy-functies worden gemockt."""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie
import portfolio_verdeling
from portfolio_calc import waarde_per_ticker_per_dag
from portfolio_verdeling import (
    bereken_verdeling_over_tijd, compute_land_sector_verdeling, gewichten_per_land, gewichten_per_positie,
    gewichten_per_sector, land_sector_fracties,
)

PROXY = {"proxy_isin": "IE000PROXY01", "proxy_land": {"United States": 0.7, "Japan": 0.3},
         "proxy_naam": "iShares Proxy", "max_afwijking_pp": 0.5}
HOLDINGS = {
    "TOP": [{"land": "United States", "gewicht": 0.5, "bron": "yfinance_top10"},
            {"land": None, "gewicht": 0.1, "bron": "yfinance_top10"}],
    "CSV": [{"land": "Germany", "gewicht": 0.4, "bron": "provider_csv"},
            {"land": "France", "gewicht": 0.6, "bron": "provider_csv"}],
    "PRX": [],
}
SECTOREN = {"TOP": {"Technology": 0.6, "Financials": 0.3}, "CSV": {"Industrials": 1.0}, "PRX": {"Technology": 1.0}}
AANDELEN = {"AAA": ("Netherlands", "Technology"), "BBB": ("United States", "Healthcare")}


def _mock_get_functies():
    return [
        patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=lambda t, *a: HOLDINGS.get(t, [])),
        patch.object(portfolio_verdeling, "get_etf_sector_verdeling", side_effect=lambda t: SECTOREN.get(t, {})),
        patch.object(portfolio_verdeling, "get_land_sector", side_effect=lambda t: AANDELEN.get(t, ("Unknown", "Unknown"))),
    ]


class MetMocks(unittest.TestCase):
    def setUp(self):
        for p in _mock_get_functies():
            p.start()
            self.addCleanup(p.stop)


def _rij(datum, ticker, aantal, totaal_eur):
    return {
        "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal, "adj_aantal": aantal, "koers": 0.0,
        "totaal_eur": totaal_eur, "waarde_eur": totaal_eur, "beurs": "EAM", "product": f"Naam {ticker}",
        "isin": f"NL{ticker}0000001",
    }


def _basis(rijen, tickers):
    dagen = pd.date_range("2024-01-01", "2024-01-21", freq="D")
    koersen = pd.DataFrame({t: np.linspace(10 + i, 12 + i, len(dagen)) for i, t in enumerate(tickers)}, index=dagen)
    return pd.DataFrame(rijen), koersen


class TestLandSectorFracties(MetMocks):
    def _check_som(self, f):
        self.assertAlmostEqual(sum(f["land_pct"].values()), 1.0)
        self.assertAlmostEqual(sum(f["sector_pct"].values()), 1.0)

    def test_etf_met_proxy(self):
        f = land_sector_fracties("PRX", True, PROXY)
        self.assertEqual(f["land_pct"], {"United States": 0.7, "Japan": 0.3})
        self.assertEqual(f["land_bron"], "proxy")
        self.assertEqual(f["land_proxy"], {"naam": "iShares Proxy", "max_afwijking_pp": 0.5})
        self._check_som(f)

    def test_etf_met_holdings_en_unknown_restant(self):
        f = land_sector_fracties("TOP", True)
        self.assertEqual(f["land_pct"]["United States"], 0.5)
        self.assertAlmostEqual(f["land_pct"]["Unknown"], 0.5)  # 0,1 zonder land + 0,4 niet gedekt
        self.assertAlmostEqual(f["sector_pct"]["Unknown"], 0.1)
        self.assertEqual(f["land_bron"], "yfinance_top10")
        self._check_som(f)

    def test_etf_zonder_holdings(self):
        f = land_sector_fracties("PRX", True)
        self.assertEqual(f["land_pct"], {"Unknown": 1.0})
        self.assertEqual(f["land_bron"], "yfinance_top10")
        self._check_som(f)

    def test_aandeel(self):
        f = land_sector_fracties("AAA", False)
        self.assertEqual(f, {"land_pct": {"Netherlands": 1.0}, "sector_pct": {"Technology": 1.0},
                             "land_bron": None, "land_proxy": None})


class TestGewichtenLandSector(unittest.TestCase):
    FRACTIES = {"E": {"land_pct": {"Germany": 0.3, "France": 0.2, "United States": 0.5},
                      "sector_pct": {"Technology": 0.6, "Unknown": 0.4}}}

    def test_europa_samenvoegen(self):
        self.assertEqual(gewichten_per_land(self.FRACTIES, europa_samenvoegen=True),
                         {"E": {"United States": 0.5, "Europe": 0.5}})
        self.assertEqual(gewichten_per_land(self.FRACTIES), {"E": {"Germany": 0.3, "France": 0.2, "United States": 0.5}})

    def test_sector(self):
        self.assertEqual(gewichten_per_sector(self.FRACTIES), {"E": {"Technology": 0.6, "Unknown": 0.4}})

    def test_som_boven_een_wordt_een(self):
        res = gewichten_per_sector({"E": {"land_pct": {}, "sector_pct": {"A": 0.6, "B": 0.6}}})
        self.assertEqual(res, {"E": {"A": 0.5, "B": 0.5}})


class TestOverTijdOrkestratie(MetMocks):
    def _bouw(self, df, koersen, is_etf_map, dimensie, samenvoegen=False, proxies=None):
        with patch.object(portfolio_orchestratie, "haal_portfolio_basis", return_value=("Test", df, koersen)), \
             patch.object(portfolio_orchestratie, "classify_tickers", return_value=is_etf_map) as mock_classify, \
             patch.object(portfolio_orchestratie, "_verwarm_land_sector_cache_parallel") as mock_warm, \
             patch.object(portfolio_orchestratie, "land_proxies_voor_etfs", return_value=proxies or {}) as mock_proxy:
            res = portfolio_orchestratie.bouw_verdeling_over_tijd("ZZTEST", dimensie, samenvoegen)
        return res, mock_classify, mock_warm, mock_proxy

    def test_gesloten_etf_krijgt_echte_land_en_wordt_opgewarmd(self):
        df, koersen = _basis([_rij("2024-01-02", "CSV", 10.0, -100.0), _rij("2024-01-12", "CSV", -10.0, 110.0),
                              _rij("2024-01-02", "AAA", 5.0, -50.0)], ["CSV", "AAA"])
        res, mock_classify, mock_warm, mock_proxy = self._bouw(df, koersen, {"CSV": True, "AAA": False}, "land")
        reeksen = {r["sleutel"]: r for r in res["reeksen"]}
        self.assertNotIn("Unknown", reeksen)
        self.assertGreater(reeksen["Germany"]["waarde"][0], 0)
        self.assertEqual(reeksen["Germany"]["waarde"][-1], 0)
        self.assertIn("CSV", mock_classify.call_args.args[0])
        self.assertIn("CSV", mock_warm.call_args.args[0])
        self.assertEqual(mock_warm.call_args.args[2]["CSV"], "NLCSV0000001")
        self.assertIn("CSV", mock_proxy.call_args.args[0])

    def test_waakhond_land_en_sector(self):
        df, koersen = _basis([_rij("2024-01-02", "TOP", 3.0, -30.0), _rij("2024-01-03", "CSV", 2.0, -20.0),
                              _rij("2024-01-09", "AAA", 4.0, -40.0), _rij("2024-01-15", "TOP", -1.0, 12.0)],
                             ["TOP", "CSV", "AAA"])
        is_etf = {"TOP": True, "CSV": True, "AAA": False}
        waarde = waarde_per_ticker_per_dag(df, koersen)
        positie = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(waarde.columns), {})
        for dimensie, samenvoegen in (("land", False), ("land", True), ("sector", False)):
            res = self._bouw(df, koersen, is_etf, dimensie, samenvoegen)[0]
            self.assertEqual(res["totaal"], positie["totaal"])
            for i in range(len(res["labels"])):
                self.assertAlmostEqual(sum(r["waarde"][i] for r in res["reeksen"]), res["totaal"][i], delta=0.05)

    def test_laatste_meetpunt_sector_gelijk_aan_huidige_verdeling(self):
        df, koersen = _basis([_rij("2024-01-02", "TOP", 3.0, -30.0), _rij("2024-01-03", "AAA", 2.0, -20.0),
                              _rij("2024-01-04", "BBB", 1.0, -10.0)], ["TOP", "AAA", "BBB"])
        is_etf = {"TOP": True, "AAA": False, "BBB": False}
        res = self._bouw(df, koersen, is_etf, "sector")[0]
        laatste = {r["sleutel"]: r["waarde"][-1] for r in res["reeksen"]}
        nu = compute_land_sector_verdeling(df, koersen, is_etf)["sector"]
        self.assertEqual(laatste, {k: round(v, 2) for k, v in nu.items()})

    def test_beperkte_dekking_alleen_top10_zonder_proxy(self):
        df, koersen = _basis([_rij("2024-01-02", t, 1.0, -10.0) for t in ("TOP", "CSV", "PRX", "AAA")],
                             ["TOP", "CSV", "PRX", "AAA"])
        is_etf = {"TOP": True, "CSV": True, "PRX": True, "AAA": False}
        res = self._bouw(df, koersen, is_etf, "land", proxies={"PRX": PROXY})[0]
        self.assertEqual(res["beperkte_dekking"], ["Naam TOP"])
        res = self._bouw(df, koersen, {"AAA": False, "TOP": False, "CSV": False, "PRX": False}, "sector")[0]
        self.assertEqual(res["beperkte_dekking"], [])


class TestLandSectorRoute(MetMocks):
    def setUp(self):
        super().setUp()
        import app as app_module
        self.client = app_module.app.test_client()
        df, koersen = _basis([_rij("2024-01-02", "AAA", 2.0, -20.0), _rij("2024-01-03", "TOP", 1.0, -10.0)],
                             ["AAA", "TOP"])
        for p in (patch.object(portfolio_orchestratie, "haal_portfolio_basis", return_value=("Test", df, koersen)),
                  patch.object(portfolio_orchestratie, "_verwarm_land_sector_cache_parallel"),
                  patch.object(portfolio_orchestratie, "land_proxies_voor_etfs", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def test_land_en_sector_200(self):
        with patch.object(portfolio_orchestratie, "classify_tickers", return_value={"AAA": False, "TOP": True}):
            land = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=land&samenvoegen=1")
            sector = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=sector")
        self.assertEqual(land.status_code, 200)
        self.assertIn("Europe", [r["sleutel"] for r in land.get_json()["reeksen"]])
        self.assertEqual(land.get_json()["beperkte_dekking"], ["Naam TOP"])
        self.assertEqual(sector.status_code, 200)
        self.assertIn("Technology", [r["sleutel"] for r in sector.get_json()["reeksen"]])

    def test_fout_bij_opwarmen_geeft_nette_500(self):
        with patch.object(portfolio_orchestratie, "classify_tickers", side_effect=RuntimeError("Yahoo weg")):
            res = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=land")
        self.assertEqual(res.status_code, 500)
        self.assertIn("opnieuw", res.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
