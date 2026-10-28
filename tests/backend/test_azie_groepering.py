"""Unit tests voor "Aziatische landen samenvoegen" op het Land-tabblad (pure functies, offline)."""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_verdeling import (
    _gekozen_regios,
    _groepeer_regios_samen,
    _groepeer_regios_samen_per_bron,
    gewichten_per_land,
)

LAND = {"Japan": 100.0, "China": 50.0, "Germany": 200.0, "United States": 650.0, "Russia": 10.0}


class TestAzieSamenvoegen(unittest.TestCase):
    def test_alleen_azie(self):
        resultaat = _groepeer_regios_samen(LAND, _gekozen_regios(False, True))
        self.assertAlmostEqual(resultaat["Asia"], 150.0)
        self.assertEqual(resultaat["Germany"], 200.0)
        self.assertNotIn("Japan", resultaat)
        self.assertNotIn("Europe", resultaat)

    def test_europa_en_azie_samen(self):
        resultaat = _groepeer_regios_samen(LAND, _gekozen_regios(True, True))
        self.assertAlmostEqual(resultaat["Asia"], 150.0)
        self.assertAlmostEqual(resultaat["Europe"], 200.0)
        self.assertAlmostEqual(sum(resultaat.values()), sum(LAND.values()))

    def test_rusland_blijft_los(self):
        self.assertEqual(_groepeer_regios_samen(LAND, _gekozen_regios(False, True))["Russia"], 10.0)

    def test_geen_regio_laat_alles_staan(self):
        self.assertEqual(_groepeer_regios_samen(LAND, _gekozen_regios(False, False)), LAND)

    def test_per_bron(self):
        per_bron = {"Japan": {"A": 60.0, "B": 40.0}, "China": {"A": 50.0}, "Germany": {"B": 200.0}}
        resultaat = _groepeer_regios_samen_per_bron(per_bron, _gekozen_regios(False, True))
        self.assertEqual(resultaat["Asia"], {"A": 110.0, "B": 40.0})
        self.assertEqual(resultaat["Germany"], {"B": 200.0})

    def test_gewichten_per_land_azie(self):
        fracties = {"WLD": {"land_pct": {"Japan": 0.1, "China": 0.1, "United States": 0.8}}}
        gewichten = gewichten_per_land(fracties, azie_samenvoegen=True)
        self.assertAlmostEqual(gewichten["WLD"]["Asia"], 0.2)
        self.assertAlmostEqual(gewichten["WLD"]["United States"], 0.8)


if __name__ == "__main__":
    unittest.main()
