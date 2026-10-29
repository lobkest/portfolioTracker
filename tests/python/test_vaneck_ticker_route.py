"""VanEck-route: holdings via de ticker-URL van VanEck. Alles gemockt, nooit vaneck.com."""
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import openpyxl
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_dividend, effect_profiel, effect_zoeken, ticker_classificatie as tc
from backend.etf_holdings_provider import (
    aanbieder_naam, fetch_vaneck_holdings_via_ticker, is_vaneck_naam, ticker_root, vaneck_holdings_url,
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EIND_URL = "https://www.vaneck.com/nl/en/investments/dividend-etf/downloads/holdings/"
VANECK_NAAM = "VanEck Morningstar Developed Markets Dividend Leaders UCITS ETF"


def _xlsx(gewichten):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Holdings per 27-10-2026"])
    ws.append([])
    ws.append(["Number", "Holding Name", "Ticker", "ISIN", "% of Net Assets"])
    isins = ["US0378331005", "NL0010273215", "GB00BP6MXD84"]
    for i, gewicht in enumerate(gewichten):
        ws.append([i + 1, f"Bedrijf {i}", f"B{i} US", isins[i % 3], f"{gewicht}%"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _antwoord(content=None, status=200, content_type=XLSX, url=EIND_URL):
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": content_type}
    r.url = url
    r.content = content if content is not None else _xlsx([50.0, 30.0, 20.0])
    return r


def _haal_op(antwoorden, roots):
    with patch("backend.etf_holdings_provider.requests.Session") as sessie_klasse, \
            patch("backend.etf_holdings_provider.dprint"):
        sessie = sessie_klasse.return_value
        sessie.headers = {}
        sessie.get.side_effect = antwoorden
        return fetch_vaneck_holdings_via_ticker(roots), sessie


class TestHerkenning(unittest.TestCase):
    def test_is_vaneck_naam(self):
        self.assertTrue(is_vaneck_naam(VANECK_NAAM))
        self.assertTrue(is_vaneck_naam("vaneck aex"))
        self.assertFalse(is_vaneck_naam("iShares Core MSCI World"))
        self.assertFalse(is_vaneck_naam(None))

    def test_url_en_root(self):
        self.assertEqual(vaneck_holdings_url("TDIV"),
                         "https://www.vaneck.com/nl/en/investments/TDIV/downloads/holdings/")
        self.assertEqual(ticker_root("tdiv.as"), "TDIV")


class TestFetchViaTicker(unittest.TestCase):
    def test_geldig_bestand_geeft_holdings_root_en_eind_url(self):
        (holdings, root, eind_url), sessie = _haal_op([_antwoord()], ["TDIV"])
        self.assertEqual((len(holdings), root, eind_url), (3, "TDIV", EIND_URL))
        sessie.get.assert_called_once_with(vaneck_holdings_url("TDIV"), timeout=30, allow_redirects=True)

    def test_disabled_cookies_is_mislukt(self):
        antwoord = _antwoord(url="https://www.vaneck.com/nl/en/disabled-cookies/")
        self.assertIsNone(_haal_op([antwoord], ["TDIV"])[0])

    def test_html_is_mislukt(self):
        self.assertIsNone(_haal_op([_antwoord(content_type="text/html; charset=utf-8")], ["TDIV"])[0])

    def test_status_404_is_mislukt(self):
        self.assertIsNone(_haal_op([_antwoord(status=404)], ["TDIV"])[0])

    def test_gewichtssom_van_20_procent_is_mislukt(self):
        self.assertIsNone(_haal_op([_antwoord(content=_xlsx([12.0, 8.0]))], ["TDIV"])[0])

    def test_gewichtssom_boven_105_procent_is_mislukt(self):
        self.assertIsNone(_haal_op([_antwoord(content=_xlsx([60.0, 50.0]))], ["TDIV"])[0])

    def test_eerste_root_faalt_tweede_slaagt(self):
        (holdings, root, _url), sessie = _haal_op([_antwoord(status=404), _antwoord()], ["AAAA", "TDIV"])
        self.assertEqual(root, "TDIV")
        self.assertEqual(sessie.get.call_count, 2)

    def test_een_uitzondering_telt_als_mislukte_poging(self):
        (_h, root, _u), _s = _haal_op([RuntimeError("timeout"), _antwoord()], ["AAAA", "TDIV"])
        self.assertEqual(root, "TDIV")

    def test_nooit_meer_dan_3_pogingen_en_dubbele_roots_tellen_een_keer(self):
        resultaat, sessie = _haal_op([_antwoord(status=404)] * 6, ["A", "A", "B", "C", "D", "E"])
        self.assertIsNone(resultaat)
        self.assertEqual(sessie.get.call_count, 3)


class _EtfHoldings(unittest.TestCase):
    def draai(self, ticker="TDIV.AS", details=None, cached=None, vaneck=None, quotes=None, isin=None, bron=None):
        details = {"long_name": VANECK_NAAM} if details is None else details
        gevonden = ([{"naam": "Bedrijf", "gewicht": 100.0, "land": "United States", "sector": None, "ticker": None}],
                    "TDIV", EIND_URL) if vaneck is None else vaneck
        patches = {
            "db_get_cached_etf_holdings": dict(return_value=cached),
            "db_save_etf_holdings": dict(),
            "get_ticker_details": dict(return_value=details),
            "ishares_fondsen": dict(return_value=[]),
            "fetch_vaneck_holdings_via_ticker": dict(side_effect=lambda roots: gevonden if gevonden else None),
            "_yq_top_holdings": dict(return_value=None),
            "get_land_sector": dict(return_value=("United States", "IT")),
            "_tel_yahoo_call": dict(),
            "dprint": dict(),
        }
        self.m = {}
        for naam, kwargs in patches.items():
            p = patch.object(tc, naam, **kwargs)
            self.m[naam] = p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(tc.yf, "Ticker", MagicMock(side_effect=RuntimeError("geen yfinance"))),
                  patch.object(effect_zoeken, "zoek_gecachet", return_value=quotes or []),
                  patch.object(tc, "ETF_HOLDINGS_BRON", bron or {}),
                  patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)
        return tc.get_etf_holdings(ticker, isin)


class TestGetEtfHoldingsVaneck(_EtfHoldings):
    def test_vaneck_ticker_geeft_provider_holdings_onder_de_ticker(self):
        holdings = self.draai()
        self.assertEqual([(h["holding_naam"], h["gewicht"], h["bron"]) for h in holdings],
                         [("Bedrijf", 1.0, "provider_csv")])
        self.m["db_save_etf_holdings"].assert_called_once_with("TDIV.AS", holdings)
        self.m["fetch_vaneck_holdings_via_ticker"].assert_called_once_with(["TDIV"])

    def test_niet_vaneck_probeert_de_route_niet(self):
        holdings = self.draai(details={"long_name": "iShares Core MSCI World"})
        self.m["fetch_vaneck_holdings_via_ticker"].assert_not_called()
        self.assertEqual(holdings, [])

    def test_vaneck_zonder_ucits_probeert_de_route_niet(self):
        holdings = self.draai(details={"long_name": "VanEck Semiconductor ETF"})
        self.m["fetch_vaneck_holdings_via_ticker"].assert_not_called()
        self.assertEqual(holdings, [])

    def test_ticker_in_de_lijst_gebruikt_de_lijst_en_niet_de_route(self):
        with patch.object(tc, "fetch_provider_holdings",
                          return_value=[{"naam": "X", "gewicht": 100.0, "land": "Japan"}]) as lijst:
            holdings = self.draai(bron={"TDIV.AS": {"provider": "vaneck", "url": "u"}})
        lijst.assert_called_once_with("TDIV.AS")
        self.m["fetch_vaneck_holdings_via_ticker"].assert_not_called()
        self.assertEqual(holdings[0]["holding_naam"], "X")

    def test_yfinance_top10_cache_wordt_geupgraded(self):
        cache = [{"holding_naam": "Apple", "holding_ticker": "AAPL", "gewicht": 0.07, "land": "US",
                  "bron": "yfinance_top10"}]
        holdings = self.draai(cached=cache)
        self.assertEqual(holdings[0]["bron"], "provider_csv")
        self.m["db_save_etf_holdings"].assert_called_once()

    def test_een_miss_wordt_onthouden_en_valt_terug_op_de_cache(self):
        cache = [{"holding_naam": "Apple", "holding_ticker": "AAPL", "gewicht": 0.07, "land": "US",
                  "bron": "yfinance_top10"}]
        self.draai(cached=None, vaneck=False)
        self.assertEqual(self.m["fetch_vaneck_holdings_via_ticker"].call_count, 1)
        self.m["db_get_cached_etf_holdings"].return_value = cache
        self.assertIs(tc.get_etf_holdings("TDIV.AS"), cache)
        self.assertEqual(self.m["fetch_vaneck_holdings_via_ticker"].call_count, 1)

    def test_andere_noteringen_van_dezelfde_naam_worden_geprobeerd_zonder_dubbele_roots(self):
        quotes = [
            {"symbol": "TDIV.AS", "quoteType": "ETF", "longname": VANECK_NAAM, "exchange": "AMS"},
            {"symbol": "TDIV.DE", "quoteType": "ETF", "longname": VANECK_NAAM, "exchange": "GER"},
            {"symbol": "TDGB.L", "quoteType": "ETF", "longname": VANECK_NAAM, "exchange": "LSE"},
            {"symbol": "ANDER.AS", "quoteType": "ETF", "longname": "VanEck Iets Anders", "exchange": "AMS"},
        ]
        gevonden = ([{"naam": "B", "gewicht": 100.0, "land": "Japan", "sector": None, "ticker": None}], "TDGB", EIND_URL)
        resultaten = iter([None, gevonden])
        self.draai(quotes=quotes)
        self.m["fetch_vaneck_holdings_via_ticker"].side_effect = lambda roots: next(resultaten)
        tc.wis_geen_fondsdata_cache()
        holdings = tc.get_etf_holdings("TDIV.AS")
        aanroepen = [c.args[0] for c in self.m["fetch_vaneck_holdings_via_ticker"].call_args_list]
        self.assertEqual(aanroepen[-2:], [["TDIV"], ["TDGB"]])
        self.assertEqual(holdings[0]["holding_naam"], "B")


class TestAanbiederNaam(unittest.TestCase):
    def test_vaneck_route_geeft_vaneck(self):
        self.assertEqual(aanbieder_naam("TDIV.AS", "VANECK MORNINGSTAR DIVIDEND LEADERS"), "VanEck")

    def test_overig_blijft_zoals_het_was(self):
        self.assertEqual(aanbieder_naam("CSPX.AS", "ISHARES CORE S&P 500"), "iShares")
        self.assertEqual(aanbieder_naam("ONBEKEND.AS"), "iShares")
        self.assertEqual(aanbieder_naam("GDX.L", "iets anders"), "VanEck")


class TestProfielTdiv(_EtfHoldings):
    def test_profiel_heeft_holdings_landen_en_top5_zonder_holdingswaarschuwing(self):
        rijen = [{"naam": f"Bedrijf {i}", "gewicht": g, "land": land, "sector": None, "ticker": None}
                 for i, (g, land) in enumerate([(30.0, "United States"), (25.0, "Japan"), (15.0, "Germany"),
                                                (12.0, "France"), (10.0, "Japan"), (8.0, "Canada")])]
        koersen = pd.DataFrame({"TDIV.AS": [30.0]}, index=pd.to_datetime(["2026-10-26"]))
        profiel_patches = {
            "get_prices": dict(return_value=koersen),
            "get_ticker_details": dict(return_value={"is_etf": True, "long_name": VANECK_NAAM, "valuta": "EUR"}),
            "get_etf_sector_verdeling": dict(return_value={"Technology": 0.7}),
            "get_etf_sector_bron": dict(return_value="yahoo"),
            "get_etf_holdings": dict(side_effect=tc.get_etf_holdings),
            "get_etf_holdings_voor_isin": dict(return_value=None),
            "db_get_isins_voor_ticker": dict(return_value=[]),
        }
        for naam, kwargs in profiel_patches.items():
            p = patch.object(effect_profiel, naam, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        for naam, kwargs in {"haal_yahoo_dividenden": dict(return_value=None), "_haal_splits_op": dict(return_value={}),
                             "_fx_actueel": dict(return_value=1.0)}.items():
            p = patch.object(effect_dividend, naam, **kwargs)
            p.start()
            self.addCleanup(p.stop)

        with patch.object(tc, "get_ticker_details", return_value={"long_name": VANECK_NAAM}):
            self.draai(vaneck=(rijen, "TDIV", EIND_URL))  # zet de tc-patches; de aanroep zelf vult de cache niet
            self.m["db_get_cached_etf_holdings"].return_value = None
            profiel = effect_profiel.bouw_effect_profiel("TDIV.AS")

        self.assertEqual(profiel["holdings_route"], "provider_ticker")
        self.assertEqual(profiel["land_bron"], "provider_csv")
        self.assertAlmostEqual(profiel["land"]["Japan"], 0.35)
        self.assertEqual(len(profiel["holdings"]["top5"]), 5)
        self.assertEqual(profiel["holdings"]["top5"][0]["naam"], "Bedrijf 0")
        self.assertNotIn("geen_holdings", profiel["waarschuwingen"])
        self.assertNotIn("alleen_top10", profiel["waarschuwingen"])


if __name__ == "__main__":
    unittest.main()
