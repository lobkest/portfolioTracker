"""
Unit tests voor analysis._sorteer_verdeling_groot_naar_klein() en
analysis._sorteer_tickers_voor_dropdown() -- resp. het taartdiagram op het
Verdeling-tabblad en de dropdown op 'Per aandeel'/'Per aandeel aankoop'
aflopend (groot naar klein) laten ogen i.p.v. de alfabetische/chronologische
volgorde die respectievelijk pandas' groupby en de transactie-volgorde
standaard opleveren.

Pure functies, geen DB/netwerk nodig.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import _sorteer_verdeling_groot_naar_klein, _sorteer_tickers_voor_dropdown


class TestSorteerVerdelingGrootNaarKlein(unittest.TestCase):
    def test_sorteert_aflopend_op_waarde(self):
        verdeling = [
            {"ticker": "A", "waarde": 100.0, "is_etf": False},
            {"ticker": "B", "waarde": 900.0, "is_etf": True},
            {"ticker": "C", "waarde": 500.0, "is_etf": False},
        ]
        resultaat = _sorteer_verdeling_groot_naar_klein(verdeling)
        self.assertEqual([r["ticker"] for r in resultaat], ["B", "C", "A"])

    def test_lege_lijst_crasht_niet(self):
        self.assertEqual(_sorteer_verdeling_groot_naar_klein([]), [])

    def test_laat_invoerlijst_ongemoeid(self):
        verdeling = [
            {"ticker": "A", "waarde": 100.0, "is_etf": False},
            {"ticker": "B", "waarde": 900.0, "is_etf": True},
        ]
        _sorteer_verdeling_groot_naar_klein(verdeling)
        self.assertEqual([r["ticker"] for r in verdeling], ["A", "B"])


class TestSorteerTickersVoorDropdown(unittest.TestCase):
    def test_in_bezit_eerst_dan_verkocht_beide_groot_naar_klein(self):
        per_ticker = {
            "KLEIN.AS": {"waarde": [100, 120], "nog_in_bezit": True},
            "GROOT.AS": {"waarde": [1000, 1500], "nog_in_bezit": True},
            "VERKOCHT_GROOT.AS": {"waarde": [800, 900, 0], "nog_in_bezit": False},
            "VERKOCHT_KLEIN.AS": {"waarde": [50, 60, 0], "nog_in_bezit": False},
        }
        volgorde = _sorteer_tickers_voor_dropdown(per_ticker)
        self.assertEqual(
            volgorde,
            ["GROOT.AS", "KLEIN.AS", "VERKOCHT_GROOT.AS", "VERKOCHT_KLEIN.AS"],
        )

    def test_lege_waarde_reeks_crasht_niet(self):
        per_ticker = {"LEEG.AS": {"waarde": [], "nog_in_bezit": False}}
        self.assertEqual(_sorteer_tickers_voor_dropdown(per_ticker), ["LEEG.AS"])


if __name__ == "__main__":
    unittest.main()
