"""Dagrange met minimale marge in euro's, en alternatieven/prijsprobleem op de dagrange i.p.v. de slotkoers.
Offline: Yahoo, cache en details worden gemockt."""
import os
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_prijscheck
import ticker_zekerheid
from ticker_prijscheck import dagrange_grenzen, vergelijk_prijs_op_datum
from ticker_zekerheid import _zoek_betere_alternatieven, _corrigeer_met_alternatief


def _mock_yahoo_omgeving(yahoo_koers, high, low):
    stack = ExitStack()
    stack.enter_context(patch.object(ticker_prijscheck, "db_get_cached_prijscheck", return_value=None))
    stack.enter_context(patch.object(ticker_prijscheck, "_haal_koers_en_dagrange_op", return_value=(yahoo_koers, high, low)))
    stack.enter_context(patch.object(ticker_prijscheck, "_ticker_details_met_cache", return_value={"valuta": "EUR"}))
    stack.enter_context(patch.object(ticker_prijscheck, "db_save_prijscheck"))
    stack.enter_context(patch.object(ticker_prijscheck, "_haal_splits_op", return_value={}))
    return stack


def _check(afwijking_pct, match, binnen_dagrange, yahoo_koers=100.0):
    return {
        "yahoo_koers": yahoo_koers, "bekende_koers": 100.0, "afwijking_pct": afwijking_pct,
        "match": match, "binnen_dagrange": binnen_dagrange, "niveau": None, "high": None, "low": None,
    }


class TestDagrangeGrenzen(unittest.TestCase):
    def _binnen(self, low, high, koers):
        ondergrens, bovengrens = dagrange_grenzen(low, high)
        return ondergrens <= koers <= bovengrens

    def test_goedkoop_aandeel_gebruikt_50_cent(self):
        # high 4.00: 2% is maar 0.08, dus de grens wordt 4.00 + 0.50 = 4.50.
        self.assertTrue(self._binnen(3.80, 4.00, 4.40))
        self.assertFalse(self._binnen(3.80, 4.00, 4.60))
        # Ondergrens: 3.80 - 0.50 = 3.30.
        self.assertTrue(self._binnen(3.80, 4.00, 3.35))
        self.assertFalse(self._binnen(3.80, 4.00, 3.25))

    def test_duur_aandeel_gebruikt_2_procent(self):
        ondergrens, bovengrens = dagrange_grenzen(190.0, 200.0)
        self.assertAlmostEqual(bovengrens, 204.0)  # 2% van 200 = 4, niet 0.50
        self.assertAlmostEqual(ondergrens, 186.2)  # 2% van 190 = 3.8
        self.assertTrue(self._binnen(190.0, 200.0, 203.0))
        self.assertFalse(self._binnen(190.0, 200.0, 205.0))

    def test_nokia_27_01_2021_valt_binnen(self):
        # Slotkoers wijkt ~14,5% af, maar de koers valt binnen high 3.632 + 0.50.
        with _mock_yahoo_omgeving(yahoo_koers=3.395, high=3.632, low=3.344):
            resultaat = vergelijk_prijs_op_datum("NOKIA.HE", date(2021, 1, 27), 3.971)
        self.assertTrue(resultaat["binnen_dagrange"])
        self.assertEqual(resultaat["niveau"], "waarschuwing")


class TestAlternatievenOpDagrange(unittest.TestCase):
    def setUp(self):
        for naam, waarde in (
            ("_ticker_details_met_cache", {}),
            ("_land_sector_voor_weergave", (None, None, None)),
            ("classify_ticker", False),
        ):
            p = patch.object(ticker_zekerheid, naam, return_value=waarde)
            p.start()
            self.addCleanup(p.stop)
        self.steekproef = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]

    def _reken(self, check):
        with patch.object(ticker_zekerheid, "vergelijk_prijs_op_datum", return_value=check):
            return _zoek_betere_alternatieven([{"symbol": "ALT", "exchange": "LSE"}], self.steekproef, ["AMS"])

    def test_grote_slotkoersafwijking_binnen_dagrange_telt_als_match(self):
        alternatieven, aanbevolen = self._reken(_check(afwijking_pct=14.5, match=False, binnen_dagrange=True))
        self.assertEqual(alternatieven[0]["aantal_matches"], 2)
        self.assertEqual(alternatieven[0]["aantal_gecontroleerd"], 2)
        self.assertNotIn("gemiddelde_afwijking_pct", alternatieven[0])
        self.assertEqual(aanbevolen, "ALT")

    def test_buiten_dagrange_telt_niet_als_match(self):
        alternatieven, aanbevolen = self._reken(_check(afwijking_pct=1.0, match=True, binnen_dagrange=False))
        self.assertEqual(alternatieven[0]["aantal_matches"], 0)
        self.assertEqual(alternatieven[0]["aantal_gecontroleerd"], 2)
        self.assertIsNone(aanbevolen)

    def test_zonder_high_low_valt_terug_op_procent(self):
        alternatieven, _ = self._reken(_check(afwijking_pct=8.0, match=False, binnen_dagrange=None))
        self.assertEqual(alternatieven[0]["aantal_matches"], 0)
        alternatieven, aanbevolen = self._reken(_check(afwijking_pct=3.0, match=True, binnen_dagrange=None))
        self.assertEqual(alternatieven[0]["aantal_matches"], 2)
        self.assertEqual(aanbevolen, "ALT")

    def test_geen_koersdata_geeft_0_gecontroleerd(self):
        alternatieven, aanbevolen = self._reken(
            _check(afwijking_pct=None, match=None, binnen_dagrange=None, yahoo_koers=None)
        )
        self.assertEqual(alternatieven[0]["aantal_matches"], 0)
        self.assertEqual(alternatieven[0]["aantal_gecontroleerd"], 0)
        self.assertIsNone(aanbevolen)


class TestPrijsprobleemOpDagrange(unittest.TestCase):
    def _resultaat(self, binnen_dagrange):
        check = _check(afwijking_pct=20.0, match=False, binnen_dagrange=binnen_dagrange)
        return {"ticker": "TICK", "zekerheid": "onzeker", "alternatieven": [], "prijs_checks": [check, dict(check)],
                "prijswaarschuwing": "x"}

    def test_grote_afwijking_binnen_dagrange_is_geen_prijsprobleem(self):
        resultaat = self._resultaat(binnen_dagrange=True)
        with patch.object(ticker_zekerheid, "_zoek_betere_alternatieven") as mock_alt:
            uit = _corrigeer_met_alternatief(resultaat, [{"datum": date(2023, 6, 10), "koers": 100.0}], "EAM")
        mock_alt.assert_not_called()
        self.assertIs(uit, resultaat)

    def test_buiten_dagrange_is_wel_prijsprobleem(self):
        resultaat = self._resultaat(binnen_dagrange=False)
        with patch.object(ticker_zekerheid, "_zoek_betere_alternatieven", return_value=([], None)) as mock_alt:
            _corrigeer_met_alternatief(resultaat, [{"datum": date(2023, 6, 10), "koers": 100.0}], "EAM")
        mock_alt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
