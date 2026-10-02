import io
import os
import sys
import unittest
from unittest.mock import patch
from contextlib import contextmanager

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from upload_verwerking import (
    VERWACHTE_KOLOMMEN, OngeldigExcelBestand, _lees_transacties_excel, _adjust_transaction_exchange_rates,
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

        with toon_df_bij_falen(df):
            self.assertEqual(len(df), 14)
            self.assertIn("Koers", df.columns)
            self.assertNotIn("Koers ", df.columns)
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["Datum"]))
            self.assertEqual(df["Datum"].iloc[0], pd.Timestamp(2026, 4, 2))

    def test_leest_echt_degiro_bestand_in_warning(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)

        df = df.drop(columns=["Aantal"])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)

        with toon_df_bij_falen(df):
            with self.assertRaises(OngeldigExcelBestand) as ctx:
                _lees_transacties_excel(buffer)
            self.assertIn("Ongeldig Excel-bestand", str(ctx.exception))
            self.assertIn("Aantal", str(ctx.exception))

    def test_normaliseer_transactie_kolommen(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)
            df = _adjust_transaction_exchange_rates(df)

        with toon_df_bij_falen(df):
            self.assertIn("_koers_eur", df.columns)
            self.assertNotIn("_kosten_eur", df.columns)

            # Rij 0: EUR-notering, geen wisselkoers -> koers ongewijzigd.
            self.assertTrue(pd.isna(df["Wisselkoers"].iloc[0]))
            self.assertEqual(df["_koers_eur"].iloc[0], 57.33)

            # Rij 1: Koers 193.65 / Wisselkoers 1.1821 = 163.82 EUR.
            self.assertAlmostEqual(df["_koers_eur"].iloc[1], 163.82, places=2)
            self.assertAlmostEqual(df["_koers_eur"].iloc[1], df["Koers"].iloc[1] / df["Wisselkoers"].iloc[1])



if __name__ == "__main__":
    unittest.main()
