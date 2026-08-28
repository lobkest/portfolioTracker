"""
Unit tests voor de performance-fix van de Ticker-zekerheid-pagina
(analysis.verifieer_ticker_met_prijs / vergelijk_prijs_op_datum).

Achtergrond: GET /api/portfolio/<code>/ticker-zekerheid gaf op Render een
500 (gunicorn worker timeout) doordat elke onzekere positie ALLE
kandidaat-tickers doorrekende, ook nadat een eerdere kandidaat al een
overtuigende match (juiste beurs + kloppende prijs) had opgeleverd. Deze
tests dekken de twee losstaande fixes:

1. Zodra een kandidaat een overtuigende match is, worden de resterende
   kandidaten niet meer gecheckt (verifieer_ticker_met_prijs).
2. Zodra de eerste prijscheck van een kandidaat geen koersdata oplevert
   (zoals '4BY1.F': "Data doesn't exist for startDate/endDate"), worden de
   overige steekproefdatums voor diezelfde kandidaat overgeslagen.
3. vergelijk_prijs_op_datum gebruikt de ticker_prijscheck-cache: een tweede
   aanroep voor dezelfde (ticker, datum) doet geen nieuwe Yahoo-call.

Draait geheel offline (test 1 en 2): analysis.find_ticker_detailed en
analysis.vergelijk_prijs_op_datum worden gemockt, dus geen echte
yahooquery/yfinance-calls. Test 3 raakt wél de echte database aan (net als
tests/test_dividend_db.py) om de ticker_prijscheck-cache zelf te testen,
maar mockt de Yahoo-call (_haal_slotkoers_op) — geen netwerkverkeer.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import verifieer_ticker_met_prijs, BEURS_MAP


def _prijscheck(match, afwijking_pct=0.0, yahoo_koers=100.0):
    return {"yahoo_koers": yahoo_koers, "bekende_koers": 100.0, "afwijking_pct": afwijking_pct, "match": match}


class TestStopBijOvertuigendeMatch(unittest.TestCase):
    """Spoor 1: zodra een kandidaat beurs+prijs overtuigend bevestigt, mogen
    latere kandidaten niet meer gecheckt worden."""

    BEURS = "TDG"
    TARGETS = BEURS_MAP["TDG"]  # ['GER', 'MUN', 'FRA']

    def setUp(self):
        self.transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        # AAA = gekozen ticker (onzeker), 5 kandidaten: ALT1 (foute beurs,
        # geen match), ALT2 (juiste beurs + kloppende prijs -> overtuigend),
        # ALT3/4/5 mogen daarna niet meer aangeraakt worden.
        self.alternatieven_patch = patch.object(
            analysis, "find_ticker_detailed",
            return_value={
                "ticker": "AAA",
                "zekerheid": "onzeker",
                "alternatieven": [
                    {"symbol": "ALT1", "exchange": "XETRA"},
                    {"symbol": "ALT2", "exchange": "MUN"},
                    {"symbol": "ALT3", "exchange": "MUN"},
                    {"symbol": "ALT4", "exchange": "MUN"},
                    {"symbol": "ALT5", "exchange": "MUN"},
                ],
            },
        )
        self.alternatieven_patch.start()
        self.addCleanup(self.alternatieven_patch.stop)

        details_patch = patch.object(analysis, "_ticker_details_met_cache", return_value={})
        details_patch.start()
        self.addCleanup(details_patch.stop)

        land_sector_patch = patch.object(analysis, "_land_sector_voor_weergave", return_value=(None, None, None))
        land_sector_patch.start()
        self.addCleanup(land_sector_patch.stop)

    def test_kandidaten_na_overtuigende_match_worden_niet_meer_gecheckt(self):
        call_count = {}

        def fake_vergelijk(ticker, datum, bekende_koers):
            call_count[ticker] = call_count.get(ticker, 0) + 1
            if ticker in ("AAA", "ALT2"):
                return _prijscheck(match=True)
            if ticker == "ALT1":
                return _prijscheck(match=False, afwijking_pct=50.0, yahoo_koers=150.0)
            raise AssertionError(f"'{ticker}' had niet meer gecheckt mogen worden na de match op ALT2")

        with patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            resultaat = verifieer_ticker_met_prijs("PRODUCT", "ISIN123", self.BEURS, self.transacties)

        self.assertIn("ALT1", call_count)
        self.assertIn("ALT2", call_count)
        self.assertNotIn("ALT3", call_count)
        self.assertNotIn("ALT4", call_count)
        self.assertNotIn("ALT5", call_count)
        self.assertEqual(resultaat["aanbevolen_alternatief"], "ALT2")

    def test_kandidaat_zonder_koersdata_op_eerste_datum_slaat_tweede_datum_over(self):
        call_count = {}

        def fake_vergelijk(ticker, datum, bekende_koers):
            call_count[ticker] = call_count.get(ticker, 0) + 1
            if ticker == "AAA":
                return _prijscheck(match=True)
            if ticker == "ALT1":
                # Simuleert '4BY1.F': geen koersdata beschikbaar.
                return _prijscheck(match=None, afwijking_pct=None, yahoo_koers=None)
            # ALT2 levert alsnog de overtuigende match, zodat de loop stopt
            # en ALT3-5 niet gecheckt worden (spoor 1, al gedekt door de
            # vorige test) -- hier gaat het puur om ALT1's call count.
            if ticker == "ALT2":
                return _prijscheck(match=True)
            raise AssertionError(f"'{ticker}' had niet meer gecheckt mogen worden")

        with patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            verifieer_ticker_met_prijs("PRODUCT", "ISIN123", self.BEURS, self.transacties)

        # 2 transacties in de steekproef, maar ALT1's eerste check faalt al
        # -> maar 1 aanroep voor ALT1, niet 2.
        self.assertEqual(call_count["ALT1"], 1)


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "DATABASE_URL niet ingesteld -- deze test raakt de ticker_prijscheck-cache in de echte database aan "
    "(bv. in CI zonder databasetoegang; draait lokaal wel via de .env)",
)
class TestPrijscheckCache(unittest.TestCase):
    """Spoor 2: een (ticker, datum)-combinatie die al in ticker_prijscheck
    staat mag geen nieuwe Yahoo-aanroep (_haal_slotkoers_op) veroorzaken."""

    TEST_TICKER = "TESTPRIJSCHECK.TST"
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

    def test_tweede_aanroep_gebruikt_cache_geen_nieuwe_yahoo_call(self):
        from analysis import vergelijk_prijs_op_datum

        call_count = {"n": 0}

        def fake_haal_slotkoers_op(ticker, datum, **kwargs):
            call_count["n"] += 1
            return 123.45

        with patch.object(analysis, "_haal_slotkoers_op", side_effect=fake_haal_slotkoers_op), \
             patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"}):
            eerste = vergelijk_prijs_op_datum(self.TEST_TICKER, self.TEST_DATUM, 123.45)
            tweede = vergelijk_prijs_op_datum(self.TEST_TICKER, self.TEST_DATUM, 123.45)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(eerste["yahoo_koers"], 123.45)
        self.assertEqual(tweede["yahoo_koers"], 123.45)


if __name__ == "__main__":
    unittest.main()
