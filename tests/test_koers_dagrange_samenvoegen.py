"""
Unit tests voor het samenvoegen van de slotkoers- en dagrange-download tot
één yf.download()-call (zie CLAUDE.md/opdracht_slotkoers_dagrange_
samenvoegen.md).

Achtergrond: vergelijk_prijs_op_datum() deed voorheen, in het pad waar de
prijs nog niet gecached is, twee losse yf.download()-aanroepen voor exact
dezelfde ticker + periode -- één voor de slotkoers (_haal_slotkoers_op),
één voor de dagrange (_haal_dagrange_op). _haal_koers_en_dagrange_op() doet
dit nu in één download. _haal_slotkoers_op()/_haal_dagrange_op() zelf
blijven ongewijzigd bestaan voor het FX-pad resp. de ticker_prijscheck-
cache-backfill, die er maar één van nodig hebben.

Draait geheel offline: analysis.yf.download wordt gemockt, geen echte
netwerkcalls.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import _haal_koers_en_dagrange_op, vergelijk_prijs_op_datum

# vergelijk_prijs_op_datum() roept bij een niet-EUR valuta ook
# _fx_koers_op_datum() aan -- niet relevant voor deze tests (alles hier is
# EUR), maar _ticker_details_met_cache() wordt wel altijd aangeroepen.


def _fake_ohlc_dataframe(datum, close, high, low):
    index = pd.date_range(start=datum, periods=3, freq="D")
    return pd.DataFrame(
        {"Open": [close] * 3, "High": [high] * 3, "Low": [low] * 3,
         "Close": [close] * 3, "Volume": [1000] * 3},
        index=index,
    )


class TestHaalKoersEnDagrangeOp(unittest.TestCase):
    """_haal_koers_en_dagrange_op() zelf: één gemockte yf.download() levert
    zowel de slotkoers als de dagrange, geen tweede downloadaanroep."""

    def test_één_download_levert_slotkoers_en_dagrange(self):
        fake_df = _fake_ohlc_dataframe(date(2024, 1, 1), close=100.0, high=105.0, low=95.0)
        with patch.object(analysis.yf, "download", return_value=fake_df) as mock_download:
            slotkoers, high, low = _haal_koers_en_dagrange_op("AAPL", date(2024, 1, 1))

        self.assertEqual(mock_download.call_count, 1)
        self.assertEqual(slotkoers, 100.0)
        self.assertEqual(high, 105.0)
        self.assertEqual(low, 95.0)

    def test_download_faalt_geeft_none_none_none(self):
        with patch.object(analysis.yf, "download", side_effect=Exception("netwerkfout")) as mock_download:
            resultaat = _haal_koers_en_dagrange_op("AAPL", date(2024, 1, 1), pogingen=1)

        self.assertEqual(mock_download.call_count, 1)
        self.assertEqual(resultaat, (None, None, None))

    def test_geen_koersdata_in_periode_geeft_none_none_none(self):
        lege_df = pd.DataFrame({"Open": [], "High": [], "Low": [], "Close": [], "Volume": []})
        with patch.object(analysis.yf, "download", return_value=lege_df):
            resultaat = _haal_koers_en_dagrange_op("AAPL", date(2024, 1, 1))

        self.assertEqual(resultaat, (None, None, None))


class TestVergelijkPrijsOpDatumÉénDownload(unittest.TestCase):
    """vergelijk_prijs_op_datum() moet in het cache-miss-pad nog maar 1
    yf.download()-aanroep per ticker/periode doen, in plaats van de 2
    (slotkoers + dagrange apart) van vóór deze wijziging."""

    def setUp(self):
        patcher1 = patch.object(analysis, "get_cached_prijscheck", return_value=None)
        patcher1.start()
        self.addCleanup(patcher1.stop)
        patcher2 = patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"})
        patcher2.start()
        self.addCleanup(patcher2.stop)
        patcher3 = patch.object(analysis, "save_prijscheck")
        patcher3.start()
        self.addCleanup(patcher3.stop)
        patcher4 = patch.object(analysis, "_haal_splits_op", return_value={})
        patcher4.start()
        self.addCleanup(patcher4.stop)

    def test_vergelijk_prijs_op_datum_doet_maar_1_download(self):
        fake_df = _fake_ohlc_dataframe(date(2024, 1, 1), close=100.0, high=105.0, low=95.0)
        with patch.object(analysis.yf, "download", return_value=fake_df) as mock_download:
            resultaat = vergelijk_prijs_op_datum("AAPL", date(2024, 1, 1), 100.0)

        self.assertEqual(mock_download.call_count, 1)
        self.assertEqual(resultaat["yahoo_koers"], 100.0)
        self.assertEqual(resultaat["high"], 105.0)
        self.assertEqual(resultaat["low"], 95.0)


class TestFaalpadGelijkAanVoorSamenvoegen(unittest.TestCase):
    """Een mislukte download moet zich hetzelfde gedragen als vóór de
    samenvoeging bij een mislukte slotkoers-download: geen yahoo_koers,
    geen dagrange, geen vergelijking mogelijk."""

    def setUp(self):
        patcher1 = patch.object(analysis, "get_cached_prijscheck", return_value=None)
        patcher1.start()
        self.addCleanup(patcher1.stop)
        patcher2 = patch.object(analysis, "_ticker_details_met_cache", return_value={"valuta": "EUR"})
        patcher2.start()
        self.addCleanup(patcher2.stop)
        patcher3 = patch.object(analysis, "save_prijscheck")
        patcher3.start()
        self.addCleanup(patcher3.stop)

    def test_mislukte_download_geeft_zelfde_leeg_resultaat_als_voorheen(self):
        with patch.object(analysis, "_haal_koers_en_dagrange_op", return_value=(None, None, None)):
            resultaat = vergelijk_prijs_op_datum("AAPL", date(2024, 1, 1), 100.0)

        # Zelfde vorm als de bestaande early-return in vergelijk_prijs_op_datum
        # voor "yahoo_koers is None" (zie ook TestBinnenDagrange in
        # tests/test_dagrange_prijscheck.py voor het analoge high/low-None-geval).
        self.assertIsNone(resultaat["yahoo_koers"])
        self.assertIsNone(resultaat["yahoo_koers_gecorrigeerd"])
        self.assertIsNone(resultaat["afwijking_pct"])
        self.assertIsNone(resultaat["niveau"])
        self.assertIsNone(resultaat["match"])
        self.assertIsNone(resultaat["high"])
        self.assertIsNone(resultaat["low"])
        self.assertIsNone(resultaat["binnen_dagrange"])


if __name__ == "__main__":
    unittest.main()
