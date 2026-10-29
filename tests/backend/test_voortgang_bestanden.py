"""Bestanden blijven leesbaar in de thread van de voortgangsstroom (Flask sluit de upload na de request).

Alles na het inlezen is gemockt: geen database, geen Yahoo."""
import json
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.upload_verwerking import VERWACHTE_KOLOMMEN, OngeldigExcelBestand


def _excel_bytes():
    df = pd.DataFrame([{
        "Datum": "2023-01-10", "Tijd": "12:00", "Product": "TESTFONDS", "ISIN": "NL0000000001", "Beurs": "EAM",
        "Aantal": 10.0, "Koers": 100.0, "Totaal EUR": -1000.0, "Order ID": "ORD-1",
    }])
    for kolom in VERWACHTE_KOLOMMEN:
        if kolom not in df.columns:
            df[kolom] = None
    buf = BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _regels(response):
    return [json.loads(r) for r in response.get_data(as_text=True).split("\n") if r.strip()]


class TestBestandenInDeThread(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.m = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        self.m.toegangslimiet.wis()
        self.addCleanup(self.m.toegangslimiet.wis)

    def _post(self, url, **extra):
        data = {"bestand1": (BytesIO(_excel_bytes()), "transacties.xlsx"), **extra}
        return self.client.post(url, data=data, content_type="multipart/form-data")

    def test_upload_leest_het_bestand_in_de_thread(self):
        gelezen = []

        def opslaan(df, rekening_df, naam, herbepaal):
            gelezen.append(list(df["Order ID"]))
            return {"ok": 1}

        with patch.object(self.m, "_upload_opslaan", opslaan), \
                patch.object(self.m, "ruim_verlopen_portfolios_op"):
            regels = _regels(self._post("/upload?voortgang=1"))
        self.assertEqual(regels[-1]["status"], 200, regels[-1])
        self.assertEqual(gelezen, [["ORD-1"]])

    def test_upload_zonder_voortgang_leest_hetzelfde(self):
        with patch.object(self.m, "_upload_opslaan", lambda *a: {"ok": 1}), \
                patch.object(self.m, "ruim_verlopen_portfolios_op"):
            response = self._post("/upload")
        self.assertEqual(response.status_code, 200)

    def test_bijwerken_leest_het_bestand_in_de_thread(self):
        echte_lezer = self.m.lees_transacties_excel
        gelezen = []

        def lezer(bestand):
            gelezen.append(list(echte_lezer(bestand)["Order ID"]))
            raise OngeldigExcelBestand("stop na inlezen")

        with patch.object(self.m, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.m, "lees_transacties_excel", lezer):
            regels = _regels(self._post("/api/portfolio/ABC/bijwerken?voortgang=1"))
        self.assertEqual(regels[-1], {"type": "klaar", "status": 400, "data": {"error": "stop na inlezen"}})
        self.assertEqual(gelezen, [["ORD-1"]])

    def test_lege_selectie_blijft_geen_bestand(self):
        with patch.object(self.m, "ruim_verlopen_portfolios_op"):
            response = self.client.post(
                "/upload?voortgang=1", data={"bestand1": (BytesIO(b""), "")}, content_type="multipart/form-data")
        regels = _regels(response)
        self.assertEqual(regels[-1]["status"], 400)
        self.assertIn("verplicht", regels[-1]["data"]["error"])

    def test_te_groot_bestand_geeft_gewone_413_json_geen_stroom(self):
        groot = BytesIO(b"x" * (self.m.app.config["MAX_CONTENT_LENGTH"] + 1))
        response = self.client.post(
            "/upload?voortgang=1", data={"bestand1": (groot, "t.xlsx")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.mimetype, "application/json")


if __name__ == "__main__":
    unittest.main()
