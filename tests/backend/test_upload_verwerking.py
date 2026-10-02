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
    _create_synthetic_order_ids,
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
            self.assertIn("Koers", df.columns)
            self.assertNotIn("Koers ", df.columns)
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["Datum"]))

    def test_leest_order_ids_mee_in_df(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)

        with toon_df_bij_falen(df):
            self.assertIn("Order ID", df.columns)
            self.assertEqual(df.attrs["aantal_order_id_rijen"], len(df))
            echte_ids = df["Order ID"].dropna()
            self.assertGreater(len(echte_ids), 0)
            self.assertTrue(all(len(order_id) == 36 and order_id.count("-") == 4 for order_id in echte_ids))

    def test_create_synthetic_order_ids_vult_synthetische_aan_en_is_uniek(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = _lees_transacties_excel(f)
        echte_ids = df["Order ID"].dropna().tolist()

        df = _create_synthetic_order_ids(df)

        print("Order IDs na _create_synthetic_order_ids:")
        print(df["Order ID"].tolist())

        with toon_df_bij_falen(df):
            self.assertEqual(df["Order ID"].isna().sum(), 0)
            self.assertTrue(df["Order ID"].is_unique)
            self.assertEqual(df["Order ID"][df["Order ID"].isin(echte_ids)].tolist(), echte_ids)
            self.assertTrue(all(order_id.startswith("SYN-") for order_id in df["Order ID"] if order_id not in echte_ids))

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
            self.assertTrue(pd.api.types.is_numeric_dtype(df["_koers_eur"]))

            for _, rij in df.iterrows():
                if pd.isna(rij["Wisselkoers"]) or rij["Wisselkoers"] == 0:
                    self.assertEqual(rij["_koers_eur"], rij["Koers"])
                else:
                    self.assertAlmostEqual(rij["_koers_eur"], rij["Koers"] / rij["Wisselkoers"])



if __name__ == "__main__":
    unittest.main()
