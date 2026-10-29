"""wat_als.simuleer_aankoop en de aanvullingen in portfolio_verdeling: invoer als dicts, geen mocks nodig."""
import json
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import portfolio_verdeling
from backend.portfolio_verdeling import (
    bedrijf_waarden, bedrijven_verdeling_uit, bereken_bedrijven_verdeling, bereken_etf_overlap, bouw_verdeling_invoer,
    land_sector_verdeling_uit,
)
from backend.wat_als import simuleer_aankoop


def holding(naam, gewicht):
    return {"holding_naam": naam, "holding_ticker": None, "gewicht": gewicht, "land": "X", "bron": "provider_csv"}


def fracties(land, sector):
    return {"land_pct": land, "sector_pct": sector, "land_bron": "provider_csv", "land_proxy": None}


def basis_met_een_etf_en_een_aandeel():
    return {
        "waarde_per_ticker": {"ETF_A": 600.0, "AAPL": 400.0},
        "is_etf_map": {"ETF_A": True, "AAPL": False},
        "fracties_per_ticker": {
            "ETF_A": fracties({"US": 0.5, "Japan": 0.5}, {"Tech": 0.6, "Health": 0.4}),
            "AAPL": fracties({"US": 1.0}, {"Tech": 1.0}),
        },
        "holdings_per_etf": {"ETF_A": [holding("Apple Inc", 0.4), holding("Nestle SA", 0.3), holding("Toyota", 0.3)]},
        "aandeel_namen": {"AAPL": "Apple Inc."},
        "bron_namen": {"ETF_A": "Wereld", "AAPL": "Apple"},
        "extra_aandelen": {},
    }


def nieuwe_etf(waarde=1000.0):
    return {
        "ticker": "ETF_B", "naam": "Zwitserland-fonds", "is_etf": True, "waarde_eur": waarde,
        "fracties": fracties({"US": 0.8, "Switzerland": 0.2}, {"Tech": 0.5, "Unknown": 0.5}),
        "holdings": [holding("Apple Inc", 0.5), holding("Nestle SA", 0.2), holding("Novartis", 0.3)],
    }


def per_naam(rijen):
    return {r["naam"]: r for r in rijen}


