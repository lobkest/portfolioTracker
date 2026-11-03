"""Splitvariant-keuze, valutacontrole van alternatieven, beurskoppeling en meldingen (fixtures, geen netwerk of DB)."""
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

from backend import ticker_prijscheck, ticker_zekerheid
from backend.ticker_matching import BEURS_MAP
from backend.ticker_prijscheck import (
    SPLIT_ONDUIDELIJK, SPLIT_ONGECORRIGEERD, SPLIT_VARIANT_MET, SPLIT_VARIANT_ZONDER,
    _prijscheck_is_probleem, classificeer_split_sprong, kies_split_variant, vergelijk_prijs_op_datum,
)
from backend.ticker_zekerheid import (
    _reken_alternatief_door, _voeg_prijsoordeel_toe, beurs_status, kies_alternatief, valuta_vergelijkbaar,
)


def _omgeving(yahoo_koers, high, low, splits, sprong):
    stack = ExitStack()
    for naam, waarde in {
        "db_get_cached_prijscheck": None, "_haal_koers_en_dagrange_op": (yahoo_koers, high, low),
        "_ticker_details_met_cache": {"valuta": "EUR"}, "_haal_splits_op": splits, "_haal_split_sprong": sprong,
    }.items():
        stack.enter_context(patch.object(ticker_prijscheck, naam, return_value=waarde))
    stack.enter_context(patch.object(ticker_prijscheck, "db_save_prijscheck"))
    return stack


class TestSplitVariant(unittest.TestCase):
    def test_unilever_doorlopende_reeks_krijgt_geen_correctie(self):
        with _omgeving(55.66, 56.2, 54.8, {"2025-12-06": 0.888889}, (55.0, 55.2)):
            check = vergelijk_prijs_op_datum("UNA.AS", date(2025, 6, 2), 54.48)
        self.assertEqual(check["split_variant"], SPLIT_VARIANT_ZONDER)
        self.assertEqual(check["split_factor"], 1.0)
        self.assertTrue(check["binnen_dagrange"])
        self.assertFalse(_prijscheck_is_probleem(check))
        self.assertIn("lag dichter bij", check["split_uitleg"])

    def test_gewone_split_met_gecorrigeerde_reeks_wordt_teruggerekend(self):
        with _omgeving(50.0, 51.0, 49.0, {"2025-12-06": 2.0}, (49.8, 50.1)):
            check = vergelijk_prijs_op_datum("X", date(2025, 6, 2), 100.0)
        self.assertEqual(check["split_variant"], SPLIT_VARIANT_MET)
        self.assertEqual(check["split_factor"], 2.0)
        self.assertAlmostEqual(check["yahoo_koers_gecorrigeerd"], 100.0)

    def test_ruwe_sprong_wordt_niet_teruggerekend(self):
        with _omgeving(100.0, 101.0, 99.0, {"2025-12-06": 2.0}, (100.0, 50.0)):
            check = vergelijk_prijs_op_datum("X", date(2025, 6, 2), 100.0)
        self.assertEqual(check["split_variant"], SPLIT_VARIANT_ZONDER)
        self.assertEqual(check["split_factor"], 1.0)

    def test_classificatie(self):
        self.assertEqual(classificeer_split_sprong(2.0, 100.0, 50.0), SPLIT_ONGECORRIGEERD)
        self.assertEqual(classificeer_split_sprong(2.0, 50.0, 50.5), SPLIT_ONDUIDELIJK)
        self.assertEqual(classificeer_split_sprong(0.888889, 55.0, 55.2), SPLIT_ONDUIDELIJK)
        self.assertEqual(classificeer_split_sprong(1.02, 10.0, 9.8), SPLIT_ONDUIDELIJK)

    def test_zonder_splits_geen_variant(self):
        self.assertEqual(kies_split_variant([], 10.0, 10.0)[0], 1.0)


