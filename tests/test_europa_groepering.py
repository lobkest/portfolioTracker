"""
Unit tests voor de "Europese landen samenvoegen"-toggle op het Land-tabblad
(analysis._groepeer_europa_samen, EUROPESE_LANDEN, en de interactie met de
bestaande <0.5% "Overig"-samenvoeging in _voeg_kleine_landen_samen).

Draait geheel offline: pure functies op handgemaakte land->bedrag-dicts,
geen database of yfinance-calls nodig.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import _groepeer_europa_samen, _voeg_kleine_landen_samen, EUROPESE_LANDEN


class TestGroepeerEuropaSamen(unittest.TestCase):
    def test_europese_landen_worden_samengevoegd_niet_europese_blijven_los(self):
        land_dict = {
            "Germany": 300.0,
            "France": 200.0,
            "Netherlands": 100.0,
            "United States": 350.0,
            "Japan": 50.0,
        }
        resultaat = _groepeer_europa_samen(land_dict)

        self.assertAlmostEqual(resultaat["Europe"], 600.0)
        self.assertAlmostEqual(resultaat["United States"], 350.0)
        self.assertAlmostEqual(resultaat["Japan"], 50.0)
        self.assertNotIn("Germany", resultaat)
        self.assertNotIn("France", resultaat)
        self.assertNotIn("Netherlands", resultaat)
        # Niets is verloren gegaan onderweg.
        self.assertAlmostEqual(sum(resultaat.values()), sum(land_dict.values()))

    def test_geen_europese_landen_geen_europe_post(self):
        land_dict = {"United States": 700.0, "Japan": 300.0}
        resultaat = _groepeer_europa_samen(land_dict)
        self.assertNotIn("Europe", resultaat)
        self.assertEqual(resultaat, land_dict)

    def test_unknown_wordt_niet_meegenomen_in_europe(self):
        land_dict = {"Germany": 100.0, "Unknown": 50.0, "United States": 850.0}
        resultaat = _groepeer_europa_samen(land_dict)
        self.assertAlmostEqual(resultaat["Europe"], 100.0)
        self.assertAlmostEqual(resultaat["Unknown"], 50.0)

    def test_rusland_en_turkije_tellen_niet_mee_als_europa(self):
        land_dict = {"Germany": 100.0, "Russia": 50.0, "Turkey": 50.0}
        resultaat = _groepeer_europa_samen(land_dict)
        self.assertAlmostEqual(resultaat["Europe"], 100.0)
        self.assertAlmostEqual(resultaat["Russia"], 50.0)
        self.assertAlmostEqual(resultaat["Turkey"], 50.0)
        self.assertNotIn("Russia", EUROPESE_LANDEN)
        self.assertNotIn("Turkey", EUROPESE_LANDEN)


class TestEuropaSamenvoegingMetOverigDrempel(unittest.TestCase):
    """Interactie met de bestaande <0.5% Overig-groepering: eerst Europa
    samenvoegen, dan pas de Overig-drempel toepassen op wat overblijft."""

    def test_klein_niet_europees_land_gaat_alsnog_naar_overig_na_europa_groepering(self):
        # Totaal 1000. Europa (Germany+France) = 500. Peru = 2.0 (0.2%,
        # onder de drempel) -> moet na Europa-groepering nog steeds in
        # Overig belanden.
        land_dict = {
            "Germany": 300.0,
            "France": 200.0,
            "United States": 498.0,
            "Peru": 2.0,
        }
        gegroepeerd = _groepeer_europa_samen(land_dict)
        resultaat = _voeg_kleine_landen_samen(
            gegroepeerd, uitgezonderd={"Europe"} if "Europe" in gegroepeerd else frozenset()
        )

        self.assertAlmostEqual(resultaat["Europe"], 500.0)
        self.assertAlmostEqual(resultaat["United States"], 498.0)
        self.assertNotIn("Peru", resultaat)
        self.assertAlmostEqual(resultaat["Overig"], 2.0)

    def test_kleine_europe_post_blijft_apart_ondanks_overig_drempel(self):
        # Europa is hier maar 0.3% van het totaal (ruim onder de 0.5%-
        # drempel) -- maar omdat de gebruiker de toggle expliciet aanzet,
        # moet "Europe" alsnog als eigen taartpunt zichtbaar blijven, niet
        # wegvallen in Overig.
        land_dict = {
            "Germany": 3.0,
            "United States": 997.0,
        }
        gegroepeerd = _groepeer_europa_samen(land_dict)
        resultaat = _voeg_kleine_landen_samen(
            gegroepeerd, uitgezonderd={"Europe"} if "Europe" in gegroepeerd else frozenset()
        )

        self.assertIn("Europe", resultaat)
        self.assertAlmostEqual(resultaat["Europe"], 3.0)
        self.assertNotIn("Overig", resultaat)

    def test_geen_europese_landen_geen_uitzondering_nodig(self):
        # Als er geen "Europe"-sleutel is, moet de bestaande Overig-logica
        # onveranderd werken (geen per-ongeluk-uitgezonderde landen).
        land_dict = {"United States": 995.0, "Peru": 5.0}
        gegroepeerd = _groepeer_europa_samen(land_dict)
        self.assertNotIn("Europe", gegroepeerd)
        resultaat = _voeg_kleine_landen_samen(
            gegroepeerd, uitgezonderd={"Europe"} if "Europe" in gegroepeerd else frozenset()
        )
        self.assertIn("Peru", resultaat)
        self.assertNotIn("Overig", resultaat)


if __name__ == "__main__":
    unittest.main()
