"""
Tests voor haal_long_names() en de product-regel bij een upload (bestaande ticker houdt zijn product,
nieuwe ticker krijgt longName, Yahoo-fout of DEG-rij geeft de DeGiro-naam). Yahoo en de database zijn gemockt.
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

import ticker_classificatie
import upload_verwerking as uv


class TestHaalLongNames(unittest.TestCase):
    def test_lege_lijst_doet_geen_call(self):
        with patch.object(ticker_classificatie, "YahooqueryTicker") as mock_ticker:
            self.assertEqual(ticker_classificatie.haal_long_names([]), {})
        mock_ticker.assert_not_called()

    def test_dict_geeft_naam_en_foutstring_geeft_none(self):
        price = {
            "ASML.AS": {"longName": "ASML Holding N.V."},
            "FOUT.AS": "Quote not found for ticker symbol: FOUT.AS",
            "LEEG.AS": {"longName": "  "},
            "GEEN.AS": {"shortName": "Alleen kort"},
        }
        mock_ticker = MagicMock()
        mock_ticker.return_value.price = price
        with patch.object(ticker_classificatie, "YahooqueryTicker", mock_ticker):
            uit = ticker_classificatie.haal_long_names(["ASML.AS", "FOUT.AS", "LEEG.AS", "GEEN.AS"])
        self.assertEqual(uit, {"ASML.AS": "ASML Holding N.V.", "FOUT.AS": None, "LEEG.AS": None, "GEEN.AS": None})
        mock_ticker.assert_called_once_with(["ASML.AS", "FOUT.AS", "LEEG.AS", "GEEN.AS"])

    def test_een_batch_call_voor_alle_tickers_zonder_dubbelen(self):
        mock_ticker = MagicMock()
        mock_ticker.return_value.price = {}
        with patch.object(ticker_classificatie, "YahooqueryTicker", mock_ticker):
            uit = ticker_classificatie.haal_long_names(["A.AS", "A.AS", None, "B.AS"])
        self.assertEqual(uit, {"A.AS": None, "B.AS": None})
        mock_ticker.assert_called_once_with(["A.AS", "B.AS"])

    def test_mislukte_call_geeft_none_voor_alle_tickers(self):
        mock_ticker = MagicMock(side_effect=RuntimeError("netwerk weg"))
        with patch.object(ticker_classificatie, "YahooqueryTicker", mock_ticker):
            uit = ticker_classificatie.haal_long_names(["A.AS", "B.AS"])
        self.assertEqual(uit, {"A.AS": None, "B.AS": None})

    def test_price_is_geen_dict_geeft_none(self):
        mock_ticker = MagicMock()
        mock_ticker.return_value.price = "Too Many Requests"
        with patch.object(ticker_classificatie, "YahooqueryTicker", mock_ticker):
            self.assertEqual(ticker_classificatie.haal_long_names(["A.AS"]), {"A.AS": None})


def _df():
    return pd.DataFrame({
        "Datum": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]),
        "Product": ["ISHARES CORE S&P 500", "ISHARES CORE S&P 500", "ASML HOLDING NV", "BYD CO LTD - NON TRADEABLE"],
        "ISIN": ["IE1", "IE1", "NL1", "KY1"],
        "Beurs": ["EAM", "EAM", "EAM", "DEG"],
    })


TICKERS = {("IE1", "EAM"): "CSPX.AS", ("NL1", "EAM"): "ASML.AS", ("KY1", "DEG"): None}


class TestBepaalProductPerTicker(unittest.TestCase):
    def test_nieuwe_ticker_krijgt_long_name(self):
        with patch.object(uv, "haal_long_names", return_value={"CSPX.AS": "iShares Core S&P 500 UCITS ETF", "ASML.AS": "ASML Holding N.V."}) as mock:
            uit = uv.bepaal_product_per_ticker(_df(), TICKERS)
        self.assertEqual(uit, {"CSPX.AS": "iShares Core S&P 500 UCITS ETF", "ASML.AS": "ASML Holding N.V."})
        mock.assert_called_once_with(["CSPX.AS", "ASML.AS"])

    def test_bestaande_ticker_houdt_zijn_product_en_wordt_niet_opgehaald(self):
        with patch.object(uv, "haal_long_names", return_value={"ASML.AS": "ASML Holding N.V."}) as mock:
            uit = uv.bepaal_product_per_ticker(_df(), TICKERS, bestaand={"CSPX.AS": "Mijn S&P"})
        self.assertEqual(uit, {"CSPX.AS": "Mijn S&P", "ASML.AS": "ASML Holding N.V."})
        mock.assert_called_once_with(["ASML.AS"])

    def test_yahoo_fout_valt_terug_op_echte_naam(self):
        with patch.object(uv, "haal_long_names", return_value={"CSPX.AS": None, "ASML.AS": None}):
            uit = uv.bepaal_product_per_ticker(_df(), TICKERS)
        self.assertEqual(uit, {"CSPX.AS": "ISHARES CORE S&P 500", "ASML.AS": "ASML HOLDING NV"})

    def test_rij_zonder_ticker_komt_niet_in_de_mapping(self):
        with patch.object(uv, "haal_long_names", return_value={}):
            uit = uv.bepaal_product_per_ticker(_df(), TICKERS)
        self.assertNotIn(None, uit)

    def test_niet_opslaan_df_gebruikt_mapping_en_laat_deg_rij_staan(self):
        df = _df().assign(**{
            "Aantal": 1.0, "_koers_eur": 1.0, "Totaal EUR": -1.0, "Tijd": "10:00",
            uv.KOSTEN_KOLOM: -1.0, uv.WAARDE_KOLOM: -1.0, uv.WISSELKOERS_KOLOM: float("nan"),
        })
        mapping = {"CSPX.AS": "S&P 500", "ASML.AS": "ASML"}
        uit = uv.bouw_transacties_df_niet_opslaan(df, TICKERS, mapping)
        self.assertEqual(uit["product"].tolist(), ["S&P 500", "S&P 500", "ASML", "BYD CO LTD - NON TRADEABLE"])
        self.assertEqual(uit["echte_naam"].tolist(), df["Product"].tolist())


class _OpnameCursor:
    def __init__(self):
        self.params = []
        self.rowcount = 1

    def execute(self, sql, params):
        self.params.append(params)


class TestInsertGebruiktProduct(unittest.TestCase):
    def test_product_uit_mapping_echte_naam_blijft_degiro_naam(self):
        df = _df().assign(**{
            "Aantal": 1.0, "_koers_eur": 1.0, "Totaal EUR": -1.0, "Tijd": "10:00", "Order ID": list("abcd"),
            uv.KOSTEN_KOLOM: -1.0, uv.WAARDE_KOLOM: -1.0, uv.WISSELKOERS_KOLOM: float("nan"),
        })
        cur = _OpnameCursor()
        with redirect_stdout(io.StringIO()), Flask(__name__).test_request_context():
            uv._insert_nieuwe_transacties(cur, "ABC", df, TICKERS, {"CSPX.AS": "S&P 500", "ASML.AS": "ASML"})
        producten = [p[2] for p in cur.params]
        echte_namen = [p[10] for p in cur.params]
        self.assertEqual(producten, ["S&P 500", "S&P 500", "ASML", "BYD CO LTD - NON TRADEABLE"])
        self.assertEqual(echte_namen, df["Product"].tolist())


if __name__ == "__main__":
    unittest.main()
