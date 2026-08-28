"""Pure validatietest voor is_geldige_code() (analysis.py) -- geen DB nodig.
Gebruikt bij het aanmaken van een nieuwe code (generate_code) en bij het
zelf kiezen van een nieuwe code via "Code wijzigen" op Instellingen."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import is_geldige_code, CODE_LENGTH


class TestIsGeldigeCode(unittest.TestCase):
    def test_drie_hoofdletters_is_geldig(self):
        self.assertTrue(is_geldige_code("ABC"))

    def test_lengte_moet_kloppen(self):
        self.assertEqual(CODE_LENGTH, 3)
        self.assertFalse(is_geldige_code("AB"))
        self.assertFalse(is_geldige_code("ABCD"))

    def test_kleine_letters_zijn_ongeldig(self):
        # De aanroeper (route/generate_code) is verantwoordelijk voor
        # .upper() vóór het valideren -- is_geldige_code zelf accepteert
        # bewust alleen al-uppercase input.
        self.assertFalse(is_geldige_code("abc"))

    def test_cijfers_en_symbolen_zijn_ongeldig(self):
        self.assertFalse(is_geldige_code("AB1"))
        self.assertFalse(is_geldige_code("A-C"))

    def test_leeg_of_none_is_ongeldig(self):
        self.assertFalse(is_geldige_code(""))
        self.assertFalse(is_geldige_code(None))


if __name__ == "__main__":
    unittest.main()
