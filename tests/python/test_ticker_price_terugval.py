"""Terugval op yahooquery .price bij een mislukte of uitgeklede .info (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import ticker_classificatie as tc

UITGEKLEED = {"trailingPegRatio": None}
PRICE_ETF = {"currency": "EUR", "longName": "VanEck ETF", "shortName": "VanEck", "quoteType": "ETF", "exchange": "AMS"}
PRICE_AANDEEL = {"currency": "USD", "longName": "Apple Inc.", "quoteType": "EQUITY", "exchange": "NMS"}


class TestInfoIsBruikbaar(unittest.TestCase):
    def test_volledig_of_alleen_currency_is_bruikbaar(self):
        self.assertTrue(tc.info_is_bruikbaar({"quoteType": "ETF", "currency": "EUR", "longName": "X"}))
        self.assertTrue(tc.info_is_bruikbaar({"currency": "EUR"}))

    def test_leeg_uitgekleed_of_none_is_onbruikbaar(self):
        for info in ({}, UITGEKLEED, None):
            self.assertFalse(tc.info_is_bruikbaar(info), info)


class _Classificatie(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)

    def classificeer(self, info, price=None, categorie="Equity"):
        yq = MagicMock()
        yq.return_value.price = {"X": price} if isinstance(price, dict) else price
        yq.return_value.fund_profile = {"X": {"categoryName": categorie}}
        yf = MagicMock()
        yf.return_value.funds_data.fund_overview = {}
        with patch.object(tc, "_fetch_yf_info", return_value=info), \
                patch.object(tc, "YahooqueryTicker", yq), patch.object(tc, "db_save_land_sector") as land_sector, \
                patch.object(tc.yf, "Ticker", yf), patch.object(tc, "dprint"):
            uit = tc._classify_ticker_uncached("X")
        self.yq = yq
        self.land_sector = land_sector
        return uit


class TestPriceTerugval(_Classificatie):
    def test_uitgeklede_info_wordt_aangevuld_uit_price_zonder_land_sector(self):
        uit = self.classificeer(UITGEKLEED, PRICE_ETF)
        self.assertEqual((uit["valuta"], uit["long_name"], uit["quote_type"], uit["is_etf"], uit["yahoo_beurs"]),
                         ("EUR", "VanEck ETF", "ETF", True, "AMS"))
        self.land_sector.assert_not_called()

    def test_mislukte_info_valt_ook_terug_op_price(self):
        self.assertEqual(self.classificeer(None, PRICE_ETF)["valuta"], "EUR")

    def test_shortname_als_longname_ontbreekt(self):
        price = {"currency": "EUR", "shortName": "Kort", "quoteType": "EQUITY"}
        self.assertEqual(self.classificeer(UITGEKLEED, price)["long_name"], "Kort")

    def test_foutstring_uit_price_geeft_none(self):
        self.assertIsNone(self.classificeer(UITGEKLEED, "Quote not found for ticker symbol: X"))
        self.land_sector.assert_not_called()

    def test_aandeel_met_uitgeklede_info_is_geen_etf(self):
        uit = self.classificeer(UITGEKLEED, PRICE_AANDEEL)
        self.assertFalse(uit["is_etf"])
        self.assertEqual(uit["valuta"], "USD")


class TestBruikbareInfo(_Classificatie):
    def test_zonder_quotetype_blijft_de_heuristiek(self):
        info = {"currency": "EUR", "totalAssets": 1e9, "fundFamily": "F"}
        uit = self.classificeer(info, PRICE_AANDEEL)
        self.assertTrue(uit["is_etf"])

    def test_price_wordt_niet_aangeroepen(self):
        info = {"quoteType": "EQUITY", "currency": "USD", "country": "US", "sector": "Tech", "longName": "Apple"}
        uit = self.classificeer(info, PRICE_AANDEEL)
        self.assertEqual(uit["long_name"], "Apple")
        self.yq.assert_not_called()
        self.land_sector.assert_called_once_with("X", "US", "Tech")


class TestCategorieCache(_Classificatie):
    def test_tweede_classificatie_doet_geen_fondsprofiel_call(self):
        info = {"quoteType": "ETF", "currency": "EUR"}
        self.classificeer(info, categorie=None)
        eerste = self.yq.call_count
        self.assertGreater(eerste, 0)
        self.classificeer(info, categorie=None)
        self.yq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
