"""
Unit tests voor de TDG (Tradegate) -> NMS/NYQ-uitbreiding in BEURS_MAP.

Draait geheel offline: _kies_beurs_match is een pure functie (geen
netwerk/DB), dus met synthetische quotes-lijsten te testen. Regressietest
voor het Netflix-geval: TDG-aandelen die alleen op hun Amerikaanse
thuismarkt genoteerd staan (Yahoo indexeert ze niet apart onder een Duitse
notering) werden voorheen altijd als "beurs komt niet overeen" gemarkeerd,
ook al was de gevonden ticker correct.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import _kies_beurs_match, BEURS_MAP


class TestBeursMapTdg(unittest.TestCase):
    def test_tdg_matcht_nu_amerikaanse_thuismarkt_ticker(self):
        """Een TDG-aandeel dat alleen op NMS/NYQ voorkomt (geen Duitse
        notering) moet nu wél een beurs-match krijgen."""
        quotes = [{"symbol": "NFLX", "exchange": "NMS"}]
        resultaat = _kies_beurs_match(quotes, BEURS_MAP["TDG"])
        self.assertEqual(resultaat, ("NFLX", "NMS"))

    def test_tdg_geeft_nog_steeds_voorrang_aan_duitse_notering(self):
        """Als zowel een Duitse als een Amerikaanse kandidaat in de
        zoekresultaten zitten, moet de Duitse nog steeds gekozen worden."""
        quotes = [
            {"symbol": "XYZ", "exchange": "NMS"},
            {"symbol": "XYZ0.MU", "exchange": "MUN"},
        ]
        resultaat = _kies_beurs_match(quotes, BEURS_MAP["TDG"])
        self.assertEqual(resultaat, ("XYZ0.MU", "MUN"))

    def test_andere_beurscodes_ongewijzigd(self):
        """Sanity check: EAM/AMS-mapping mag niet per ongeluk meeveranderd
        zijn."""
        self.assertEqual(BEURS_MAP["EAM"], ["AMS"])


if __name__ == "__main__":
    unittest.main()
