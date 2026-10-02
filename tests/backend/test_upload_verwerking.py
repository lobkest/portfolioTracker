import io
import os
import sys
import unittest
from unittest.mock import patch
from contextlib import contextmanager

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from upload_verwerking import (
    VERWACHTE_KOLOMMEN, OngeldigExcelBestand, _lees_transacties_excel, _normaliseer_transactie_kolommen,
)

BESTAND_Transactions = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_files", "Transactions_test.xlsx",
)


@contextmanager
def toon_df_bij_falen(df):
    try:
        yield
    except AssertionError:
        print("columns:", df.columns.tolist())
        print("first row:")
        print(df.iloc[0])
        raise


class Test_Upload_verwerking(unittest.TestCase):
    def test_leest_echt_degiro_bestand_in(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)

        print("columns:", df.columns)

        with toon_df_bij_falen(df):
            self.assertEqual(len(df), 14)
            self.assertIn("Koers", df.columns)
            self.assertNotIn("Koers ", df.columns)
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["Datum"]))
            self.assertEqual(df["Datum"].iloc[0], pd.Timestamp(2026, 4, 2))

    def test_normaliseer_transactie_kolommen(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)
            df = _normaliseer_transactie_kolommen(df)

        with toon_df_bij_falen(df):
            self.assertIn("_waarde_eur", df.columns)
            self.assertIn("_koers_eur", df.columns)
            self.assertNotIn("_kosten_eur", df.columns)

            # Rij 0: EUR-notering, geen wisselkoers -> koers ongewijzigd.
            self.assertTrue(pd.isna(df["Wisselkoers"].iloc[0]))
            self.assertEqual(df["_koers_eur"].iloc[0], 57.33)

            # Rij 1: Koers 193.65 / Wisselkoers 1.1821 = 163.82 EUR.
            self.assertAlmostEqual(df["_koers_eur"].iloc[1], 163.82, places=2)
            self.assertAlmostEqual(df["_koers_eur"].iloc[1], df["Koers"].iloc[1] / df["Wisselkoers"].iloc[1])


class TestKolomControle(unittest.TestCase):
    def _excel(self, kolommen):
        rij = {kolom: ["01-02-2025"] if kolom == "Datum" else [1] for kolom in kolommen}
        buffer = io.BytesIO()
        pd.DataFrame(rij).to_excel(buffer, index=False)
        return buffer

    def test_alle_verwachte_kolommen_wordt_geaccepteerd(self):
        df = _lees_transacties_excel(self._excel(VERWACHTE_KOLOMMEN))
        self.assertEqual(len(df), 1)

    def test_ontbrekende_kolom_geeft_ongeldig_excel_bestand(self):
        kolommen = [k for k in VERWACHTE_KOLOMMEN if k != "Wisselkoers"]
        with self.assertRaises(OngeldigExcelBestand) as ctx:
            _lees_transacties_excel(self._excel(kolommen))
        self.assertIn("Ongeldig Excel-bestand", str(ctx.exception))
        self.assertIn("Wisselkoers", str(ctx.exception))

    def test_kolomnamen_met_spaties_worden_geaccepteerd(self):
        kolommen = [k + " " if k == "Koers" else k for k in VERWACHTE_KOLOMMEN]
        df = _lees_transacties_excel(self._excel(kolommen))
        self.assertIn("Koers", df.columns)


class TestUploadRouteOngeldigBestand(unittest.TestCase):
    def test_ongeldig_bestand_geeft_400_met_melding_zonder_database(self):
        import app as app_module
        buffer = io.BytesIO()
        pd.DataFrame({"Datum": ["01-02-2025"], "Koers": [1]}).to_excel(buffer, index=False)
        buffer.seek(0)
        with patch.object(app_module, "get_db_connection", side_effect=AssertionError("database geraakt")):
            res = app_module.app.test_client().post(
                "/upload", data={"bestand1": (buffer, "fout.xlsx")}, content_type="multipart/form-data",
            )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Ongeldig Excel-bestand", res.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