class TestNieuweEtf(unittest.TestCase):
    def setUp(self):
        self.r = simuleer_aankoop(basis_met_een_etf_en_een_aandeel(), nieuwe_etf(), top_n=3)

    def test_totalen(self):
        self.assertEqual(self.r["totaal"], {"voor": 1000.0, "na": 2000.0})
        self.assertFalse(self.r["aankoop"]["bijkopen"])

    def test_posities_inclusief_de_nieuwe(self):
        posities = {p["ticker"]: p for p in self.r["posities"]}
        self.assertEqual((posities["ETF_B"]["voor_pct"], posities["ETF_B"]["na_pct"], posities["ETF_B"]["nieuw"]), (0.0, 50.0, True))
        self.assertEqual((posities["ETF_A"]["voor_pct"], posities["ETF_A"]["na_pct"], posities["ETF_A"]["verschil_pp"]),
                         (60.0, 30.0, -30.0))
        self.assertEqual(self.r["posities"][0]["ticker"], "ETF_B")

    def test_etf_aandeel(self):
        self.assertEqual(self.r["etf_aandeel"], {"voor": {"etf_pct": 60.0, "aandeel_pct": 40.0},
                                                 "na": {"etf_pct": 80.0, "aandeel_pct": 20.0},
                                                 "verschil_pp": {"etf_pct": 20.0, "aandeel_pct": -20.0}})

    def test_etf_aandeel_verschil_is_na_min_voor_zoals_geleverd(self):
        # Voor: ETF 100 + aandeel 200 = 33,33% / 66,67%. Na: een ETF van 100 erbij = 200 van 400 = 50% / 50%.
        basis = basis_met_een_etf_en_een_aandeel()
        basis["waarde_per_ticker"] = {"ETF_A": 100.0, "AAPL": 200.0}
        r = simuleer_aankoop(basis, nieuwe_etf(waarde=100.0), top_n=5)
        etf_aandeel = r["etf_aandeel"]
        self.assertEqual(etf_aandeel["voor"], {"etf_pct": 33.33, "aandeel_pct": 66.67})
        self.assertEqual(etf_aandeel["na"], {"etf_pct": 50.0, "aandeel_pct": 50.0})
        self.assertEqual(etf_aandeel["verschil_pp"], {"etf_pct": 16.67, "aandeel_pct": -16.67})

    def test_etf_aandeel_verschil_is_negatief_voor_de_categorie_die_krimpt(self):
        basis = basis_met_een_etf_en_een_aandeel()
        basis["waarde_per_ticker"] = {"ETF_A": 100.0, "AAPL": 100.0}
        aankoop = dict(nieuwe_etf(waarde=100.0), ticker="AAPL", is_etf=False, holdings=None)
        r = simuleer_aankoop(basis, aankoop, top_n=5)
        # ETF 100 + aandeel 100 (50/50), daarna een aandeel van 100 erbij: ETF 33,33%, aandeel 66,67%.
        self.assertEqual(r["etf_aandeel"]["verschil_pp"], {"etf_pct": -16.67, "aandeel_pct": 16.67})

    def test_land_en_sector_verschillen_kloppen_met_handberekening(self):
        land = per_naam(self.r["land"])
        # Voor: US 700 + Japan 300 van 1000. Na: US 1500, Japan 300, Switzerland 200 van 2000.
        self.assertEqual((land["US"]["voor_pct"], land["US"]["na_pct"], land["US"]["verschil_pp"]), (70.0, 75.0, 5.0))
        self.assertEqual((land["Japan"]["verschil_pp"], land["Switzerland"]["verschil_pp"]), (-15.0, 10.0))
        sector = per_naam(self.r["sector"])
        self.assertEqual((sector["Tech"]["voor_pct"], sector["Tech"]["na_pct"]), (76.0, 63.0))
        self.assertEqual((sector["Unknown"]["voor_pct"], sector["Unknown"]["na_pct"]), (0.0, 25.0))

    def test_som_van_na_procenten_is_honderd_en_sortering_op_na(self):
        for onderdeel in ("land", "sector"):
            self.assertAlmostEqual(sum(r["na_pct"] for r in self.r[onderdeel]), 100.0)
            self.assertEqual([r["na_pct"] for r in self.r[onderdeel]],
                             sorted((r["na_pct"] for r in self.r[onderdeel]), reverse=True))

    def test_overlap_per_etf_en_al_in_portfolio(self):
        # Gedeeld: Apple min(40, 50) + Nestle min(30, 20) = 60%.
        self.assertEqual(self.r["overlap"]["per_etf"], {"ETF_A": 60.0})
        # Apple: min(50, 64% van de portfolio) + Nestle: min(20, 18%) + Novartis: niet aanwezig.
        self.assertEqual(self.r["overlap"]["al_in_portfolio_pct"], 68.0)
        self.assertIsNone(self.r["al_in_bezit"])

    def test_bedrijven_top_n_op_na_met_voor_percentage(self):
        bedrijven = per_naam(self.r["bedrijven"])
        self.assertEqual(list(bedrijven), ["Apple Inc", "Nestle SA", "Novartis"])
        self.assertEqual((bedrijven["Apple Inc"]["voor_pct"], bedrijven["Apple Inc"]["na_pct"]), (64.0, 57.0))
        self.assertEqual((bedrijven["Novartis"]["voor_pct"], bedrijven["Novartis"]["na_pct"]), (0.0, 15.0))
        self.assertNotIn("Toyota", bedrijven)

    def test_bedrijf_dat_voor_buiten_de_top_n_viel_krijgt_het_juiste_voor_percentage(self):
        basis = basis_met_een_etf_en_een_aandeel()
        basis["waarde_per_ticker"] = {"ETF_A": 1000.0}
        basis["is_etf_map"] = {"ETF_A": True}
        basis["holdings_per_etf"] = {"ETF_A": [holding("Apple Inc", 0.5), holding("Nestle SA", 0.3), holding("Toyota", 0.2)]}
        aandeel = {"ticker": "TM", "naam": "Toyota", "is_etf": False, "waarde_eur": 1000.0,
                   "fracties": fracties({"Japan": 1.0}, {"Auto": 1.0}), "holdings": None}
        r = simuleer_aankoop(basis, aandeel, top_n=1)
        self.assertEqual(r["bedrijven"], [{"naam": "Toyota", "voor_pct": 20.0, "na_pct": 60.0, "verschil_pp": 40.0}])


