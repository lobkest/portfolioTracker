import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_zekerheid
from ticker_matching import _kies_beurs_match, BEURS_MAP
from ticker_zekerheid import beurs_status, BEURS_OTC_NA_DELISTING, _voeg_kaartvelden_toe


def _check(binnen_dagrange):
    return {"match": binnen_dagrange, "binnen_dagrange": binnen_dagrange, "afwijking_pct": 0.0}


class TestBeursStatus(unittest.TestCase):
    def test_nsy_naar_nyq_klopt(self):
        self.assertIs(beurs_status("NSY", "NYQ", []), True)

    def test_ndq_naar_ncm_klopt(self):
        self.assertIs(beurs_status("NDQ", "NCM", []), True)

    def test_ndq_naar_pnk_met_kloppende_prijs_is_otc_na_delisting(self):
        self.assertEqual(beurs_status("NDQ", "PNK", [_check(True), _check(True)]), BEURS_OTC_NA_DELISTING)

    def test_ndq_naar_pnk_met_afwijkende_prijs_is_mismatch(self):
        self.assertIs(beurs_status("NDQ", "PNK", [_check(True), _check(False)]), False)

    def test_ndq_naar_pnk_zonder_koersdata_is_mismatch(self):
        self.assertIs(beurs_status("NDQ", "PNK", [{"match": None, "afwijking_pct": None}]), False)

    def test_europese_beurs_naar_pnk_is_mismatch(self):
        self.assertIs(beurs_status("EAM", "PNK", [_check(True)]), False)


class TestKaartveldenOtc(unittest.TestCase):
    def _kaart(self, prijs_checks):
        resultaat = {"ticker": "XELA", "zekerheid": "onzeker", "prijs_checks": prijs_checks}
        with patch.object(ticker_zekerheid, "_ticker_details_met_cache", return_value={"yahoo_beurs": "PNK"}), \
                patch.object(ticker_zekerheid, "_land_sector_voor_weergave", return_value=(None, None, None)), \
                patch.object(ticker_zekerheid, "classify_ticker", return_value="Aandeel"):
            return _voeg_kaartvelden_toe(resultaat, "NDQ")

    def test_kloppende_prijs_maakt_beurs_geen_reden_voor_onzeker(self):
        kaart = self._kaart([_check(True), _check(True)])
        self.assertEqual(kaart["beurs_klopt"], BEURS_OTC_NA_DELISTING)
        self.assertEqual(kaart["zekerheid"], "zeker")

    def test_afwijkende_prijs_blijft_onzeker(self):
        kaart = self._kaart([_check(False)])
        self.assertIs(kaart["beurs_klopt"], False)
        self.assertEqual(kaart["zekerheid"], "onzeker")


class TestZoekenKiestGeenOtc(unittest.TestCase):
    def test_ndq_kiest_nasdaq_boven_pnk(self):
        quotes = [{"symbol": "BBRYF", "exchange": "PNK"}, {"symbol": "XYZ", "exchange": "NGM"}]
        self.assertEqual(_kies_beurs_match(quotes, BEURS_MAP["NDQ"]), ("XYZ", "NGM"))

    def test_ndq_met_alleen_pnk_geeft_geen_beurs_match(self):
        self.assertIsNone(_kies_beurs_match([{"symbol": "XELA", "exchange": "PNK"}], BEURS_MAP["NDQ"]))


if __name__ == "__main__":
    unittest.main()
