"""
Unit tests voor analysis.haal_openfigi_resultaten() — EXPERIMENTEEL/
DIAGNOSTISCH paneel op de Ticker-zekerheid-pagina (zie CLAUDE.md), inmiddels
aangevuld met een permanente DB-cache (openfigi_cache). Draait geheel
offline: analysis.requests.post EN de cache-functies (get_cached_openfigi/
save_openfigi) worden gemockt, dus geen echte OpenFIGI-netwerk-calls en geen
(gedeelde, persistente) databasetoegang nodig -- zonder die laatste mock zou
elke test tegen dezelfde echte Neon-DB lopen en elkaars cache-writes zien.
"""
import sys
import os
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import analysis
from analysis import haal_openfigi_resultaten


def _mock_response(status_code=200, json_data=None):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


def _leeg_cache_patch():
    """Patcht de DB-cache leeg/no-op zodat elke test daadwerkelijk het
    requests.post-mock-pad doorloopt i.p.v. een cache-hit van een vorige
    test (of een vorige testrun) te zien."""
    return (
        patch.object(analysis, "get_cached_openfigi", return_value=None),
        patch.object(analysis, "save_openfigi"),
    )


class TestHaalOpenfigiResultaten(unittest.TestCase):
    def setUp(self):
        self._cache_get_patcher, self._cache_save_patcher = _leeg_cache_patch()
        self.mock_get_cache = self._cache_get_patcher.start()
        self.mock_save_cache = self._cache_save_patcher.start()
        self.addCleanup(self._cache_get_patcher.stop)
        self.addCleanup(self._cache_save_patcher.stop)

    @patch("analysis.requests.post")
    def test_succesvolle_match(self, mock_post):
        mock_post.return_value = _mock_response(200, [{
            "data": [{
                "ticker": "AAPL", "exchCode": "US", "name": "APPLE INC",
                "securityType": "Common Stock", "marketSector": "Equity",
                "compositeFIGI": "BBG000B9XVN3",
            }]
        }])
        result = haal_openfigi_resultaten("US0378331005")
        self.assertIsNone(result["fout"])
        self.assertEqual(len(result["resultaten"]), 1)
        self.assertEqual(result["resultaten"][0]["ticker"], "AAPL")
        self.mock_save_cache.assert_called_once_with("US0378331005", result["resultaten"])

    @patch("analysis.requests.post")
    def test_geen_match(self, mock_post):
        mock_post.return_value = _mock_response(200, [{"warning": "No identifier found."}])
        result = haal_openfigi_resultaten("XX0000000000")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("No identifier found", result["fout"])
        # Een "geen match" wordt WEL gecached (als lege lijst, zie
        # haal_openfigi_resultaten) -- net zo stabiel als een positieve match.
        self.mock_save_cache.assert_called_once_with("XX0000000000", [])

    @patch("analysis.requests.post")
    def test_rate_limit(self, mock_post):
        mock_post.return_value = _mock_response(429)
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("rate limit", result["fout"].lower())
        self.mock_save_cache.assert_not_called()

    @patch("analysis.requests.post")
    def test_netwerkfout(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("boom")
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("niet bereikbaar", result["fout"].lower())
        self.mock_save_cache.assert_not_called()

    @patch("analysis.requests.post")
    def test_meerdere_beursnoteringen(self, mock_post):
        mock_post.return_value = _mock_response(200, [{
            "data": [
                {"ticker": "VWCE", "exchCode": "GR", "name": "VANGUARD FTSE ALL-WRLD"},
                {"ticker": "VWCE", "exchCode": "MI", "name": "VANGUARD FTSE ALL-WRLD"},
            ]
        }])
        result = haal_openfigi_resultaten("IE00BK5BQT80")
        self.assertEqual(len(result["resultaten"]), 2)

    @patch("analysis.requests.post")
    def test_lege_isin_geen_crash_geen_netwerkcall(self, mock_post):
        result = haal_openfigi_resultaten("")
        self.assertEqual(result["resultaten"], [])
        self.assertIsNotNone(result["fout"])
        mock_post.assert_not_called()

    @patch("analysis.requests.post")
    def test_none_isin_geen_crash_geen_netwerkcall(self, mock_post):
        result = haal_openfigi_resultaten(None)
        self.assertEqual(result["resultaten"], [])
        self.assertIsNotNone(result["fout"])
        mock_post.assert_not_called()

    @patch("analysis.requests.post")
    def test_onverwachte_status_code(self, mock_post):
        mock_post.return_value = _mock_response(500)
        result = haal_openfigi_resultaten("US0378331005")
        self.assertEqual(result["resultaten"], [])
        self.assertIn("500", result["fout"])
        self.mock_save_cache.assert_not_called()

    @patch("analysis.requests.post")
    def test_cache_hit_slaat_netwerkcall_over(self, mock_post):
        self.mock_get_cache.return_value = [{"ticker": "AAPL", "exchCode": "US"}]
        result = haal_openfigi_resultaten("US0378331005")
        mock_post.assert_not_called()
        self.assertIsNone(result["fout"])
        self.assertEqual(result["resultaten"], [{"ticker": "AAPL", "exchCode": "US"}])

    @patch("analysis.requests.post")
    def test_cache_hit_op_geen_match_geeft_lege_lijst_geen_fout(self, mock_post):
        # Een eerder gecachete "geen match" (lege lijst, zie test_geen_match
        # hierboven) mag bij een volgende aanroep niet als fout terugkomen.
        self.mock_get_cache.return_value = []
        result = haal_openfigi_resultaten("XX0000000000")
        mock_post.assert_not_called()
        self.assertIsNone(result["fout"])
        self.assertEqual(result["resultaten"], [])


class TestOpenfigiRootBekend(unittest.TestCase):
    def test_root_gevonden_exact(self):
        resultaten = [{"ticker": "BY6", "exchCode": "GR"}, {"ticker": "1211", "exchCode": "HK"}]
        self.assertIs(analysis._openfigi_root_bekend("BY6.MU", resultaten), True)

    def test_root_gevonden_met_suffix_variant(self):
        resultaten = [{"ticker": "1211HKD", "exchCode": "X2"}]
        self.assertIs(analysis._openfigi_root_bekend("1211.HK", resultaten), True)

    def test_root_niet_gevonden(self):
        resultaten = [{"ticker": "VWRL", "exchCode": "NA"}, {"ticker": "VGWL", "exchCode": "GR"}]
        self.assertIs(analysis._openfigi_root_bekend("VWCE.AS", resultaten), False)

    def test_geen_oordeel_zonder_ticker(self):
        self.assertIsNone(analysis._openfigi_root_bekend(None, [{"ticker": "AAPL"}]))

    def test_geen_oordeel_zonder_resultaten(self):
        self.assertIsNone(analysis._openfigi_root_bekend("AAPL", []))

    def test_ticker_zonder_punt_geen_crash(self):
        self.assertIs(analysis._openfigi_root_bekend("AAPL", [{"ticker": "AAPL"}]), True)


class TestOpenfigiRootMatches(unittest.TestCase):
    def test_telt_alle_matchende_rijen_ongeacht_beurs(self):
        resultaten = [
            {"ticker": "BY6", "exchCode": "GR"}, {"ticker": "BY6", "exchCode": "GF"},
            {"ticker": "1211", "exchCode": "HK"},
        ]
        self.assertEqual(analysis._openfigi_root_matches("BY6.MU", resultaten), 2)

    def test_geen_matches_geeft_nul_niet_none(self):
        resultaten = [{"ticker": "VWRL", "exchCode": "NA"}]
        self.assertEqual(analysis._openfigi_root_matches("VWCE.AS", resultaten), 0)

    def test_none_zonder_ticker_of_resultaten(self):
        self.assertIsNone(analysis._openfigi_root_matches(None, [{"ticker": "AAPL"}]))
        self.assertIsNone(analysis._openfigi_root_matches("AAPL", []))


class TestVoegOpenfigiCheckToe(unittest.TestCase):
    """_voeg_openfigi_check_toe() is de gedeelde hook die zowel
    find_ticker_met_snelle_prijscheck() als verifieer_ticker_met_prijs()
    gebruiken -- hier los getest op de drie oordelen (True/False/None) en
    het configureerbare waarschuwing_veld (die twee functies gebruiken
    allebei een andere sleutel: 'prijswaarschuwing' resp. 'waarschuwing')."""

    def test_root_bekend_zet_samenvattingsvelden_zonder_waarschuwing(self):
        with patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "AAPL", "exchCode": "US"}], "fout": None},
        ):
            resultaat = analysis._voeg_openfigi_check_toe(
                {"ticker": "AAPL", "zekerheid": "zeker", "prijswaarschuwing": None}, "US0378331005"
            )
        self.assertIs(resultaat["openfigi_root_bekend"], True)
        self.assertEqual(resultaat["openfigi_root_matches"], 1)
        self.assertIsNone(resultaat["prijswaarschuwing"])
        self.assertEqual(resultaat["zekerheid"], "zeker")

    def test_root_niet_bekend_voegt_waarschuwing_toe_op_gegeven_veld(self):
        with patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
        ):
            resultaat = analysis._voeg_openfigi_check_toe(
                {"ticker": "VWCE.AS", "zekerheid": "zeker", "waarschuwing": None},
                "LU1737085518", waarschuwing_veld="waarschuwing",
            )
        self.assertIs(resultaat["openfigi_root_bekend"], False)
        self.assertEqual(resultaat["openfigi_root_matches"], 0)
        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertIn("VWCE", resultaat["waarschuwing"])
        self.assertNotIn("prijswaarschuwing", resultaat)

    def test_root_niet_bekend_vult_bestaande_waarschuwing_aan_i_p_v_te_overschrijven(self):
        with patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
        ):
            resultaat = analysis._voeg_openfigi_check_toe(
                {"ticker": "VWCE.AS", "zekerheid": "onzeker", "waarschuwing": "Bestaande prijswaarschuwing."},
                "LU1737085518", waarschuwing_veld="waarschuwing",
            )
        self.assertIn("Bestaande prijswaarschuwing.", resultaat["waarschuwing"])
        self.assertIn("VWCE", resultaat["waarschuwing"])
        self.assertIn("\n", resultaat["waarschuwing"])

    def test_geen_oordeel_laat_resultaat_ongemoeid(self):
        with patch.object(analysis, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}):
            resultaat = analysis._voeg_openfigi_check_toe(
                {"ticker": "AAPL", "zekerheid": "zeker", "prijswaarschuwing": None}, "US0378331005"
            )
        self.assertIsNone(resultaat["openfigi_root_bekend"])
        self.assertIsNone(resultaat["openfigi_root_matches"])
        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertIsNone(resultaat["prijswaarschuwing"])

    def test_zonder_ticker_geen_netwerkcall_en_lege_samenvattingsvelden(self):
        with patch.object(analysis, "haal_openfigi_resultaten") as mock_haal:
            resultaat = analysis._voeg_openfigi_check_toe(
                {"ticker": None, "zekerheid": "geen_match"}, "US0378331005"
            )
        mock_haal.assert_not_called()
        self.assertIsNone(resultaat["openfigi_root_bekend"])
        self.assertIsNone(resultaat["openfigi_root_matches"])


