"""Welke posities (nog) geen koersen hebben, voor de melding bovenaan het dashboard; en de splits per aandeel."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_orchestratie import bepaal_koersstatus, splits_voor_grafiek

NAMEN = {"AA": "Alfa", "BB": "Beta", "CC": "Gamma"}


class TestKoersstatus(unittest.TestCase):
    def test_alles_compleet(self):
        status = bepaal_koersstatus(["AA", "BB"], {"AA", "BB"}, [], NAMEN)
        self.assertEqual(status, {"koersen_compleet": True, "koersen_onvolledig": [], "koersen_ontbreken": []})

    def test_onvolledig_en_ontbrekend_apart(self):
        # BB nog niet opgehaald (tijdbudget), CC: Yahoo gaf niets.
        status = bepaal_koersstatus(["AA", "BB", "CC"], {"AA"}, ["BB"], NAMEN)
        self.assertFalse(status["koersen_compleet"])
        self.assertEqual(status["koersen_onvolledig"], [{"ticker": "BB", "naam": "Beta"}])
        self.assertEqual(status["koersen_ontbreken"], [{"ticker": "CC", "naam": "Gamma"}])

    def test_alleen_ontbrekend_is_ook_niet_compleet(self):
        status = bepaal_koersstatus(["AA", "XX"], {"AA"}, [], NAMEN)
        self.assertFalse(status["koersen_compleet"])
        self.assertEqual(status["koersen_ontbreken"], [{"ticker": "XX", "naam": "XX"}])


class TestSplitsVoorGrafiek(unittest.TestCase):
    def test_op_datum_gesorteerd(self):
        self.assertEqual(splits_voor_grafiek({"2023-05-15": 0.005, "2021-01-26": 1 / 3}),
                         [{"datum": "2021-01-26", "ratio": 1 / 3}, {"datum": "2023-05-15", "ratio": 0.005}])
        self.assertEqual(splits_voor_grafiek({}), [])


if __name__ == "__main__":
    unittest.main()
