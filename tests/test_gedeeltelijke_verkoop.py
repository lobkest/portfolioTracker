"""
Unit tests voor gedeeltelijke verkopen: het "deels_verkocht"-veld op
bereken_holdings_en_gesloten() en de evenredige daling van "geinvesteerd"
in compute_per_ticker() (analysis.py). Zie instructiedocument
"gedeeltelijke verkopen correct verwerken".

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bereken_holdings_en_gesloten, compute_per_ticker, bereken_statistieken


def _rij(datum, aantal, totaal_eur, ticker="TEST", koers=0.0, beurs="EAM"):
    return {
        "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
        "adj_aantal": aantal, "koers": koers, "totaal_eur": totaal_eur,
        "beurs": beurs, "product": ticker,
    }


class TestDeelsVerkocht(unittest.TestCase):
    """bereken_holdings_en_gesloten(): een positie die nog (deels) open
    staat, maar onderweg wel verkocht is, moet dat gerealiseerde resultaat
    meegeven via het 'deels_verkocht'-veld i.p.v. het weg te gooien."""

    def test_deels_verkocht_verschijnt_bij_open_positie(self):
        # Koop 10 @ 10 (kosten 100), verkoop 5 @ 12 (opbrengst 60).
        # Restpositie: aantal 5, gak 10 (kostenbasis 50).
        # Verkocht: aantal 5, gerealiseerd 5*(12-10)=10.
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-06-01", -5.0, 60.0),
        ])
        open_posities, gesloten_posities = bereken_holdings_en_gesloten(df)

        self.assertEqual(gesloten_posities, {})  # positie staat nog open
        self.assertAlmostEqual(open_posities["TEST"]["aantal"], 5.0)
        self.assertAlmostEqual(open_posities["TEST"]["gak"], 10.0)
        deels = open_posities["TEST"]["deels_verkocht"]
        self.assertAlmostEqual(deels["aantal"], 5.0)
        self.assertAlmostEqual(deels["gemiddelde_aankoopkoers"], 10.0)
        self.assertAlmostEqual(deels["gemiddelde_verkoopkoers"], 12.0)
        self.assertAlmostEqual(deels["gerealiseerd_eur"], 10.0)

    def test_meerdere_deelverkopen_cumuleren(self):
        # Twee losse gedeeltelijke verkopen op dezelfde ticker moeten optellen.
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-03-01", -2.0, 24.0),   # @12
            _rij("2024-06-01", -3.0, 45.0),   # @15
        ])
        open_posities, _ = bereken_holdings_en_gesloten(df)
        deels = open_posities["TEST"]["deels_verkocht"]
        self.assertAlmostEqual(deels["aantal"], 5.0)
        self.assertAlmostEqual(deels["gemiddelde_verkoopkoers"], 69.0 / 5)
        self.assertAlmostEqual(deels["gerealiseerd_eur"], 69.0 - 50.0)  # kostenbasis 5*10=50

    def test_volledige_verkoop_ongewijzigd_regressie(self):
        # Bestaand pad: volledig verkocht -> gesloten_posities, geen deels_verkocht.
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-06-01", -10.0, 130.0),
        ])
        open_posities, gesloten_posities = bereken_holdings_en_gesloten(df)
        self.assertEqual(open_posities, {})
        self.assertAlmostEqual(gesloten_posities["TEST"]["aantal"], 10.0)
        self.assertAlmostEqual(gesloten_posities["TEST"]["gerealiseerd_eur"], 30.0)

    def test_corporate_action_raakt_kostenbasis_niet(self):
        # Split-conversierij (totaal_eur=0) mag geen invloed hebben op
        # totaal_verkochte_kostenbasis, ook niet bij een open restpositie.
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-03-01", -10.0, 0.0, beurs="DEG"),
            _rij("2024-03-01", 20.0, 0.0),
            _rij("2024-06-01", -5.0, 60.0),
        ])
        open_posities, _ = bereken_holdings_en_gesloten(df)
        # 20 stuks na split, gak blijft 100/20=5, restant 15 stuks -> kostenbasis 75
        self.assertAlmostEqual(open_posities["TEST"]["aantal"], 15.0)
        self.assertAlmostEqual(open_posities["TEST"]["gak"], 5.0)
        deels = open_posities["TEST"]["deels_verkocht"]
        self.assertAlmostEqual(deels["aantal"], 5.0)
        self.assertAlmostEqual(deels["gemiddelde_aankoopkoers"], 5.0)
        self.assertAlmostEqual(deels["gerealiseerd_eur"], 60.0 - 25.0)


class TestComputePerTickerGeinvesteerdBijDeelverkoop(unittest.TestCase):
    def test_geinvesteerd_evenredig_bij_deelverkoop(self):
        # Zelfde scenario als het rekenvoorbeeld: na verkoop van de helft
        # van de stukken moet 'geinvesteerd' evenredig dalen (naar 50),
        # niet naar (bijna) 0 zoals bij de oude cashflow-gebaseerde
        # berekening.
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-06-01", -5.0, 60.0),
        ])
        price_data = pd.DataFrame(
            {"TEST": 12.0}, index=pd.date_range("2024-01-01", "2024-06-01", freq="D"),
        )
        result = compute_per_ticker(df, price_data)
        self.assertAlmostEqual(result["TEST"]["geinvesteerd"][-1], 50.0)
        self.assertAlmostEqual(result["TEST"]["waarde"][-1], 60.0)  # 5 stukken * 12
        self.assertTrue(result["TEST"]["nog_in_bezit"])

    def test_volledige_verkoop_geinvesteerd_naar_nul(self):
        # Na een volledige verkoop moeten zowel waarde als geinvesteerd naar
        # 0 zakken (nieuw, gewenst gedrag -- voorheen bleef geinvesteerd
        # hangen op het gerealiseerde-winstniveau).
        df = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0),
            _rij("2024-06-01", -10.0, 130.0),
        ])
        price_data = pd.DataFrame(
            {"TEST": 13.0}, index=pd.date_range("2024-01-01", "2024-06-02", freq="D"),
        )
        result = compute_per_ticker(df, price_data)
        self.assertAlmostEqual(result["TEST"]["geinvesteerd"][-1], 0.0)
        self.assertAlmostEqual(result["TEST"]["waarde"][-1], 0.0)
        self.assertFalse(result["TEST"]["nog_in_bezit"])


class TestStatistiekenDeelsVerkocht(unittest.TestCase):
    """bereken_statistieken(): een deels-verkochte, nog open positie moet
    ook als aparte entry in gesloten_posities verschijnen, met
    nog_in_bezit=True en het juiste resterend_aantal, naast de normale
    entry in posities (open posities)."""

    def _transacties(self):
        return pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0, koers=10.0),
            _rij("2024-06-01", -5.0, 60.0, koers=12.0),
        ])

    def _resultaat(self):
        return pd.DataFrame(
            {"waarde": [60.0], "geinvesteerd": [50.0], "rendement": [10.0]},
            index=[pd.Timestamp("2024-12-01")],
        )

    def _price_data(self):
        return pd.DataFrame({"TEST": [12.0]}, index=[pd.Timestamp("2024-12-01")])

    def test_open_positie_heeft_verlaagd_aantal_en_gak(self):
        stats = bereken_statistieken(self._transacties(), self._price_data(), self._resultaat())
        positie = next(p for p in stats["posities"] if p["ticker"] == "TEST")
        self.assertAlmostEqual(positie["aantal"], 5.0)
        self.assertAlmostEqual(positie["gak"], 10.0)

    def test_deels_verkocht_verschijnt_in_gesloten_posities_met_nog_in_bezit(self):
        stats = bereken_statistieken(self._transacties(), self._price_data(), self._resultaat())
        entry = next(p for p in stats["gesloten_posities"] if p["ticker"] == "TEST")
        self.assertTrue(entry["nog_in_bezit"])
        self.assertAlmostEqual(entry["aantal"], 5.0)
        self.assertAlmostEqual(entry["resterend_aantal"], 5.0)
        self.assertAlmostEqual(entry["gemiddelde_aankoopkoers"], 10.0)
        self.assertAlmostEqual(entry["gemiddelde_verkoopkoers"], 12.0)
        self.assertAlmostEqual(entry["rendement_eur"], 10.0)

    def test_volledig_gesloten_positie_heeft_nog_in_bezit_false(self):
        transacties = pd.DataFrame([
            _rij("2024-01-01", 10.0, -100.0, koers=10.0, ticker="Y"),
            _rij("2024-03-01", -10.0, 130.0, koers=13.0, ticker="Y"),
        ])
        resultaat = pd.DataFrame(
            {"waarde": [0.0], "geinvesteerd": [0.0], "rendement": [0.0]},
            index=[pd.Timestamp("2024-12-01")],
        )
        price_data = pd.DataFrame({"Y": [13.0]}, index=[pd.Timestamp("2024-12-01")])
        stats = bereken_statistieken(transacties, price_data, resultaat)
        entry = next(p for p in stats["gesloten_posities"] if p["ticker"] == "Y")
        self.assertFalse(entry["nog_in_bezit"])
        self.assertAlmostEqual(entry["resterend_aantal"], 0.0)


if __name__ == "__main__":
    unittest.main()