class TestBijkopen(unittest.TestCase):
    def setUp(self):
        aankoop = {"ticker": "ETF_A", "naam": "Eigen naam", "is_etf": True, "waarde_eur": 400.0,
                   "fracties": fracties({"Mars": 1.0}, {"Mars": 1.0}), "holdings": [holding("Marsbedrijf", 1.0)]}
        self.r = simuleer_aankoop(basis_met_een_etf_en_een_aandeel(), aankoop, top_n=5)

    def test_waarde_wordt_opgeteld_zonder_dubbele_positie(self):
        posities = {p["ticker"]: p for p in self.r["posities"]}
        self.assertEqual(len(self.r["posities"]), 2)
        self.assertEqual((posities["ETF_A"]["na_pct"], posities["ETF_A"]["nieuw"]), (round(1000 / 1400 * 100, 4), False))
        self.assertTrue(self.r["aankoop"]["bijkopen"])

    def test_bestaande_fracties_en_holdings_blijven_gelden(self):
        self.assertNotIn("Mars", per_naam(self.r["land"]))
        self.assertNotIn("Marsbedrijf", per_naam(self.r["bedrijven"]))
        self.assertEqual(per_naam(self.r["land"])["Japan"]["na_pct"], round(500 / 1400 * 100, 4))

    def test_overlap_slaat_de_eigen_etf_over(self):
        self.assertEqual(self.r["overlap"]["per_etf"], {})


class TestBijkopenOverlapMetAndereEtfs(unittest.TestCase):
    def test_bijkopen_van_a_in_een_portfolio_met_a_en_b_toont_alleen_de_overlap_met_b(self):
        basis = basis_voor_aandeel()
        basis["holdings_per_etf"] = {"ETF_A": [holding("Apple Inc", 0.4), holding("Nestle SA", 0.6)],
                                     "ETF_B": [holding("Apple Inc", 0.2), holding("Nestle SA", 0.5)]}
        aankoop = {"ticker": "ETF_A", "naam": "A", "is_etf": True, "waarde_eur": 100.0,
                   "fracties": fracties({"Mars": 1.0}, {"Mars": 1.0}), "holdings": [holding("Marsbedrijf", 1.0)]}
        r = simuleer_aankoop(basis, aankoop, top_n=5)
        # Overlap A-B uit de bestaande holdings van A: min(40, 20) + min(60, 50) = 70%; de holdings van de aankoop tellen niet.
        self.assertEqual(r["overlap"]["per_etf"], {"ETF_B": 70.0})
        self.assertGreater(r["overlap"]["al_in_portfolio_pct"], 0.0)

    def test_bijkopen_van_een_aandeel_matcht_op_de_naam_van_de_bestaande_positie(self):
        aankoop = dict(aandeel_aankoop("Heel Andere Naam"), ticker="AAPL")
        r = simuleer_aankoop(basis_voor_aandeel(), aankoop, top_n=5)
        self.assertEqual(r["al_in_bezit"]["direct_eur"], 300.0)


