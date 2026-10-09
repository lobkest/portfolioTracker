import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import etf_proxy
import ticker_classificatie
from ticker_classificatie import get_etf_holdings

IUSA_ISIN = "IE0031442068"
FONDSEN = [
    {"isin": IUSA_ISIN, "naam": "iShares Core S&P 500 UCITS ETF", "product_url": "/nl/producten/251900/iusa"},
    {"isin": "IE00B4L5Y983", "naam": "iShares Core MSCI World", "product_url": "/nl/producten/251882/iwda"},
]
PRODUCTPAGINA_HOLDINGS = [
    {"naam": "Apple Inc", "gewicht": 7.0, "land": "United States", "sector": "IT", "ticker": "AAPL"},
    {"naam": "Microsoft Corp", "gewicht": 6.5, "land": "United States", "sector": "IT", "ticker": "MSFT"},
]


def _yahoo_top10():
    top = pd.DataFrame({"Name": ["Apple Inc"], "Holding Percent": [0.07]}, index=["AAPL"])
    nep = MagicMock()
    nep.funds_data.top_holdings = top
    return MagicMock(return_value=nep)


class TestIsharesHoldingsOpIsin(unittest.TestCase):
    def _haal_op(self, ticker, isin, productpagina=PRODUCTPAGINA_HOLDINGS, fondsen=FONDSEN, cached=None):
        with patch.object(ticker_classificatie, "db_get_cached_etf_holdings", return_value=cached), \
             patch.object(ticker_classificatie, "db_save_etf_holdings") as mock_save, \
             patch.object(ticker_classificatie, "ishares_fondsen", return_value=fondsen), \
             patch.object(ticker_classificatie, "fetch_ishares_holdings_via_productpagina",
                          return_value=productpagina) as mock_pagina, \
             patch.object(ticker_classificatie, "_tel_yahoo_call"), \
             patch.object(ticker_classificatie, "get_land_sector", return_value=("United States", "IT")), \
             patch.object(ticker_classificatie.yf, "Ticker", _yahoo_top10()), \
             patch("builtins.print"):
            return get_etf_holdings(ticker, isin), mock_save, mock_pagina

    def test_isin_in_fondsenlijst_geeft_productpagina_holdings(self):
        holdings, mock_save, mock_pagina = self._haal_op("IUSA.AS", IUSA_ISIN)
        mock_pagina.assert_called_once_with("/nl/producten/251900/iusa")
        self.assertEqual([h["bron"] for h in holdings], ["provider_csv", "provider_csv"])
        self.assertEqual([h["holding_naam"] for h in holdings], ["Apple Inc", "Microsoft Corp"])
        self.assertAlmostEqual(holdings[0]["gewicht"], 0.07)
        mock_save.assert_called_once_with("IUSA.AS", holdings)

    def test_isin_niet_in_fondsenlijst_geeft_yfinance_top10(self):
        holdings, _save, mock_pagina = self._haal_op("VUSA.AS", "IE00B3XXRP09")
        mock_pagina.assert_not_called()
        self.assertEqual([h["bron"] for h in holdings], ["yfinance_top10"])

    def test_productpagina_faalt_geeft_yfinance_top10(self):
        holdings, _save, _pagina = self._haal_op("IUSA.AS", IUSA_ISIN, productpagina=None)
        self.assertEqual([h["bron"] for h in holdings], ["yfinance_top10"])

    def test_fondsenlijst_onbereikbaar_geeft_yfinance_top10(self):
        holdings, _save, mock_pagina = self._haal_op("IUSA.AS", IUSA_ISIN, fondsen=None)
        mock_pagina.assert_not_called()
        self.assertEqual([h["bron"] for h in holdings], ["yfinance_top10"])

    def test_yfinance_cache_wordt_geupgraded_bij_isin_match(self):
        cache = [{"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.07, "land": "United States",
                  "bron": "yfinance_top10"}]
        holdings, _save, _pagina = self._haal_op("IUSA.AS", IUSA_ISIN, cached=cache)
        self.assertEqual(holdings[0]["bron"], "provider_csv")

    def test_yfinance_cache_zonder_isin_blijft_staan(self):
        cache = [{"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.07, "land": "United States",
                  "bron": "yfinance_top10"}]
        holdings, mock_save, mock_pagina = self._haal_op("IUSA.AS", None, cached=cache)
        self.assertIs(holdings, cache)
        mock_pagina.assert_not_called()
        mock_save.assert_not_called()


class TestGeenProxyBijProviderHoldings(unittest.TestCase):
    def test_provider_csv_slaat_proxy_over(self):
        provider = [{"holding_naam": "Apple Inc", "holding_ticker": None, "gewicht": 0.07, "land": None,
                     "bron": "provider_csv"}]
        with patch.object(etf_proxy, "get_etf_holdings", return_value=provider), \
             patch.object(etf_proxy, "db_get_etf_proxies") as mock_cache, \
             patch.object(etf_proxy, "_bepaal_proxy") as mock_bepaal:
            self.assertEqual(etf_proxy.land_proxies_voor_etfs({"IUSA.AS": IUSA_ISIN}), {})
        mock_cache.assert_not_called()
        mock_bepaal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
