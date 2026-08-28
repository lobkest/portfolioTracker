"""
Unit tests voor het samenvoegen van kleine landen tot "Overig" op het
Land-tabblad (analysis._voeg_kleine_landen_samen / LAND_OVERIG_DREMPEL).

Draait geheel offline: pure functie op een handgemaakt land->bedrag-dict,
geen database of yfinance-calls nodig.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import _voeg_kleine_landen_samen, LAND_OVERIG_DREMPEL


class TestVoegKleineLandenSamen(unittest.TestCase):
    def test_kleine_landen_worden_samengevoegd_grote_blijven_los(self):
        # Totaal 1000. "Nederland" (0.3%) en "Peru" (0.1%) zitten onder de
        # 0.5%-drempel -> moeten samen in Overig (0.4% -> €4.00). De rest
        # blijft ongewijzigd.
        land_dict = {
            "United States": 600.0,
            "Germany": 350.0,
            "Nederland": 3.0,
            "Peru": 1.0,
            "France": 46.0,
        }
        resultaat = _voeg_kleine_landen_samen(land_dict)

        self.assertEqual(resultaat["United States"], 600.0)
        self.assertEqual(resultaat["Germany"], 350.0)
        self.assertEqual(resultaat["France"], 46.0)
        self.assertNotIn("Nederland", resultaat)
        self.assertNotIn("Peru", resultaat)
        self.assertAlmostEqual(resultaat["Overig"], 4.0)
        # Niets is verloren gegaan onderweg.
        self.assertAlmostEqual(sum(resultaat.values()), sum(land_dict.values()))

    def test_geen_land_onder_de_drempel_geen_overig_post(self):
        land_dict = {"United States": 700.0, "Germany": 300.0}
        resultaat = _voeg_kleine_landen_samen(land_dict)
        self.assertNotIn("Overig", resultaat)
        self.assertEqual(resultaat, land_dict)

    def test_alle_landen_onder_de_drempel_precies_1_overig_post_van_100_procent(self):
        # Een percentage is per definitie relatief aan de som van de dict
        # zelf — "alles onder 0.5%" kan dus alleen met >200 (ongeveer)
        # gelijkwaardige posten (pigeonhole: 3 landen kunnen nooit alle 3
        # onder 0.5% van hun eigen som zitten). 250 landen van elk gewicht 1
        # -> elk 0.4% van de som van 250.
        land_dict = {f"Land{i}": 1.0 for i in range(250)}
        resultaat = _voeg_kleine_landen_samen(land_dict)
        self.assertEqual(list(resultaat.keys()), ["Overig"])
        self.assertAlmostEqual(resultaat["Overig"], 250.0)

    def test_unknown_onder_drempel_telt_gewoon_mee_in_overig(self):
        # Unknown is GEEN uitzondering: valt die zelf onder de drempel
        # (hier 0.4%, duidelijk onder de 0.5%-grens), dan gaat 'ie gewoon
        # in de Overig-pot, geen aparte "Unknown"-categorie meer in het
        # resultaat.
        land_dict = {"United States": 992.0, "Unknown": 4.0, "Peru": 4.0}
        resultaat = _voeg_kleine_landen_samen(land_dict)
        self.assertNotIn("Unknown", resultaat)
        self.assertAlmostEqual(resultaat["Overig"], 8.0)

    def test_unknown_boven_drempel_blijft_apart_naast_overig(self):
        # Unknown = 9% (ruim boven de drempel, blijft apart), Peru = 0.3%
        # (duidelijk onder de drempel, gaat naar Overig).
        land_dict = {"United States": 907.0, "Unknown": 90.0, "Peru": 3.0}
        resultaat = _voeg_kleine_landen_samen(land_dict)
        self.assertAlmostEqual(resultaat["Unknown"], 90.0)
        self.assertAlmostEqual(resultaat["Overig"], 3.0)

    def test_land_precies_op_de_drempel_blijft_los(self):
        # 0.5% van 1000 = 5.0 -> exact op de drempel telt als "niet onder",
        # blijft dus een eigen categorie (drempel is een "<"-vergelijking).
        land_dict = {"United States": 995.0, "Peru": 5.0}
        resultaat = _voeg_kleine_landen_samen(land_dict)
        self.assertIn("Peru", resultaat)
        self.assertNotIn("Overig", resultaat)

    def test_leeg_dict_blijft_leeg(self):
        self.assertEqual(_voeg_kleine_landen_samen({}), {})

    def test_configureerbare_drempel_wordt_gebruikt(self):
        land_dict = {"United States": 900.0, "Peru": 100.0}
        # Peru = 10%: onder een drempel van 20% val, boven de standaard 0.5%.
        resultaat_hoge_drempel = _voeg_kleine_landen_samen(land_dict, drempel=0.20)
        self.assertIn("Overig", resultaat_hoge_drempel)
        resultaat_standaard = _voeg_kleine_landen_samen(land_dict, drempel=LAND_OVERIG_DREMPEL)
        self.assertNotIn("Overig", resultaat_standaard)


if __name__ == "__main__":
    unittest.main()
