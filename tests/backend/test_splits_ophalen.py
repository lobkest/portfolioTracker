import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import ticker_prijscheck
import ticker_zekerheid
from ticker_prijscheck import _haal_splits_op
from ticker_zekerheid import vind_tickers_met_snelle_prijscheck_parallel


def _yf_ticker_met_splits(splits):
    nep = MagicMock()
    nep.splits = splits
    return MagicMock(return_value=nep)


class TestHaalSplitsOp(unittest.TestCase):
    def _roep_aan(self, splits):
        with patch.object(ticker_prijscheck, "db_get_cached_splits", return_value=None), \
             patch.object(ticker_prijscheck, "db_save_splits") as mock_save, \
             patch.object(ticker_prijscheck, "_tel_yahoo_call"), \
             patch.object(ticker_prijscheck.yf, "Ticker", _yf_ticker_met_splits(splits)):
            return _haal_splits_op("STAR-USD.AS"), mock_save

    def test_none_van_yahoo_geeft_leeg_en_cachet_niet(self):
        resultaat, mock_save = self._roep_aan(None)
        self.assertEqual(resultaat, {})
        mock_save.assert_not_called()

    def test_onverwacht_formaat_geeft_leeg_en_cachet_niet(self):
        resultaat, mock_save = self._roep_aan("geen series")
        self.assertEqual(resultaat, {})
        mock_save.assert_not_called()

    def test_normale_series_wordt_omgezet_en_gecachet(self):
        splits = pd.Series([4.0, 2.0], index=pd.to_datetime(["2020-08-31", "2022-07-18"]))
        resultaat, mock_save = self._roep_aan(splits)
        verwacht = {"2020-08-31": 4.0, "2022-07-18": 2.0}
        self.assertEqual(resultaat, verwacht)
        mock_save.assert_called_once_with("STAR-USD.AS", verwacht)


class TestVindTickersParallelMetFout(unittest.TestCase):
    def test_een_positie_gooit_rest_loopt_door(self):
        posities = [
            ("FONDS A", "ISINA", "EAM", []),
            ("STAR", "ISINB", "EAM", []),
            ("FONDS C", "ISINC", "EAM", []),
        ]

        def fake_find(product, isin, beurs, transacties, bekende_ticker=None):
            if isin == "ISINB":
                raise AttributeError("'NoneType' object has no attribute 'items'")
            return {"ticker": f"T-{isin}", "zekerheid": "zeker", "alternatieven": [],
                    "prijs_checks": [], "prijswaarschuwing": None}

        with patch.object(ticker_zekerheid, "find_ticker_met_snelle_prijscheck", side_effect=fake_find):
            resultaten = vind_tickers_met_snelle_prijscheck_parallel(posities)

        self.assertEqual(resultaten[0]["ticker"], "T-ISINA")
        self.assertEqual(resultaten[2]["ticker"], "T-ISINC")
        self.assertIsNone(resultaten[1]["ticker"])
        self.assertEqual(resultaten[1]["zekerheid"], "geen_match")
        self.assertEqual(resultaten[1]["prijs_checks"], [])
        self.assertIsNone(resultaten[1]["prijswaarschuwing"])


if __name__ == "__main__":
    unittest.main()
