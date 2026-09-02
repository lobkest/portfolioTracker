"""
Unit tests voor de High/Low-dagrange op de Ticker-zekerheid-pagina
(analysis.vergelijk_prijs_op_datum's 'binnen_dagrange'-veld, en de twee
samenvattende waarschuwingsmeldingen die er voortaan op leunen i.p.v. op de
%-afwijkingsdrempel -- zie CLAUDE.md/opdracht_high_low_dagrange.md).

Draait geheel offline: get_cached_prijscheck/_haal_slotkoers_op/
_haal_dagrange_op/save_prijscheck worden gemockt (net als
tests/test_ticker_verificatie.py), behalve TestBackfillHighLowDoUpdate, die
bewust de echte database raakt (net als TestPrijscheckCache aldaar) om het
bekende ON CONFLICT DO NOTHING-patroon te regressietesten.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import vergelijk_prijs_op_datum, verifieer_ticker_met_prijs


def _mock_yahoo_omgeving(yahoo_koers, high, low):
    stack = ExitStack()
    stack.enter_context(patch.object(analysis, "get_cached_prijscheck", return_value=None))
    stack.enter_context(patch.object(analysis, "_haal_slotkoers_op", return_value=yahoo_koers))
    stack.enter_context(patch.object(analysis, "_haal_dagrange_op", return_value=(high, low)))
    stack.enter_context(patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"}))
    stack.enter_context(patch.object(analysis, "save_prijscheck"))
    stack.enter_context(patch.object(analysis, "_haal_splits_op", return_value={}))
    return stack


class TestBinnenDagrange(unittest.TestCase):
    def test_vergelijk_prijs_binnen_dagrange(self):
        # Excel-koers (102) ligt tussen low (95) en high (105) -- ook al wijkt
        # hij fors af van de slotkoers (80, >6%), telt dit als 'binnen
        # dagrange': een legitieme, gewoon die dag verhandelde prijs.
        with _mock_yahoo_omgeving(yahoo_koers=80.0, high=105.0, low=95.0):
            resultaat = vergelijk_prijs_op_datum("TICK", date(2024, 1, 1), 102.0)

        self.assertEqual(resultaat["niveau"], "waarschuwing")  # ~22.5% afwijking van de slotkoers
        self.assertTrue(resultaat["binnen_dagrange"])

    def test_vergelijk_prijs_buiten_dagrange(self):
        with _mock_yahoo_omgeving(yahoo_koers=100.0, high=105.0, low=95.0):
            resultaat = vergelijk_prijs_op_datum("TICK", date(2024, 1, 1), 110.0)

        self.assertFalse(resultaat["binnen_dagrange"])

    def test_geen_high_low_beschikbaar_geeft_none(self):
        # Mislukte dagrange-fetch (of een ticker/datum zonder handelsdata)
        # mag geen valse binnen/buiten-uitspraak opleveren.
        with _mock_yahoo_omgeving(yahoo_koers=100.0, high=None, low=None):
            resultaat = vergelijk_prijs_op_datum("TICK", date(2024, 1, 1), 100.0)

        self.assertIsNone(resultaat["binnen_dagrange"])


class TestMeldingGebruiktDagrangeNietAfwijking(unittest.TestCase):
    """verifieer_ticker_met_prijs() moet z'n samenvattende waarschuwing nu op
    binnen_dagrange baseren i.p.v. op de %-afwijkingsdrempel."""

    def setUp(self):
        self.transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        patcher = patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        details_patch = patch.object(analysis, "_ticker_details_met_cache", return_value={})
        details_patch.start()
        self.addCleanup(details_patch.stop)
        land_sector_patch = patch.object(analysis, "_land_sector_voor_weergave", return_value=(None, None, None))
        land_sector_patch.start()
        self.addCleanup(land_sector_patch.stop)

    def _check(self, **overrides):
        basis = {
            "yahoo_koers": 80.0, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
            "bekende_koers": 100.0, "afwijking_pct": 20.0, "niveau": "waarschuwing", "match": False,
            "high": 105.0, "low": 95.0, "binnen_dagrange": True,
        }
        basis.update(overrides)
        return basis

    def test_melding_gebruikt_dagrange_niet_afwijking(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum",
                           side_effect=lambda *a, **kw: self._check()):
            resultaat = verifieer_ticker_met_prijs("APPLE INC", "US0378331005", "NASDAQ", self.transacties)

        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertIsNone(resultaat["waarschuwing"])

    def test_buiten_dagrange_degradeert_naar_onzeker_met_dagrange_tekst(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum",
                           side_effect=lambda *a, **kw: self._check(binnen_dagrange=False)):
            resultaat = verifieer_ticker_met_prijs("APPLE INC", "US0378331005", "NASDAQ", self.transacties)

        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertIn("valt buiten de dagrange", resultaat["waarschuwing"])
        self.assertNotIn("wijkt meer dan", resultaat["waarschuwing"])  # oude %-drempel-formulering weg


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "DATABASE_URL niet ingesteld -- deze test raakt de ticker_prijscheck-tabel in de echte database aan "
    "(bv. in CI zonder databasetoegang; draait lokaal wel via de .env)",
)
class TestBackfillHighLowDoUpdate(unittest.TestCase):
    """Regressietest voor het bekende ON CONFLICT DO NOTHING-patroon (zie
    CLAUDE.md, dividenden.dividend_id): een hernieuwde save_prijscheck-
    aanroep met nieuwe high/low moet een bestaande NULL-rij overschrijven,
    niet stilzwijgend negeren."""

    TEST_TICKER = "TESTDAGRANGE.TST"
    TEST_DATUM = date(2023, 3, 15)

    def _cleanup(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM ticker_prijscheck WHERE ticker = %s AND datum = %s",
            (self.TEST_TICKER, self.TEST_DATUM),
        )
        conn.commit()
        cur.close()
        conn.close()

    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def test_backfill_high_low_do_update(self):
        from db import save_prijscheck, get_cached_prijscheck

        # Eerste keer: alleen de slotkoers bekend (zoals een rij van vóór de
        # dagrange-uitbreiding), high/low blijven NULL.
        save_prijscheck(self.TEST_TICKER, self.TEST_DATUM, 123.45, "EUR")
        eerste = get_cached_prijscheck(self.TEST_TICKER, self.TEST_DATUM)
        self.assertEqual(eerste, (123.45, "EUR", None, None))

        # Her-aanroep met inmiddels bekende high/low moet die bijschrijven --
        # met ON CONFLICT DO NOTHING zou dit stil genegeerd worden.
        save_prijscheck(self.TEST_TICKER, self.TEST_DATUM, 123.45, "EUR", high=130.0, low=120.0)
        tweede = get_cached_prijscheck(self.TEST_TICKER, self.TEST_DATUM)
        self.assertEqual(tweede, (123.45, "EUR", 130.0, 120.0))


if __name__ == "__main__":
    unittest.main()
