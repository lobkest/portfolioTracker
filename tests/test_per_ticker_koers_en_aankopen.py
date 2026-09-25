"""
Unit tests voor compute_per_ticker_koers_en_aankopen(), t.b.v.
het "Per aandeel aankoop"-tabblad: kale koers per aandeel over tijd, het
aantal aangehouden aandelen over tijd, en apart de datums van aankopen en
verkopen (elk in hun eigen lijst, niet gemengd).

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from portfolio_calc import compute_per_ticker_koers_en_aankopen, compute_per_ticker


class TestPerTickerKoersEnAankopen(unittest.TestCase):
    def _rij(self, datum, aantal, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "adj_aantal": aantal, "koers": 0.0, "totaal_eur": totaal_eur,
            "beurs": beurs, "product": ticker,
        }

    def test_koers_volgt_de_kale_prijs_niet_de_waarde(self):
        df = pd.DataFrame([self._rij("2023-01-01", 10.0, -100.0)])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0]},
            index=[pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        # Kale koers, NIET holdings * koers (dat zou 100/110 zijn).
        self.assertEqual(result["X"]["koers"], [10.0, 11.0])
        self.assertEqual(result["X"]["holdings"], [10.0, 10.0])

    def test_alleen_aankopen_krijgen_een_aankoopdatum_verkopen_niet(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-03", 5.0, -60.0),
            self._rij("2023-01-05", -8.0, 90.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 10.0, 12.0, 12.0, 11.0, 11.0]},
            index=[pd.Timestamp(d) for d in [
                "2023-01-01", "2023-01-02", "2023-01-03",
                "2023-01-04", "2023-01-05", "2023-01-06",
            ]],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertEqual(result["X"]["aankoop_datums"], ["2023-01-01", "2023-01-03"])
        self.assertNotIn("2023-01-05", result["X"]["aankoop_datums"])

    def test_verkoop_krijgt_een_eigen_verkoopdatum(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-03", 5.0, -60.0),
            self._rij("2023-01-05", -8.0, 90.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 10.0, 12.0, 12.0, 11.0, 11.0]},
            index=[pd.Timestamp(d) for d in [
                "2023-01-01", "2023-01-02", "2023-01-03",
                "2023-01-04", "2023-01-05", "2023-01-06",
            ]],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertEqual(result["X"]["verkoop_datums"], ["2023-01-05"])
        self.assertNotIn("2023-01-01", result["X"]["verkoop_datums"])
        self.assertNotIn("2023-01-03", result["X"]["verkoop_datums"])
        # De verkoopdatum moet exact samenvallen met de rij waar holdings
        # daadwerkelijk daalt (7 -> zie test_holdings_is_trapvormig_cumulatief
        # voor dezelfde aanname bij een aankoop) -- 2023-01-05 is index 4.
        verkoop_index = result["X"]["labels"].index("2023-01-05")
        self.assertEqual(result["X"]["holdings"][verkoop_index], 7.0)
        self.assertEqual(result["X"]["holdings"][verkoop_index - 1], 15.0)

    def test_holdings_is_trapvormig_cumulatief(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-03", -4.0, 50.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 10.0, 12.0, 12.0]},
            index=[pd.Timestamp(d) for d in [
                "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04",
            ]],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertEqual(result["X"]["holdings"], [10.0, 10.0, 6.0, 6.0])

    def test_ontbrekende_koers_wordt_null_niet_nul(self):
        df = pd.DataFrame([self._rij("2023-01-01", 10.0, -100.0)])
        price_data = pd.DataFrame(
            {"X": [10.0, float("nan"), 12.0]},
            index=[pd.Timestamp(d) for d in [
                "2023-01-01", "2023-01-02", "2023-01-03",
            ]],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertEqual(result["X"]["koers"], [10.0, None, 12.0])

    def test_zelfde_crop_range_als_compute_per_ticker(self):
        df = pd.DataFrame([
            self._rij("2023-01-02", 10.0, -100.0),
            self._rij("2023-01-04", -10.0, 90.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 10.0, 9.0, 9.0, 9.0, 9.0]},
            index=[pd.Timestamp(d) for d in [
                "2023-01-01", "2023-01-02", "2023-01-03",
                "2023-01-04", "2023-01-05", "2023-01-06",
            ]],
        )
        aankoop_result = compute_per_ticker_koers_en_aankopen(df, price_data)
        waarde_result = compute_per_ticker(df, price_data)
        self.assertEqual(aankoop_result["X"]["labels"], waarde_result["X"]["labels"])

    def test_aankoop_voor_start_van_koersdata_valt_buiten_crop_range(self):
        # Zeldzaam edge-geval: de aankoop ligt vóór de
        # eerste beschikbare koersdatum, dus de "1 dag ervoor erbij"-buffer
        # (die start_pos op 0 capt) haalt 'm niet meer binnen de crop-range.
        # aankoop_datums wordt dan leeg -- geen crash, gewoon geen
        # verticale lijn te tekenen.
        df = pd.DataFrame([self._rij("2022-12-15", 10.0, -100.0)])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0]},
            index=[pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertEqual(result["X"]["aankoop_datums"], [])
        self.assertGreaterEqual(len(result["X"]["labels"]), 1)

    def test_open_positie_is_nog_in_bezit(self):
        df = pd.DataFrame([self._rij("2023-01-01", 10.0, -100.0)])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0]},
            index=[pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertIs(result["X"]["nog_in_bezit"], True)

    def test_gesloten_positie_is_niet_meer_in_bezit(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-02", -10.0, 110.0),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0, 12.0]},
            index=[pd.Timestamp(d) for d in ["2023-01-01", "2023-01-02", "2023-01-03"]],
        )
        result = compute_per_ticker_koers_en_aankopen(df, price_data)
        self.assertIs(result["X"]["nog_in_bezit"], False)

    def test_nog_in_bezit_gelijk_aan_compute_per_ticker(self):
        # Eén drempel voor beide tabbladen: de knoppen op 'Per aandeel
        # aankoop' en de "(oud)"-dropdown mogen elkaar niet tegenspreken.
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -100.0),
            self._rij("2023-01-02", -10.0, 110.0),
            self._rij("2023-01-01", 5.0, -50.0, ticker="Y"),
        ])
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0, 12.0], "Y": [10.0, 10.0, 10.0]},
            index=[pd.Timestamp(d) for d in ["2023-01-01", "2023-01-02", "2023-01-03"]],
        )
        aankoop = compute_per_ticker_koers_en_aankopen(df, price_data)
        waarde = compute_per_ticker(df, price_data)
        for t in ["X", "Y"]:
            self.assertEqual(aankoop[t]["nog_in_bezit"], waarde[t]["nog_in_bezit"])


if __name__ == "__main__":
    unittest.main()
