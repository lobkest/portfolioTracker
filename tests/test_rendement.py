"""
Unit tests voor de rendementsberekeningen achter het Statistieken-tabblad
(analysis.py). Gebruikt Python's ingebouwde unittest-module (dit project
had nog geen testsuite/pytest, zie CLAUDE.md "Open aandachtspunten").

Draait geheel offline: geen database, geen yfinance-calls — alle geteste
functies zijn pure functies (getallen/DataFrames in, getallen uit).
"""
import sys
import os
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import (
    bereken_positie_rendement,
    bereken_totaal_rendement,
    bereken_jaar_rendement,
    bereken_xirr,
    bereken_holdings_gak,
    bereken_jaren_overzicht,
    bereken_totale_transactiekosten,
    bereken_statistieken,
)


class TestPositieRendement(unittest.TestCase):
    def test_winst_20_procent(self):
        r = bereken_positie_rendement(gak=100, aantal=1, huidige_koers=120)
        self.assertAlmostEqual(r["rendement_pct"], 20.0)

    def test_verlies_20_procent(self):
        r = bereken_positie_rendement(gak=100, aantal=1, huidige_koers=80)
        self.assertAlmostEqual(r["rendement_pct"], -20.0)

    def test_geinvesteerd_en_waarde(self):
        r = bereken_positie_rendement(gak=50, aantal=4, huidige_koers=60)
        self.assertAlmostEqual(r["geinvesteerd"], 200.0)
        self.assertAlmostEqual(r["waarde"], 240.0)
        self.assertAlmostEqual(r["rendement_pct"], 20.0)


class TestTotaalRendement(unittest.TestCase):
    def test_gelijk_gebleven(self):
        r = bereken_totaal_rendement(geinvesteerd=1000, waarde=1000)
        self.assertAlmostEqual(r["rendement_eur"], 0.0)
        self.assertAlmostEqual(r["rendement_pct"], 0.0)

    def test_winst(self):
        r = bereken_totaal_rendement(geinvesteerd=1000, waarde=1250)
        self.assertAlmostEqual(r["rendement_eur"], 250.0)
        self.assertAlmostEqual(r["rendement_pct"], 25.0)

    def test_niets_geinvesteerd_geeft_geen_deling_door_nul(self):
        r = bereken_totaal_rendement(geinvesteerd=0, waarde=0)
        self.assertIsNone(r["rendement_pct"])


class TestJaarRendement(unittest.TestCase):
    def test_voorbeeld_uit_opdracht(self):
        # "2025 (318d, 87.1% of year): start €0.00 + invested €8980.40
        #  -> end €9779.60 | gain €799.20 (8.90%)"
        r = bereken_jaar_rendement(startwaarde=0.0, ingelegd=8980.40, eindwaarde=9779.60)
        self.assertAlmostEqual(r["winst_eur"], 799.20, places=2)
        self.assertAlmostEqual(r["winst_pct"], 8.90, places=2)

    def test_jaar_met_startwaarde(self):
        # start 1000, niets bijgestort, eind 1100 -> winst 100 (10%)
        r = bereken_jaar_rendement(startwaarde=1000.0, ingelegd=0.0, eindwaarde=1100.0)
        self.assertAlmostEqual(r["winst_eur"], 100.0)
        self.assertAlmostEqual(r["winst_pct"], 10.0)


class TestXirr(unittest.TestCase):
    def test_een_investering_een_jaar_later_10_procent(self):
        # €1000 op 1 jan 2023, waarde €1100 op 1 jan 2024 (exact 365 dagen,
        # 2023 is geen schrikkeljaar) -> XIRR moet precies 10% zijn.
        cashflows = [(date(2023, 1, 1), -1000.0), (date(2024, 1, 1), 1100.0)]
        r = bereken_xirr(cashflows)
        self.assertAlmostEqual(r, 0.10, places=4)

    def test_te_weinig_cashflows_geeft_none(self):
        self.assertIsNone(bereken_xirr([(date(2023, 1, 1), -1000.0)]))
        self.assertIsNone(bereken_xirr([]))


