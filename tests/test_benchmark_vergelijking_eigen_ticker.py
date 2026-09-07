"""
Route-level tests voor /api/portfolio/<code>/benchmark-vergelijking met het
nieuwe 'eigen_ticker'-query-param ("vergelijk ook met eigen aandeel" op het
Rendement-tabblad). De bestaande 'benchmark'-query-param blijft ongewijzigd
werken -- zie tests/test_benchmark_vergelijking.py voor de pure-functie-
tests van bereken_benchmark_vergelijking zelf, die hier niet herhaald worden.

Mockt _laad_transacties_en_resultaat en get_prices (zelfde patroon als
tests/test_ticker_koers_bereik_route.py), dus geen echte DB-rijen of
yfinance-calls nodig -- wel importeert app.py init_db() op moduleniveau,
vandaar de DATABASE_URL-skip-guard.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestBenchmarkVergelijkingEigenTicker(unittest.TestCase):
    TEST_CODE = "TESTBV"

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def _transacties_en_resultaat(self, tickers=("VWCE.AS",)):
        transacties_df = pd.DataFrame({
            "ticker": list(tickers),
            "datum": [pd.Timestamp("2023-01-01")] * len(tickers),
            "aantal": [10.0] * len(tickers),
            "koers": [0.0] * len(tickers),
            "totaal_eur": [-100.0] * len(tickers),
            "beurs": ["EAM"] * len(tickers),
            "product": list(tickers),
        })
        resultaat = pd.DataFrame(
            {"waarde": [100.0, 110.0], "geinvesteerd": [100.0, 100.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02"]),
        )
        return transacties_df, resultaat

    def test_eigen_ticker_geeft_vergelijking_terug(self):
        transacties_df, resultaat = self._transacties_en_resultaat(("VWCE.AS",))
        prices = pd.DataFrame(
            {"VWCE.AS": [10.0, 11.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02"]),
        )
        with patch.object(self.app_module, "_laad_transacties_en_resultaat", return_value=(transacties_df, resultaat)), \
             patch.object(self.app_module, "get_prices", return_value=prices):
            res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/benchmark-vergelijking?eigen_ticker=VWCE.AS")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2023-01-01", "2023-01-02"])

    def test_eigen_ticker_niet_in_portfolio_geeft_400(self):
        transacties_df, resultaat = self._transacties_en_resultaat(("VWCE.AS",))
        with patch.object(self.app_module, "_laad_transacties_en_resultaat", return_value=(transacties_df, resultaat)):
            res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/benchmark-vergelijking?eigen_ticker=NIETBESTAAND.AS")

        self.assertEqual(res.status_code, 400)

    def test_bestaande_benchmark_param_blijft_werken(self):
        transacties_df, resultaat = self._transacties_en_resultaat(("VWCE.AS",))
        prices = pd.DataFrame(
            {"URTH": [10.0, 11.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02"]),
        )
        with patch.object(self.app_module, "_laad_transacties_en_resultaat", return_value=(transacties_df, resultaat)), \
             patch.object(self.app_module, "get_prices", return_value=prices), \
             patch.object(self.app_module, "BENCHMARK_TICKERS", {"S&P 500": "URTH"}):
            res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/benchmark-vergelijking?benchmark=S%26P%20500")

        self.assertEqual(res.status_code, 200)

    def test_onbekende_code_geeft_404(self):
        with patch.object(self.app_module, "_laad_transacties_en_resultaat", return_value=(None, None)):
            res = self.client.get("/api/portfolio/ZZZ/benchmark-vergelijking?eigen_ticker=X")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
