"""Een lege cel in de kostenkolom is €0 (DeGiro zet de kosten van een deelorder op één rij); herupload vult NULL aan."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
import upload_verwerking as uv
from diagnostiek_checks import check_ontbrekende_kolommen

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


def _excel(kosten):
    n = len(kosten)
    data = {kolom: [None] * n for kolom in uv.VERWACHTE_KOLOMMEN}
    data.update({"Datum": ["02-01-2024"] * n, "Order ID": [f"ID-{i}" for i in range(n)], uv.KOSTEN_KOLOM: kosten})
    return pd.DataFrame(data)


class TestLegeKostencel(unittest.TestCase):
    def _lees(self, kosten):
        with patch.object(uv, "controleer_excel_bestand"), patch.object(uv, "controleer_aantal_rijen"), \
                patch.object(uv.pd, "read_excel", return_value=_excel(kosten)), redirect_stdout(io.StringIO()):
            return uv.lees_transacties_excel(object())

    def test_lege_cel_wordt_nul_en_gevulde_blijft(self):
        df = self._lees([-1.0, None, float("nan")])
        self.assertEqual(df[uv.KOSTEN_KOLOM].tolist(), [-1.0, 0.0, 0.0])

    def test_niet_opslaan_krijgt_dezelfde_nul(self):
        df = self._lees([None])
        df["_koers_eur"] = 1.0
        df["Aantal"] = 1.0
        df["Totaal EUR"] = -1.0
        df[uv.WAARDE_KOLOM] = -1.0
        df[uv.WISSELKOERS_KOLOM] = float("nan")
        df[uv.AUTOFX_KOLOM] = float("nan")
        uit = uv.bouw_transacties_df_niet_opslaan(df, {})
        self.assertEqual(uit["transactiekosten"].tolist(), [0.0])


class TestAanvullenGeeftKostenDoor(unittest.TestCase):
    def test_kosten_per_order_id_naar_de_db(self):
        df = pd.DataFrame({"Order ID": ["A", "B"], uv.KOSTEN_KOLOM: [-1.0, 0.0]})
        with patch.object(uv, "db_vul_bronkolommen_aan", return_value=0), \
                patch.object(uv, "db_vul_transactiekosten_aan", return_value=2) as vul, \
                patch.object(uv, "_bronwaarden", return_value=()), Flask(__name__).test_request_context():
            self.assertEqual(uv.vul_bronkolommen_aan(None, "ABC", df), 2)
        self.assertEqual(vul.call_args.args[2], [("A", -1.0), ("B", 0.0)])


class TestMeldingstekst(unittest.TestCase):
    def _df(self, **extra):
        rij = {"datum": date(2024, 1, 2), "product": "A", "isin": "NL1", "beurs": "EAM", "ticker": "A.AS",
               "aantal": 1.0, "transactiekosten": 1.0, "waarde_eur": -1.0, "tijd": "10:00", "echte_naam": "A",
               "koers": 1.0, "order_id": "x", "totaal_eur": -1.0}
        rij.update(extra)
        return pd.DataFrame([rij])

    def test_kosten_wijzen_naar_een_nieuwe_export(self):
        [b] = check_ontbrekende_kolommen(self._df(transactiekosten=None))
        self.assertIn("Upload een nieuwe DeGiro-export", b["tekst"])
        self.assertNotIn("verwijderen", b["tekst"])

    def test_tijd_heeft_geen_aanvulling_en_houdt_het_oude_advies(self):
        [b] = check_ontbrekende_kolommen(self._df(tijd=None))
        self.assertIn("verwijderen", b["tekst"])


@vereist_database
class TestKostenAanvullenDatabase(unittest.TestCase):
    CODE = "TESTKOST"

    def _leeg_op(self):
        with db.db_transactie() as cur:
            cur.execute("DELETE FROM transacties WHERE code = %s", (self.CODE,))
            cur.execute("DELETE FROM portfolios WHERE code = %s", (self.CODE,))

    def setUp(self):
        self._leeg_op()
        self.addCleanup(self._leeg_op)
        with db.db_transactie() as cur:
            db.db_maak_portfolio(cur, self.CODE, None)

    def _kosten(self, order_id):
        with db.db_transactie() as cur:
            cur.execute("SELECT transactiekosten FROM transacties WHERE code = %s AND order_id = %s",
                        (self.CODE, order_id))
            waarde = cur.fetchone()[0]
        return None if waarde is None else float(waarde)

    def test_vult_null_aan_en_overschrijft_niets(self):
        with db.db_transactie() as cur:
            for order_id, kosten in (("A", None), ("B", -1.0)):
                db.db_insert_transactie(cur, self.CODE, date(2024, 1, 2), "T", "NL1", "EAM", None, 1.0, 1.0, -1.0,
                                        order_id, "T", kosten, None, None, None)
            aantal = db.db_vul_transactiekosten_aan(cur, self.CODE, [("A", 0.0), ("B", -9.0)])
        self.assertEqual(aantal, 1)
        self.assertEqual(self._kosten("A"), 0.0)
        self.assertEqual(self._kosten("B"), -1.0)


if __name__ == "__main__":
    unittest.main()