class TestFindTickerMetSnellePrijscheckOpenfigiIntegratie(unittest.TestCase):
    """Integratietest: een positie waarvan de prijscontrole GEEN afwijking
    laat zien, maar waarvan OpenFIGI een niet-matchende resultatenlijst
    teruggeeft, moet alsnog als 'onzeker' eindigen met een waarschuwing --
    het VWCE.AS-geval uit de opdracht (mild geen prijsprobleem, maar de
    ticker-root zelf komt niet voor bij OpenFIGI voor deze ISIN)."""

    def test_root_niet_bekend_degradeert_zekere_match_naar_onzeker(self):
        from datetime import date
        transacties = [{"datum": date(2023, 6, 10), "koers": 100.0}]

        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "VWCE.AS", "zekerheid": "zeker", "alternatieven": []},
        ), patch.object(
            analysis, "vergelijk_prijs_op_datum",
            return_value={
                "yahoo_koers": 100.0, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
                "bekende_koers": 100.0, "afwijking_pct": 0.5, "niveau": "ok", "match": True,
            },
        ), patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
        ):
            resultaat = analysis.find_ticker_met_snelle_prijscheck(
                "VANGUARD FTSE ALL-WORLD USD DIS", "LU1737085518", "EAM", transacties
            )

        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertIsNotNone(resultaat["prijswaarschuwing"])
        self.assertIn("OpenFIGI", resultaat["prijswaarschuwing"])
        self.assertIn("VWCE", resultaat["prijswaarschuwing"])

    def test_root_wel_bekend_laat_zekere_match_ongemoeid(self):
        from datetime import date
        transacties = [{"datum": date(2023, 6, 10), "koers": 100.0}]

        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []},
        ), patch.object(
            analysis, "vergelijk_prijs_op_datum",
            return_value={
                "yahoo_koers": 100.0, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
                "bekende_koers": 100.0, "afwijking_pct": 0.5, "niveau": "ok", "match": True,
            },
        ), patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "AAPL", "exchCode": "US"}], "fout": None},
        ):
            resultaat = analysis.find_ticker_met_snelle_prijscheck(
                "APPLE INC", "US0378331005", "NASDAQ", transacties
            )

        self.assertEqual(resultaat["zekerheid"], "zeker")
        self.assertIsNone(resultaat["prijswaarschuwing"])