class TestValutaAlternatieven(unittest.TestCase):
    def _reken(self, kandidaat_valuta, transactie_valuta):
        steekproef = [{"datum": "2024-01-02", "koers": 50.0, "koers_valuta": transactie_valuta}]
        with patch.object(ticker_zekerheid, "_ticker_details_met_cache", return_value={"valuta": kandidaat_valuta}), \
             patch.object(ticker_zekerheid, "vergelijk_prijs_op_datum", side_effect=AssertionError("prijscheck")):
            return _reken_alternatief_door({"symbol": "UL", "exchange": "NYQ"}, steekproef)

    def test_usd_kandidaat_tegenover_eur_transactie_is_nvt(self):
        alt = self._reken("USD", "EUR")
        self.assertTrue(alt["prijscheck_nvt"])
        self.assertEqual((alt["aantal_matches"], alt["aantal_gecontroleerd"]), (0, 0))

    def test_pence_en_pond_zijn_verschillend(self):
        self.assertFalse(valuta_vergelijkbaar("GBX", "GBP"))
        self.assertTrue(valuta_vergelijkbaar("GBX", "GBp"))
        self.assertIsNone(valuta_vergelijkbaar(None, "EUR"))

    def test_kies_alternatief_beurs_dan_valuta_dan_matches(self):
        alts = [
            {"ticker": "A", "beurs": "GER", "valuta_gelijk": False, "aantal_matches": 3, "aantal_gecontroleerd": 3},
            {"ticker": "B", "beurs": "AMS", "valuta_gelijk": True, "aantal_matches": 2, "aantal_gecontroleerd": 3},
            {"ticker": "C", "beurs": "AMS", "valuta_gelijk": True, "aantal_matches": 3, "aantal_gecontroleerd": 3},
        ]
        self.assertEqual(kies_alternatief(alts, 3, ["AMS"], prijsprobleem=True)["ticker"], "C")

    def test_nvt_alternatief_telt_niet_als_geen_koershistorie(self):
        checks = [{"match": True, "binnen_dagrange": True}]
        nvt = [{"beurs": "AMS", "aantal_gecontroleerd": 0, "prijscheck_nvt": True}]
        self.assertFalse(beurs_status("EAM", "GER", checks, nvt))


class TestBeurskoppeling(unittest.TestCase):
    def test_matches(self):
        for degiro, yahoo in [("NSY", "NYQ"), ("NDQ", "NCM"), ("EAM", "AMS"), ("ASE", "ASE"), ("XET", "GER"),
                              ("LSE", "LSE"), ("EPA", "PAR"), ("EBR", "BRU"), ("MIL", "MIL"), ("MAD", "MCE"),
                              ("SWX", "EBS"), ("VIE", "VIE")]:
            self.assertIn(yahoo, BEURS_MAP[degiro], (degiro, yahoo))

    def test_geen_match(self):
        self.assertNotIn("VIE", BEURS_MAP["NSY"])
        self.assertFalse(beurs_status("NSY", "VIE", []))


def _check(binnen):
    return {"match": True, "niveau": "ok", "binnen_dagrange": binnen, "afwijking_pct": 0.0 if binnen else 9.0}


class TestMelding(unittest.TestCase):
    def _oordeel(self, uitkomsten):
        steekproef = [{"datum": f"2024-0{i + 1}-02", "koers": 10.0} for i in range(len(uitkomsten))]
        checks = iter(uitkomsten)
        resultaat = {"ticker": "T", "zekerheid": "zeker", "waarschuwing": None}
        with patch.object(ticker_zekerheid, "vergelijk_prijs_op_datum", side_effect=lambda *a: _check(next(checks))), \
             patch.object(ticker_zekerheid, "vergelijk_prijzen_op_datums", return_value=[]):
            return _voeg_prijsoordeel_toe(resultaat, steekproef, steekproef)

    def test_alles_in_orde_geen_waarschuwing(self):
        uit = self._oordeel([True, True, True])
        self.assertEqual((uit["zekerheid"], uit["waarschuwing"], uit["opmerking"]), ("zeker", None, None))

    def test_enkele_uitschieter_is_neutrale_opmerking(self):
        uit = self._oordeel([True, False, True])
        self.assertEqual(uit["zekerheid"], "zeker")
        self.assertIsNone(uit["waarschuwing"])
        self.assertIn("Eén datum", uit["opmerking"])

    def test_meerdere_afwijkingen_geven_waarschuwing(self):
        uit = self._oordeel([False, False, True])
        self.assertEqual(uit["zekerheid"], "onzeker")
        self.assertTrue(uit["waarschuwing"])


if __name__ == "__main__":
    unittest.main()
