"""
Unit tests voor _haal_valuta_op() in prijzen.py: een onbekende valuta of een
mislukte Yahoo-opvraging mag niet meer stil als EUR behandeld worden, maar
moet een WARN-print geven (de koers blijft wel ongewijzigd, aanname EUR).

Draait geheel offline: yf.Ticker wordt gemockt.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prijzen


def _fake_ticker(info):
    ticker = MagicMock()
    ticker.info = info
    return ticker


class TestHaalValutaOp(unittest.TestCase):

    def _roep_aan(self, t="TEST"):
        uitvoer = io.StringIO()
        with redirect_stdout(uitvoer):
            valuta = prijzen._haal_valuta_op(t)
        return valuta, uitvoer.getvalue()

    @patch("prijzen.yf.Ticker")
    def test_usd_geeft_usd_zonder_waarschuwing(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({"currency": "USD"})
        valuta, uitvoer = self._roep_aan("TTWO")
        self.assertEqual(valuta, "USD")
        self.assertNotIn("WARN", uitvoer)

    @patch("prijzen.yf.Ticker")
    def test_eur_geeft_eur_zonder_waarschuwing(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({"currency": "EUR"})
        valuta, uitvoer = self._roep_aan("ASML.AS")
        self.assertEqual(valuta, "EUR")
        self.assertNotIn("WARN", uitvoer)

    @patch("prijzen.yf.Ticker")
    def test_mislukte_opvraging_geeft_eur_met_waarschuwing(self, mock_ticker):
        mock_ticker.side_effect = RuntimeError("rate limit")
        valuta, uitvoer = self._roep_aan("TTWO")
        self.assertEqual(valuta, "EUR")
        self.assertIn("WARN TTWO", uitvoer)
        self.assertIn("mislukt", uitvoer)

    @patch("prijzen.yf.Ticker")
    def test_geen_valuta_in_info_geeft_eur_met_waarschuwing(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({})
        valuta, uitvoer = self._roep_aan("XYZ")
        self.assertEqual(valuta, "EUR")
        self.assertIn("WARN XYZ", uitvoer)
        self.assertIn("geen valuta", uitvoer)

    @patch("prijzen.yf.Ticker")
    def test_niet_ondersteunde_valuta_geeft_waarschuwing(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({"currency": "CHF"})
        valuta, uitvoer = self._roep_aan("NESN.SW")
        self.assertEqual(valuta, "CHF")
        self.assertIn("WARN NESN.SW", uitvoer)
        self.assertIn("'CHF'", uitvoer)


if __name__ == "__main__":
    unittest.main()
