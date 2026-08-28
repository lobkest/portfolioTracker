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
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import verifieer_ticker_met_prijs, vergelijk_prijs_op_datum, _cumulatieve_split_factor, BEURS_MAP


def _prijscheck(match, afwijking_pct=0.0, yahoo_koers=100.0, niveau=None):
    return {
        "yahoo_koers": yahoo_koers, "bekende_koers": 100.0, "afwijking_pct": afwijking_pct,
        "match": match, "niveau": niveau,
    }


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
             patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"}), \
             patch.object(analysis, "_haal_splits_op", return_value={}):
            eerste = vergelijk_prijs_op_datum(self.TEST_TICKER, self.TEST_DATUM, 123.45)
            tweede = vergelijk_prijs_op_datum(self.TEST_TICKER, self.TEST_DATUM, 123.45)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(eerste["yahoo_koers"], 123.45)
        self.assertEqual(tweede["yahoo_koers"], 123.45)


def _mock_yahoo_omgeving(yahoo_koers, splits=None):
    """
    Context manager die alles mockt wat vergelijk_prijs_op_datum aanraakt
    behalve de eigenlijke split-correctie- en drempel-berekening zelf, zodat
    deze los van DB/netwerk getest kan worden. 'splits': {iso_datum: ratio}
    of None (= geen bekende splits, de standaard "geen correctie"-situatie).
    """
    stack = ExitStack()
    stack.enter_context(patch.object(analysis, "get_cached_prijscheck", return_value=None))
    stack.enter_context(patch.object(analysis, "_haal_slotkoers_op", return_value=yahoo_koers))
    stack.enter_context(patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"}))
    stack.enter_context(patch.object(analysis, "save_prijscheck"))
    stack.enter_context(patch.object(analysis, "_haal_splits_op", return_value=splits or {}))
    return stack


class TestSplitCorrectie(unittest.TestCase):
    """
    Bugfix: vergelijk_prijs_op_datum hield geen rekening met aandelensplits.
    _haal_slotkoers_op gebruikt yf.download(..., auto_adjust=True), dat
    historische slotkoersen aanpast naar de HUIDIGE aandelenbasis — een
    koers van vóór een latere split komt dus terug als (koers / cumulatieve
    split-ratio), terwijl de Excel/DEGIRO-transactieprijs de ruwe prijs van
    dat moment is. Zie het echte BYD/BY6.MU-geval: een oude transactiedatum
    leek 71% af te wijken, puur door een split die daarna heeft
    plaatsgevonden.
    """

    def test_split_na_transactiedatum_corrigeert_een_grote_schijnbare_afwijking(self):
        # Simuleert een 3-voor-1-split op 2024-08-01: de Excel-prijs op
        # 2024-06-21 was 90.0, Yahoo's (auto_adjust=True) slotkoers komt
        # terug als 30.0 (=90.0 / 3, de aanpassing naar de huidige basis).
        # Ongecorrigeerd zou dit een afwijking van 66.7% zijn (comfortabel
        # "waarschuwing"-niveau); met de split-correctie moet dit ~0% zijn.
        with _mock_yahoo_omgeving(yahoo_koers=30.0, splits={"2024-08-01": 3.0}):
            resultaat = vergelijk_prijs_op_datum("BY6.MU", date(2024, 6, 21), 90.0)

        ongecorrigeerde_afwijking = abs(30.0 - 90.0) / 90.0 * 100
        self.assertGreater(ongecorrigeerde_afwijking, 60)

        self.assertEqual(resultaat["split_factor"], 3.0)
        self.assertAlmostEqual(resultaat["yahoo_koers_gecorrigeerd"], 90.0)
        self.assertLess(resultaat["afwijking_pct"], 2)
        self.assertEqual(resultaat["niveau"], "ok")
        self.assertTrue(resultaat["match"])
        # De ruwe (ongecorrigeerde) Yahoo-koers blijft gewoon zichtbaar.
        self.assertEqual(resultaat["yahoo_koers"], 30.0)

    def test_split_voor_transactiedatum_telt_niet_mee(self):
        # Een split die AL had plaatsgevonden vóór de transactiedatum zit al
        # verdisconteerd in zowel de Excel-prijs als Yahoo's koers van na
        # die datum — die mag dus niet nog eens meegeteld worden.
        with patch.object(analysis, "_haal_splits_op", return_value={"2023-01-01": 3.0}):
            factor = _cumulatieve_split_factor("BY6.MU", date(2024, 6, 21))
        self.assertEqual(factor, 1.0)

    def test_geen_split_data_beschikbaar_valt_netjes_terug_op_ongecorrigeerd(self):
        # Ticker zonder bekende (of niet op te halen) splits -> gewoon de
        # bestaande, ongecorrigeerde vergelijking, geen crash.
        with _mock_yahoo_omgeving(yahoo_koers=95.0, splits={}):
            resultaat = vergelijk_prijs_op_datum("AAPL", date(2024, 1, 1), 100.0)

        self.assertEqual(resultaat["split_factor"], 1.0)
        self.assertIsNone(resultaat["yahoo_koers_gecorrigeerd"])
        self.assertAlmostEqual(resultaat["afwijking_pct"], 5.0)


class TestDrieNiveausIndicator(unittest.TestCase):
    """Bugfix: elke afwijking >0% kreeg hetzelfde ⚠️-icoon. Nu drie niveaus
    (zie PRIJSCHECK_DREMPEL_OK/_WAARSCHUWING in analysis.py)."""

    def _niveau_voor_afwijking(self, afwijking_pct):
        # bekende_koers=100 -> yahoo_koers = 100 - afwijking_pct geeft
        # precies afwijking_pct% afwijking (voor koersen boven yahoo_koers,
        # simpelste manier om een exacte, voorspelbare afwijking te forceren).
        with _mock_yahoo_omgeving(yahoo_koers=100 - afwijking_pct, splits={}):
            resultaat = vergelijk_prijs_op_datum("TICK", date(2024, 1, 1), 100.0)
        return resultaat

    def test_1_procent_is_ok(self):
        r = self._niveau_voor_afwijking(1.0)
        self.assertEqual(r["niveau"], "ok")
        self.assertTrue(r["match"])

    def test_4_procent_is_mild(self):
        r = self._niveau_voor_afwijking(4.0)
        self.assertEqual(r["niveau"], "mild")
        self.assertTrue(r["match"])  # mild telt nog mee als "match" voor het zeker/onzeker-oordeel

    def test_8_procent_is_waarschuwing(self):
        r = self._niveau_voor_afwijking(8.0)
        self.assertEqual(r["niveau"], "waarschuwing")
        self.assertFalse(r["match"])

    def test_precies_2_procent_valt_in_mild_niet_in_ok(self):
        r = self._niveau_voor_afwijking(2.0)
        self.assertEqual(r["niveau"], "mild")

    def test_precies_6_procent_valt_in_waarschuwing_niet_in_mild(self):
        r = self._niveau_voor_afwijking(6.0)
        self.assertEqual(r["niveau"], "waarschuwing")
        self.assertFalse(r["match"])


class TestZekerheidOordeelMetMildeAfwijking(unittest.TestCase):
    """Een positie met uitsluitend milde (2-6%) afwijkingen mag niet meer
    van 'Zeker' naar 'Onzeker' gedegradeerd worden — alleen een echte
    'waarschuwing' (>6%) doet dat nog. Zie AKZO NOBEL/TDT.AS/EUEA.AS/
    VWCE.AS/VUSA.AS: die hadden allemaal alleen kleine (0.8-5.5%)
    afwijkingen en werden voorheen onterecht 'Onzeker'."""

    def setUp(self):
        self.transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        patcher = patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AKZA.AS", "zekerheid": "zeker", "alternatieven": []},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        details_patch = patch.object(analysis, "_ticker_details_met_cache", return_value={})
        details_patch.start()
        self.addCleanup(details_patch.stop)
        land_sector_patch = patch.object(analysis, "_land_sector_voor_weergave", return_value=(None, None, None))
        land_sector_patch.start()
        self.addCleanup(land_sector_patch.stop)

    def test_alleen_milde_afwijkingen_blijft_zeker(self):
        # side_effect (i.p.v. return_value) geeft elke aanroep een NIEUW
        # dict terug -- verifieer_ticker_met_prijs muteert het teruggegeven
        # dict (voegt "datum" toe), een gedeeld dict zou dus de datum van de
        # vorige aanroep overschrijven.
        with patch.object(analysis, "vergelijk_prijs_op_datum",
                           side_effect=lambda *a, **kw: _prijscheck(match=True, afwijking_pct=4.7, niveau="mild")):
            resultaat = verifieer_ticker_met_prijs("AKZO NOBEL NV", "NL0013267909", "EAM", self.transacties)

        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertIsNone(resultaat["waarschuwing"])

    def test_een_echte_waarschuwing_degradeert_nog_altijd_naar_onzeker(self):
        def fake_vergelijk(ticker, datum, bekende_koers):
            if str(datum) == "2023-01-10":
                return _prijscheck(match=False, afwijking_pct=71.3, niveau="waarschuwing")
            return _prijscheck(match=True, afwijking_pct=3.7, niveau="ok")

        with patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            resultaat = verifieer_ticker_met_prijs("AKZO NOBEL NV", "NL0013267909", "EAM", self.transacties)

        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertIn("1 van de 2", resultaat["waarschuwing"])
        self.assertIn("71.3%", resultaat["waarschuwing"])


if __name__ == "__main__":
    unittest.main()
