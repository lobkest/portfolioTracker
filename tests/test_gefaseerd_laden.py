"""
Unit tests voor de gefaseerd-laden-refactor in app.py: analyze_transacties()
is gesplitst in analyze_transacties_kern() (Home/Rendement/Per-aandeel/
Statistieken -- alles wat de /upload- en /api/portfolio/<code>-routes nu
standaard teruggeven) en analyze_transacties_verrijking() (Verdeling/Land/
Sector/Bedrijven/ETF-overlap -- lui opgevraagd via /api/portfolio/<code>/
verrijking). Zie CLAUDE.md / opdracht_gefaseerd_laden.md.

Raakt de echte database aan via 'import app' (init_db() draait bij import,
zie CLAUDE.md) -- daarom, net als tests/test_upload_route_foutafhandeling.py,
overgeslagen zonder DATABASE_URL. classify_tickers/land-sector/ETF-holdings-
functies worden gemockt zodat er verder geen yfinance-calls plaatsvinden.
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

VERRIJKINGSVELDEN = {"verdeling", "land_sector_verdeling", "bedrijven_verdeling", "etf_overlap"}


def _transacties_df():
    return pd.DataFrame({
        "datum": [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-01")],
        "product": ["ETF_A", "AAPL"],
        "isin": ["IE000000001", "US0378331005"],
        "beurs": ["EAM", "NASDAQ"],
        "ticker": ["ETF_A", "AAPL"],
        "aantal": [1.0, 1.0],
        "koers": [100.0, 100.0],
        "totaal_eur": [-100.0, -100.0],
        "echte_naam": ["ETF_A", "AAPL"],
        "transactiekosten": [float("nan"), float("nan")],
        "tijd": [None, None],
    })


def _price_data():
    return pd.DataFrame(
        {"ETF_A": [100.0, 105.0], "AAPL": [100.0, 110.0]},
        index=[pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02")],
    )


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestGefaseerdLaden(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self._patchers = [
            patch.object(app_module, "get_prices", return_value=_price_data()),
            patch.object(app_module, "ticker_waarschuwingen_voor_transacties", return_value=[]),
            patch.object(app_module, "classify_tickers", return_value={"ETF_A": True, "AAPL": False}),
            patch.object(app_module, "_verwarm_land_sector_cache_parallel", return_value=None),
            patch.object(app_module, "compute_land_sector_verdeling", return_value={"land": {}, "sector": {}}),
            patch.object(app_module, "bereken_bedrijven_verdeling", return_value={"top": []}),
            patch.object(app_module, "bereken_etf_overlap", return_value={}),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_kern_bevat_geen_verrijkingsvelden(self):
        resultaat = self.app_module.analyze_transacties_kern(_transacties_df(), code=None, naam="Test")

        self.assertIsNotNone(resultaat["chart_data"])
        self.assertIn("per_ticker", resultaat)
        self.assertIn("statistieken", resultaat)
        for veld in VERRIJKINGSVELDEN:
            self.assertNotIn(veld, resultaat)

    def test_verrijking_bevat_alleen_verrijkingsvelden(self):
        resultaat = self.app_module.analyze_transacties_verrijking(_transacties_df(), code=None)

        self.assertEqual(set(resultaat.keys()), VERRIJKINGSVELDEN)

    def test_analyze_transacties_wrapper_is_gelijk_aan_kern_plus_verrijking(self):
        kern = self.app_module.analyze_transacties_kern(_transacties_df(), code=None, naam="Test")
        verrijking = self.app_module.analyze_transacties_verrijking(_transacties_df(), code=None)
        verwacht = {**kern, **verrijking}

        wrapper_resultaat = self.app_module.analyze_transacties(_transacties_df(), code=None, naam="Test")

        self.assertEqual(wrapper_resultaat, verwacht)


if __name__ == "__main__":
    unittest.main()
