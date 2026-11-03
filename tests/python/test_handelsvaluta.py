"""Handelsvaluta (Berkshire-geval) en het split-label van de prijscontrole (fixtures, geen netwerk of DB)."""
import unittest
from datetime import date
from unittest.mock import patch

from backend import ticker_prijscheck, ticker_wissel, ticker_zekerheid
from backend.ticker_matching import BEURS_MAP
from backend.ticker_prijscheck import _beoordeel_prijs, vergelijk_prijs_op_datum
from backend.ticker_wissel import beslis_automatische_wissel, verwerk_automatische_wissels
from backend.ticker_zekerheid import _reken_alternatief_door, bepaal_handelsvaluta

NSY = BEURS_MAP["NSY"]
EAM = BEURS_MAP["EAM"]


def _berkshire(koers_valuta):
    return [{"datum": "2025-08-28", "koers": 365.0, "wisselkoers": 1.1685, "koers_valuta": koers_valuta}]


class TestBepaalHandelsvaluta(unittest.TestCase):
    def test_wisselkoers_met_valuta_eur_of_leeg_geeft_beursvaluta(self):
        for valuta in ("EUR", None):
            self.assertEqual(bepaal_handelsvaluta(_berkshire(valuta), NSY), "USD", valuta)

    def test_valutakolom_gaat_voor(self):
        self.assertEqual(bepaal_handelsvaluta(_berkshire("USD"), NSY), "USD")

    def test_eur_aandeel_zonder_wisselkoers_blijft_eur(self):
        rij = [{"datum": "2025-08-28", "koers": 50.0, "wisselkoers": None, "koers_valuta": "EUR"}]
        self.assertEqual(bepaal_handelsvaluta(rij, EAM), "EUR")

    def test_wisselkoers_een_telt_niet_als_vreemde_valuta(self):
        rij = [{"datum": "2025-08-28", "koers": 50.0, "wisselkoers": 1.0, "koers_valuta": "EUR"}]
        self.assertEqual(bepaal_handelsvaluta(rij, NSY), "EUR")

    def test_onbekende_beurs_blijft_eur_of_none(self):
        self.assertIsNone(bepaal_handelsvaluta(_berkshire(None), ["ONBEKEND"]))


def _alternatief(ticker, beurs, valuta, steekproef):
    with patch.object(ticker_zekerheid, "_ticker_details_met_cache", return_value={"valuta": valuta}), \
         patch.object(ticker_zekerheid, "vergelijk_prijs_op_datum",
                      return_value={"yahoo_koers": 365.0, "match": True, "binnen_dagrange": True}), \
         patch.object(ticker_zekerheid, "classify_ticker", return_value=False), \
         patch.object(ticker_zekerheid, "_land_sector_voor_weergave", return_value=("US", "Financials", None)):
        return _reken_alternatief_door({"symbol": ticker, "exchange": beurs}, steekproef, NSY)


