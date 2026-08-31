"""
Unit tests voor analysis.basis_ticker_zekerheid() -- de goedkope,
NIET-prijsgeverifieerde ticker-zekerheid die het 'niet opslaan'-pad in
app.py sinds het Statistieken-incident van 2026-08-31 standaard gebruikt
i.p.v. altijd de dure verifieer_tickers_met_prijs_parallel() voor de volle
portfolio te draaien (zie CLAUDE.md). Puur: alleen find_ticker_detailed()
wordt gemockt, geen echte yahooquery/DB-aanroepen.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import basis_ticker_zekerheid


class TestBasisTickerZekerheid(unittest.TestCase):
    def test_zekere_match_geeft_ticker_en_zekerheid_door(self):
        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AKZA.AS", "zekerheid": "zeker", "alternatieven": []},
        ):
            resultaat = basis_ticker_zekerheid("AKZO NOBEL NV", "NL0013267909", "EAM")

        self.assertEqual(resultaat["ticker"], "AKZA.AS")
        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertTrue(resultaat["basis_alleen"])

    def test_geen_prijsverificatie_uitgevoerd_dus_alle_prijsvelden_leeg(self):
        # Het hele punt van deze lichte variant: geen enkele Yahoo-
        # prijscall, dus land/sector/valuta/prijs_checks/alternatieven
        # (die verifieer_ticker_met_prijs WEL zou vullen) blijven leeg.
        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "GDX.L", "zekerheid": "onzeker", "alternatieven": [{"symbol": "VEF5.MU", "exchange": "MUN"}]},
        ):
            resultaat = basis_ticker_zekerheid("VANECK GOLD MINERS", "IE00BQQP9F84", "TDG")

        self.assertIsNone(resultaat["waarschuwing"])
        self.assertIsNone(resultaat["land"])
        self.assertIsNone(resultaat["sector"])
        self.assertIsNone(resultaat["valuta"])
        self.assertIsNone(resultaat["yahoo_beurs"])
        self.assertIsNone(resultaat["beurs_klopt"])
        self.assertEqual(resultaat["prijs_checks"], [])
        # De alternatieven van find_ticker_detailed() worden bewust NIET
        # doorgegeven: die hebben een ander veldformaat (symbol/exchange)
        # dan wat de frontend-kaart voor prijs-geverifieerde alternatieven
        # verwacht (ticker/beurs/land/sector/valuta/...), en zonder
        # prijscheck kan er toch geen aanbevolen alternatief bepaald worden.
        self.assertEqual(resultaat["alternatieven"], [])

    def test_excel_beurs_wordt_overgenomen_yahoo_beurs_nog_onbekend(self):
        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []},
        ):
            resultaat = basis_ticker_zekerheid("APPLE INC", "US0378331005", "NSY")

        self.assertEqual(resultaat["excel_beurs"], "NSY")
        self.assertIsNone(resultaat["yahoo_beurs"])

    def test_geen_ticker_gevonden(self):
        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": None, "zekerheid": "geen_match", "alternatieven": []},
        ):
            resultaat = basis_ticker_zekerheid("ONBEKEND FONDS", "XX0000000000", "XYZ")

        self.assertIsNone(resultaat["ticker"])
        self.assertEqual(resultaat["zekerheid"], "geen_match")


if __name__ == "__main__":
    unittest.main()