class TestVerifieerTickerMetPrijsOpenfigiIntegratie(unittest.TestCase):
    """Zelfde soort integratietest als hierboven, maar dan voor de
    duurdere, volledige check achter de Ticker-zekerheid-pagina
    (verifieer_ticker_met_prijs) -- gebruikt het 'waarschuwing'-veld i.p.v.
    'prijswaarschuwing', zie _voeg_openfigi_check_toe()."""

    def test_root_niet_bekend_degradeert_en_waarschuwt_op_waarschuwing_veld(self):
        from datetime import date
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        prijs_ok = {
            "yahoo_koers": 100.0, "bekende_koers": 100.0, "afwijking_pct": 0.5,
            "niveau": "ok", "match": True, "binnen_dagrange": True,
        }

        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": "VWCE.AS", "zekerheid": "zeker", "alternatieven": []},
        ), patch.object(
            analysis, "vergelijk_prijs_op_datum", return_value=prijs_ok,
        ), patch.object(
            analysis, "_ticker_details_met_cache", return_value={},
        ), patch.object(
            analysis, "_land_sector_voor_weergave", return_value=(None, None, None),
        ), patch.object(
            analysis, "classify_ticker", return_value=False,
        ), patch.object(
            analysis, "haal_openfigi_resultaten",
            return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
        ):
            resultaat = analysis.verifieer_ticker_met_prijs(
                "VANGUARD FTSE ALL-WORLD USD DIS", "LU1737085518", "EAM", transacties
            )

        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertIsNotNone(resultaat["waarschuwing"])
        self.assertIn("OpenFIGI", resultaat["waarschuwing"])
        self.assertIs(resultaat["openfigi_root_bekend"], False)

    def test_geen_ticker_pad_zet_ook_lege_openfigi_velden_geen_crash(self):
        with patch.object(
            analysis, "find_ticker_detailed",
            return_value={"ticker": None, "zekerheid": "geen_match", "alternatieven": []},
        ), patch.object(analysis, "haal_openfigi_resultaten") as mock_haal:
            resultaat = analysis.verifieer_ticker_met_prijs("ONBEKEND FONDS", "XX0000000000", "XYZ", [])

        mock_haal.assert_not_called()
        self.assertIsNone(resultaat["ticker"])
        self.assertIsNone(resultaat["openfigi_root_bekend"])


