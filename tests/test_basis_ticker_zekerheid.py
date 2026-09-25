"""
Unit tests voor de basis-vorm van ticker_zekerheid.basis_ticker_zekerheid_
parallel() -- de goedkope, NIET-prijsgeverifieerde ticker-zekerheid die het
'niet opslaan'-pad in app.py (zie CLAUDE.md: Yahoo en tickers)
standaard gebruikt i.p.v. altijd de dure verifieer_tickers_met_prijs_
parallel() voor de volle portfolio te draaien. Hier steeds met 1 positie;
volgorde bij meerdere posities staat in test_snelle_prijscheck.py. Puur:
alleen find_ticker_detailed() wordt gemockt, geen echte yahooquery/DB-
aanroepen.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ticker_zekerheid
from ticker_zekerheid import basis_ticker_zekerheid_parallel


def _basis_voor_een_positie(product, isin, beurs):
    """Basis-vorm-resultaat voor 1 positie zonder transacties."""
    return basis_ticker_zekerheid_parallel([(product, isin, beurs, [])])[0]


# basis_ticker_zekerheid_parallel() draait via find_ticker_met_snelle_prijscheck(),
# dat sinds de OpenFIGI-root-check (zie _voeg_openfigi_check_toe in
# ticker_zekerheid.py) altijd haal_openfigi_resultaten() aanroept -- zonder deze
# patch dus een echte DB/netwerk-call. Module-breed op "geen resultaten"
# gepatcht zodat deze tests offline blijven, net als in de andere
# find_ticker_met_snelle_prijscheck-tests.
_openfigi_patcher = None


def setUpModule():
    global _openfigi_patcher
    _openfigi_patcher = patch.object(
        ticker_zekerheid, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}
    )
    _openfigi_patcher.start()


def tearDownModule():
    _openfigi_patcher.stop()


class TestBasisTickerZekerheid(unittest.TestCase):
    def test_zekere_match_geeft_ticker_en_zekerheid_door(self):
        with patch.object(
            ticker_zekerheid, "find_ticker_detailed",
            return_value={"ticker": "AKZA.AS", "zekerheid": "zeker", "alternatieven": []},
        ):
            resultaat = _basis_voor_een_positie("AKZO NOBEL NV", "NL0013267909", "EAM")

        self.assertEqual(resultaat["ticker"], "AKZA.AS")
        self.assertEqual(resultaat["zekerheid"], "zeker")

    def test_geen_prijsverificatie_uitgevoerd_dus_alle_prijsvelden_leeg(self):
        # Het hele punt van deze lichte variant: geen enkele Yahoo-
        # prijscall, dus land/sector/valuta/prijs_checks/alternatieven
        # (die verifieer_ticker_met_prijs WEL zou vullen) blijven leeg.
        with patch.object(
            ticker_zekerheid, "find_ticker_detailed",
            return_value={"ticker": "GDX.L", "zekerheid": "onzeker", "alternatieven": [{"symbol": "VEF5.MU", "exchange": "MUN"}]},
        ):
            resultaat = _basis_voor_een_positie("VANECK GOLD MINERS", "IE00BQQP9F84", "TDG")

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
            ticker_zekerheid, "find_ticker_detailed",
            return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []},
        ):
            resultaat = _basis_voor_een_positie("APPLE INC", "US0378331005", "NSY")

        self.assertEqual(resultaat["excel_beurs"], "NSY")
        self.assertIsNone(resultaat["yahoo_beurs"])

    def test_geen_ticker_gevonden(self):
        with patch.object(
            ticker_zekerheid, "find_ticker_detailed",
            return_value={"ticker": None, "zekerheid": "geen_match", "alternatieven": []},
        ):
            resultaat = _basis_voor_een_positie("ONBEKEND FONDS", "XX0000000000", "XYZ")

        self.assertIsNone(resultaat["ticker"])
        self.assertEqual(resultaat["zekerheid"], "geen_match")


if __name__ == "__main__":
    unittest.main()