class TestWatAlsOverlapOpenPosities(unittest.TestCase):
    def test_per_etf_bevat_geen_gesloten_etf(self):
        transacties = pd.DataFrame({"ticker": ["ETF_A", "ETF_C", "ETF_C"], "aantal": [1.0, 2.0, -2.0]})
        koersen = pd.DataFrame({"ETF_A": [100.0], "ETF_C": [100.0]}, index=[pd.Timestamp("2024-01-02")])
        holdings = {"ETF_A": [holding("Apple Inc", 1.0)], "ETF_C": [holding("Apple Inc", 1.0)]}
        with patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=lambda t: holdings[t]),                 patch.object(portfolio_verdeling, "get_etf_sector_verdeling", return_value={"Tech": 1.0}):
            basis = bouw_verdeling_invoer(transacties, koersen, {"ETF_A": True, "ETF_C": True},
                                          onderdelen={"land_sector", "bedrijven"})
        aankoop = dict(nieuwe_etf(), holdings=[holding("Apple Inc", 1.0)])
        r = simuleer_aankoop(basis, aankoop, top_n=5)
        self.assertEqual(set(r["overlap"]["per_etf"]), {"ETF_A"})


def basis_voor_aandeel():
    return {
        "waarde_per_ticker": {"ETF_A": 1000.0, "ETF_B": 500.0, "AAPL": 300.0},
        "is_etf_map": {"ETF_A": True, "ETF_B": True, "AAPL": False},
        "fracties_per_ticker": {"ETF_A": fracties({"US": 1.0}, {"Tech": 1.0}), "ETF_B": fracties({"US": 1.0}, {"Tech": 1.0}),
                                "AAPL": fracties({"US": 1.0}, {"Tech": 1.0})},
        "holdings_per_etf": {"ETF_A": [holding("Apple Inc", 0.4)], "ETF_B": [holding("Apple Inc", 0.2)]},
        "aandeel_namen": {"AAPL": "Apple Inc."}, "bron_namen": {}, "extra_aandelen": {},
    }


def aandeel_aankoop(naam):
    return {"ticker": "NIEUW", "naam": naam, "is_etf": False, "waarde_eur": 100.0,
            "fracties": fracties({"US": 1.0}, {"Tech": 1.0}), "holdings": None}


class TestAandeel(unittest.TestCase):
    def test_aandeel_dat_direct_en_via_twee_etfs_in_bezit_is(self):
        r = simuleer_aankoop(basis_voor_aandeel(), aandeel_aankoop("Apple Inc"), top_n=5)
        self.assertEqual(r["al_in_bezit"], {"direct_eur": 300.0, "via_etf_eur": 500.0,
                                            "per_etf": {"ETF_A": 400.0, "ETF_B": 100.0}, "gematchte_naam": "Apple Inc"})
        self.assertIsNone(r["overlap"])

    def test_naam_met_ander_leesteken_matcht_toch(self):
        r = simuleer_aankoop(basis_voor_aandeel(), aandeel_aankoop("APPLE INC."), top_n=5)
        self.assertEqual(r["al_in_bezit"]["via_etf_eur"], 500.0)

    def test_aandeel_dat_nergens_voorkomt(self):
        r = simuleer_aankoop(basis_voor_aandeel(), aandeel_aankoop("Zzz Corp"), top_n=5)
        self.assertEqual(r["al_in_bezit"], {"direct_eur": 0.0, "via_etf_eur": 0.0, "per_etf": {}, "gematchte_naam": None})

    def test_nieuw_aandeel_komt_in_posities_en_bedrijven(self):
        r = simuleer_aankoop(basis_voor_aandeel(), aandeel_aankoop("Zzz Corp"), top_n=10)
        self.assertIn("NIEUW", [p["ticker"] for p in r["posities"]])
        self.assertEqual(per_naam(r["bedrijven"])["Zzz Corp"]["voor_pct"], 0.0)


