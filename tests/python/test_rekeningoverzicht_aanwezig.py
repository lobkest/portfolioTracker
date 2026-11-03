"""totalen.rekeningoverzicht_aanwezig: True met rekeningregels of kassaldo, anders False. Geen database."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.statistieken import bereken_statistieken


def _totalen(**kwargs):
    transacties = pd.DataFrame([{
        "ticker": "TEST", "datum": pd.Timestamp("2024-01-01"), "aantal": 10.0, "adj_aantal": 10.0,
        "koers": 10.0, "totaal_eur": -100.0, "beurs": "EAM", "product": "TEST",
    }])
    resultaat = pd.DataFrame({"waarde": [120.0], "geinvesteerd": [100.0], "rendement": [20.0]},
                             index=[pd.Timestamp("2024-12-01")])
    prijzen = pd.DataFrame({"TEST": [12.0]}, index=[pd.Timestamp("2024-12-01")])
    return bereken_statistieken(transacties, prijzen, resultaat, **kwargs)["totalen"]


class TestRekeningoverzichtAanwezig(unittest.TestCase):
    def test_false_zonder_rekeningoverzicht(self):
        self.assertIs(_totalen()["rekeningoverzicht_aanwezig"], False)
        self.assertIs(_totalen(rekening_regels=[])["rekeningoverzicht_aanwezig"], False)

    def test_true_met_kassaldo(self):
        kassaldo = {"saldo_eur": 10.0, "netto_gestort_eur": 110.0, "per_datum": datetime.date(2024, 12, 1)}
        self.assertIs(_totalen(kassaldo=kassaldo)["rekeningoverzicht_aanwezig"], True)

    def test_true_met_rekeningregels_zonder_kassaldo(self):
        regels = [{"datum": datetime.date(2024, 1, 1), "soort": "storting", "bedrag_eur": 100.0}]
        self.assertIs(_totalen(rekening_regels=regels)["rekeningoverzicht_aanwezig"], True)


if __name__ == "__main__":
    unittest.main()
