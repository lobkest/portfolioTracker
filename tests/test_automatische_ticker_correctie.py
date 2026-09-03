"""
Unit tests voor de tweetraps automatische ticker-correctie in
analysis.find_ticker_met_snelle_prijscheck() (stap 3).

Achtergrond: bij een forse prijsafwijking rekent stap 3 alternatieve
kandidaat-tickers door (_zoek_betere_alternatieven). Vroeger was dat puur
informatief ('aanbevolen_alternatief'); nu wordt een alternatief in twee
gevallen automatisch overgenomen:
  - Tier 1: kandidaat staat op een VERWACHTE beurs (BEURS_MAP) en de prijs
    klopt op >= MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE steekproefdatums.
  - Tier 2 (alleen als tier 1 niets oplevert): kandidaat staat op een
    ANDERE beurs, maar de prijs klopt op ALLE gecontroleerde
    steekproefdatums (Vanguard/iShares-scenario: de juiste UCITS-notering
    staat regelmatig op een andere beurs dan DEGIRO's beurscode verwacht).

Draait geheel offline: find_ticker_detailed, vergelijk_prijs_op_datum en
_zoek_betere_alternatieven worden gemockt, dus geen echte yahooquery/
yfinance-calls.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis

# find_ticker_met_snelle_prijscheck() roept sinds de OpenFIGI-root-check
# (zie _voeg_openfigi_check_toe in analysis.py) altijd haal_openfigi_
# resultaten() aan, die zonder deze patch een echte DB/netwerk-call zou
# doen. Module-breed op "geen resultaten" gepatcht zodat de bestaande
# tests hier offline en ongewijzigd blijven -- _openfigi_root_bekend()
# geeft dan None terug (geen oordeel), dus geen effect op deze tests.
_openfigi_patcher = None


def setUpModule():
    global _openfigi_patcher
    _openfigi_patcher = patch.object(
        analysis, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}
    )
    _openfigi_patcher.start()


def tearDownModule():
    _openfigi_patcher.stop()


def _afwijkende_check():
    return {
        "yahoo_koers": 1.0, "yahoo_koers_gecorrigeerd": 1.0, "split_factor": 1.0,
        "bekende_koers": 10.0, "afwijking_pct": 20.0, "niveau": "waarschuwing",
        "match": False, "high": None, "low": None, "binnen_dagrange": None,
    }


class TestAutomatischeTickerCorrectie(unittest.TestCase):
    def setUp(self):
        self.transacties = [
            {"datum": "2024-01-01", "koers": 10.0},
            {"datum": "2024-06-01", "koers": 10.0},
            {"datum": "2024-12-01", "koers": 10.0},
        ]

    @patch("analysis._zoek_betere_alternatieven")
    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_tier1_beurs_en_2_matches_wordt_overgenomen(self, mock_find, mock_vergelijk, mock_alt):
        mock_find.return_value = {
            "ticker": "FOUT.AS", "zekerheid": "zeker",
            "alternatieven": [{"symbol": "GOED.AS", "exchange": "AMS"}],
        }
        mock_vergelijk.return_value = _afwijkende_check()
        mock_alt.return_value = (
            [{"ticker": "GOED.AS", "beurs": "AMS", "is_etf": True, "land": None,
              "sector": None, "high": None, "low": None, "valuta": "EUR",
              "gemiddelde_afwijking_pct": 1.0, "aantal_matches": 2}],  # 2 van de 3 datums
            "GOED.AS",
        )
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "Fout Fonds", "NL000TEST01", "EAM", self.transacties
        )
        self.assertEqual(resultaat["ticker"], "GOED.AS")
        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertEqual(resultaat.get("automatisch_gecorrigeerd_van"), "FOUT.AS")

    @patch("analysis._zoek_betere_alternatieven")
    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_tier2_andere_beurs_maar_alle_datums_wordt_overgenomen(self, mock_find, mock_vergelijk, mock_alt):
        # Vanguard/iShares-scenario: kandidaat NIET op AMS (verwachte
        # beurs voor EAM), maar wel alle 3 steekproefdatums kloppend.
        mock_find.return_value = {
            "ticker": "VUSA.AS", "zekerheid": "zeker",
            "alternatieven": [{"symbol": "VUAA.L", "exchange": "LSE"}],
        }
        mock_vergelijk.return_value = _afwijkende_check()
        mock_alt.return_value = (
            [{"ticker": "VUAA.L", "beurs": "LSE", "is_etf": True, "land": None,
              "sector": None, "high": None, "low": None, "valuta": "USD",
              "gemiddelde_afwijking_pct": 0.8, "aantal_matches": 3}],  # alle 3
            "VUAA.L",
        )
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "Vanguard S&P 500 UCITS ETF USD Dis", "IE00B3XXRP09", "EAM", self.transacties
        )
        self.assertEqual(resultaat["ticker"], "VUAA.L")
        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertEqual(resultaat.get("automatisch_gecorrigeerd_van"), "VUSA.AS")

    @patch("analysis._zoek_betere_alternatieven")
    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_andere_beurs_met_slechts_2_van_3_wordt_NIET_overgenomen(self, mock_find, mock_vergelijk, mock_alt):
        # Zelfde als hierboven, maar nu maar 2 van de 3 datums matchen op
        # de andere beurs -- te weinig voor tier 2 (die eist ALLE datums).
        mock_find.return_value = {
            "ticker": "VUSA.AS", "zekerheid": "zeker",
            "alternatieven": [{"symbol": "TWIJFEL.L", "exchange": "LSE"}],
        }
        mock_vergelijk.return_value = _afwijkende_check()
        mock_alt.return_value = (
            [{"ticker": "TWIJFEL.L", "beurs": "LSE", "is_etf": True, "land": None,
              "sector": None, "high": None, "low": None, "valuta": "USD",
              "gemiddelde_afwijking_pct": 3.0, "aantal_matches": 2}],  # niet alle 3
            "TWIJFEL.L",
        )
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "Vanguard S&P 500 UCITS ETF USD Dis", "IE00B3XXRP09", "EAM", self.transacties
        )
        self.assertEqual(resultaat["ticker"], "VUSA.AS")  # NIET overgenomen
        self.assertEqual(resultaat.get("aanbevolen_alternatief"), "TWIJFEL.L")
        self.assertNotIn("automatisch_gecorrigeerd_van", resultaat)


if __name__ == "__main__":
    unittest.main()