class TestConsistentieMetDeKernen(unittest.TestCase):
    def test_voor_is_gelijk_aan_de_bestaande_kernen_op_dezelfde_basis(self):
        basis = basis_met_een_etf_en_een_aandeel()
        r = simuleer_aankoop(basis, nieuwe_etf(), top_n=50)
        kern = land_sector_verdeling_uit(basis["waarde_per_ticker"], basis["is_etf_map"], basis["fracties_per_ticker"])
        for onderdeel, totaal in (("land", kern["land_totaal"]), ("sector", kern["sector_totaal"])):
            totaal_eur = sum(totaal.values())
            for rij in r[onderdeel]:
                self.assertAlmostEqual(rij["voor_pct"], totaal.get(rij["naam"], 0.0) / totaal_eur * 100, places=4)

        bedrijven = bedrijven_verdeling_uit(basis["waarde_per_ticker"], basis["is_etf_map"], basis["holdings_per_etf"],
                                            basis["aandeel_namen"], basis["bron_namen"], 50)
        voor = {b["bedrijf"]: b["waarde"] / bedrijven["totaal_waarde"] * 100 for b in bedrijven["top"]}
        for rij in r["bedrijven"]:
            if rij["naam"] in voor:
                self.assertAlmostEqual(rij["voor_pct"], voor[rij["naam"]], places=2)
        self.assertTrue(set(voor) & {r["naam"] for r in r["bedrijven"]})

    def test_basis_wordt_niet_aangepast(self):
        basis = basis_met_een_etf_en_een_aandeel()
        simuleer_aankoop(basis, nieuwe_etf(), top_n=3)
        self.assertEqual(basis, basis_met_een_etf_en_een_aandeel())


class TestLegePortfolio(unittest.TestCase):
    def test_lege_basis_geeft_voor_nul_en_na_honderd_zonder_deling_door_nul(self):
        leeg = {"waarde_per_ticker": {}, "is_etf_map": {}, "fracties_per_ticker": {}, "holdings_per_etf": {},
                "aandeel_namen": {}, "bron_namen": {}, "extra_aandelen": {}}
        r = simuleer_aankoop(leeg, nieuwe_etf(), top_n=5)
        self.assertEqual(r["totaal"], {"voor": 0.0, "na": 1000.0})
        self.assertEqual((r["posities"][0]["voor_pct"], r["posities"][0]["na_pct"]), (0.0, 100.0))
        self.assertEqual(r["etf_aandeel"]["voor"], {"etf_pct": 0.0, "aandeel_pct": 0.0})
        self.assertTrue(all(rij["voor_pct"] == 0.0 for rij in r["land"] + r["sector"] + r["bedrijven"]))
        self.assertAlmostEqual(sum(rij["na_pct"] for rij in r["land"]), 100.0)
        self.assertEqual(r["overlap"], {"per_etf": {}, "al_in_portfolio_pct": 0.0})


class TestUitvoer(unittest.TestCase):
    def test_geen_nan_en_json_serialiseerbaar(self):
        aankoop = nieuwe_etf()
        aankoop["holdings"] = None
        aankoop["fracties"] = fracties({"US": float("nan")}, {"Tech": 1.0})
        r = simuleer_aankoop(basis_met_een_etf_en_een_aandeel(), aankoop, top_n=3)
        json.dumps(r, allow_nan=False)


