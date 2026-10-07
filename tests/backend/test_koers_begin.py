"""koers_begin in get_prices(): een ticker waarvan Yahoo's historie later begint dan de gevraagde startdatum wordt niet bij
elke aanroep opnieuw volledig gedownload. Zonder database en zonder Yahoo: de cache-tabellen zijn dicts."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import prijzen

START = pd.Timestamp("2016-10-07")
YAHOO_BEGIN = pd.Timestamp("2022-06-01")
EIND = pd.Timestamp("2026-10-06")


class _NepCache:
    """Koersen- en koers_begin-tabel in het geheugen, plus de downloads die get_prices() doet."""

    def __init__(self, yahoo_begin=YAHOO_BEGIN, download_leeg=False):
        self.koersen = {}
        self.bijgewerkt = {}
        self.koers_begin = {}
        self.volledige_downloads = []
        self.incrementele_downloads = []
        self.yahoo_begin = yahoo_begin
        self.download_leeg = download_leeg

    def gecachte_koersen(self, tickers, start_datum):
        datums = {t: (min(r), max(r)) for t, r in self.koersen.items() if t in tickers}
        rijen = [(t, d, k) for t, r in self.koersen.items() if t in tickers for d, k in r.items() if d >= start_datum]
        return datums, {t: self.bijgewerkt[t] for t in datums}, rijen

    def download(self, tickers, vanaf, verversen):
        self.volledige_downloads.append((tuple(tickers), pd.Timestamp(vanaf)))
        if self.download_leeg:
            return pd.DataFrame(), {}
        index = pd.bdate_range(max(self.yahoo_begin, pd.Timestamp(vanaf)), EIND)
        return pd.DataFrame({t: 10.0 for t in tickers}, index=index), {t: {} for t in tickers}

    def save_koersen(self, rijen, splits):
        for t, d, k in rijen:
            self.koersen.setdefault(t, {})[d] = k
            self.bijgewerkt[t] = pd.Timestamp.now()

    def get_koers_begin(self, tickers):
        return {t: v for t, v in self.koers_begin.items() if t in tickers}

    def save_koers_begin(self, per_ticker):
        for t, vanaf in per_ticker.items():
            self.koers_begin[t] = (vanaf, pd.Timestamp.now())

    def download_incrementeel(self, ticker, vanaf):
        self.incrementele_downloads.append((ticker, pd.Timestamp(vanaf)))
        return pd.DataFrame(), {}

    def get_prices(self, start=START, verversen=False):
        with patch.object(prijzen, "db_get_gecachte_koersen", self.gecachte_koersen), \
                patch.object(prijzen, "_download_ruwe_koersen_in_eur", self.download), \
                patch.object(prijzen, "db_save_koersen", self.save_koersen), \
                patch.object(prijzen, "db_get_koers_begin", self.get_koers_begin), \
                patch.object(prijzen, "db_save_koers_begin", self.save_koers_begin), \
                patch.object(prijzen, "download_koersen_met_retry", self.download_incrementeel), \
                patch.object(prijzen, "_ruwe_koersen_in_eur", lambda *a, **k: (pd.DataFrame(), {})), \
                patch.object(prijzen, "_meld_koersen", lambda *a: None):
            return prijzen.get_prices(["JONG"], start, verversen=verversen)


class TestKoersBegin(unittest.TestCase):
    def test_tweede_aanroep_voor_jonge_ticker_geen_volledige_download(self):
        cache = _NepCache()
        cache.get_prices()
        self.assertEqual(cache.koers_begin["JONG"][0], START.date())
        prijzen_df = cache.get_prices()
        self.assertEqual(len(cache.volledige_downloads), 1)
        self.assertEqual(prijzen_df.index.min(), YAHOO_BEGIN)

    def test_eerdere_startdatum_geeft_wel_volledige_download_en_werkt_rij_bij(self):
        cache = _NepCache()
        cache.get_prices()
        eerder = pd.Timestamp("2014-01-02")
        cache.get_prices(start=eerder)
        self.assertEqual(len(cache.volledige_downloads), 2)
        self.assertEqual(cache.volledige_downloads[-1][1], eerder)
        self.assertEqual(cache.koers_begin["JONG"][0], eerder.date())

    def test_latere_startdatum_dan_gevraagd_vanaf_geen_volledige_download(self):
        cache = _NepCache()
        cache.get_prices()
        cache.get_prices(start=pd.Timestamp("2018-01-02"))
        self.assertEqual(len(cache.volledige_downloads), 1)

    def test_mislukte_of_lege_download_geeft_geen_rij(self):
        cache = _NepCache(download_leeg=True)
        cache.get_prices()
        self.assertEqual(cache.koers_begin, {})

    def test_ticker_die_op_tijd_begint_geeft_geen_rij(self):
        cache = _NepCache(yahoo_begin=START)
        cache.get_prices()
        self.assertEqual(cache.koers_begin, {})

    def test_verlopen_rij_geeft_opnieuw_volledige_download(self):
        cache = _NepCache()
        cache.get_prices()
        verlopen = pd.Timestamp.now() - prijzen.KOERS_BEGIN_GELDIGHEID - pd.Timedelta(days=1)
        cache.koers_begin["JONG"] = (START.date(), verlopen)
        cache.get_prices()
        self.assertEqual(len(cache.volledige_downloads), 2)

    def test_geldige_rij_gaat_nog_door_de_incrementele_verversing(self):
        cache = _NepCache()
        cache.get_prices()
        cache.bijgewerkt["JONG"] = pd.Timestamp.now() - pd.Timedelta(hours=1)
        cache.get_prices(verversen=True)
        self.assertEqual(len(cache.volledige_downloads), 1)
        self.assertEqual([t for t, _ in cache.incrementele_downloads], ["JONG"])

    def test_ticker_buiten_het_tijdbudget_geeft_geen_rij(self):
        cache = _NepCache()
        with patch.object(prijzen, "KOERS_TIJDBUDGET_SECONDEN", -1):
            cache.get_prices()
        self.assertEqual(cache.volledige_downloads, [])
        self.assertEqual(cache.koers_begin, {})


if __name__ == "__main__":
    unittest.main()