class TestBerkshireWissel(unittest.TestCase):
    def _resultaat(self, koers_valuta):
        steekproef = _berkshire(koers_valuta)
        vie = _alternatief("BRKB.VI", "VIE", "EUR", steekproef)
        nyq = _alternatief("BRK-B", "NYQ", "USD", steekproef)
        resultaat = {"ticker": "BRKB.VI", "excel_beurs": "NSY", "yahoo_beurs": "VIE", "beurs_klopt": False,
                     "prijs_checks": [], "alternatieven": [vie, nyq]}
        return resultaat, vie, nyq

    def test_brk_b_is_match_en_vie_is_mismatch(self):
        for valuta in ("EUR", None):
            _, vie, nyq = self._resultaat(valuta)
            self.assertIs(nyq["valuta_gelijk"], True, valuta)
            self.assertIs(vie["valuta_gelijk"], False, valuta)

    def test_automatische_wissel_naar_brk_b(self):
        resultaat, _, _ = self._resultaat("EUR")
        handelsvaluta = bepaal_handelsvaluta(_berkshire("EUR"), NSY)
        self.assertEqual(beslis_automatische_wissel(resultaat, handelsvaluta)["naar"], "BRK-B")

    def test_handmatige_keuze_wordt_niet_overschreven(self):
        info = {"naam": "BERKSHIRE", "echte_naam": "BERKSHIRE", "beurs": "NSY", "isin": "US0846707026",
                "isins": ["US0846707026"], "ticker": "BRKB.VI", "transacties": _berkshire("EUR")}
        handmatig = [{"isin": "US0846707026", "beurs": "NSY", "oude_ticker": "BRK-B", "nieuwe_ticker": "BRKB.VI",
                      "soort": "handmatig", "reden": None}]
        waarschuwingen = [{"ticker": "BRKB.VI", "redenen": ["koers"]}]
        with patch.object(ticker_wissel, "db_get_ticker_wissels", return_value=handmatig), \
             patch.object(ticker_wissel, "verifieer_ticker_met_prijs", side_effect=AssertionError("beoordeeld")), \
             patch.object(ticker_wissel, "pas_ticker_wissel_toe", side_effect=AssertionError("gewisseld")):
            uit, aantal = verwerk_automatische_wissels("ABC", waarschuwingen, [(("US0846707026", "NSY"), info)])
        self.assertEqual((uit, aantal), (waarschuwingen, 0))


def _omgeving(splits):
    return patch.multiple(
        ticker_prijscheck, db_get_cached_prijscheck=lambda *a: None, db_save_prijscheck=lambda *a: None,
        _haal_koers_en_dagrange_op=lambda *a: (50.0, 51.0, 49.0), _haal_splits_op=lambda *a: splits,
        _haal_split_sprong=lambda *a: (49.8, 50.1), _ticker_details_met_cache=lambda *a: {"valuta": "EUR"})


class TestSplitLabel(unittest.TestCase):
    def test_split_voor_transactiedatum_geeft_geen_label(self):
        with _omgeving({"2019-06-03": 2.0}):
            check = vergelijk_prijs_op_datum("RCAT", date(2024, 3, 4), 50.0)
        self.assertFalse(check["split_gecorrigeerd"])
        self.assertEqual(check["split_factor"], 1.0)
        self.assertIsNone(check["yahoo_koers_gecorrigeerd"])

    def test_split_na_transactiedatum_geeft_label(self):
        with _omgeving({"2025-12-06": 2.0}):
            check = vergelijk_prijs_op_datum("X", date(2025, 6, 2), 100.0)
        self.assertTrue(check["split_gecorrigeerd"])
        self.assertEqual(check["split_factor"], 2.0)

    def test_unilever_zonder_correctie_geeft_geen_label(self):
        with _omgeving({"2025-12-06": 0.888889}), \
             patch.object(ticker_prijscheck, "_haal_koers_en_dagrange_op", lambda *a: (55.66, 56.2, 54.8)), \
             patch.object(ticker_prijscheck, "_haal_split_sprong", lambda *a: (55.0, 55.2)):
            check = vergelijk_prijs_op_datum("UNA.AS", date(2025, 6, 2), 54.48)
        self.assertFalse(check["split_gecorrigeerd"])

    def test_alleen_valutaomrekening_geeft_geen_splitlabel_maar_noteert_valuta(self):
        with patch.object(ticker_prijscheck, "_fx_koers_op_datum", return_value=0.9):
            check = _beoordeel_prijs("AMD", date(2024, 3, 4), 90.0, 100.0, "USD", 101.0, 99.0, 1.0, [])
        self.assertFalse(check["split_gecorrigeerd"])
        self.assertEqual(check["valuta_omgerekend"], "USD")
        self.assertAlmostEqual(check["yahoo_koers_gecorrigeerd"], 90.0)


if __name__ == "__main__":
    unittest.main()
