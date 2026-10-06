"""
Unit tests voor etf_proxy.py: een iShares-ETF kiezen op basis van de top-10 van het bronfonds, en de
landverdeling die daarmee in compute_land_sector_verdeling() terechtkomt.

Draait geheel offline: pure functies, geen database, geen Yahoo of iShares.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_verdeling
from etf_proxy import kies_proxy, land_uit_holdings, vergelijk_top10
from portfolio_verdeling import compute_land_sector_verdeling


def _h(naam, gewicht_pct, ticker=None):
    return {"naam": naam, "ticker": ticker, "gewicht_pct": gewicht_pct}


# Top-3 van een FTSE All-World-achtig bronfonds, zoals Yahoo die geeft.
BRON = [
    _h("NVIDIA Corp", 4.8, "NVDA"),
    _h("Apple Inc", 4.3, "AAPL"),
    _h("Taiwan Semiconductor Manufacturing Co Ltd", 1.7, "2330.TW"),
]

# ACWI-achtig: zelfde bedrijven, gewichten dicht bij de bron.
ACWI = [
    _h("NVIDIA", 5.2, "NVDA"),
    _h("APPLE", 4.7, "AAPL"),
    _h("TAIWAN SEMICONDUCTOR MANUFACTURING", 1.9, "2330"),
    _h("MICROSOFT", 3.5, "MSFT"),
]

# World-achtig: alleen ontwikkelde markten, dus hogere VS-gewichten en geen TSMC.
WORLD = [
    _h("NVIDIA", 5.9, "NVDA"),
    _h("APPLE", 5.3, "AAPL"),
    _h("MICROSOFT", 4.3, "MSFT"),
]


def _beoordeling(isin, naam, kandidaat):
    return {"isin": isin, "naam": naam, **vergelijk_top10(BRON, kandidaat)}


class TestKoppelen(unittest.TestCase):
    def test_koppelt_op_ticker_root_ondanks_andere_naam(self):
        # "2330.TW" (Yahoo) en "2330" (iShares) hebben dezelfde root; de namen verschillen.
        uit = vergelijk_top10([_h("TSMC", 1.7, "2330.TW")], [_h("Iets heel anders", 1.9, "2330")])
        self.assertEqual(uit["ontbrekend"], [])
        self.assertEqual(uit["rijen"][0]["proxy_pct"], 1.9)

    def test_koppelt_op_naam_als_ticker_niet_past(self):
        # Ticker ontbreekt bij de kandidaat; "Meta Platforms Inc Class A" en "META PLATFORMS CLASS A" -> "meta platforms".
        uit = vergelijk_top10([_h("Meta Platforms Inc Class A", 1.2, "META")],
                              [_h("META PLATFORMS CLASS A", 1.5, None)])
        self.assertEqual(uit["ontbrekend"], [])
        self.assertEqual(uit["rijen"][0]["proxy_pct"], 1.5)

    def test_ticker_gaat_voor_naam(self):
        # Klasse A en C hebben na normalisatie dezelfde naam; de ticker houdt ze uit elkaar.
        uit = vergelijk_top10([_h("Alphabet Inc Class C", 1.4, "GOOG")],
                              [_h("ALPHABET CLASS A", 1.9, "GOOGL"), _h("ALPHABET CLASS C", 1.5, "GOOG")])
        self.assertEqual(uit["rijen"][0]["proxy_pct"], 1.5)


class TestAfwijking(unittest.TestCase):
    def test_grootste_verschil_in_procentpunten(self):
        # |5,2-4,8| = 0,4; |4,7-4,3| = 0,4; |1,9-1,7| = 0,2 -> max 0,4.
        uit = vergelijk_top10(BRON, ACWI)
        self.assertAlmostEqual(uit["max_afwijking_pp"], 0.4)
        self.assertEqual([r["verschil_pp"] for r in uit["rijen"]], [0.4, 0.4, 0.2])

    def test_ontbrekende_holding(self):
        uit = vergelijk_top10(BRON, WORLD)
        self.assertEqual(uit["ontbrekend"], ["Taiwan Semiconductor Manufacturing Co Ltd"])
        tsmc = uit["rijen"][2]
        self.assertEqual((tsmc["proxy_pct"], tsmc["verschil_pp"]), (None, None))
        # Alleen de gevonden holdings tellen voor de afwijking: |5,9-4,8| = 1,1.
        self.assertAlmostEqual(uit["max_afwijking_pp"], 1.1)


class TestKeuze(unittest.TestCase):
    def test_acwi_wint_van_world_met_hogere_gewichten(self):
        keuze = kies_proxy([_beoordeling("IE_WORLD", "World", WORLD), _beoordeling("IE_ACWI", "ACWI", ACWI)])
        self.assertEqual(keuze["gekozen"]["isin"], "IE_ACWI")
        self.assertIsNone(keuze["reden"])

    def test_laagste_afwijking_wint_onder_de_geaccepteerde(self):
        dichtbij = [_h("NVIDIA", 4.9, "NVDA"), _h("APPLE", 4.3, "AAPL"), _h("TSMC", 1.7, "2330")]
        keuze = kies_proxy([_beoordeling("IE_ACWI", "ACWI", ACWI), _beoordeling("IE_DICHT", "Dicht", dichtbij)])
        self.assertEqual(keuze["gekozen"]["isin"], "IE_DICHT")

    def test_geen_kandidaat_geaccepteerd_door_afwijking(self):
        # Alle holdings gevonden, maar 1,5 pp > 1,0 pp.
        te_ver = [_h("NVIDIA", 6.3, "NVDA"), _h("APPLE", 4.3, "AAPL"), _h("TSMC", 1.7, "2330")]
        keuze = kies_proxy([_beoordeling("IE_X", "Te ver", te_ver)])
        self.assertIsNone(keuze["gekozen"])
        self.assertEqual(keuze["beste"]["isin"], "IE_X")
        self.assertEqual(keuze["reden"], "top-10 wijkt max. 1,50 pp af (grens 1,00 pp)")

    def test_holding_ontbreekt_bij_enige_kandidaat(self):
        # World heeft geen TSMC: nooit geaccepteerd, ook al zou de afwijking klein zijn.
        keuze = kies_proxy([_beoordeling("IE_WORLD", "World", WORLD)])
        self.assertIsNone(keuze["gekozen"])
        self.assertEqual(keuze["reden"],
                         "niet gevonden bij de kandidaat: Taiwan Semiconductor Manufacturing Co Ltd")

    def test_geen_kandidaten(self):
        self.assertEqual(kies_proxy([]), {"gekozen": None, "beste": None, "reden": "geen kandidaten"})


class TestLandverdelingMetProxy(unittest.TestCase):
    def test_land_uit_holdings_met_restant_unknown(self):
        # 60 + 30 = 90% gedekt -> 10% Unknown.
        uit = land_uit_holdings([{"gewicht": 60.0, "land": "United States"}, {"gewicht": 30.0, "land": "Japan"}])
        self.assertAlmostEqual(uit["United States"], 0.6)
        self.assertAlmostEqual(uit["Japan"], 0.3)
        self.assertAlmostEqual(uit["Unknown"], 0.1)

    def test_land_uit_proxy_sector_uit_yahoo(self):
        # ETF van 1000 euro: land 70/30 uit de proxy; de eigen top-10 (100% VS, 20% gedekt) wordt niet gebruikt.
        transacties_df = pd.DataFrame({"ticker": ["VWCE.DE"], "aantal": [10.0]})
        price_data = pd.DataFrame({"VWCE.DE": [100.0]}, index=[pd.Timestamp("2024-01-02")])
        proxies = {"VWCE.DE": {
            "proxy_isin": "IE00B6R52259", "proxy_naam": "iShares MSCI ACWI", "max_afwijking_pp": 0.47,
            "proxy_land": {"United States": 0.7, "Japan": 0.3},
        }}
        top10 = [{"holding_naam": "Apple", "holding_ticker": "AAPL", "gewicht": 0.2, "land": "United States",
                  "bron": "yfinance_top10"}]
        with patch.object(portfolio_verdeling, "get_etf_sector_verdeling", return_value={"Technology": 1.0}), \
             patch.object(portfolio_verdeling, "get_etf_holdings", return_value=top10):
            uit = compute_land_sector_verdeling(transacties_df, price_data, {"VWCE.DE": True}, land_proxies=proxies)

        self.assertAlmostEqual(uit["land"]["United States"], 700.0)
        self.assertAlmostEqual(uit["land"]["Japan"], 300.0)
        self.assertNotIn("Unknown", uit["land"])
        self.assertEqual(uit["sector"], {"Technology": 1000.0})
        self.assertEqual(uit["per_etf"]["VWCE.DE"]["land_bron"], "proxy")
        self.assertEqual(uit["per_etf"]["VWCE.DE"]["land_proxy"],
                         {"naam": "iShares MSCI ACWI", "max_afwijking_pp": 0.47})

    def test_zonder_gevonden_proxy_blijft_eigen_top10(self):
        transacties_df = pd.DataFrame({"ticker": ["VWCE.DE"], "aantal": [10.0]})
        price_data = pd.DataFrame({"VWCE.DE": [100.0]}, index=[pd.Timestamp("2024-01-02")])
        geen_proxy = {"VWCE.DE": {"proxy_isin": None, "proxy_naam": None, "max_afwijking_pp": None,
                                  "proxy_land": None}}
        top10 = [{"holding_naam": "Apple", "holding_ticker": "AAPL", "gewicht": 0.2, "land": "United States",
                  "bron": "yfinance_top10"}]
        with patch.object(portfolio_verdeling, "get_etf_sector_verdeling", return_value={}), \
             patch.object(portfolio_verdeling, "get_etf_holdings", return_value=top10):
            uit = compute_land_sector_verdeling(transacties_df, price_data, {"VWCE.DE": True},
                                                land_proxies=geen_proxy)

        self.assertAlmostEqual(uit["land"]["Unknown"], 800.0)
        self.assertEqual(uit["per_etf"]["VWCE.DE"]["land_bron"], "yfinance_top10")
        self.assertIsNone(uit["per_etf"]["VWCE.DE"]["land_proxy"])


if __name__ == "__main__":
    unittest.main()
