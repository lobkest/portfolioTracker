"""De valuta uit de Excel (Wisselkoers leeg of 0 = DeGiro rekende in EUR) stuurt de tickerkeuze, voor elke beurs."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import ticker_matching as tm
from backend.ticker_matching import find_ticker_detailed, zoeknaam_zonder_rechtsvorm
from backend.ticker_zekerheid import handelt_in_eur

NFLX = {"symbol": "NFLX", "exchange": "NMS"}
NFC = {"symbol": "NFC.DE", "exchange": "GER"}
ISIN = "US64110L1061"


def _zoek(resultaten):
    return lambda query: resultaten.get(query, [])


def _find(resultaten, in_eur, bekend=None, beurs="TDG", product="NETFLIX, INC."):
    with patch.object(tm, "_yahoo_search", side_effect=_zoek(resultaten)), \
            patch.object(tm, "db_get_ticker_details", return_value=bekend or {}):
        return find_ticker_detailed(product, ISIN, beurs, in_eur=in_eur)


class TestEurTransactie(unittest.TestCase):
    def test_alleen_een_usd_kandidaat_is_niet_zeker(self):
        uit = _find({"NETFLIX, INC.": [NFLX], "NETFLIX": [NFLX], ISIN: [NFLX]}, in_eur=True)
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFLX", "onzeker"))

    def test_eur_kandidaat_via_de_naam_zonder_rechtsvorm(self):
        uit = _find({"NETFLIX, INC.": [NFLX], "NETFLIX": [NFLX, NFC], ISIN: [NFLX]}, in_eur=True)
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFC.DE", "zeker"))

    def test_onzeker_fallback_geeft_een_passende_kandidaat_voorrang(self):
        uit = _find({"NETFLIX, INC.": [NFLX, {"symbol": "NFC.F", "exchange": "FRA"}]}, in_eur=True, beurs="EAM")
        self.assertEqual(uit["ticker"], "NFC.F")

    def test_bekende_valuta_uit_ticker_info_gaat_voor_de_beurscode(self):
        uit = _find({"NETFLIX, INC.": [NFLX]}, in_eur=True, bekend={"NFLX": {"valuta": "EUR"}})
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFLX", "zeker"))

    def test_zelfde_regel_voor_een_andere_beurs(self):
        quotes = {"ACME CORP": [{"symbol": "ACM", "exchange": "NYQ"}, {"symbol": "ACM.AS", "exchange": "AMS"}]}
        uit = _find(quotes, in_eur=True, beurs="EAM", product="ACME CORP")
        self.assertEqual(uit["ticker"], "ACM.AS")


class TestTransactieMetWisselkoers(unittest.TestCase):
    def test_usd_transactie_blijft_zoals_het_was(self):
        uit = _find({"NETFLIX, INC.": [NFLX]}, in_eur=False)
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFLX", "zeker"))

    def test_eur_kandidaat_is_dan_niet_zeker_en_de_usd_kandidaat_wel(self):
        uit = _find({"NETFLIX, INC.": [NFC, NFLX]}, in_eur=False)
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFLX", "zeker"))

    def test_onbekende_valuta_doet_niet_mee(self):
        uit = _find({"NETFLIX, INC.": [NFLX]}, in_eur=None)
        self.assertEqual((uit["ticker"], uit["zekerheid"]), ("NFLX", "zeker"))


class TestHandeltInEur(unittest.TestCase):
    def test_zonder_kolom_onbekend(self):
        self.assertIsNone(handelt_in_eur([{"datum": "2024-01-01", "koers": 1.0}]))

    def test_leeg_en_nul_zijn_eur(self):
        self.assertTrue(handelt_in_eur([{"wisselkoers": None}, {"wisselkoers": 0.0}, {"wisselkoers": float("nan")}]))

    def test_een_gevulde_wisselkoers_is_geen_eur(self):
        self.assertFalse(handelt_in_eur([{"wisselkoers": None}, {"wisselkoers": 1.17}]))


class TestZoeknaamZonderRechtsvorm(unittest.TestCase):
    def test_voorbeelden(self):
        self.assertEqual(zoeknaam_zonder_rechtsvorm("NETFLIX, INC."), "NETFLIX")
        self.assertEqual(zoeknaam_zonder_rechtsvorm("BYD CO LTD"), "BYD")
        self.assertEqual(zoeknaam_zonder_rechtsvorm("KONINKLIJKE AHOLD DELHAIZE N.V."), "KONINKLIJKE AHOLD DELHAIZE")

    def test_niets_te_strippen_geeft_leeg(self):
        self.assertEqual(zoeknaam_zonder_rechtsvorm("VANGUARD FTSE ALL-WORLD UCITS ETF"), "")


if __name__ == "__main__":
    unittest.main()