class TestPrijswaarschuwingVoorTickerOpenfigiIntegratie(unittest.TestCase):
    """prijswaarschuwing_voor_ticker() (de permanente banner bovenaan een
    opgeslagen portfolio) krijgt de OpenFIGI-root-check alleen als een isin
    wordt meegegeven -- bestaande aanroepen zonder isin blijven ongewijzigd."""

    def test_root_niet_bekend_geeft_waarschuwing_ook_zonder_prijsprobleem(self):
        from datetime import date
        geen_probleem = {
            "yahoo_koers": 100.0, "bekende_koers": 100.0, "afwijking_pct": 0.5,
            "niveau": "ok", "match": True, "binnen_dagrange": True,
        }
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=geen_probleem), \
             patch.object(
                 analysis, "haal_openfigi_resultaten",
                 return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
             ):
            boodschap = analysis.prijswaarschuwing_voor_ticker(
                "VWCE.AS", [{"datum": date(2023, 6, 10), "koers": 100.0}], isin="LU1737085518",
            )

        self.assertIsNotNone(boodschap)
        self.assertIn("VWCE", boodschap)
        self.assertIn("OpenFIGI", boodschap)

    def test_zonder_isin_geen_openfigi_call_bestaand_gedrag(self):
        from datetime import date
        geen_probleem = {
            "yahoo_koers": 100.0, "bekende_koers": 100.0, "afwijking_pct": 0.5,
            "niveau": "ok", "match": True, "binnen_dagrange": True,
        }
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=geen_probleem), \
             patch.object(analysis, "haal_openfigi_resultaten") as mock_haal:
            boodschap = analysis.prijswaarschuwing_voor_ticker(
                "VWCE.AS", [{"datum": date(2023, 6, 10), "koers": 100.0}],
            )

        mock_haal.assert_not_called()
        self.assertIsNone(boodschap)

    def test_root_niet_bekend_vult_bestaande_prijswaarschuwing_aan(self):
        from datetime import date
        afwijking = {
            "yahoo_koers": 50.0, "bekende_koers": 100.0, "afwijking_pct": 50.0,
            "niveau": "waarschuwing", "match": False, "binnen_dagrange": False,
        }
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=afwijking), \
             patch.object(
                 analysis, "haal_openfigi_resultaten",
                 return_value={"resultaten": [{"ticker": "VWRL", "exchCode": "NA"}], "fout": None},
             ):
            boodschap = analysis.prijswaarschuwing_voor_ticker(
                "VWCE.AS", [{"datum": date(2023, 6, 10), "koers": 100.0}], isin="LU1737085518",
            )

        self.assertIn("dagrange", boodschap)
        self.assertIn("OpenFIGI", boodschap)
        self.assertIn("\n", boodschap)


if __name__ == "__main__":
    unittest.main()
