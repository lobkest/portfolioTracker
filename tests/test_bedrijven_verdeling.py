"""
Unit tests voor analysis.bereken_bedrijven_verdeling() en de bijbehorende
bedrijfsnaam-normalisatie (_normaliseer_bedrijfsnaam, BEDRIJF_NAAM_OVERRIDES)
-- zie CLAUDE.md "Top 10 bedrijven".

Draait geheel offline: classify_tickers en get_etf_holdings worden gemockt
(zelfde patroon als tests/test_snelle_prijscheck.py), dus geen echte
yfinance/database-calls nodig.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import _normaliseer_bedrijfsnaam, bereken_bedrijven_verdeling


class TestNormaliseerBedrijfsnaam(unittest.TestCase):
    def test_casing_verschil_zelfde_sleutel(self):
        self.assertEqual(_normaliseer_bedrijfsnaam("Apple Inc"), _normaliseer_bedrijfsnaam("APPLE INC"))

    def test_leesteken_verschil_zelfde_sleutel(self):
        self.assertEqual(
            _normaliseer_bedrijfsnaam("ASML Holding NV"),
            _normaliseer_bedrijfsnaam("ASML Holding N.V."),
        )

    def test_override_vangt_ontbrekend_woord(self):
        # "ASML HOLDING" mist het woord "NV" t.o.v. "ASML Holding NV" --
        # leesteken-normalisatie alleen lost dit niet op, vereist de
        # BEDRIJF_NAAM_OVERRIDES-entry.
        self.assertEqual(
            _normaliseer_bedrijfsnaam("ASML HOLDING"),
            _normaliseer_bedrijfsnaam("ASML Holding NV"),
        )

    def test_lege_naam_geeft_lege_string(self):
        self.assertEqual(_normaliseer_bedrijfsnaam(""), "")
        self.assertEqual(_normaliseer_bedrijfsnaam(None), "")


def _price_data(tickers, waarde=100.0, datum="2024-01-02"):
    return pd.DataFrame({t: [waarde] for t in tickers}, index=[pd.Timestamp(datum)])


class TestBerekenBedrijvenVerdeling(unittest.TestCase):
    def test_los_aandeel_en_etf_beide_zichtbaar_in_per_bron(self):
        # Apple wordt zowel los aangehouden (ticker AAPL, 1 stuk a 100 EUR)
        # als via een ETF (CSPX.AS, 1 stuk a 100 EUR, 50% Apple) -- per_bron
        # moet allebei laten zien.
        transacties_df = pd.DataFrame({
            "ticker": ["AAPL", "CSPX.AS"],
            "aantal": [1.0, 1.0],
            "echte_naam": ["Apple Inc", None],
        })
        price_data = _price_data(["AAPL", "CSPX.AS"])

        with patch.object(analysis, "classify_tickers", return_value={"AAPL": False, "CSPX.AS": True}), \
             patch.object(analysis, "get_etf_holdings", return_value=[
                 {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.5,
                  "land": "United States", "bron": "provider_csv"},
                 {"holding_naam": "Microsoft Corp", "holding_ticker": "MSFT", "gewicht": 0.5,
                  "land": "United States", "bron": "provider_csv"},
             ]):
            resultaat = bereken_bedrijven_verdeling(transacties_df, price_data)

        apple_entry = next(e for e in resultaat["top"] if e["bedrijf"] == "Apple Inc")
        self.assertAlmostEqual(apple_entry["waarde"], 150.0)  # 100 (los) + 50 (via ETF)
        self.assertAlmostEqual(apple_entry["per_bron"]["AAPL"], 100.0)
        self.assertAlmostEqual(apple_entry["per_bron"]["CSPX.AS"], 50.0)

    def test_dekking_gedeeltelijke_etf_holdings_naar_overig(self):
        # ETF met maar 60% gedekte holdings (bv. yfinance-top-10) -- de
        # overige 40% (40 EUR) mag niet als los bedrijf verschijnen, moet in
        # "overig" belanden, en dekking_pct moet dit reflecteren.
        transacties_df = pd.DataFrame({"ticker": ["CSPX.AS"], "aantal": [1.0]})
        price_data = _price_data(["CSPX.AS"], waarde=100.0)

        with patch.object(analysis, "classify_tickers", return_value={"CSPX.AS": True}), \
             patch.object(analysis, "get_etf_holdings", return_value=[
                 {"holding_naam": "Apple Inc", "holding_ticker": "AAPL", "gewicht": 0.6,
                  "land": "United States", "bron": "yfinance_top10"},
             ]):
            resultaat = bereken_bedrijven_verdeling(transacties_df, price_data)

        self.assertAlmostEqual(resultaat["dekking_pct"], 0.6)
        self.assertAlmostEqual(resultaat["overig"], 40.0)
        self.assertAlmostEqual(resultaat["top"][0]["waarde"], 60.0)


if __name__ == "__main__":
    unittest.main()
