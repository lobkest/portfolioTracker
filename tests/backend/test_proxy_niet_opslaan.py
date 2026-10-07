"""
Unit tests: bij "niet opslaan" (analyze_transacties) wordt de land-proxy overgeslagen, in de opgeslagen flow
(analyze_transacties_verrijking via /verrijking) niet.

Draait geheel offline: importeert app niet (geen db_init()), alle Yahoo- en databasefuncties zijn gemockt.
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie as po
import portfolio_verdeling


def _transacties_df():
    return pd.DataFrame({
        "datum": [pd.Timestamp("2024-01-01")],
        "product": ["All-World"],
        "isin": ["IE00BK5BQT80"],
        "ticker": ["VWCE.DE"],
        "aantal": [10.0],
    })


def _price_data():
    return pd.DataFrame({"VWCE.DE": [100.0]}, index=[pd.Timestamp("2024-01-02")])


# Alleen een Yahoo-top-10: 20% gedekt, dus 80% onbekend land (boven de drempel).
TOP10 = [{"holding_naam": "Apple", "holding_ticker": "AAPL", "gewicht": 0.2, "land": "United States",
          "bron": "yfinance_top10"}]

PROXY = {"VWCE.DE": {"proxy_isin": "IE00B6R52259", "proxy_naam": "iShares MSCI ACWI", "max_afwijking_pp": 0.47,
                     "proxy_land": {"United States": 0.7, "Japan": 0.3}, "vergelijking": {"rijen": []}}}


class TestProxyAlleenInOpgeslagenFlow(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for doel, naam, waarde in [
            (po, "classify_tickers", {"VWCE.DE": True}),
            (po, "_verwarm_land_sector_cache_parallel", None),
            (po, "compute_valuta_verdeling", {"valuta": {}}),
            (po, "compute_beurs_verdeling", {"beurs": {}}),
            (po, "bereken_bedrijven_verdeling", {"top": []}),
            (po, "bereken_etf_overlap", {}),
            (po, "get_etf_holdings_uit_cache", []),
            (po, "get_prices", _price_data()),
            (portfolio_verdeling, "get_etf_holdings", TOP10),
            (portfolio_verdeling, "get_etf_sector_verdeling", {}),
        ]:
            stack.enter_context(patch.object(doel, naam, return_value=waarde))
        stack.enter_context(patch.object(po, "compute_split_adjusted_shares", side_effect=lambda df: df))
        self.mock_proxy = stack.enter_context(patch.object(po, "land_proxies_voor_etfs", return_value=PROXY))

    def test_niet_opslaan_roept_proxy_niet_aan(self):
        with patch.object(po, "analyze_transacties_kern", return_value={"chart_data": {"labels": []}}):
            resultaat = po.analyze_transacties(_transacties_df(), code=None, naam="Test")

        self.mock_proxy.assert_not_called()
        info = resultaat["land_sector_verdeling"]["per_etf"]["VWCE.DE"]
        self.assertEqual(info["land_bron"], "yfinance_top10")
        self.assertIsNone(info["land_proxy"])
        self.assertAlmostEqual(resultaat["land_sector_verdeling"]["land"]["Unknown"], 800.0)

    def test_opgeslagen_flow_roept_proxy_wel_aan(self):
        resultaat = po.analyze_transacties_verrijking(_transacties_df(), "ZZTESTPROXY", prijs_data_al_klaar=_price_data())

        self.mock_proxy.assert_called_once_with({"VWCE.DE": "IE00BK5BQT80"})
        self.assertEqual(resultaat["land_sector_verdeling"]["per_etf"]["VWCE.DE"]["land_bron"], "proxy")


if __name__ == "__main__":
    unittest.main()
