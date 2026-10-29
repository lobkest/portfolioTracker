"""Terugval op yahooquery .price bij een mislukte of uitgeklede .info (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import pandas as pd

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


CHART_ETF = {"currency": "EUR", "exchangeName": "AMS", "instrumentType": "ETF", "longName": "VanEck ETF",
             "shortName": "VanEck"}


def _yf_met_metadata(metadata=None, fout=None):
    yf = MagicMock()
    if fout:
        yf.return_value.history.side_effect = fout
    yf.return_value.history_metadata = metadata
    return yf


class TestClassifyViaChart(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)

    def via_chart(self, metadata=None, fout=None):
        with patch.object(tc.yf, "Ticker", _yf_met_metadata(metadata, fout)), \
                patch.object(tc, "_yq_categorie", return_value="Equity"), patch.object(tc, "dprint"):
            return tc._classify_via_chart("X")

    def test_volledige_etf_metadata_vult_alle_velden(self):
        self.assertEqual(self.via_chart(CHART_ETF), {
            "is_etf": True, "quote_type": "ETF", "valuta": "EUR", "yahoo_beurs": "AMS", "fund_family": None,
            "category": "Equity", "long_name": "VanEck ETF"})

    def test_gbp_pence_blijft_gbp_pence(self):
        self.assertEqual(self.via_chart({**CHART_ETF, "currency": "GBp"})["valuta"], "GBp")

    def test_equity_is_geen_etf(self):
        uit = self.via_chart({"currency": "USD", "instrumentType": "EQUITY", "shortName": "Kort"})
        self.assertFalse(uit["is_etf"])
        self.assertIsNone(uit["category"])
        self.assertEqual(uit["long_name"], "Kort")

    def test_lege_metadata_of_exceptie_geeft_none(self):
        self.assertIsNone(self.via_chart({}))
        self.assertIsNone(self.via_chart({"symbol": "X"}))
        self.assertIsNone(self.via_chart(fout=RuntimeError("boem")))


class TestChartMetadataRetry(unittest.TestCase):
    def chart(self, geschiedenis, metadata_per_poging, locked):
        yf = MagicMock()
        yf.return_value.history.side_effect = geschiedenis
        metadata = PropertyMock(side_effect=metadata_per_poging)
        type(yf.return_value).history_metadata = metadata
        with patch.object(tc.yf, "Ticker", yf), patch.object(tc, "_database_locked_sinds", side_effect=locked), \
                patch.object(tc.time, "sleep") as slaap, patch.object(tc, "dprint") as log:
            uit = tc._chart_metadata("X")
        return uit, yf, slaap, log

    def test_lege_history_zonder_melding_geeft_none_met_logregel_en_zonder_retry(self):
        uit, yf, slaap, log = self.chart([pd.DataFrame()], [{}], [False])
        self.assertIsNone(uit)
        self.assertEqual(yf.return_value.history.call_count, 1)
        slaap.assert_not_called()
        regel = log.call_args.args[0]
        self.assertIn("history 0 rijen", regel)
        self.assertIn("met 0 sleutels", regel)
        self.assertIn("database locked: False", regel)

    def test_metadata_zonder_currency_en_type_wordt_gelogd_en_niet_opnieuw_geprobeerd(self):
        uit, yf, slaap, log = self.chart([pd.DataFrame({"Close": [1.0]})], [{"symbol": "X"}], [False])
        self.assertEqual(uit, {"symbol": "X"})
        self.assertIn("met 1 sleutels", log.call_args.args[0])
        slaap.assert_not_called()

    def test_lege_history_met_melding_wordt_een_keer_opnieuw_geprobeerd(self):
        rijen = pd.DataFrame({"Close": [1.0]})
        uit, yf, slaap, _log = self.chart([pd.DataFrame(), rijen], [{}, CHART_ETF], [True, False])
        self.assertEqual(uit, CHART_ETF)
        self.assertEqual(yf.return_value.history.call_count, 2)
        slaap.assert_called_once_with(tc.BULK_DOWNLOAD_WACHTTIJD)

    def test_blijft_de_melding_komen_dan_blijft_het_bij_een_retry(self):
        uit, yf, slaap, _log = self.chart([pd.DataFrame()] * 3, [{}] * 3, [True] * 3)
        self.assertIsNone(uit)
        self.assertEqual(yf.return_value.history.call_count, 2)
        self.assertEqual(slaap.call_count, 1)

    def test_melding_met_gevulde_history_geeft_geen_retry(self):
        uit, yf, slaap, _log = self.chart([pd.DataFrame({"Close": [1.0]})], [CHART_ETF], [True])
        self.assertEqual(uit, CHART_ETF)
        slaap.assert_not_called()


class TestVolgordeInfoChartPrice(unittest.TestCase):
    def classificeer(self, info, chart, price):
        with patch.object(tc, "_fetch_yf_info", return_value=info), \
                patch.object(tc, "_classify_via_chart", return_value=chart) as via_chart, \
                patch.object(tc, "_classify_via_price", return_value=price) as via_price, \
                patch.object(tc, "db_save_land_sector") as land_sector, patch.object(tc, "dprint"):
            uit = tc._classify_ticker_uncached("X")
        return uit, via_chart, via_price, land_sector

    def test_chart_gaat_voor_price_en_slaat_geen_land_sector_op(self):
        uit, via_chart, via_price, land_sector = self.classificeer(UITGEKLEED, {"valuta": "EUR"}, {"valuta": "USD"})
        self.assertEqual(uit, {"valuta": "EUR"})
        via_price.assert_not_called()
        land_sector.assert_not_called()

    def test_zonder_chart_valt_door_naar_price(self):
        uit, _, via_price, land_sector = self.classificeer(UITGEKLEED, None, {"valuta": "USD"})
        self.assertEqual(uit, {"valuta": "USD"})
        land_sector.assert_not_called()

    def test_alles_faalt_geeft_none(self):
        self.assertIsNone(self.classificeer(None, None, None)[0])

    def test_bruikbare_info_roept_chart_en_price_niet_aan(self):
        _, via_chart, via_price, _ = self.classificeer({"quoteType": "EQUITY", "currency": "USD"}, None, None)
        via_chart.assert_not_called()
        via_price.assert_not_called()


class TestPriceFoutLog(unittest.TestCase):
    def test_foutstring_staat_afgekapt_in_de_log(self):
        lang = "Unauthorized " + "x" * 300
        with patch.object(tc, "_yq_fonds_data", return_value=lang), patch.object(tc, "dprint") as dprint:
            self.assertIsNone(tc._yq_price("X"))
        bericht = dprint.call_args[0][0]
        self.assertIn("Unauthorized", bericht)
        self.assertLess(len(bericht), 200)


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