class TestHoldingsGak(unittest.TestCase):
    def _rij(self, datum, aantal, koers, totaal_eur, beurs="EAM"):
        return {
            "ticker": "X", "datum": pd.Timestamp(datum), "aantal": aantal,
            "koers": koers, "totaal_eur": totaal_eur, "beurs": beurs, "product": "X",
        }

    def test_simpele_aankoop(self):
        df = pd.DataFrame([self._rij("2023-01-01", 10.0, 10.0, -100.0)])
        result = bereken_holdings_gak(df)
        self.assertAlmostEqual(result["X"]["aantal"], 10.0)
        self.assertAlmostEqual(result["X"]["gak"], 10.0)

    def test_aankoop_verkoop_aankoop(self):
        # 10 stuks @ €10 (kost €100) -> verkoop 4 @ €20 (gak blijft €10,
        # kostenbasis -€40, resteert 6 stuks/€60) -> 10 stuks @ €30 (kost
        # €300) -> totaal 16 stuks, kostenbasis €360 -> GAK €22,50
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -4.0, 20.0, 80.0),
            self._rij("2023-09-01", 10.0, 30.0, -300.0),
        ])
        result = bereken_holdings_gak(df)
        self.assertAlmostEqual(result["X"]["aantal"], 16.0)
        self.assertAlmostEqual(result["X"]["gak"], 22.5)

    def test_volledig_verkochte_positie_niet_in_resultaat(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -10.0, 15.0, 150.0),
        ])
        result = bereken_holdings_gak(df)
        self.assertNotIn("X", result)

    def test_stocksplit_via_corporate_action_rij_verdunt_gak(self):
        # 10 stuks @ €10 (kost €100), dan een 2-voor-1 split: DEG-boekingsrij
        # boekt de 10 oude stuks weg (aantal=-10, totaal_eur=0 — geen
        # cashflow), de conversie-rij zet er 20 nieuwe stuks voor terug
        # (aantal=+20, totaal_eur=0). Resultaat: 20 stuks, kostenbasis
        # ongewijzigd (€100) -> GAK €5 (was €10, nu twee keer zoveel stukken
        # voor dezelfde kostenbasis).
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -10.0, 0.0, 0.0, beurs="DEG"),
            self._rij("2023-06-01", 20.0, 0.0, 0.0),
        ])
        result = bereken_holdings_gak(df)
        self.assertAlmostEqual(result["X"]["aantal"], 20.0)
        self.assertAlmostEqual(result["X"]["gak"], 5.0)


class TestJarenOverzicht(unittest.TestCase):
    def test_een_kalenderjaar_volledig(self):
        index = pd.date_range("2023-01-01", "2023-12-31", freq="D")
        resultaat = pd.DataFrame({
            "waarde": [0.0] * len(index),
            "geinvesteerd": [0.0] * len(index),
        }, index=index)
        resultaat.loc["2023-12-31", "waarde"] = 1100.0
        resultaat.loc["2023-12-31", "geinvesteerd"] = 1000.0
        jaren = bereken_jaren_overzicht(resultaat)
        self.assertEqual(len(jaren), 1)
        j = jaren[0]
        self.assertEqual(j["jaar"], 2023)
        self.assertAlmostEqual(j["startwaarde"], 0.0)
        self.assertAlmostEqual(j["ingelegd"], 1000.0)
        self.assertAlmostEqual(j["eindwaarde"], 1100.0)
        self.assertAlmostEqual(j["winst_eur"], 100.0)
        self.assertAlmostEqual(j["winst_pct"], 10.0)

    def test_leeg_resultaat_geeft_lege_lijst(self):
        self.assertEqual(bereken_jaren_overzicht(pd.DataFrame()), [])


