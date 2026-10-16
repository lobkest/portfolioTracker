"""yfinance gooit bij 'database is locked' niets maar logt het en geeft een leeg resultaat: dat moet de retry in gang zetten."""
import logging
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import yahoo_client
from yahoo_client import download_koersen_met_retry, init_yfinance_cache, YFINANCE_CACHE_MAP

LOCKED_BERICHT = "['EMIM.AS']: OperationalError('database is locked')"


def koersen():
    index = pd.to_datetime(["2026-10-15", "2026-10-16"])
    return pd.DataFrame({"Close": [10.0, 11.0], "Stock Splits": [0.0, 0.0]}, index=index)


def leeg():
    return pd.DataFrame(columns=["Close", "Stock Splits"], dtype=float)


class TestDatabaseLockedRetry(unittest.TestCase):
    def setUp(self):
        yahoo_client._yfinance_fouten.clear()
        self.addCleanup(yahoo_client._yfinance_fouten.clear)
        patcher = patch.object(yahoo_client.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def _download(self, resultaten, log_bij_poging=()):
        aanroepen = []

        def nep(*args, **kwargs):
            aanroepen.append(args)
            if len(aanroepen) in log_bij_poging:
                logging.getLogger("yfinance").error(LOCKED_BERICHT)
            return resultaten[len(aanroepen) - 1]

        return nep, aanroepen

    def test_locked_bij_eerste_poging_wordt_opnieuw_geprobeerd(self):
        nep, aanroepen = self._download([leeg(), koersen()], log_bij_poging=(1,))
        with patch.object(yahoo_client.yf, "download", side_effect=nep):
            close, _splits = download_koersen_met_retry("EMIM.AS", "2026-10-01")
        self.assertEqual(len(aanroepen), 2)
        self.assertEqual(list(close["Close"]) if "Close" in close else list(close.iloc[:, 0]), [10.0, 11.0])
        self.sleep.assert_called_once()

    def test_locked_bij_groepsdownload_met_een_getroffen_ticker_herhaalt_de_groep(self):
        gelukt = pd.concat({"Close": koersen()[["Close"]].rename(columns={"Close": "EMIM.AS"})}, axis=1)
        gelukt.columns = pd.MultiIndex.from_tuples([("Close", "EMIM.AS")])
        gelukt[("Stock Splits", "EMIM.AS")] = 0.0
        nep, aanroepen = self._download([gelukt.iloc[:0], gelukt], log_bij_poging=(1,))
        with patch.object(yahoo_client.yf, "download", side_effect=nep):
            close, _ = download_koersen_met_retry(["EMIM.AS", "VWCE.DE"], "2026-10-01")
        self.assertEqual(len(aanroepen), 2)
        self.assertFalse(close.empty)

    def test_locked_voor_een_andere_ticker_geeft_geen_extra_poging(self):
        nep, aanroepen = self._download([koersen()], log_bij_poging=(1,))
        with patch.object(yahoo_client.yf, "download", side_effect=nep):
            download_koersen_met_retry("VWCE.DE", "2026-10-01")
        self.assertEqual(len(aanroepen), 1)

    def test_na_alle_pogingen_een_leeg_resultaat_zonder_exception(self):
        nep, aanroepen = self._download([leeg()] * 3, log_bij_poging=(1, 2, 3))
        with patch.object(yahoo_client.yf, "download", side_effect=nep):
            close, splits = download_koersen_met_retry("EMIM.AS", "2026-10-01", pogingen=3)
        self.assertEqual(len(aanroepen), 3)
        self.assertTrue(close.empty)
        self.assertEqual(splits, {})

    def test_oude_fouten_van_voor_de_download_tellen_niet_mee(self):
        logging.getLogger("yfinance").error(LOCKED_BERICHT)
        nep, aanroepen = self._download([koersen()])
        with patch.object(yahoo_client.yf, "download", side_effect=nep):
            download_koersen_met_retry("EMIM.AS", "2026-10-01")
        self.assertEqual(len(aanroepen), 1)

    def test_opvanger_wordt_maar_een_keer_geinstalleerd(self):
        yahoo_client.installeer_database_locked_opvanger()
        yahoo_client.installeer_database_locked_opvanger()
        handlers = [h for h in logging.getLogger("yfinance").handlers
                    if isinstance(h, yahoo_client._DatabaseLockedOpvanger)]
        self.assertEqual(len(handlers), 1)


class TestInitYfinanceCache(unittest.TestCase):
    def test_zet_een_eigen_map_en_maakt_de_cache_een_keer_aan(self):
        with patch.object(yahoo_client.yf, "set_tz_cache_location") as zet, \
                patch.object(yahoo_client, "get_tz_cache") as cache:
            init_yfinance_cache()
        zet.assert_called_once_with(YFINANCE_CACHE_MAP)
        cache.return_value.initialise.assert_called_once_with()

    def test_een_fout_bij_voorbereiden_breekt_het_opstarten_niet(self):
        with patch.object(yahoo_client.yf, "set_tz_cache_location", side_effect=OSError("geen schrijfrechten")):
            init_yfinance_cache()


if __name__ == "__main__":
    unittest.main()
