"""
Unit tests voor de FX-caching-fix en de gedeelde rate-limit-retry-helper
(zie CLAUDE.md, performance-meting upload/analyse-flow -> opdracht
FX-caching + retry/backoff-consolidatie).

Achtergrond: _fx_koers_op_datum() (gebruikt door vergelijk_prijs_op_datum
tijdens ticker-resolutie) en _converteer_naar_eur() (gebruikt door
get_prices()) downloadden allebei onafhankelijk van elkaar dezelfde
FX-koers, zonder enige caching -- ook binnen één upload werd bv. USDEUR=X
op dezelfde datum meermaals opnieuw opgehaald. Nu gaat elke FX-opzoeking
via _fx_prijzen_serie(), die de bestaande prijzen-tabel/get_prices()-cache
hergebruikt (een FX-paar is voor yfinance gewoon een ticker).

Daarnaast stond het rate-limit-detectiepatroon (+ oplopende backoff)
drie keer bijna-identiek uitgeschreven in _fetch_yf_info, _haal_slotkoers_op
en _haal_dagrange_op -- nu gedeeld via _met_rate_limit_retry().

Draait geheel offline: get_db_connection/download_met_retry/save_prices/
yf.Ticker/time.sleep worden gemockt, geen echte database- of Yahoo-calls.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis


def _mock_conn_voor_twee_aanroepen(
    eerste_min_max, eerste_cached, tweede_min_max, tweede_cached,
    eerste_laatst_ververst=None, tweede_laatst_ververst=None,
):
    """Bouwt een gemockte get_db_connection()-return die na elkaar de
    fetchall()-resultaten voor TWEE opeenvolgende get_prices()-aanroepen
    teruggeeft (elk: MIN/MAX-rij(en), dan (ticker, bijgewerkt_op) voor de
    rij van 'vandaag', dan gecachete (ticker, datum, koers_eur)-rijen) --
    zelfde patroon als tests/test_koersen_cache.py."""
    cur = MagicMock()
    cur.fetchall.side_effect = [
        eerste_min_max, eerste_laatst_ververst or [], eerste_cached,
        tweede_min_max, tweede_laatst_ververst or [], tweede_cached,
    ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestFxKoersCaching(unittest.TestCase):
    """Eenzelfde (valuta, datum)-combinatie mag maar één keer een echte
    yfinance-download veroorzaken, ook over losse aanroepen van
    _fx_koers_op_datum() heen -- vóór de fix downloadde elke aanroep zijn
    eigen, ongecachete FX-koers."""

    @patch("analysis.yf.Ticker")
    @patch("analysis.save_prices")
    @patch("analysis.download_met_retry")
    @patch("analysis.get_db_connection")
    def test_tweede_fx_opzoeking_zelfde_valuta_en_datum_doet_geen_nieuwe_download(
        self, mock_get_conn, mock_download, mock_save, mock_yf_ticker
    ):
        # De FX-ticker zelf noteert al in EUR -- _converteer_naar_eur() (dat
        # get_prices() intern aanroept voor elke net gedownloade ticker)
        # mag hier dus geen extra conversieslag op toepassen.
        mock_yf_ticker.return_value.info = {"currency": "EUR"}

        vandaag = pd.Timestamp.now().normalize()
        gevraagde_datum = pd.Timestamp("2024-03-01")

        mock_get_conn.return_value = _mock_conn_voor_twee_aanroepen(
            eerste_min_max=[],  # 1e aanroep: FX-paar nog nooit gecached
            eerste_cached=[],
            tweede_min_max=[("USDEUR=X", analysis.FX_ANKER_DATUM.date(), vandaag.date())],
            tweede_cached=[("USDEUR=X", gevraagde_datum.date(), 0.9)],
            # 2e aanroep: net (binnen 2 min) ververst door de 1e aanroep
            # (net als in productie -- de prijzen-tabel zet bijgewerkt_op
            # via een kolom-default bij elke INSERT) -> geen nieuwe download.
            tweede_laatst_ververst=[("USDEUR=X", datetime.now())],
        )
        mock_download.return_value = pd.Series({gevraagde_datum: 0.9}, name="USDEUR=X")

        eerste = analysis._fx_koers_op_datum("USD", gevraagde_datum)
        tweede = analysis._fx_koers_op_datum("USD", gevraagde_datum)

        self.assertEqual(mock_download.call_count, 1)
        self.assertEqual(eerste, 0.9)
        self.assertEqual(tweede, 0.9)

    def test_onbekende_valuta_geeft_none_zonder_download(self):
        with patch("analysis.download_met_retry") as mock_download:
            resultaat = analysis._fx_koers_op_datum("JPY", pd.Timestamp("2024-03-01"))

        self.assertIsNone(resultaat)
        mock_download.assert_not_called()


class TestIsRateLimitFout(unittest.TestCase):
    """_is_rate_limit_fout() herkent naast de letterlijke rate-limit-
    meldingen ook Yahoo's 'Invalid Crumb'/HTTP 401-foutbeeld (zie de
    28-posities-pooltest bij TICKER_RESOLUTIE_POOL_GROOTTE) -- dat kwam
    daar vooral voor bij breed-genoteerde ETF's zoals IWDA.AS/VWRL.AS en
    faalde voorheen in één keer definitief, zonder retry-poging."""

    def test_bestaande_rate_limit_meldingen_blijven_herkend(self):
        self.assertTrue(analysis._is_rate_limit_fout(Exception("Rate limit exceeded")))
        self.assertTrue(analysis._is_rate_limit_fout(Exception("Too Many Requests")))

    def test_invalid_crumb_wordt_herkend(self):
        fout = Exception(
            'HTTP Error 401: {"finance":{"result":null,"error":'
            '{"code":"Unauthorized","description":"Invalid Crumb"}}}'
        )
        self.assertTrue(analysis._is_rate_limit_fout(fout))

    def test_http_401_zonder_invalid_crumb_wordt_ook_herkend(self):
        fout = Exception(
            'HTTP Error 401: {"finance":{"result":null,"error":'
            '{"code":"Unauthorized","description":"User is unable to access '
            'this feature - https://bit.ly/yahoo-finance-api-feedback"}}}'
        )
        self.assertTrue(analysis._is_rate_limit_fout(fout))

    def test_andere_fout_wordt_niet_als_rate_limit_herkend(self):
        self.assertFalse(analysis._is_rate_limit_fout(ValueError("iets heel anders")))
        self.assertFalse(analysis._is_rate_limit_fout(Exception("Data doesn't exist for startDate")))


class TestMetRateLimitRetry(unittest.TestCase):
    """Gedeelde retry/backoff-helper voor _fetch_yf_info, _haal_slotkoers_op
    en _haal_dagrange_op."""

    @patch("analysis.time.sleep")
    def test_rate_limit_gevolgd_door_succes_retryt_met_oplopende_backoff(self, mock_sleep):
        pogingen_gedaan = {"n": 0}

        def actie():
            pogingen_gedaan["n"] += 1
            if pogingen_gedaan["n"] < 3:
                raise Exception("Too Many Requests")
            return "ok"

        resultaat, fout = analysis._met_rate_limit_retry(actie, "test", "'X'", pogingen=3, wachttijd=8)

        self.assertEqual(resultaat, "ok")
        self.assertIsNone(fout)
        self.assertEqual(pogingen_gedaan["n"], 3)
        # oplopende backoff: 8s na poging 1, 16s na poging 2.
        self.assertEqual(mock_sleep.call_args_list, [call(8), call(16)])

    @patch("analysis.time.sleep")
    def test_definitieve_mislukking_na_alle_pogingen_geeft_fout_terug(self, mock_sleep):
        def actie():
            raise Exception("rate limit exceeded")

        resultaat, fout = analysis._met_rate_limit_retry(actie, "test", "'X'", pogingen=2, wachttijd=5)

        self.assertIsNone(resultaat)
        self.assertIsInstance(fout, Exception)
        # maar 2 pogingen ingesteld -> maar 1 keer wachten (na poging 1).
        self.assertEqual(mock_sleep.call_args_list, [call(5)])

    @patch("analysis.time.sleep")
    def test_invalid_crumb_gevolgd_door_succes_retryt_nu_ook(self, mock_sleep):
        # Voorheen faalde dit in één keer definitief: _is_rate_limit_fout()
        # herkende "Invalid Crumb" niet, dus geen retry-poging.
        pogingen_gedaan = {"n": 0}

        def actie():
            pogingen_gedaan["n"] += 1
            if pogingen_gedaan["n"] < 2:
                raise Exception(
                    'HTTP Error 401: {"finance":{"result":null,"error":'
                    '{"code":"Unauthorized","description":"Invalid Crumb"}}}'
                )
            return "ok"

        resultaat, fout = analysis._met_rate_limit_retry(actie, "test", "'X'", pogingen=3, wachttijd=8)

        self.assertEqual(resultaat, "ok")
        self.assertIsNone(fout)
        self.assertEqual(pogingen_gedaan["n"], 2)
        mock_sleep.assert_called_once_with(8)

    @patch("analysis.time.sleep")
    def test_niet_rate_limit_fout_stopt_meteen_zonder_retry(self, mock_sleep):
        def actie():
            raise ValueError("iets heel anders")

        resultaat, fout = analysis._met_rate_limit_retry(actie, "test", "'X'", pogingen=3, wachttijd=5)

        self.assertIsNone(resultaat)
        self.assertIsInstance(fout, ValueError)
        mock_sleep.assert_not_called()

    @patch("analysis.time.sleep")
    def test_fetch_yf_info_gebruikt_gedeelde_retry_en_geeft_none_na_mislukking(self, mock_sleep):
        with patch("analysis.yf.Ticker", side_effect=Exception("Too Many Requests")):
            resultaat = analysis._fetch_yf_info("AAPL", pogingen=2, wachttijd=5)

        self.assertIsNone(resultaat)
        self.assertEqual(mock_sleep.call_args_list, [call(5)])


if __name__ == "__main__":
    unittest.main()
