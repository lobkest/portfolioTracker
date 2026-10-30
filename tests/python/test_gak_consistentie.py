"""
De GAK-berekening staat twee keer (bereken_holdings_en_gesloten() in statistieken.py en compute_per_ticker() in
portfolio_calc.py; zie CLAUDE.md: Data en rekenen). Deze test vergelijkt per positie het aantal en de kostenbasis uit
beide routes, zodat een wijziging aan maar één van de twee direct opvalt.

Draait geheel offline: geen database, geen yfinance-calls.
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.portfolio_calc import compute_per_ticker
from backend.statistieken import bereken_holdings_en_gesloten

KOERS = 10.0
EINDDATUM = "2025-03-31"


def _rij(ticker, datum, tijd, aantal, totaal_eur, waarde_eur):
    return {
        "datum": pd.Timestamp(datum), "tijd": tijd, "ticker": ticker, "aantal": aantal, "koers": 0.0,
        "waarde_eur": waarde_eur, "totaal_eur": totaal_eur, "adj_aantal": aantal, "product": ticker, "beurs": "EAM",
    }


# Bedragen: waarde_eur = aantal x koers in EUR, totaal_eur = daarna kosten (negatief = uitgegeven).
TRANSACTIES = [
    _rij("KOOP", "2025-01-10", "10:00:00", 10, -1002.0, -1000.0),
    _rij("DEELS", "2025-01-10", "10:00:00", 10, -1002.0, -1000.0),
    _rij("DEELS", "2025-02-10", "10:00:00", -4, 598.0, 600.0),
    _rij("VOLLEDIG", "2025-01-10", "10:00:00", 10, -1002.0, -1000.0),
    _rij("VOLLEDIG", "2025-02-10", "10:00:00", -10, 1498.0, 1500.0),
    # Vreemde valuta: de FX-kosten zitten in totaal_eur, niet in waarde_eur; de GAK gebruikt waarde_eur.
    _rij("USD", "2025-01-15", "10:00:00", 5, -464.0, -460.5),
    # Split 1:2: de splitrij heeft geen geld (totaal_eur 0) en verdunt alleen de kostenbasis per stuk.
    _rij("SPLIT", "2025-01-10", "10:00:00", 10, -1000.0, -1000.0),
    _rij("SPLIT", "2025-02-01", "09:00:00", 10, 0.0, None),
]


class TestGakConsistentie(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(TRANSACTIES)
        dagen = pd.date_range("2025-01-01", EINDDATUM)
        self.prijzen = pd.DataFrame({t: KOERS for t in self.df["ticker"].unique()}, index=dagen)
        self.open_posities, self.gesloten = bereken_holdings_en_gesloten(self.df)
        self.per_ticker = compute_per_ticker(self.df, self.prijzen)

    def _statistieken(self, ticker):
        """(aantal, kostenbasis) uit bereken_holdings_en_gesloten(); een gesloten positie is (0, 0)."""
        positie = self.open_posities.get(ticker)
        return (positie["aantal"], positie["aantal"] * positie["gak"]) if positie else (0.0, 0.0)

    def _per_ticker(self, ticker):
        """(aantal, kostenbasis) uit compute_per_ticker(); het aantal volgt uit de waarde bij een vaste koers."""
        reeks = self.per_ticker[ticker]
        return reeks["waarde"][-1] / KOERS, reeks["geinvesteerd"][-1]

    def test_aantal_en_kostenbasis_komen_overeen_per_positie(self):
        for ticker in self.df["ticker"].unique():
            with self.subTest(ticker=ticker):
                aantal_a, basis_a = self._statistieken(ticker)
                aantal_b, basis_b = self._per_ticker(ticker)
                self.assertAlmostEqual(aantal_a, aantal_b, places=2)
                self.assertAlmostEqual(basis_a, basis_b, places=2)

    def test_nog_in_bezit_komt_overeen(self):
        for ticker in self.df["ticker"].unique():
            with self.subTest(ticker=ticker):
                self.assertEqual(ticker in self.open_posities, self.per_ticker[ticker]["nog_in_bezit"])

    def test_verwachte_waarden_met_de_hand_nagerekend(self):
        verwacht = {
            "KOOP": (10, 1000.0),            # waarde_eur, niet het totaal van 1002
            "DEELS": (6, 600.0),             # GAK 100 x 6 over na verkoop van 4
            "VOLLEDIG": (0, 0.0),
            "USD": (5, 460.5),               # waarde_eur 460,50, niet totaal_eur 464
            "SPLIT": (20, 1000.0),           # kostenbasis blijft, aantal verdubbelt
        }
        for ticker, (aantal, basis) in verwacht.items():
            with self.subTest(ticker=ticker):
                a, b = self._statistieken(ticker)
                self.assertAlmostEqual(a, aantal, places=2)
                self.assertAlmostEqual(b, basis, places=2)

    def test_verkoopkant_gebruikt_totaal_eur(self):
        # DEELS: 4 stuks verkocht voor totaal_eur 598 tegen een kostenbasis van 400 (GAK 100).
        deels = self.open_posities["DEELS"]["deels_verkocht"]
        self.assertAlmostEqual(deels["gerealiseerd_eur"], 598.0 - 400.0, places=2)
        # VOLLEDIG: verkoop totaal_eur 1498 min aankoop waarde_eur 1000.
        self.assertAlmostEqual(self.gesloten["VOLLEDIG"]["gerealiseerd_eur"], 1498.0 - 1000.0, places=2)


if __name__ == "__main__":
    unittest.main()
