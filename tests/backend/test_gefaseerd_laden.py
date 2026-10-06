"""
Unit tests voor de gefaseerd-laden-refactor in app.py: analyze_transacties()
is gesplitst in analyze_transacties_kern() (Home/Rendement/Per-aandeel/
Statistieken -- alles wat de /upload- en /api/portfolio/<code>-routes nu
standaard teruggeven) en analyze_transacties_verrijking() (Verdeling/Land/
Sector/Bedrijven/ETF-overlap -- lui opgevraagd via /api/portfolio/<code>/
verrijking). Zie CLAUDE.md: Flows.

Raakt de echte database aan via 'import app' (db_init() draait bij import,
zie CLAUDE.md: Tech stack en omgeving) -- daarom, net als tests/test_upload_route_foutafhandeling.py,
overgeslagen zonder DATABASE_URL. classify_tickers/land-sector/ETF-holdings-
functies worden gemockt zodat er verder geen yfinance-calls plaatsvinden.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database

VERRIJKINGSVELDEN = {
    "verdeling", "verdeling_samenvatting", "land_sector_verdeling", "valuta_verdeling",
    "bedrijven_verdeling", "etf_overlap",
}


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


@vereist_database
class TestGefaseerdLaden(unittest.TestCase):
    def setUp(self):
        import app as app_module  # noqa: F401
        import portfolio_orchestratie
        self.portfolio_orchestratie = portfolio_orchestratie
        self._patchers = [
            patch.object(portfolio_orchestratie, "get_prices", return_value=_price_data()),
            patch.object(portfolio_orchestratie, "ticker_waarschuwingen_voor_transacties", return_value=([], {})),
            patch.object(portfolio_orchestratie, "classify_tickers", return_value={"ETF_A": True, "AAPL": False}),
            patch.object(portfolio_orchestratie, "_verwarm_land_sector_cache_parallel", return_value=None),
            patch.object(portfolio_orchestratie, "land_proxies_voor_etfs", return_value={}),
            patch.object(portfolio_orchestratie, "compute_land_sector_verdeling", return_value={"land": {}, "sector": {}}),
            patch.object(portfolio_orchestratie, "compute_valuta_verdeling", return_value={"valuta": {}}),
            patch.object(portfolio_orchestratie, "bereken_bedrijven_verdeling", return_value={"top": []}),
            patch.object(portfolio_orchestratie, "bereken_etf_overlap", return_value={}),
        ]
        for p in self._patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_kern_bevat_geen_verrijkingsvelden(self):
        resultaat = self.portfolio_orchestratie.analyze_transacties_kern(_transacties_df(), code=None, naam="Test")

        self.assertIsNotNone(resultaat["chart_data"])
        self.assertIn("per_ticker", resultaat)
        self.assertIn("statistieken", resultaat)
        for veld in VERRIJKINGSVELDEN:
            self.assertNotIn(veld, resultaat)

    def test_verrijking_bevat_alleen_verrijkingsvelden(self):
        resultaat = self.portfolio_orchestratie.analyze_transacties_verrijking(_transacties_df(), code=None)

        self.assertEqual(set(resultaat.keys()), VERRIJKINGSVELDEN)

    def test_verrijking_vraagt_bedrijven_tot_het_maximum_op(self):
        # De frontend kiest zelf N (10/20/50/eigen aantal) en knipt in; de
        # backend moet daarvoor tot BEDRIJVEN_TOP_N_MAX meeleveren.
        from portfolio_verdeling import BEDRIJVEN_TOP_N_MAX
        with patch.object(
            self.portfolio_orchestratie, "bereken_bedrijven_verdeling", return_value={"top": []}
        ) as mock_bedrijven:
            self.portfolio_orchestratie.analyze_transacties_verrijking(_transacties_df(), code=None)

        self.assertEqual(mock_bedrijven.call_args.kwargs["top_n"], BEDRIJVEN_TOP_N_MAX)

    def test_analyze_transacties_wrapper_is_gelijk_aan_kern_plus_verrijking(self):
        kern = self.portfolio_orchestratie.analyze_transacties_kern(_transacties_df(), code=None, naam="Test")
        verrijking = self.portfolio_orchestratie.analyze_transacties_verrijking(_transacties_df(), code=None)
        verwacht = {**kern, **verrijking}

        wrapper_resultaat = self.portfolio_orchestratie.analyze_transacties(_transacties_df(), code=None, naam="Test")

        self.assertEqual(wrapper_resultaat, verwacht)


if __name__ == "__main__":
    unittest.main()
