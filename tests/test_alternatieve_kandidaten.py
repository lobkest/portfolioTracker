"""
Unit tests voor het verrijken van alternatieve tickerkandidaten op de
Ticker-zekerheid-pagina (analysis._verzamel_extra_kandidaten /
verifieer_ticker_met_prijs) -- zie CLAUDE.md/opdracht_alternatieve_
kandidaten_dagrange.md.

Achtergrond: de "Alternatieve kandidaten"-tabel op de Ticker-zekerheid-
pagina toonde niets zodra de oorspronkelijke zoekopdracht die de gekozen
ticker vond toevallig geen restlijst had (bv. BYD/CNE100000296: de query die
4BY1.F vond leverde maar dat ene resultaat op). find_ticker_detailed()'s
'alternatieven' is namelijk geen eigen zoekopdracht naar alternatieven, puur
een restlijst. _verzamel_extra_kandidaten() doet nu een gerichte extra
zoekopdracht (volledige productnaam + ISIN, zonder beurs-beperking) zodra
die restlijst leeg is EN de ticker niet "zeker" is.

Draait geheel offline: analysis.find_ticker_detailed, analysis._yahoo_search
en analysis.vergelijk_prijs_op_datum worden gemockt, dus geen echte
yahooquery/yfinance-calls.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import verifieer_ticker_met_prijs, _verzamel_extra_kandidaten

# verifieer_ticker_met_prijs() roept sinds de OpenFIGI-root-check (zie
# _voeg_openfigi_check_toe in analysis.py) altijd haal_openfigi_resultaten()
# aan, die zonder deze patch een echte DB/netwerk-call zou doen. Module-breed
# op "geen resultaten" gepatcht zodat deze tests offline en ongewijzigd
# blijven -- _openfigi_root_bekend() geeft dan None terug (geen oordeel).
_openfigi_patcher = None


def setUpModule():
    global _openfigi_patcher
    _openfigi_patcher = patch.object(
        analysis, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}
    )
    _openfigi_patcher.start()


def tearDownModule():
    _openfigi_patcher.stop()


def _prijscheck(match, binnen_dagrange=True, afwijking_pct=0.0, yahoo_koers=100.0):
    return {
        "yahoo_koers": yahoo_koers, "bekende_koers": 100.0, "afwijking_pct": afwijking_pct,
        "match": match, "binnen_dagrange": binnen_dagrange, "niveau": None,
        "high": yahoo_koers + 1, "low": yahoo_koers - 1,
    }


class TestVerzamelExtraKandidaten(unittest.TestCase):
    """Pure dedup-/uitsluitlogica van _verzamel_extra_kandidaten() zelf,
    los van verifieer_ticker_met_prijs()."""

    def test_dedupliceert_en_sluit_gekozen_ticker_uit(self):
        def fake_search(query):
            if query == "PRODUCT NAAM":
                return [
                    {"symbol": "GEKOZEN", "exchange": "TDG"},  # is de al gekozen ticker -> uitgesloten
                    {"symbol": "ALT1", "exchange": "MUN"},
                    {"symbol": "ALT1", "exchange": "MUN"},  # dubbel binnen dezelfde query
                ]
            if query == "ISIN123":
                return [
                    {"symbol": "ALT1", "exchange": "MUN"},  # dubbel t.o.v. de eerste query
                    {"symbol": "BESTAAND", "exchange": "FRA"},  # dubbel t.o.v. bestaande_alternatieven
                    {"symbol": "ALT2", "exchange": "FRA"},
                ]
            return []

        with patch.object(analysis, "_yahoo_search", side_effect=fake_search) as mock_search:
            resultaat = _verzamel_extra_kandidaten(
                "PRODUCT NAAM", "ISIN123",
                bestaande_alternatieven=[{"symbol": "BESTAAND", "exchange": "FRA"}],
                uitgesloten_ticker="GEKOZEN",
            )

        mock_search.assert_any_call("PRODUCT NAAM")
        mock_search.assert_any_call("ISIN123")
        self.assertEqual([r["symbol"] for r in resultaat], ["ALT1", "ALT2"])

    def test_geen_kandidaten_gevonden_geeft_lege_lijst(self):
        with patch.object(analysis, "_yahoo_search", return_value=[]):
            resultaat = _verzamel_extra_kandidaten("PRODUCT", "ISIN", [], "GEKOZEN")
        self.assertEqual(resultaat, [])


class TestExtraZoekopdrachtBijLegeAlternatieven(unittest.TestCase):
    """BYD-achtig geval: de oorspronkelijke zoekopdracht die de gekozen
    ticker vond leverde geen restlijst op (alternatieven=[]). Zodra de
    prijscheck de ticker naar 'onzeker' degradeert, moet
    verifieer_ticker_met_prijs() een extra, gerichte zoekopdracht doen zodat
    er alsnog kandidaten getoond kunnen worden."""

    def setUp(self):
        self.transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        for name, value in (
            ("_ticker_details_met_cache", {}),
            ("_land_sector_voor_weergave", (None, None, None)),
            ("classify_ticker", False),
        ):
            p = patch.object(analysis, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

        find_patch = patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "4BY1.F", "zekerheid": "zeker", "alternatieven": []},
        )
        find_patch.start()
        self.addCleanup(find_patch.stop)

    def test_lege_alternatieven_triggert_extra_zoekopdracht(self):
        def fake_yahoo_search(query):
            if query == "BYD COMPANY LIMITED":
                # Zelfde spelling vindt alleen de al gekozen (foute) notering.
                return [{"symbol": "4BY1.F", "exchange": "FRA"}]
            if query == "CNE100000296":
                return [{"symbol": "BY6.MU", "exchange": "MUN"}]
            return []

        def fake_vergelijk(ticker, datum, bekende_koers):
            if ticker == "4BY1.F":
                return _prijscheck(match=False, binnen_dagrange=False, afwijking_pct=90.0, yahoo_koers=10.0)
            if ticker == "BY6.MU":
                return _prijscheck(match=True, binnen_dagrange=True)
            raise AssertionError(f"onverwachte ticker {ticker}")

        with patch.object(analysis, "_yahoo_search", side_effect=fake_yahoo_search) as mock_search, \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            resultaat = verifieer_ticker_met_prijs(
                "BYD COMPANY LIMITED", "CNE100000296", "TDG", self.transacties
            )

        mock_search.assert_any_call("BYD COMPANY LIMITED")
        mock_search.assert_any_call("CNE100000296")
        self.assertEqual(resultaat["zekerheid"], "onzeker")
        alt_tickers = [a["ticker"] for a in resultaat["alternatieven"]]
        self.assertIn("BY6.MU", alt_tickers)
        self.assertEqual(resultaat["aanbevolen_alternatief"], "BY6.MU")

    def test_ook_extra_zoekopdracht_levert_niets_op_blijft_lege_lijst_geen_crash(self):
        with patch.object(analysis, "_yahoo_search", return_value=[]) as mock_search, \
             patch.object(analysis, "vergelijk_prijs_op_datum",
                           side_effect=lambda *a, **kw: _prijscheck(match=False, binnen_dagrange=False)):
            resultaat = verifieer_ticker_met_prijs(
                "BYD COMPANY LIMITED", "CNE100000296", "TDG", self.transacties
            )

        self.assertTrue(mock_search.called)
        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertEqual(resultaat["alternatieven"], [])


class TestGevuldeAlternatievenNietOverschreven(unittest.TestCase):
    """TDT.AS-achtig geval: de oorspronkelijke zoekopdracht leverde al
    kandidaten op. De extra zoekopdracht mag dan niet draaien -- bestaande
    kandidaten blijven ongewijzigd en er ontstaan geen dubbele entries."""

    def setUp(self):
        self.transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        for name, value in (
            ("_ticker_details_met_cache", {}),
            ("_land_sector_voor_weergave", (None, None, None)),
            ("classify_ticker", True),
        ):
            p = patch.object(analysis, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

        find_patch = patch.object(
            analysis, "find_ticker_detailed",
            return_value={
                "ticker": "TDT.MU",
                "zekerheid": "zeker",
                "alternatieven": [
                    {"symbol": "TDT.AS", "exchange": "MUN"},
                    {"symbol": "TDT.L", "exchange": "LSE"},
                ],
            },
        )
        find_patch.start()
        self.addCleanup(find_patch.stop)

    def test_bestaande_kandidaten_blijven_ongewijzigd_geen_extra_zoekopdracht(self):
        def fake_vergelijk(ticker, datum, bekende_koers):
            if ticker == "TDT.MU":
                return _prijscheck(match=False, binnen_dagrange=False, afwijking_pct=15.0, yahoo_koers=85.0)
            if ticker == "TDT.AS":
                # Overtuigende match (juiste beurs + kloppende prijs) -> de
                # loop in _zoek_betere_alternatieven stopt hierna, TDT.L
                # wordt niet meer gecheckt.
                return _prijscheck(match=True, binnen_dagrange=True)
            raise AssertionError(f"'{ticker}' had niet meer gecheckt mogen worden")

        with patch.object(analysis, "_yahoo_search") as mock_search, \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            resultaat = verifieer_ticker_met_prijs(
                "VANECK AEX UCITS ETF", "NL0009690239", "TDG", self.transacties
            )

        mock_search.assert_not_called()
        alt_tickers = [a["ticker"] for a in resultaat["alternatieven"]]
        self.assertEqual(alt_tickers, ["TDT.AS"])
        self.assertEqual(len(alt_tickers), len(set(alt_tickers)))
        self.assertEqual(resultaat["aanbevolen_alternatief"], "TDT.AS")


class TestZekerGeenExtraZoekopdracht(unittest.TestCase):
    """Bij een zekere match (ook ná de prijscheck) mag er geen enkele extra
    _yahoo_search-call gebeuren -- bestaand gedrag voor zekere matches blijft
    ongewijzigd."""

    def setUp(self):
        self.transacties = [{"datum": date(2023, 1, 10), "koers": 100.0}]
        for name, value in (
            ("_ticker_details_met_cache", {}),
            ("_land_sector_voor_weergave", (None, None, None)),
            ("classify_ticker", False),
        ):
            p = patch.object(analysis, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

        find_patch = patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []},
        )
        find_patch.start()
        self.addCleanup(find_patch.stop)

    def test_geen_yahoo_search_call_bij_zekere_match(self):
        with patch.object(analysis, "_yahoo_search") as mock_search, \
             patch.object(analysis, "vergelijk_prijs_op_datum",
                           side_effect=lambda *a, **kw: _prijscheck(match=True, binnen_dagrange=True)):
            resultaat = verifieer_ticker_met_prijs("APPLE INC", "US0378331005", "NASDAQ", self.transacties)

        mock_search.assert_not_called()
        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertEqual(resultaat["alternatieven"], [])


if __name__ == "__main__":
    unittest.main()
