"""ONTWIKKEL_CODES: inlezen en de vlag `ontwikkelaar` in het kernantwoord. Zonder database; de codes zijn verzonnen."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.portfolio_admin import ONTWIKKEL_CODES_VARIABELE, is_ontwikkel_code, ontwikkel_codes

VERZONNEN = "QQA,QQB"


def _omgeving(waarde):
    return patch.dict(os.environ, {ONTWIKKEL_CODES_VARIABELE: waarde})


class TestOntwikkelCodes(unittest.TestCase):
    def test_ontbrekende_variabele_geeft_lege_set(self):
        with patch.dict(os.environ):
            os.environ.pop(ONTWIKKEL_CODES_VARIABELE, None)
            self.assertEqual(ontwikkel_codes(), set())
            self.assertFalse(is_ontwikkel_code("QQA"))

    def test_lege_waarde_geeft_lege_set(self):
        with _omgeving(""):
            self.assertEqual(ontwikkel_codes(), set())
        with _omgeving(" , ,"):
            self.assertEqual(ontwikkel_codes(), set())

    def test_spaties_en_kleine_letters(self):
        with _omgeving(" qqa , QqB ,,"):
            self.assertEqual(ontwikkel_codes(), {"QQA", "QQB"})
            self.assertTrue(is_ontwikkel_code("QQB"))

    def test_andere_code_en_geen_code(self):
        with _omgeving(VERZONNEN):
            self.assertFalse(is_ontwikkel_code("QQC"))
            self.assertFalse(is_ontwikkel_code(None))
            self.assertFalse(is_ontwikkel_code(""))


class TestOntwikkelaarVlag(unittest.TestCase):
    def _kern(self, code):
        from backend.portfolio_orchestratie import analyze_transacties_kern
        transacties = pd.DataFrame({"ticker": ["AAA"]})
        with patch("backend.portfolio_orchestratie.bepaal_koersstatus", return_value={}),                 patch("backend.portfolio_orchestratie._ticker_namen", return_value={}):
            return analyze_transacties_kern(transacties, code, "Test", prijs_data_al_klaar=pd.DataFrame())

    def test_vlag_true_voor_ontwikkelcode(self):
        with _omgeving(VERZONNEN):
            self.assertIs(self._kern("QQA")["ontwikkelaar"], True)

    def test_vlag_false_voor_andere_code(self):
        with _omgeving(VERZONNEN):
            self.assertIs(self._kern("QQC")["ontwikkelaar"], False)

    def test_niet_opslaan_zonder_code_is_false(self):
        with _omgeving(VERZONNEN):
            self.assertIs(self._kern(None)["ontwikkelaar"], False)

    def test_lijst_met_codes_gaat_nooit_mee(self):
        with _omgeving(VERZONNEN):
            self.assertNotIn("QQB", str(self._kern("QQA")))


if __name__ == "__main__":
    unittest.main()
