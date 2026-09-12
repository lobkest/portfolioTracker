"""
Unit tests voor de per-request memoization van _fx_prijzen_serie() (zie
CLAUDE.md, performance-meting: FX-prijzenreeks memoizen per request +
geheugenmeting).

Achtergrond: ticker_waarschuwingen_voor_transacties() draait sinds kort bij
ELK bezoek aan een opgeslagen portfolio en roept per unieke ticker
uiteindelijk _fx_prijzen_serie(valuta) aan. Die functie deed voorheen bij
ELKE aanroep een volledige get_prices([fx_pair], FX_ANKER_DATUM) -- dus bij
bv. 8 USD-tickers in één portfolio-load 8x dezelfde DB-query + pivot/ffill
voor precies dezelfde (fx_pair, ankerdatum). _fx_prijzen_serie() memoized
het resultaat nu per fx_pair op Flask's `g`-object, zodat dit binnen één
requestcontext maar één keer gebeurt -- maar NIET tussen requests heen (dat
blijft de 2-minuten-staleness-check in get_prices() bewaken).

Draait geheel offline: get_prices() wordt gemockt, geen echte database- of
Yahoo-calls. Gebruikt een kale Flask-app (niet app.py, want die roept
init_db() aan bij import) enkel om app-/requestcontexten te kunnen pushen.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis


def _fx_prijzen_frame(fx_pair, waarde=0.9):
    return pd.DataFrame({fx_pair: [waarde]}, index=[pd.Timestamp("2024-03-01")])


class TestFxSerieMemoizationBinnenRequest(unittest.TestCase):
    """Twee aanroepen voor dezelfde valuta binnen dezelfde requestcontext
    mogen maar 1x get_prices() aanroepen."""

    def setUp(self):
        self.app = Flask(__name__)

    @patch("analysis.get_prices")
    def test_tweede_aanroep_zelfde_valuta_binnen_request_geen_nieuwe_get_prices(self, mock_get_prices):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        with self.app.test_request_context():
            eerste = analysis._fx_prijzen_serie("USD")
            tweede = analysis._fx_prijzen_serie("USD")

        self.assertEqual(mock_get_prices.call_count, 1)
        pd.testing.assert_series_equal(eerste, tweede)

    @patch("analysis.get_prices")
    def test_verschillende_valuta_binnen_request_wel_eigen_get_prices_aanroep(self, mock_get_prices):
        def fake_get_prices(tickers, start_date, verversen=True):
            return _fx_prijzen_frame(tickers[0])

        mock_get_prices.side_effect = fake_get_prices

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD")
            analysis._fx_prijzen_serie("GBP")

        self.assertEqual(mock_get_prices.call_count, 2)
        aangevraagde_pairs = [c.args[0][0] for c in mock_get_prices.call_args_list]
        self.assertEqual(aangevraagde_pairs, ["USDEUR=X", "GBPEUR=X"])


class TestFxSerieMemoizationVerversenBewust(unittest.TestCase):
    """Opdracht 'FX-koers in prijscheck-stap niet onnodig verversen': de
    memo op `g` moet ONDERSCHEID maken tussen een resultaat dat met
    verversen=False is opgehaald (nooit geprobeerd te verversen) en een
    resultaat met verversen=True (wél geprobeerd) -- anders zou bv. tijdens
    /upload de (verversen=False) prijscontrole van een net-opgeloste ticker
    de latere (verversen=True) aandelenkoers-conversie in dezelfde request
    stilzwijgend blokkeren."""

    def setUp(self):
        self.app = Flask(__name__)

    @patch("analysis.get_prices")
    def test_na_verversen_false_triggert_een_latere_verversen_true_aanroep_alsnog_get_prices(
        self, mock_get_prices
    ):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD", verversen=False)
            analysis._fx_prijzen_serie("USD", verversen=True)

        self.assertEqual(mock_get_prices.call_count, 2)
        self.assertEqual(mock_get_prices.call_args_list[0].kwargs.get("verversen"), False)
        self.assertEqual(mock_get_prices.call_args_list[1].kwargs.get("verversen"), True)

    @patch("analysis.get_prices")
    def test_na_verversen_true_hergebruikt_een_latere_verversen_false_aanroep_de_cache(
        self, mock_get_prices
    ):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD", verversen=True)
            analysis._fx_prijzen_serie("USD", verversen=False)

        self.assertEqual(mock_get_prices.call_count, 1)

    @patch("analysis.get_prices")
    def test_twee_verversen_false_aanroepen_hergebruiken_elkaars_cache(self, mock_get_prices):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD", verversen=False)
            analysis._fx_prijzen_serie("USD", verversen=False)

        self.assertEqual(mock_get_prices.call_count, 1)


class TestFxSerieMemoizationTussenRequests(unittest.TestCase):
    """Geen lek tussen requests: een nieuwe requestcontext (nieuwe `g`)
    triggert weer een nieuwe get_prices()-aanroep."""

    def setUp(self):
        self.app = Flask(__name__)

    @patch("analysis.get_prices")
    def test_nieuwe_requestcontext_triggert_nieuwe_get_prices_aanroep(self, mock_get_prices):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD")
            analysis._fx_prijzen_serie("USD")

        with self.app.test_request_context():
            analysis._fx_prijzen_serie("USD")

        self.assertEqual(mock_get_prices.call_count, 2)


class TestFxSerieMemoizationBuitenRequestContext(unittest.TestCase):
    """Buiten een Flask-requestcontext (bv. losse scripts, of bestaande
    tests zoals test_fx_caching_en_retry.py die _fx_koers_op_datum zonder
    app-context aanroepen) blijft het oude gedrag intact: gewoon geen
    memoization, geen crash door een ontbrekende applicatiecontext."""

    @patch("analysis.get_prices")
    def test_werkt_zonder_crash_zonder_app_context(self, mock_get_prices):
        mock_get_prices.return_value = _fx_prijzen_frame("USDEUR=X")

        eerste = analysis._fx_prijzen_serie("USD")
        tweede = analysis._fx_prijzen_serie("USD")

        self.assertEqual(mock_get_prices.call_count, 2)
        pd.testing.assert_series_equal(eerste, tweede)


if __name__ == "__main__":
    unittest.main()