class TestAanvullingenPortfolioVerdeling(unittest.TestCase):
    def test_land_totaal_en_sector_totaal_zijn_ongegroepeerd(self):
        f = fracties({"US": 0.999, "Fiji": 0.001}, {"Tech": 1.0})
        r = land_sector_verdeling_uit({"ETF_A": 1000.0}, {"ETF_A": True}, {"ETF_A": f})
        self.assertIn("Fiji", r["land_totaal"])
        self.assertNotIn("Fiji", r["land"])
        self.assertIn("Overig", r["land"])
        self.assertAlmostEqual(r["land_totaal"]["Fiji"], 1.0)
        self.assertEqual(r["sector_totaal"], {"Tech": 1000.0})

    def test_bedrijf_waarden_geeft_alle_bedrijven_en_de_gedekte_waarde(self):
        basis = basis_met_een_etf_en_een_aandeel()
        bedrijven, gedekt = bedrijf_waarden(basis["waarde_per_ticker"], basis["is_etf_map"], basis["holdings_per_etf"],
                                            basis["aandeel_namen"])
        self.assertEqual(sorted(b["naam"] for b in bedrijven.values()), ["Apple Inc", "Nestle SA", "Toyota"])
        self.assertEqual(bedrijven["apple inc"]["waarde"], 640.0)
        self.assertEqual(bedrijven["apple inc"]["per_bron"], {"ETF_A": 240.0, "AAPL": 400.0})
        self.assertEqual(gedekt, 1000.0)
        kern = bedrijven_verdeling_uit(basis["waarde_per_ticker"], basis["is_etf_map"], basis["holdings_per_etf"],
                                       basis["aandeel_namen"], basis["bron_namen"], 1)
        self.assertEqual((kern["top"][0]["bedrijf"], kern["overig"]), ("Apple Inc", 360.0))


class TestBouwVerdelingInvoer(unittest.TestCase):
    def test_wrappers_doen_alleen_de_calls_van_hun_eigen_onderdeel(self):
        transacties = pd.DataFrame({"ticker": ["ETF_A"], "aantal": [1.0]})
        koersen = pd.DataFrame({"ETF_A": [100.0]}, index=[pd.Timestamp("2024-01-02")])
        stuk = AssertionError("onnodige call")
        with patch.object(portfolio_verdeling, "land_sector_fracties", side_effect=stuk), \
                patch.object(portfolio_verdeling, "get_etf_holdings", side_effect=stuk):
            # Eén ETF: de overlap hoeft geen holdings op te halen.
            self.assertEqual(bereken_etf_overlap(transacties, koersen, {"ETF_A": True}), {})
        with patch.object(portfolio_verdeling, "land_sector_fracties", side_effect=stuk), \
                patch.object(portfolio_verdeling, "get_etf_holdings", return_value=[holding("Apple Inc", 1.0)]):
            r = bereken_bedrijven_verdeling(transacties, koersen, {"ETF_A": True})
        self.assertEqual(r["totaal_waarde"], 100.0)

    def test_alle_onderdelen_geven_de_sleutels_van_de_basis(self):
        transacties = pd.DataFrame({"ticker": ["ETF_A", "AAPL"], "aantal": [1.0, 1.0], "product": ["W", "A"],
                                    "echte_naam": [None, "Apple Inc."]})
        koersen = pd.DataFrame({"ETF_A": [100.0], "AAPL": [50.0]}, index=[pd.Timestamp("2024-01-02")])
        with patch.object(portfolio_verdeling, "get_etf_holdings", return_value=[holding("Apple Inc", 1.0)]), \
                patch.object(portfolio_verdeling, "get_etf_sector_verdeling", return_value={"Tech": 1.0}), \
                patch.object(portfolio_verdeling, "get_land_sector", return_value=("US", "Tech")):
            invoer = bouw_verdeling_invoer(transacties, koersen, {"ETF_A": True, "AAPL": False})
        self.assertEqual(set(invoer), {"waarde_per_ticker", "is_etf_map", "fracties_per_ticker", "extra_aandelen",
                                       "holdings_per_etf", "aandeel_namen", "bron_namen", "gewichten_per_etf"})
        self.assertEqual(invoer["aandeel_namen"], {"AAPL": "Apple Inc."})
        self.assertEqual(invoer["gewichten_per_etf"], {})


if __name__ == "__main__":
    unittest.main()
