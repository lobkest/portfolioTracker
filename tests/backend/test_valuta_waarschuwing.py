"""
Unit tests voor _haal_valuta_op() in prijzen.py: een onbekende valuta of een
mislukte Yahoo-opvraging mag niet stil als EUR behandeld worden, maar geeft een
Diagnostiek-melding (de koers blijft wel ongewijzigd, aanname EUR).

Draait geheel offline: yf.Ticker wordt gemockt.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import diagnostiek
import prijzen


def _fake_ticker(info):
    ticker = MagicMock()
    ticker.info = info
    return ticker


class TestHaalValutaOpDiagnostiek(unittest.TestCase):
    """LET_OP, categorie Wisselkoersen, sleutel = ticker."""

    def setUp(self):
        self.app = Flask(__name__)

    def _roep_aan_in_request(self, t):
        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            valuta = prijzen._haal_valuta_op(t)
            meldingen = diagnostiek.haal_meldingen()
        return valuta, meldingen

    def _assert_een_let_op(self, meldingen, ticker, tekst_deel):
        self.assertEqual(len(meldingen), 1)
        m = meldingen[0]
        self.assertEqual(m["categorie"], diagnostiek.CATEGORIE_WISSELKOERSEN)
        self.assertEqual(m["niveau"], diagnostiek.LET_OP)
        self.assertEqual(m["sleutel"], ticker)
        self.assertIn(tekst_deel, m["tekst"])

    @patch("prijzen.yf.Ticker")
    def test_mislukte_opvraging_geeft_let_op(self, mock_ticker):
        mock_ticker.side_effect = RuntimeError("rate limit")
        valuta, meldingen = self._roep_aan_in_request("TTWO")
        self.assertEqual(valuta, "EUR")
        self._assert_een_let_op(meldingen, "TTWO", "Valuta van 'TTWO' niet op te halen bij Yahoo")

    @patch("prijzen.yf.Ticker")
    def test_geen_valuta_geeft_let_op(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({})
        valuta, meldingen = self._roep_aan_in_request("XYZ")
        self.assertEqual(valuta, "EUR")
        self._assert_een_let_op(meldingen, "XYZ", "Yahoo geeft geen valuta voor 'XYZ'")

    @patch("prijzen.yf.Ticker")
    def test_chf_geeft_let_op(self, mock_ticker):
        mock_ticker.return_value = _fake_ticker({"currency": "CHF"})
        valuta, meldingen = self._roep_aan_in_request("NESN.SW")
        self.assertEqual(valuta, "CHF")
        self._assert_een_let_op(meldingen, "NESN.SW", "Valuta CHF van 'NESN.SW' wordt niet ondersteund")

    @patch("prijzen.yf.Ticker")
    def test_usd_en_eur_geven_geen_melding(self, mock_ticker):
        for valuta_yahoo, ticker in (("USD", "TTWO"), ("EUR", "ASML.AS")):
            mock_ticker.return_value = _fake_ticker({"currency": valuta_yahoo})
            valuta, meldingen = self._roep_aan_in_request(ticker)
            self.assertEqual(valuta, valuta_yahoo)
            self.assertEqual(meldingen, [])


class TestConverteerNaarEurOngewijzigd(unittest.TestCase):
    """Regressie: de meldingen veranderen niets aan de omrekening in
    _converteer_naar_eur()."""

    def setUp(self):
        self.app = Flask(__name__)
        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        self.raw = pd.DataFrame({"T": [10.0, 20.0]}, index=index)
        self.fx = pd.Series([0.9, 0.8], index=index)

    def _converteer(self):
        # Lege valuta-cache: de valuta komt dan via yf.Ticker(...).info, zoals deze tests mocken.
        with self.app.test_request_context(), redirect_stdout(io.StringIO()),                 patch("prijzen.db_get_ticker_details", return_value={}):
            prijzen._converteer_naar_eur(self.raw, ["T"])
            return diagnostiek.haal_meldingen()

    @patch("prijzen._fx_prijzen_serie")
    @patch("prijzen.yf.Ticker")
    def test_mislukte_opvraging_laat_koers_ongewijzigd(self, mock_ticker, mock_fx):
        mock_ticker.side_effect = RuntimeError("rate limit")
        meldingen = self._converteer()
        self.assertEqual(self.raw["T"].tolist(), [10.0, 20.0])
        self.assertEqual([m["niveau"] for m in meldingen], [diagnostiek.LET_OP])
        mock_fx.assert_not_called()

    @patch("prijzen._fx_prijzen_serie")
    @patch("prijzen.yf.Ticker")
    def test_chf_laat_koers_ongewijzigd(self, mock_ticker, mock_fx):
        mock_ticker.return_value = _fake_ticker({"currency": "CHF"})
        meldingen = self._converteer()
        self.assertEqual(self.raw["T"].tolist(), [10.0, 20.0])
        self.assertEqual([m["niveau"] for m in meldingen], [diagnostiek.LET_OP])
        mock_fx.assert_not_called()

    @patch("prijzen._fx_prijzen_serie")
    @patch("prijzen.yf.Ticker")
    def test_usd_wordt_omgerekend_zoals_voorheen(self, mock_ticker, mock_fx):
        mock_ticker.return_value = _fake_ticker({"currency": "USD"})
        mock_fx.return_value = self.fx
        meldingen = self._converteer()
        # 10 x 0,9 = 9,0 en 20 x 0,8 = 16,0
        self.assertEqual(self.raw["T"].round(6).tolist(), [9.0, 16.0])
        self.assertEqual(meldingen, [])


if __name__ == "__main__":
    unittest.main()