class TestTotaleTransactiekosten(unittest.TestCase):
    """Dekt de bug waarbij de kolom 'Transactiekosten en/of kosten van
    derden EUR' wel in het Excel-bestand stond, maar nergens werd
    opgepikt/gesommeerd (zie opdracht 'transactiekosten worden niet
    gevonden')."""

    def test_som_van_bekende_kosten_als_positief_bedrag(self):
        # brondata is negatief (kosten worden afgeboekt), UI moet een
        # positief totaalbedrag tonen: -3, -1, -1 -> €5
        df = pd.DataFrame({"transactiekosten": [-3, -1, -1]})
        r = bereken_totale_transactiekosten(df)
        self.assertTrue(r["beschikbaar"])
        self.assertAlmostEqual(r["totaal"], 5.0)

    def test_kolom_ontbreekt_geeft_data_ontbreekt_zonder_crash(self):
        df = pd.DataFrame({"ticker": ["X", "Y"], "aantal": [1.0, 2.0]})
        r = bereken_totale_transactiekosten(df)
        self.assertFalse(r["beschikbaar"])
        self.assertIsNone(r["totaal"])

    def test_alleen_nan_waarden_geeft_data_ontbreekt_zonder_crash(self):
        df = pd.DataFrame({"transactiekosten": [None, None, float("nan")]})
        r = bereken_totale_transactiekosten(df)
        self.assertFalse(r["beschikbaar"])
        self.assertIsNone(r["totaal"])


class TestStatistiekenTransactiekosten(unittest.TestCase):
    """bereken_statistieken() levert precies dezelfde totalen-dict die zowel
    het Statistieken-tabblad als het totalenblok op Portfolio-home
    rechtstreeks renderen (zie app.js toonStatistieken/toonPortfolio) — deze
    tests bevestigen dat die ene bron van waarheid het kosten-veld correct
    doorgeeft, zodat de twee weergaven niet uit elkaar kunnen lopen."""

    def _transacties(self, met_kosten):
        rijen = [
            {"ticker": "X", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
             "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "X"},
            {"ticker": "X", "datum": pd.Timestamp("2023-02-01"), "aantal": 5.0,
             "koers": 12.0, "totaal_eur": -60.0, "beurs": "EAM", "product": "X"},
        ]
        if met_kosten:
            rijen[0]["transactiekosten"] = -3.0
            rijen[1]["transactiekosten"] = -1.0
        return pd.DataFrame(rijen)

    def _resultaat(self):
        return pd.DataFrame(
            {"waarde": [180.0], "geinvesteerd": [160.0]},
            index=[pd.Timestamp("2023-06-01")],
        )

    def _price_data(self):
        return pd.DataFrame({"X": [12.0]}, index=[pd.Timestamp("2023-06-01")])

    def test_totalen_bevat_correct_kostenbedrag(self):
        stats = bereken_statistieken(self._transacties(met_kosten=True), self._price_data(), self._resultaat())
        self.assertTrue(stats["totalen"]["transactiekosten_beschikbaar"])
        self.assertAlmostEqual(stats["totalen"]["totale_transactiekosten"], 4.0)

    def test_totalen_zonder_kostenkolom_geeft_data_ontbreekt(self):
        stats = bereken_statistieken(self._transacties(met_kosten=False), self._price_data(), self._resultaat())
        self.assertFalse(stats["totalen"]["transactiekosten_beschikbaar"])
        self.assertIsNone(stats["totalen"]["totale_transactiekosten"])

    def test_totalen_komt_overeen_met_losse_kostenberekening(self):
        # Portfolio-home en Statistieken lezen letterlijk hetzelfde
        # totalen-object (huidigeData.statistieken.totalen) — dit borgt dat
        # dat object zelf consistent is met bereken_totale_transactiekosten().
        transacties_df = self._transacties(met_kosten=True)
        stats = bereken_statistieken(transacties_df, self._price_data(), self._resultaat())
        losse_berekening = bereken_totale_transactiekosten(transacties_df)
        self.assertEqual(stats["totalen"]["totale_transactiekosten"], losse_berekening["totaal"])
        self.assertEqual(stats["totalen"]["transactiekosten_beschikbaar"], losse_berekening["beschikbaar"])


if __name__ == "__main__":
    unittest.main()
