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
    bereken_holdings_en_gesloten,
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


class TestHoldingsEnGesloten(unittest.TestCase):
    """bereken_holdings_en_gesloten() -- zelfde GAK-methode als
    bereken_holdings_gak(), maar houdt nu ook volledig verkochte posities
    bij (Feature 'verkochte posities' op het Statistieken-tabblad)."""

    def _rij(self, datum, aantal, koers, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "koers": koers, "totaal_eur": totaal_eur, "beurs": beurs, "product": ticker,
        }

    def test_volledig_verkocht_komt_in_gesloten_niet_in_open(self):
        # 10 stuks @ €10 (kost €100), volledig verkocht @ €15 (opbrengst €150)
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -10.0, 15.0, 150.0),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertNotIn("X", open_posities)
        self.assertIn("X", gesloten)
        self.assertAlmostEqual(gesloten["X"]["aantal"], 10.0)
        self.assertAlmostEqual(gesloten["X"]["gemiddelde_aankoopkoers"], 10.0)
        self.assertAlmostEqual(gesloten["X"]["gemiddelde_verkoopkoers"], 15.0)
        self.assertAlmostEqual(gesloten["X"]["gerealiseerd_eur"], 50.0)

    def test_deels_verkocht_alleen_in_open_matcht_bereken_holdings_gak(self):
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -4.0, 20.0, 80.0),
            self._rij("2023-09-01", 10.0, 30.0, -300.0),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertNotIn("X", gesloten)
        self.assertIn("X", open_posities)
        oud_resultaat = bereken_holdings_gak(df)
        self.assertAlmostEqual(open_posities["X"]["aantal"], oud_resultaat["X"]["aantal"])
        self.assertAlmostEqual(open_posities["X"]["gak"], oud_resultaat["X"]["gak"])

    def test_split_voor_volledige_verkoop_aankoopkoers_klopt(self):
        # 10 stuks @ €10 (kost €100), dan 2-voor-1 split (DEG-boekingsrij +
        # conversie-rij, allebei totaal_eur=0 -- geen echte cashflow), dan
        # alle 20 stuks verkocht voor in totaal €200. De split-rijen mogen
        # de gemiddelde aankoopkoers niet vertekenen (blijft €100/10=€10),
        # ook al waren er op het verkoopmoment 20 stuks.
        df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, 10.0, -100.0),
            self._rij("2023-06-01", -10.0, 0.0, 0.0, beurs="DEG"),
            self._rij("2023-06-01", 20.0, 0.0, 0.0),
            self._rij("2023-09-01", -20.0, 10.0, 200.0),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertNotIn("X", open_posities)
        self.assertIn("X", gesloten)
        self.assertAlmostEqual(gesloten["X"]["gemiddelde_aankoopkoers"], 10.0)
        self.assertAlmostEqual(gesloten["X"]["gemiddelde_verkoopkoers"], 10.0)
        self.assertAlmostEqual(gesloten["X"]["gerealiseerd_eur"], 100.0)

    def test_alleen_corporate_action_geen_echte_koop_verkoop_komt_in_geen_van_beide(self):
        # Alleen een split-boekingspaar, geen enkele rij met een echte
        # cashflow -- mag geen ruis opleveren in open_posities of gesloten.
        df = pd.DataFrame([
            self._rij("2023-06-01", -5.0, 0.0, 0.0, ticker="Y", beurs="DEG"),
            self._rij("2023-06-01", 5.0, 0.0, 0.0, ticker="Y"),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertNotIn("Y", open_posities)
        self.assertNotIn("Y", gesloten)


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

    def test_eerste_jaar_medio_jaar_gestart_dagen_verstreken_niet_365(self):
        # Eerst gekocht op 1 maart 2023, data loopt door tot in 2024 -> het
        # eerste jaar (2023) mag alleen de dagen vanaf 1 maart tellen, niet
        # het hele jaar (was de bug: alleen het LAATSTE jaar in de data werd
        # als "onvolledig" behandeld, niet het eerste).
        index = pd.date_range("2023-03-01", "2024-06-30", freq="D")
        resultaat = pd.DataFrame({
            "waarde": [0.0] * len(index),
            "geinvesteerd": [0.0] * len(index),
        }, index=index)
        resultaat.loc["2023-12-31":, "waarde"] = 1100.0
        resultaat.loc["2023-12-31":, "geinvesteerd"] = 1000.0
        jaren = bereken_jaren_overzicht(resultaat, eerste_datum="2023-03-01")
        j2023 = next(j for j in jaren if j["jaar"] == 2023)
        verwachte_dagen = (pd.Timestamp("2023-12-31") - pd.Timestamp("2023-03-01")).days + 1
        self.assertEqual(j2023["dagen_verstreken"], verwachte_dagen)
        self.assertNotEqual(j2023["dagen_verstreken"], 365)

    def test_nog_geen_jaar_oud_dagen_verstreken_klopt_binnen_een_kalenderjaar(self):
        # Alle data valt binnen hetzelfde kalenderjaar (net begonnen met
        # beleggen) -> dagen_verstreken moet (laatste - eerste).days + 1
        # zijn, niet het volledige jaar van 365 dagen.
        index = pd.date_range("2024-05-01", "2024-08-15", freq="D")
        resultaat = pd.DataFrame({
            "waarde": [0.0] * len(index),
            "geinvesteerd": [0.0] * len(index),
        }, index=index)
        resultaat.loc["2024-08-15", "waarde"] = 550.0
        resultaat.loc["2024-08-15", "geinvesteerd"] = 500.0
        jaren = bereken_jaren_overzicht(resultaat, eerste_datum="2024-05-01")
        self.assertEqual(len(jaren), 1)
        j = jaren[0]
        verwachte_dagen = (pd.Timestamp("2024-08-15") - pd.Timestamp("2024-05-01")).days + 1
        self.assertEqual(j["dagen_verstreken"], verwachte_dagen)
        self.assertNotEqual(j["dagen_verstreken"], 365)

    def test_meerjaren_tussenjaar_blijft_volledig(self):
        # Regressie: begonnen medio 2022, data loopt door tot medio 2024 ->
        # het VOLLEDIGE tussenliggende jaar (2023) moet nog steeds 100%/365
        # dagen tonen, ook nu het eerste jaar wél begrensd wordt.
        index = pd.date_range("2022-06-01", "2024-06-30", freq="D")
        resultaat = pd.DataFrame({
            "waarde": [0.0] * len(index),
            "geinvesteerd": [0.0] * len(index),
        }, index=index)
        resultaat["waarde"] = 1000.0
        resultaat["geinvesteerd"] = 900.0
        jaren = bereken_jaren_overzicht(resultaat, eerste_datum="2022-06-01")
        j2023 = next(j for j in jaren if j["jaar"] == 2023)
        self.assertEqual(j2023["dagen_verstreken"], 365)
        self.assertAlmostEqual(j2023["pct_van_jaar"], 100.0)

    def test_winst_berekening_ongewijzigd_door_eerste_datum_begrenzing(self):
        # Puur regressie tegen het per ongeluk stukmaken van de al-correcte
        # euro-berekening (zie "voorbeeld uit opdracht" in TestJaarRendement)
        # terwijl dagen_verstreken/pct_van_jaar wordt gefixed.
        index = pd.date_range("2025-02-16", "2025-12-31", freq="D")
        resultaat = pd.DataFrame({
            "waarde": [0.0] * len(index),
            "geinvesteerd": [0.0] * len(index),
        }, index=index)
        resultaat.loc["2025-12-31", "waarde"] = 9779.60
        resultaat.loc["2025-12-31", "geinvesteerd"] = 8980.40
        jaren = bereken_jaren_overzicht(resultaat, eerste_datum="2025-02-16")
        j = jaren[0]
        self.assertAlmostEqual(j["winst_eur"], 799.20, places=2)
        self.assertAlmostEqual(j["winst_pct"], 8.90, places=2)


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


class TestAllTimeHigh(unittest.TestCase):
    """Regressietest voor de bug waarbij all-time-high op portefeuillewaarde
    werd bepaald i.p.v. op rendement -- een hoge waarde vlak na een grote
    storting hoeft geen hoog rendement te zijn (bv. veel ingelegd vlak vóór
    het hoogste-waarde-punt, waardoor het rendement daar lager is dan op een
    eerder punt met een kleinere inleg)."""

    def test_ath_volgt_rendement_niet_waarde(self):
        resultaat = pd.DataFrame(
            {
                # dag 1: kleine inleg (200), groot rendement (800)
                # dag 2: grote inleg vlak ervoor (1400), hogere waarde (1500)
                #        maar lager rendement (100)
                "waarde": [1000.0, 1500.0],
                "geinvesteerd": [200.0, 1400.0],
            },
            index=[pd.Timestamp("2023-01-01"), pd.Timestamp("2023-06-01")],
        )
        resultaat["rendement"] = resultaat["waarde"] - resultaat["geinvesteerd"]

        transacties_df = pd.DataFrame([{
            "ticker": "X", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
            "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "X",
        }])
        price_data = pd.DataFrame({"X": [10.0, 10.0]}, index=resultaat.index)

        stats = bereken_statistieken(transacties_df, price_data, resultaat)
        ath = stats["totalen"]["all_time_high"]
        self.assertAlmostEqual(ath["waarde"], 800.0)
        self.assertEqual(ath["datum"], "2023-01-01")


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
        # 'rendement' hoort er altijd bij (compute_value_over_time() zet 'm),
        # bereken_statistieken() leest deze kolom voor all_time_high.
        return pd.DataFrame(
            {"waarde": [180.0], "geinvesteerd": [160.0], "rendement": [20.0]},
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


class TestDividendEnGeslotenPositiesInStatistieken(unittest.TestCase):
    """Feature: dividend per positie + verkochte posities op het
    Statistieken-tabblad. bereken_statistieken() krijgt dividend_per_ticker
    en ticker_namen als optionele extra input aangeleverd door
    analyze_transacties() (app.py) -- deze functie zelf blijft DB-vrij."""

    def _transacties(self):
        return pd.DataFrame([
            # X: nog open, 10 stuks in bezit
            {"ticker": "X", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
             "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "X"},
            # Y: volledig gekocht en verkocht -> gesloten positie
            {"ticker": "Y", "datum": pd.Timestamp("2023-02-01"), "aantal": 5.0,
             "koers": 20.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "Y"},
            {"ticker": "Y", "datum": pd.Timestamp("2023-03-01"), "aantal": -5.0,
             "koers": 24.0, "totaal_eur": 120.0, "beurs": "EAM", "product": "Y"},
        ])

    def _resultaat(self):
        return pd.DataFrame(
            {"waarde": [120.0], "geinvesteerd": [100.0], "rendement": [20.0]},
            index=[pd.Timestamp("2023-06-01")],
        )

    def _price_data(self):
        return pd.DataFrame({"X": [12.0]}, index=[pd.Timestamp("2023-06-01")])

    def test_open_positie_krijgt_dividend_ontvangen_veld(self):
        stats = bereken_statistieken(
            self._transacties(), self._price_data(), self._resultaat(),
            dividend_per_ticker={"X": 15.0, "Y": 5.0},
        )
        x_positie = next(p for p in stats["posities"] if p["ticker"] == "X")
        self.assertAlmostEqual(x_positie["dividend_ontvangen"], 15.0)

    def test_verkochte_positie_verschijnt_niet_bij_open_posities(self):
        stats = bereken_statistieken(
            self._transacties(), self._price_data(), self._resultaat(),
            dividend_per_ticker={"X": 15.0, "Y": 5.0},
        )
        self.assertNotIn("Y", [p["ticker"] for p in stats["posities"]])

    def test_geen_dividend_per_ticker_geeft_nul_zonder_crash(self):
        stats = bereken_statistieken(self._transacties(), self._price_data(), self._resultaat())
        x_positie = next(p for p in stats["posities"] if p["ticker"] == "X")
        self.assertAlmostEqual(x_positie["dividend_ontvangen"], 0.0)

    def test_gesloten_positie_heeft_los_koersrendement_en_dividend(self):
        stats = bereken_statistieken(
            self._transacties(), self._price_data(), self._resultaat(),
            dividend_per_ticker={"X": 15.0, "Y": 5.0},
            ticker_namen={"X": "Aandeel X", "Y": "Aandeel Y"},
        )
        y_positie = next(p for p in stats["gesloten_posities"] if p["ticker"] == "Y")
        self.assertEqual(y_positie["naam"], "Aandeel Y")
        self.assertAlmostEqual(y_positie["gemiddelde_aankoopkoers"], 20.0)
        self.assertAlmostEqual(y_positie["gemiddelde_verkoopkoers"], 24.0)
        # koersrendement: (120-100)/100 * 100 = 20% -- los van het
        # ontvangen dividend, bewust niet samengevoegd tot 1 percentage.
        self.assertAlmostEqual(y_positie["rendement_pct"], 20.0)
        self.assertAlmostEqual(y_positie["dividend_ontvangen"], 5.0)

    def test_gesloten_positie_heeft_rendement_eur_gelijk_aan_gerealiseerd(self):
        # Y: gekocht voor 100, verkocht voor 120 -> gerealiseerd/rendement_eur = 20.
        stats = bereken_statistieken(
            self._transacties(), self._price_data(), self._resultaat(),
        )
        y_positie = next(p for p in stats["gesloten_posities"] if p["ticker"] == "Y")
        self.assertAlmostEqual(y_positie["rendement_eur"], 20.0)


class TestRendementEurInPosities(unittest.TestCase):
    """rendement_eur op een open positie moet gelijk zijn aan huidige_waarde
    - geinvesteerd, zowel bij winst als bij verlies (teken/afronding)."""

    def _transacties(self):
        return pd.DataFrame([
            # WIN: 10 stuks gekocht a 10, nu 12 -> winst
            {"ticker": "WIN", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
             "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "WIN"},
            # VERLIES: 10 stuks gekocht a 10, nu 7 -> verlies
            {"ticker": "VERLIES", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
             "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "VERLIES"},
        ])

    def _resultaat(self):
        return pd.DataFrame(
            {"waarde": [190.0], "geinvesteerd": [200.0], "rendement": [-10.0]},
            index=[pd.Timestamp("2023-06-01")],
        )

    def _price_data(self):
        return pd.DataFrame(
            {"WIN": [12.0], "VERLIES": [7.0]}, index=[pd.Timestamp("2023-06-01")],
        )

    def test_winst_positie(self):
        stats = bereken_statistieken(self._transacties(), self._price_data(), self._resultaat())
        win = next(p for p in stats["posities"] if p["ticker"] == "WIN")
        self.assertAlmostEqual(win["rendement_eur"], win["huidige_waarde"] - win["geinvesteerd"])
        self.assertAlmostEqual(win["rendement_eur"], 20.0)

    def test_verlies_positie(self):
        stats = bereken_statistieken(self._transacties(), self._price_data(), self._resultaat())
        verlies = next(p for p in stats["posities"] if p["ticker"] == "VERLIES")
        self.assertAlmostEqual(verlies["rendement_eur"], verlies["huidige_waarde"] - verlies["geinvesteerd"])
        self.assertAlmostEqual(verlies["rendement_eur"], -30.0)


if __name__ == "__main__":
    unittest.main()
