"""Valuta voor de EUR-omrekening uit de ticker_info-cache; alleen bij een gemiste cache een .info-call naar Yahoo."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import prijzen


class TestValutaPerTicker(unittest.TestCase):
    def _valuta(self, tickers, cache):
        with patch("prijzen.db_get_ticker_details", return_value=cache) as mock_cache, \
                patch("prijzen._haal_valuta_op", side_effect=lambda t: "USD") as mock_info, \
                redirect_stdout(io.StringIO()):
            return prijzen._valuta_per_ticker(tickers), mock_cache, mock_info

    def test_cache_treffer_doet_geen_yahoo_call(self):
        valuta, _, mock_info = self._valuta(["ASML.AS", "AAPL"], {"ASML.AS": {"valuta": "EUR"}, "AAPL": {"valuta": "USD"}})
        self.assertEqual(valuta, {"ASML.AS": "EUR", "AAPL": "USD"})
        mock_info.assert_not_called()

    def test_gemiste_of_lege_cache_valt_terug_op_info(self):
        valuta, _, mock_info = self._valuta(["ASML.AS", "NIEUW", "LEEG"], {"ASML.AS": {"valuta": "EUR"}, "LEEG": {"valuta": None}})
        self.assertEqual(valuta, {"ASML.AS": "EUR", "NIEUW": "USD", "LEEG": "USD"})
        self.assertEqual(sorted(c.args[0] for c in mock_info.call_args_list), ["LEEG", "NIEUW"])

    def test_fx_paar_zonder_opzoeking(self):
        valuta, mock_cache, mock_info = self._valuta(["USDEUR=X"], {})
        self.assertEqual(valuta, {"USDEUR=X": "EUR"})
        mock_cache.assert_not_called()
        mock_info.assert_not_called()

    def test_onleesbare_cache_valt_terug_op_info(self):
        with patch("prijzen.db_get_ticker_details", side_effect=RuntimeError("geen database")), \
                patch("prijzen._haal_valuta_op", return_value="GBp") as mock_info, redirect_stdout(io.StringIO()):
            self.assertEqual(prijzen._valuta_per_ticker(["X.L"]), {"X.L": "GBp"})
        mock_info.assert_called_once_with("X.L")


if __name__ == "__main__":
    unittest.main()
