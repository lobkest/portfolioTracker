import io
import os
import sys
import unittest
from unittest.mock import patch
from contextlib import contextmanager

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from upload_verwerking import (
    VERWACHTE_KOLOMMEN, OngeldigExcelBestand, lees_transacties_excel, voeg_koers_eur_toe,
    vul_synthetische_order_ids_aan, _kolom_of_naamloze_buurkolom,
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
            df = lees_transacties_excel(f)

        with toon_df_bij_falen(df):
            self.assertIn("Koers", df.columns)
            self.assertNotIn("Koers ", df.columns)
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["Datum"]))

    def test_kolom_of_naamloze_buurkolom_pakt_gevulde_naamloze_buur_rechts(self):
        df = pd.DataFrame({"Product": ["A", "B"], "Order ID": [None, None], "Unnamed: 2": ["id-1", "id-2"]})

        kolom, bron = _kolom_of_naamloze_buurkolom(df, "Order ID")
        self.assertEqual((kolom.tolist(), bron), (["id-1", "id-2"], "rechts"))

    def test_kolom_of_naamloze_buurkolom_houdt_gevulde_kolom_en_negeert_benoemde_buur(self):
        gevuld = pd.DataFrame({"Order ID": ["id-1", "id-2"], "Unnamed: 1": ["x", "y"]})
        benoemde_buur = pd.DataFrame({"Order ID": [None, None], "Product": ["A", "B"]})

        kolom, bron = _kolom_of_naamloze_buurkolom(gevuld, "Order ID")
        self.assertEqual((kolom.tolist(), bron), (["id-1", "id-2"], "eigen_kop"))
        kolom, bron = _kolom_of_naamloze_buurkolom(benoemde_buur, "Order ID")
        self.assertTrue(kolom.isna().all())
        self.assertIsNone(bron)

    def test_leest_order_ids_mee_in_df(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = lees_transacties_excel(f)

        with toon_df_bij_falen(df):
            self.assertIn("Order ID", df.columns)
            echte_ids = df["Order ID"].dropna()
            self.assertGreater(len(echte_ids), 0)
            self.assertTrue(all(len(order_id) == 36 and order_id.count("-") == 4 for order_id in echte_ids))

    def test_create_synthetic_order_ids_vult_synthetische_aan_en_is_uniek(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = lees_transacties_excel(f)
        echte_ids = df["Order ID"].dropna().tolist()

        df = vul_synthetische_order_ids_aan(df)

        print("Order IDs na vul_synthetische_order_ids_aan:")
        print(df["Order ID"].tolist())

        with toon_df_bij_falen(df):
            self.assertEqual(df["Order ID"].isna().sum(), 0)
            self.assertTrue(df["Order ID"].is_unique)
            self.assertEqual(df["Order ID"][df["Order ID"].isin(echte_ids)].tolist(), echte_ids)
            self.assertTrue(all(order_id.startswith("SYN-") for order_id in df["Order ID"] if order_id not in echte_ids))

    def test_leest_echt_degiro_bestand_in_warning(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = lees_transacties_excel(f)

        df = df.drop(columns=["Aantal"])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        buffer.seek(0)

        with toon_df_bij_falen(df):
            with self.assertRaises(OngeldigExcelBestand) as ctx:
                lees_transacties_excel(buffer)
            self.assertIn("Ongeldig Excel-bestand", str(ctx.exception))
            self.assertIn("Aantal", str(ctx.exception))

    def test_normaliseer_transactie_kolommen(self):
        with open(BESTAND_Transactions, "rb") as f:
            df = lees_transacties_excel(f)
            df = voeg_koers_eur_toe(df)

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
