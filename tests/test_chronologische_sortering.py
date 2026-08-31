"""
Unit tests voor de 'verkoopkoers onbekend bij same-day transacties'-bug.

Oorzaak: de 'transacties'-tabel sloeg alleen een DATE op, geen tijdstip.
Bij een koop en verkoop op dezelfde kalenderdag (bv. een beurswissel) kon
de fetch-volgorde uit de database (geen ORDER BY) de verkooprij vóór de
koop plaatsen -- bereken_holdings_en_gesloten() verwerkte de verkoop dan
terwijl er nog geen aandelen 'in bezit' waren volgens de lopende telling,
waardoor de verkoopopbrengst stilzwijgend werd genegeerd (gemiddelde_
verkoopkoers=None, rendement_pct=-100%).

Fix: een 'tijd'-kolom (TIME) erbij, en overal waar transacties chronologisch
verwerkt worden een gecombineerde datum+tijd-sortering via
analysis._sorteer_chronologisch() i.p.v. losse sort_values("datum").

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import (
    bereken_holdings_en_gesloten,
    bereken_holdings_gak,
    compute_value_over_time,
    compute_per_ticker,
    _sorteer_chronologisch,
)


class TestSorteerChronologisch(unittest.TestCase):
    def test_sorteert_op_tijd_binnen_dezelfde_dag(self):
        df = pd.DataFrame([
            {"datum": pd.Timestamp("2026-06-17"), "tijd": "13:42", "label": "derde"},
            {"datum": pd.Timestamp("2026-06-17"), "tijd": "13:39", "label": "eerste"},
            {"datum": pd.Timestamp("2026-06-17"), "tijd": "13:41", "label": "tweede"},
        ])
        gesorteerd = _sorteer_chronologisch(df)
        self.assertEqual(list(gesorteerd["label"]), ["eerste", "tweede", "derde"])

    def test_respecteert_nog_steeds_de_datum_over_meerdere_dagen(self):
        df = pd.DataFrame([
            {"datum": pd.Timestamp("2023-09-01"), "tijd": "08:00", "label": "laatste"},
            {"datum": pd.Timestamp("2023-01-01"), "tijd": "23:59", "label": "eerste"},
            {"datum": pd.Timestamp("2023-06-01"), "tijd": "00:01", "label": "middelste"},
        ])
        gesorteerd = _sorteer_chronologisch(df)
        self.assertEqual(list(gesorteerd["label"]), ["eerste", "middelste", "laatste"])

    def test_ontbrekende_tijd_kolom_valt_terug_op_datum_only(self):
        df = pd.DataFrame([
            {"datum": pd.Timestamp("2023-06-01"), "label": "tweede"},
            {"datum": pd.Timestamp("2023-01-01"), "label": "eerste"},
        ])
        gesorteerd = _sorteer_chronologisch(df)
        self.assertEqual(list(gesorteerd["label"]), ["eerste", "tweede"])

    def test_tijd_is_none_valt_terug_op_middernacht_en_behoudt_invoervolgorde(self):
        df = pd.DataFrame([
            {"datum": pd.Timestamp("2026-06-17"), "tijd": None, "label": "als_eerste_aangeleverd"},
            {"datum": pd.Timestamp("2026-06-17"), "tijd": None, "label": "als_tweede_aangeleverd"},
        ])
        gesorteerd = _sorteer_chronologisch(df)
        self.assertEqual(list(gesorteerd["label"]), ["als_eerste_aangeleverd", "als_tweede_aangeleverd"])


class TestHoldingsEnGeslotenSameDayVolgorde(unittest.TestCase):
    """Reproductie van het exacte productiescenario (ISIN IE00BKM4GZ66,
    beurswissel TDG->EAM op 17-06-2026), zie opdracht 'verkoopkoers
    onbekend bij same-day transacties'."""

    def _rij(self, tijd, aantal, totaal_eur, ticker, beurs, datum="2026-06-17"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "tijd": tijd,
            "aantal": aantal, "koers": 0.0, "totaal_eur": totaal_eur,
            "beurs": beurs, "product": ticker,
        }

    def test_reproductie_verkoop_voor_koop_in_db_fetch_volgorde(self):
        # Precies het productiescenario: koop 13:39, verkoop 13:41 (beide
        # IS3N.DE/TDG), koop 13:42 (EMIM.AS/EAM, na de beurswissel) --
        # AANGELEVERD in de volgorde die de database zonder ORDER BY
        # teruggaf (verkoop vóór koop).
        df = pd.DataFrame([
            self._rij("13:41", -12.0, 585.81, "IS3N.DE", "TDG"),
            self._rij("13:39", 12.0, -588.08, "IS3N.DE", "TDG"),
            self._rij("13:42", 12.0, -589.62, "EMIM.AS", "EAM"),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)

        self.assertIn("IS3N.DE", gesloten)
        self.assertIsNotNone(gesloten["IS3N.DE"]["gemiddelde_verkoopkoers"])
        self.assertAlmostEqual(gesloten["IS3N.DE"]["gemiddelde_aankoopkoers"], 588.08 / 12, places=4)
        self.assertAlmostEqual(gesloten["IS3N.DE"]["gemiddelde_verkoopkoers"], 585.81 / 12, places=4)

        rendement_pct = gesloten["IS3N.DE"]["gerealiseerd_eur"] / 588.08 * 100
        self.assertAlmostEqual(rendement_pct, (585.81 - 588.08) / 588.08 * 100, places=4)
        # Klein verlies (~-0.4%), zeker geen -100% -- dat zou betekenen dat
        # de verkoopopbrengst helemaal niet is meegeteld (de oorspronkelijke bug).
        self.assertGreater(rendement_pct, -5.0)

        self.assertIn("EMIM.AS", open_posities)
        self.assertAlmostEqual(open_posities["EMIM.AS"]["aantal"], 12.0)

    def test_ook_correct_als_de_rijen_toevallig_al_chronologisch_binnenkomen(self):
        df = pd.DataFrame([
            self._rij("13:39", 12.0, -588.08, "IS3N.DE", "TDG"),
            self._rij("13:41", -12.0, 585.81, "IS3N.DE", "TDG"),
        ])
        _open, gesloten = bereken_holdings_en_gesloten(df)
        self.assertAlmostEqual(gesloten["IS3N.DE"]["gemiddelde_verkoopkoers"], 585.81 / 12, places=4)

    def test_zonder_tijd_geen_crash_en_niet_slechter_dan_voorheen(self):
        # Backfill-overgangsperiode: oudere rijen hebben tijd=None. Zonder
        # tijdinformatie kan de volgorde niet ECHT gecorrigeerd worden --
        # dit mag niet crashen, en mag niet slechter zijn dan de oude
        # datum-only sortering (die had dit probleem al net zo goed).
        df = pd.DataFrame([
            self._rij(None, -12.0, 585.81, "IS3N.DE", "TDG"),
            self._rij(None, 12.0, -588.08, "IS3N.DE", "TDG"),
        ])
        try:
            _open, gesloten = bereken_holdings_en_gesloten(df)
        except Exception as e:
            self.fail(f"bereken_holdings_en_gesloten() crashte zonder tijd: {e}")
        self.assertIn("IS3N.DE", gesloten)
        # Bekende, geaccepteerde beperking tijdens de backfill-overgang: zonder
        # tijd kan de verkoop nog steeds vóór de koop verwerkt worden (identiek
        # aan het gedrag vóór deze fix) -- geen crash, geen erger resultaat.
        self.assertIsNone(gesloten["IS3N.DE"]["gemiddelde_verkoopkoers"])

    def test_bestaande_verschillende_dagen_scenario_blijft_ongewijzigd(self):
        # Regressie tegen bereken_holdings_gak's al-geteste 'aankoop-verkoop-
        # aankoop'-scenario (test_rendement.py), nu met een tijd-kolom erbij.
        df = pd.DataFrame([
            self._rij("10:00", 10.0, -100.0, "X", "EAM", datum="2023-01-01"),
            self._rij("10:00", -4.0, 80.0, "X", "EAM", datum="2023-06-01"),
            self._rij("10:00", 10.0, -300.0, "X", "EAM", datum="2023-09-01"),
        ])
        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertNotIn("X", gesloten)
        self.assertAlmostEqual(open_posities["X"]["aantal"], 16.0)
        self.assertAlmostEqual(open_posities["X"]["gak"], 22.5)

    def test_geen_tijd_kolom_gedraagt_zich_zoals_de_oude_bereken_holdings_gak(self):
        # bereken_holdings_gak's bestaande fixtures (zonder 'tijd'-kolom,
        # verschillende dagen) moeten identiek resultaat blijven geven.
        rijen = [
            {"ticker": "X", "datum": pd.Timestamp("2023-01-01"), "aantal": 10.0,
             "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "X"},
            {"ticker": "X", "datum": pd.Timestamp("2023-06-01"), "aantal": -4.0,
             "koers": 20.0, "totaal_eur": 80.0, "beurs": "EAM", "product": "X"},
            {"ticker": "X", "datum": pd.Timestamp("2023-09-01"), "aantal": 10.0,
             "koers": 30.0, "totaal_eur": -300.0, "beurs": "EAM", "product": "X"},
        ]
        df = pd.DataFrame(rijen)
        self.assertNotIn("tijd", df.columns)
        result = bereken_holdings_gak(df)
        self.assertAlmostEqual(result["X"]["aantal"], 16.0)
        self.assertAlmostEqual(result["X"]["gak"], 22.5)


class TestComputeValueOverTimeSameDayVolgorde(unittest.TestCase):
    """compute_value_over_time werkt per kalenderdag (batcht alle transacties
    van die dag samen vóór de eindstand van die dag te bepalen), dus is in
    de praktijk niet gevoelig voor de rij-volgorde binnen 1 dag (optellen is
    commutatief). Toch als regressie afgedekt: dezelfde chronologische
    sortering wordt ook hier toegepast (zie _sorteer_chronologisch), voor
    consistentie met bereken_holdings_en_gesloten()."""

    def test_same_day_koop_en_verkoop_in_omgekeerde_volgorde_geeft_correcte_eindstand(self):
        df = pd.DataFrame([
            {"ticker": "IS3N.DE", "datum": pd.Timestamp("2026-06-17"), "tijd": "13:41",
             "aantal": -12.0, "adj_aantal": -12.0, "totaal_eur": 585.81, "beurs": "TDG", "product": "IS3N.DE"},
            {"ticker": "IS3N.DE", "datum": pd.Timestamp("2026-06-17"), "tijd": "13:39",
             "aantal": 12.0, "adj_aantal": 12.0, "totaal_eur": -588.08, "beurs": "TDG", "product": "IS3N.DE"},
        ])
        price_data = pd.DataFrame({"IS3N.DE": [50.0]}, index=[pd.Timestamp("2026-06-17")])
        resultaat = compute_value_over_time(df, price_data)
        # 12 gekocht, 12 verkocht -> 0 in bezit -> waarde 0. Netto ingelegd:
        # 588.08 betaald - 585.81 ontvangen = 2.27.
        self.assertAlmostEqual(resultaat["waarde"].iloc[-1], 0.0)
        self.assertAlmostEqual(resultaat["geinvesteerd"].iloc[-1], 2.27, places=2)


class TestComputePerTickerSameDayVolgorde(unittest.TestCase):
    def test_same_day_koop_en_verkoop_in_omgekeerde_volgorde_geeft_correcte_eindstand(self):
        df = pd.DataFrame([
            {"ticker": "IS3N.DE", "datum": pd.Timestamp("2026-06-17"), "tijd": "13:41",
             "aantal": -12.0, "adj_aantal": -12.0, "totaal_eur": 585.81, "beurs": "TDG", "product": "IS3N.DE"},
            {"ticker": "IS3N.DE", "datum": pd.Timestamp("2026-06-17"), "tijd": "13:39",
             "aantal": 12.0, "adj_aantal": 12.0, "totaal_eur": -588.08, "beurs": "TDG", "product": "IS3N.DE"},
        ])
        price_data = pd.DataFrame({"IS3N.DE": [50.0]}, index=[pd.Timestamp("2026-06-17")])
        result = compute_per_ticker(df, price_data)
        self.assertIn("IS3N.DE", result)
        self.assertAlmostEqual(result["IS3N.DE"]["geinvesteerd"][-1], 2.27, places=2)


if __name__ == "__main__":
    unittest.main()
