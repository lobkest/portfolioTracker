"""Orderkosten-schatting voor wat-als: Yahoo-beurs naar DeGiro-codes, mediaan per order, aansluitingskostenmelding.
Alles gemockt; geen database, geen netwerk."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import portfolio_orchestratie as po
from backend.aansluitingskosten import (
    aansluitingskosten_melding_nodig, bouw_aansluitingskosten_samenvatting, eigen_jaarbedrag_aansluitingskosten,
    schat_orderkosten,
)
from backend.ticker_matching import degiro_codes_voor_yahoo_beurs

PEIL = pd.Timestamp("2026-06-01")


def rij(datum, beurs, kosten, aantal=1.0, order_id=None, ticker="ABC.DE", product="Fonds"):
    return {"datum": pd.Timestamp(datum), "beurs": beurs, "transactiekosten": kosten, "aantal": aantal,
            "order_id": order_id, "ticker": ticker, "product": product}


def df(*rijen):
    return pd.DataFrame(list(rijen))


class TestCodesVoorYahooBeurs(unittest.TestCase):
    def test_amsterdam(self):
        self.assertEqual(degiro_codes_voor_yahoo_beurs("AMS"), {"EAM", "XAMS"})

    def test_duitsland_is_xetra_en_tradegate_zonder_frankfurt(self):
        self.assertEqual(degiro_codes_voor_yahoo_beurs("GER"), {"XET", "TDG"})

    def test_onbekende_of_lege_beurs_geeft_een_lege_set(self):
        self.assertEqual(degiro_codes_voor_yahoo_beurs("ZZZ"), set())
        self.assertEqual(degiro_codes_voor_yahoo_beurs(None), set())


class TestSchatOrderkosten(unittest.TestCase):
    def test_mediaan_van_drie_orders(self):
        t = df(rij("2026-05-01", "EAM", -1.0), rij("2026-05-02", "EAM", -3.0), rij("2026-05-03", "EAM", -2.0))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["status"], r["mediaan_eur"], r["aantal_orders"], r["periode_dagen"]), ("geschat", 2.0, 3, 365))
        self.assertEqual(r["beurs_namen"], ["Euronext Amsterdam"])

    def test_andere_beurzen_verkopen_en_corporate_actions_tellen_niet(self):
        t = df(rij("2026-05-01", "EAM", -2.0), rij("2026-05-02", "XET", -9.0), rij("2026-05-03", "EAM", -9.0, aantal=-1.0),
               rij("2026-05-04", "DEG", -9.0), rij("2026-05-05", "EAM", -9.0, product="NON TRADEABLE X"))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["mediaan_eur"], r["aantal_orders"]), (2.0, 1))

    def test_deelorders_zijn_een_order(self):
        t = df(rij("2026-05-01", "EAM", -1.0, order_id="A1"), rij("2026-05-01", "EAM", 0.0, order_id="A1-1"),
               rij("2026-05-02", "EAM", -3.0, order_id="B1"))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["aantal_orders"], r["mediaan_eur"]), (2, 2.0))

    def test_kostenkorting_is_al_afgetrokken(self):
        # pas_kostenkorting_toe telt de korting bij de negatieve kosten op: -3,00 met 1,00 korting = -2,00 betaald.
        t = df(rij("2026-05-01", "EAM", -3.0 + 1.0), rij("2026-05-02", "EAM", -3.0 + 3.0))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["mediaan_eur"], r["aantal_orders"]), (1.0, 2))

    def test_alleen_laatste_365_dagen(self):
        t = df(rij("2026-05-01", "EAM", -2.0), rij("2024-01-01", "EAM", -9.0))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["mediaan_eur"], r["aantal_orders"], r["periode_dagen"]), (2.0, 1, 365))

    def test_niets_in_het_laatste_jaar_gebruikt_alles(self):
        t = df(rij("2024-01-01", "EAM", -1.0), rij("2024-02-01", "EAM", -3.0))
        r = schat_orderkosten(t, {"EAM"}, PEIL)
        self.assertEqual((r["status"], r["mediaan_eur"], r["aantal_orders"], r["periode_dagen"]), ("geschat", 2.0, 2, None))

    def test_geen_orders_op_deze_beurs(self):
        r = schat_orderkosten(df(rij("2026-05-01", "XET", -2.0)), {"EAM"}, PEIL)
        self.assertEqual((r["status"], r["mediaan_eur"], r["aantal_orders"]), ("geen_eigen_orders", None, 0))

    def test_geen_codes_is_beurs_onbekend(self):
        r = schat_orderkosten(df(rij("2026-05-01", "EAM", -2.0)), set(), PEIL)
        self.assertEqual((r["status"], r["beurs_namen"]), ("beurs_onbekend", []))


class TestAansluitingskostenMelding(unittest.TestCase):
    def test_thuisbeurs_geeft_geen_melding(self):
        self.assertFalse(aansluitingskosten_melding_nodig(df(rij("2026-05-01", "XET", -1.0)), {"EAM", "XAMS"}))

    def test_andere_beurs_zonder_positie_geeft_melding(self):
        gesloten = df(rij("2026-01-01", "XET", -1.0, aantal=5.0), rij("2026-02-01", "XET", -1.0, aantal=-5.0))
        self.assertTrue(aansluitingskosten_melding_nodig(gesloten, {"XET", "TDG"}))
        self.assertTrue(aansluitingskosten_melding_nodig(df(rij("2026-01-01", "EAM", -1.0)), {"XET"}))

    def test_andere_beurs_met_open_positie_geeft_geen_melding(self):
        self.assertFalse(aansluitingskosten_melding_nodig(df(rij("2026-01-01", "XET", -1.0, aantal=5.0)), {"XET", "TDG"}))

    def test_onbekende_beurs_geeft_geen_melding(self):
        self.assertFalse(aansluitingskosten_melding_nodig(df(rij("2026-01-01", "EAM", -1.0)), set()))

    def test_eigen_jaarbedrag_is_het_laatste_jaar_over_de_beurzen(self):
        regels = [{"omschrijving": f"DEGIRO Aansluitingskosten {jaar} ({naam} - {code})", "mutatie_valuta": "EUR", "mutatie": -bedrag}
                  for jaar, naam, code, bedrag in [(2024, "Xetra", "XET", 2.5), (2025, "Xetra", "XET", 3.0),
                                                   (2025, "Tradegate AG", "TDG", 1.0), (2025, "Parijs", "EPA", 9.0)]]
        samenvatting = bouw_aansluitingskosten_samenvatting(regels)
        self.assertEqual(eigen_jaarbedrag_aansluitingskosten(samenvatting, {"XET", "TDG"}), (2025, 4.0))
        self.assertIsNone(eigen_jaarbedrag_aansluitingskosten(samenvatting, {"EBR"}))


class TestBouwOrderkosten(unittest.TestCase):
    INVOER = {"code": "ZZTEST", "transacties_df": df(rij("2026-01-01", "EAM", -1.0))}

    def test_zonder_invoer_geen_transacties(self):
        self.assertEqual(po.bouw_orderkosten(None, "AMS", PEIL), {"status": "geen_transacties"})

    def test_thuisbeurs_geen_aansluitingskosten_en_geen_database(self):
        with patch.object(po, "db_get_rekening_regels_eur", side_effect=AssertionError("geen database")):
            r = po.bouw_orderkosten(self.INVOER, "AMS", PEIL)
        self.assertEqual((r["status"], r["aansluitingskosten"]), ("geschat", None))

    def test_andere_beurs_met_melding_en_bedrag(self):
        regels = [{"omschrijving": "DEGIRO Aansluitingskosten 2025 (Xetra - XET)", "mutatie_valuta": "EUR", "mutatie": -2.5}]
        with patch.object(po, "db_get_rekening_regels_eur", return_value=regels):
            r = po.bouw_orderkosten(self.INVOER, "GER", PEIL)
        self.assertEqual(r["status"], "geen_eigen_orders")
        self.assertEqual(r["aansluitingskosten"], {"jaar": 2025, "bedrag_eur": 2.5})

    def test_databasefout_geeft_melding_zonder_bedrag(self):
        with patch.object(po, "db_get_rekening_regels_eur", side_effect=RuntimeError("weg")), patch.object(po, "dprint") as dp:
            r = po.bouw_orderkosten(self.INVOER, "GER", PEIL)
        self.assertEqual(r["aansluitingskosten"], {"jaar": None, "bedrag_eur": None})
        dp.assert_called_once()

    def test_onbekende_yahoo_beurs(self):
        r = po.bouw_orderkosten(self.INVOER, None, PEIL)
        self.assertEqual((r["status"], r["aansluitingskosten"]), ("beurs_onbekend", None))


class TestBerekenWatAlsOrderkosten(unittest.TestCase):
    def basis(self, **extra):
        etf = {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None, "land_proxy": None}
        return dict({"waarde_per_ticker": {"A": 100.0}, "is_etf_map": {"A": False}, "fracties_per_ticker": {"A": etf},
                     "holdings_per_etf": {}, "aandeel_namen": {"A": "A"}, "bron_namen": {}, "extra_aandelen": {}}, **extra)

    AANKOOP = ({"ticker": "MSFT", "naam": "Microsoft", "is_etf": False, "waarde_eur": 10.0,
                "fracties": {"land_pct": {"US": 1.0}, "sector_pct": {"Tech": 1.0}, "land_bron": None, "land_proxy": None},
                "holdings": None}, {"ticker": "MSFT"})

    def test_met_code_bevat_orderkosten_van_de_yahoo_beurs(self):
        invoer = {"code": "ZZTEST", "transacties_df": df(rij("2026-05-01", "EAM", -2.0))}
        with patch.object(po, "bouw_aankoop", return_value=self.AANKOOP), \
                patch.object(po, "db_get_ticker_details", return_value={"MSFT": {"yahoo_beurs": "AMS"}}) as details:
            r = po.bereken_wat_als(self.basis(orderkosten_invoer=invoer), "MSFT", None, 1)
        details.assert_called_once_with(["MSFT"])
        self.assertEqual((r["orderkosten"]["status"], r["orderkosten"]["mediaan_eur"]), ("geschat", 2.0))
        self.assertEqual(r["wat_als"]["totaal"], {"voor": 100.0, "na": 110.0})

    def test_niet_opslaan_geeft_geen_transacties_zonder_database(self):
        with patch.object(po, "bouw_aankoop", return_value=self.AANKOOP), \
                patch.object(po, "db_get_ticker_details", side_effect=AssertionError("geen database")):
            r = po.bereken_wat_als(self.basis(), "MSFT", None, 1)
        self.assertEqual(r["orderkosten"], {"status": "geen_transacties"})


if __name__ == "__main__":
    unittest.main()
