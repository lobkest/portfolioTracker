"""Zoeken & wat als en Koersen zijn geen ontwikkeltabs meer: de routes weigeren een gewone portfolio niet. Zonder database."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.portfolio_admin import ONTWIKKEL_CODES_VARIABELE


class TestRoutesZonderOntwikkelcode(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        omgeving = patch.dict(os.environ, {ONTWIKKEL_CODES_VARIABELE: "QQA"})
        omgeving.start()
        self.addCleanup(omgeving.stop)

    def test_koersoverzicht_gewone_code_geeft_geen_weigering(self):
        antwoord = {"reeksen": []}
        transacties = pd.DataFrame({"ticker": ["AAA"]})
        prijzen = pd.DataFrame({"AAA": [1.0]}, index=[pd.Timestamp("2024-01-02")])
        with patch.object(self.app_module, "haal_portfolio_basis", return_value=("N", transacties, prijzen)), \
             patch.object(self.app_module, "db_get_koers_splits", return_value={}), \
             patch.object(self.app_module, "bouw_koersoverzicht", return_value=antwoord):
            res = self.client.get("/api/portfolio/QQC/koersoverzicht")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), antwoord)

    def test_koersoverzicht_onbekende_code_blijft_404(self):
        with patch.object(self.app_module, "haal_portfolio_basis", return_value=(None, None, None)):
            res = self.client.get("/api/portfolio/QQC/koersoverzicht")
        self.assertEqual(res.status_code, 404)

    def test_wat_als_zonder_opslaan_geen_ontwikkelweigering(self):
        res = self.client.post("/api/wat-als", json={})
        self.assertNotIn(res.status_code, (401, 403))

    def test_wat_als_met_code_geen_ontwikkelweigering(self):
        res = self.client.post("/api/portfolio/QQC/wat-als", json={})
        self.assertNotIn(res.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
