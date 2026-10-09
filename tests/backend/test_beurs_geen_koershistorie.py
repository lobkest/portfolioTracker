import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ticker_zekerheid import beurs_status, BEURS_GEEN_KOERSHISTORIE, _beurs_zonder_koershistorie


def _check(binnen_dagrange):
    return {"match": binnen_dagrange, "binnen_dagrange": binnen_dagrange, "afwijking_pct": 0.0}


def _alt(ticker, beurs, gecontroleerd, matches=0):
    return {"ticker": ticker, "beurs": beurs, "aantal_gecontroleerd": gecontroleerd, "aantal_matches": matches}


GEEN_DATA_OP_AMS = [_alt("STAR-USD.AS", "AMS", 0), _alt("IE000A9G9R73.SG", "STU", 0)]


class TestBeursStatusGeenKoershistorie(unittest.TestCase):
    def test_geen_data_op_verwachte_beurs_en_prijs_klopt(self):
        status = beurs_status("EAM", "PAR", [_check(True), _check(True)], GEEN_DATA_OP_AMS)
        self.assertEqual(status, BEURS_GEEN_KOERSHISTORIE)

    def test_koersbare_kandidaat_op_verwachte_beurs_blijft_mismatch(self):
        alternatieven = GEEN_DATA_OP_AMS + [_alt("STAR.AS", "AMS", 3, 3)]
        self.assertIs(beurs_status("EAM", "PAR", [_check(True)], alternatieven), False)

    def test_prijs_klopt_niet_blijft_mismatch(self):
        self.assertIs(beurs_status("EAM", "PAR", [_check(True), _check(False)], GEEN_DATA_OP_AMS), False)

    def test_zonder_koersdata_eigen_ticker_blijft_mismatch(self):
        self.assertIs(beurs_status("EAM", "PAR", [{"match": None, "afwijking_pct": None}], GEEN_DATA_OP_AMS), False)

    def test_geen_kandidaat_op_verwachte_beurs_blijft_mismatch(self):
        self.assertIs(beurs_status("EAM", "PAR", [_check(True)], [_alt("X.SG", "STU", 0)]), False)

    def test_zonder_alternatieven_zoals_diagnostiek_blijft_mismatch(self):
        self.assertIs(beurs_status("EAM", "PAR", [_check(True)]), False)


class TestKaartZonderKoershistorie(unittest.TestCase):
    BEURS_WAARSCHUWING = "Opgeslagen ticker 'STAR.PA' staat bij Yahoo op PAR, de transacties op EAM."

    def _resultaat(self, waarschuwing, alternatieven=GEEN_DATA_OP_AMS):
        return {
            "ticker": "STAR.PA", "zekerheid": "onzeker", "waarschuwing": waarschuwing, "yahoo_beurs": "PAR",
            "beurs_klopt": False, "prijs_checks": [_check(True), _check(True)], "alternatieven": alternatieven,
        }

    def test_alleen_beurswaarschuwing_wordt_zeker_zonder_waarschuwing(self):
        kaart = _beurs_zonder_koershistorie(self._resultaat(self.BEURS_WAARSCHUWING), "EAM", self.BEURS_WAARSCHUWING)
        self.assertEqual(kaart["beurs_klopt"], BEURS_GEEN_KOERSHISTORIE)
        self.assertEqual(kaart["zekerheid"], "zeker")
        self.assertIsNone(kaart["waarschuwing"])

    def test_gevonden_ticker_zonder_beurs_match_wordt_zeker(self):
        kaart = _beurs_zonder_koershistorie(self._resultaat(None), "EAM", None)
        self.assertEqual(kaart["zekerheid"], "zeker")

    def test_andere_waarschuwing_blijft_onzeker(self):
        openfigi = "Ticker-root 'STAR' komt niet voor in OpenFIGI's resultaten."
        kaart = _beurs_zonder_koershistorie(
            self._resultaat(f"{self.BEURS_WAARSCHUWING}\n{openfigi}"), "EAM", self.BEURS_WAARSCHUWING)
        self.assertEqual(kaart["beurs_klopt"], BEURS_GEEN_KOERSHISTORIE)
        self.assertEqual(kaart["zekerheid"], "onzeker")
        self.assertEqual(kaart["waarschuwing"], openfigi)

    def test_koersbare_kandidaat_laat_kaart_ongemoeid(self):
        resultaat = self._resultaat(self.BEURS_WAARSCHUWING, GEEN_DATA_OP_AMS + [_alt("STAR.AS", "AMS", 3, 3)])
        self.assertIs(_beurs_zonder_koershistorie(resultaat, "EAM", self.BEURS_WAARSCHUWING), resultaat)


if __name__ == "__main__":
    unittest.main()
