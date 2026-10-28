"""Pure kernen van portfolio_verdeling (invoer als dicts) en hun gelijkwaardigheid met de wrappers."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_verdeling
from portfolio_verdeling import (
    bedrijven_verdeling_uit, bereken_bedrijven_verdeling, bereken_etf_overlap, compute_land_sector_verdeling,
    etf_overlap_uit, land_sector_verdeling_uit, waarde_per_ticker_nu,
)

ETF_FRACTIES = {"land_pct": {"United States": 0.6, "Japan": 0.4}, "sector_pct": {"Technology": 0.5, "Unknown": 0.5},
                "land_bron": "provider_csv", "land_proxy": None}
AANDEEL_FRACTIES = {"land_pct": {"Netherlands": 1.0}, "sector_pct": {"Technology": 1.0}, "land_bron": None,
                    "land_proxy": None}


def holding(naam, gewicht, land="United States"):
    return {"holding_naam": naam, "holding_ticker": None, "gewicht": gewicht, "land": land, "bron": "provider_csv"}


def prijzen(tickers, waarde=100.0):
    return pd.DataFrame({t: [waarde] for t in tickers}, index=[pd.Timestamp("2024-01-02")])


class TestWaardePerTicker(unittest.TestCase):
    def test_gesloten_positie_zonder_koers_en_negatieve_waarde_vallen_eruit(self):
        transacties = pd.DataFrame({
            "ticker": ["OPEN", "GESLOTEN", "GESLOTEN", "ZONDERKOERS", "NEGATIEF", None],
            "aantal": [2.0, 3.0, -3.0, 1.0, -1.0, 5.0],
        })
        waarden = waarde_per_ticker_nu(transacties, prijzen(["OPEN", "GESLOTEN", "NEGATIEF"], waarde=50.0))
        self.assertEqual(waarden, {"OPEN": 100.0})

    def test_ticker_met_nan_als_laatste_koers_valt_eruit(self):
        koersen = pd.DataFrame({"A": [10.0], "B": [float("nan")]}, index=[pd.Timestamp("2024-01-02")])
        transacties = pd.DataFrame({"ticker": ["A", "B"], "aantal": [1.0, 1.0]})
        self.assertEqual(waarde_per_ticker_nu(transacties, koersen), {"A": 10.0})

    def test_gebruikt_de_laatste_koers(self):
        koersen = pd.DataFrame({"A": [10.0, 20.0]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        self.assertEqual(waarde_per_ticker_nu(pd.DataFrame({"ticker": ["A"], "aantal": [3.0]}), koersen), {"A": 60.0})


class TestLandSectorKern(unittest.TestCase):
    def setUp(self):
        self.r = land_sector_verdeling_uit(
            {"ETF_A": 200.0, "ASML": 100.0}, {"ETF_A": True, "ASML": False},
            {"ETF_A": ETF_FRACTIES, "ASML": AANDEEL_FRACTIES}, extra_aandelen={"OUD": ("Germany", "Industrials")})

    def test_bedragen_per_land_en_sector(self):
        self.assertEqual(self.r["land"], {"United States": 120.0, "Japan": 80.0, "Netherlands": 100.0})
        self.assertEqual(self.r["sector"], {"Technology": 200.0, "Unknown": 100.0})

    def test_per_etf_en_per_aandeel_inclusief_extra_aandeel(self):
        self.assertEqual(self.r["per_etf"], {"ETF_A": {"land": ETF_FRACTIES["land_pct"], "sector": ETF_FRACTIES["sector_pct"],
                                                      "land_bron": "provider_csv", "land_proxy": None}})
        self.assertEqual(self.r["per_aandeel"], {"ASML": {"land": "Netherlands", "sector": "Technology"},
                                                 "OUD": {"land": "Germany", "sector": "Industrials"}})

    def test_extra_aandeel_telt_niet_mee_in_de_bedragen(self):
        self.assertNotIn("Germany", self.r["land"])

    def test_land_per_bron_top_en_sector_per_bron(self):
        self.assertEqual(self.r["land_per_bron_top"]["United States"], {"ETF_A": 120.0})
        self.assertEqual(self.r["land_per_bron_top"]["Netherlands"], {"ASML": 100.0})
        self.assertEqual(self.r["sector_per_bron"]["Technology"], {"ETF_A": 100.0, "ASML": 100.0})

    def test_extra_aandeel_overschrijft_geen_aandeel_met_waarde(self):
        r = land_sector_verdeling_uit({"ASML": 100.0}, {"ASML": False}, {"ASML": AANDEEL_FRACTIES},
                                      {"ASML": ("Germany", "Industrials")})
        self.assertEqual(r["per_aandeel"]["ASML"]["land"], "Netherlands")


class TestBedrijvenKern(unittest.TestCase):
    WAARDEN = {"ETF_A": 1000.0, "ETF_B": 500.0, "AAPL": 300.0}
    ETFS = {"ETF_A": True, "ETF_B": True, "AAPL": False}
    HOLDINGS = {"ETF_A": [holding("Apple Inc", 0.5), holding("Microsoft Corp", 0.3)], "ETF_B": [holding("APPLE INC", 0.2)]}

    def draai(self, top_n=10):
        return bedrijven_verdeling_uit(self.WAARDEN, self.ETFS, self.HOLDINGS, {"AAPL": "Apple Inc."},
                                       {"ETF_A": "Wereld", "AAPL": "Apple"}, top_n)

    def test_bedrijf_in_twee_etfs_en_los_aandeel_wordt_samengevoegd(self):
        r = self.draai()
        self.assertEqual([b["bedrijf"] for b in r["top"]], ["Apple Inc", "Microsoft Corp"])
        self.assertEqual(r["top"][0]["waarde"], 900.0)

    def test_per_bron_in_procent_van_het_totaal(self):
        per_bron = self.draai()["top"][0]["per_bron"]
        self.assertEqual(per_bron, {"ETF_A": round(500 / 1800 * 100, 4), "ETF_B": round(100 / 1800 * 100, 4),
                                    "AAPL": round(300 / 1800 * 100, 4)})

    def test_overig_dekking_en_totaal(self):
        r = self.draai()
        self.assertEqual((r["totaal_waarde"], r["overig"]), (1800.0, 600.0))
        self.assertAlmostEqual(r["dekking_pct"], 1200 / 1800)

    def test_buiten_de_top_n_komt_in_overig(self):
        r = self.draai(top_n=1)
        self.assertEqual(len(r["top"]), 1)
        self.assertEqual(r["overig"], 300.0 + 600.0)

    def test_bronnen_aflopend_op_bijdrage_met_legendanamen(self):
        self.assertEqual(self.draai()["bronnen"], [{"ticker": "ETF_A", "naam": "Wereld"}, {"ticker": "AAPL", "naam": "Apple"},
                                                   {"ticker": "ETF_B", "naam": "ETF_B"}])

    def test_aandeel_zonder_naam_valt_terug_op_de_ticker(self):
        r = bedrijven_verdeling_uit({"XYZ": 100.0}, {"XYZ": False}, {}, {}, {}, 10)
        self.assertEqual(r["top"][0]["bedrijf"], "XYZ")

    def test_zonder_posities_geen_deling_door_nul(self):
        r = bedrijven_verdeling_uit({}, {}, {}, {}, {}, 10)
        self.assertEqual((r["top"], r["dekking_pct"], r["totaal_waarde"]), ([], 0.0, 0.0))


class TestOverlapKern(unittest.TestCase):
    def test_twee_etfs_met_bekende_overlap(self):
        matrix = etf_overlap_uit({"A": {"x": 0.6, "y": 0.4}, "B": {"x": 0.3, "z": 0.7}})
        self.assertAlmostEqual(matrix["A"]["B"], 0.3)
        self.assertEqual(matrix["A"]["B"], matrix["B"]["A"])

    def test_een_etf_geeft_lege_matrix(self):
        self.assertEqual(etf_overlap_uit({"A": {"x": 1.0}}), {})
        self.assertEqual(etf_overlap_uit({}), {})


TRANSACTIES = pd.DataFrame({
    "ticker": ["ETF_A", "ETF_B", "AAPL", "OUD", "OUD"],
    "aantal": [2.0, 1.0, 3.0, 4.0, -4.0],
    "product": ["Wereld", "Europa", "Apple", "Oud aandeel", "Oud aandeel"],
    "echte_naam": [None, None, "Apple Inc.", "Oud NV", "Oud NV"],
})
IS_ETF = {"ETF_A": True, "ETF_B": True, "AAPL": False, "OUD": False}
KOERSEN = prijzen(["ETF_A", "ETF_B", "AAPL", "OUD"])
HOLDINGS_PER_ETF = {"ETF_A": [holding("Apple Inc", 0.5), holding("Microsoft Corp", 0.3)],
                    "ETF_B": [holding("Apple Inc", 0.2, "Japan")]}


class TestWrappersGelijkAanKern(unittest.TestCase):
    """De wrappers lopen de tickers alfabetisch af (groupby), dus de handmatige invoer ook. De wrappers halen holdings en land/sector op (gemockt) en moeten exact de kern met die invoer teruggeven."""

    def setUp(self):
        for naam, kwargs in {
            "get_etf_holdings": dict(side_effect=lambda t: HOLDINGS_PER_ETF[t]),
            "get_etf_sector_verdeling": dict(return_value={"Technology": 0.5}),
            "get_land_sector": dict(return_value=("United States", "Technology")),
        }.items():
            p = patch.object(portfolio_verdeling, naam, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def test_land_sector(self):
        fracties_etf = {
            t: {"land_pct": {"United States": 0.8, "Unknown": 1.0 - 0.8} if t == "ETF_A" else {"Japan": 0.2, "Unknown": 1.0 - 0.2},
                "sector_pct": {"Technology": 0.5, "Unknown": 0.5}, "land_bron": "provider_csv", "land_proxy": None}
            for t in ("ETF_A", "ETF_B")}
        aandeel = {"land_pct": {"United States": 1.0}, "sector_pct": {"Technology": 1.0}, "land_bron": None,
                   "land_proxy": None}
        kern = land_sector_verdeling_uit(
            {"AAPL": 300.0, "ETF_A": 200.0, "ETF_B": 100.0}, IS_ETF, {**fracties_etf, "AAPL": aandeel},
            {"OUD": ("United States", "Technology")})
        self.assertEqual(compute_land_sector_verdeling(TRANSACTIES, KOERSEN, IS_ETF), kern)
        self.assertIn("OUD", kern["per_aandeel"])

    def test_bedrijven(self):
        kern = bedrijven_verdeling_uit(
            {"AAPL": 300.0, "ETF_A": 200.0, "ETF_B": 100.0}, IS_ETF, HOLDINGS_PER_ETF, {"AAPL": "Apple Inc."},
            {"ETF_A": "Wereld", "ETF_B": "Europa", "AAPL": "Apple", "OUD": "Oud aandeel"}, 10)
        self.assertEqual(bereken_bedrijven_verdeling(TRANSACTIES, KOERSEN, IS_ETF), kern)

    def test_overlap(self):
        kern = etf_overlap_uit({t: portfolio_verdeling._groepeer_holdings_per_bedrijf(HOLDINGS_PER_ETF[t])[0]
                                for t in ("ETF_A", "ETF_B")})
        self.assertEqual(bereken_etf_overlap(TRANSACTIES, KOERSEN, IS_ETF), kern)

    def test_overlap_telt_een_gesloten_etf_wel_mee(self):
        # Bewust: het overlap-tabblad heeft een knop "Verkochte ETF's meenemen" die rekent op verkochte ETF's in de matrix
        # (static/js/tabs/etf_overlap.js). Alleen de andere verdelingen laten een gesloten positie weg.
        transacties = pd.DataFrame({"ticker": ["ETF_A", "ETF_B", "ETF_B"], "aantal": [1.0, 1.0, -1.0]})
        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=lambda t: HOLDINGS_PER_ETF[t]):
            matrix = bereken_etf_overlap(transacties, prijzen(["ETF_A", "ETF_B"]), {"ETF_A": True, "ETF_B": True})
        self.assertAlmostEqual(matrix["ETF_A"]["ETF_B"], 0.2)
        self.assertEqual(waarde_per_ticker_nu(transacties, prijzen(["ETF_A", "ETF_B"])), {"ETF_A": 100.0})

    def test_overlap_met_twee_open_en_een_gesloten_etf_bevat_alle_drie(self):
        transacties = pd.DataFrame({"ticker": ["ETF_A", "ETF_B", "ETF_C", "ETF_C"], "aantal": [1.0, 1.0, 2.0, -2.0]})
        holdings = {**HOLDINGS_PER_ETF, "ETF_C": [holding("Apple Inc", 1.0)]}
        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=lambda t: holdings[t]):
            matrix = bereken_etf_overlap(transacties, prijzen(["ETF_A", "ETF_B", "ETF_C"]),
                                         {"ETF_A": True, "ETF_B": True, "ETF_C": True})
        self.assertEqual(set(matrix), {"ETF_A", "ETF_B", "ETF_C"})
        self.assertAlmostEqual(matrix["ETF_A"]["ETF_C"], 0.5)

    def test_overlap_slaat_een_etf_zonder_koerskolom_over(self):
        transacties = pd.DataFrame({"ticker": ["ETF_A", "ETF_B"], "aantal": [1.0, 1.0]})
        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=lambda t: HOLDINGS_PER_ETF[t]):
            self.assertEqual(bereken_etf_overlap(transacties, prijzen(["ETF_A"]), {"ETF_A": True, "ETF_B": True}), {})


if __name__ == "__main__":
    unittest.main()
