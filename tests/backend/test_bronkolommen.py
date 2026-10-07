"""De zes extra Excel-kolommen (uitvoeringsplaats, lokale koers/waarde + valuta, AutoFX): inlezen, opslaan en
aanvullen bij een herupload."""
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

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database

TESTBESTAND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "test_files", "Transactions_test.xlsx")
TAKE_TWO = "US8740541094"
BYD = "CNE100000296"


class TestNaamlozeKolomRechts(unittest.TestCase):
    def setUp(self):
        self.df = pd.read_excel(TESTBESTAND)
        self.df.columns = self.df.columns.str.strip()

    def _valuta(self, isin):
        rij = self.df["ISIN"] == isin
        koers = uv.naamloze_kolom_rechts(self.df, "Koers")[rij].iloc[0]
        lokaal = uv.naamloze_kolom_rechts(self.df, "Lokale waarde")[rij].iloc[0]
        return koers, lokaal

    def test_take_two_in_usd(self):
        self.assertEqual(self._valuta(TAKE_TWO), ("USD", "USD"))

    def test_byd_in_eur(self):
        self.assertEqual(self._valuta(BYD), ("EUR", "EUR"))

    def test_zonder_naamloze_buurkolom_none(self):
        df = pd.DataFrame({"Koers": [1.0, 2.0], "Lokale waarde": [3.0, 4.0]})
        self.assertEqual(uv.naamloze_kolom_rechts(df, "Koers").tolist(), [None, None])
        self.assertEqual(uv.naamloze_kolom_rechts(df, "Lokale waarde").tolist(), [None, None])


def _rij(**extra):
    data = {
        "Datum": pd.to_datetime(["2026-02-17"]), "Tijd": ["15:30"], "Product": ["TAKE-TWO"],
        "ISIN": [TAKE_TWO], "Beurs": ["NDQ"], "Uitvoeringsplaats": ["XNAS"], "Aantal": [2.0],
        "Koers": [193.65], "_koers_valuta": ["USD"], "Lokale waarde": [-387.30], "_lokale_waarde_valuta": ["USD"],
        "AutoFX Kosten": [-0.819098], "_koers_eur": [163.82], "Totaal EUR": [-330.46], "Order ID": ["TST-BRON-1"],
        uv.KOSTEN_KOLOM: [-2.0], uv.WAARDE_KOLOM: [-327.64], uv.WISSELKOERS_KOLOM: [1.1821],
    }
    data.update(extra)
    return pd.DataFrame(data)


class TestInsertGeeftBronwaardenDoor(unittest.TestCase):
    def test_koers_lokaal_is_ruwe_koers(self):
        with patch.object(uv, "db_insert_transactie", return_value=True) as insert, \
                redirect_stdout(io.StringIO()), Flask(__name__).test_request_context():
            uv._insert_nieuwe_transacties(None, "TESTBRON", _rij(), {(TAKE_TWO, "NDQ"): "TTWO"})
        args = insert.call_args.args
        self.assertEqual(args[8], 163.82)
        self.assertEqual(args[16:], ("XNAS", 193.65, "USD", -387.30, "USD", -0.819098))

    def test_lege_cellen_worden_none(self):
        rij = _rij(**{"AutoFX Kosten": [float("nan")], "_koers_valuta": [float("nan")]})
        with patch.object(uv, "db_insert_transactie", return_value=True) as insert, \
                redirect_stdout(io.StringIO()), Flask(__name__).test_request_context():
            uv._insert_nieuwe_transacties(None, "TESTBRON", rij, {(TAKE_TWO, "NDQ"): "TTWO"})
        args = insert.call_args.args
        self.assertIsNone(args[18])
        self.assertIsNone(args[21])


@vereist_database
class TestBronkolommenDatabase(unittest.TestCase):
    CODE = "TESTBRON"
    ANDERE = "TESTBRON2"
    KOLOMMEN = "uitvoeringsplaats, koers_lokaal, koers_valuta, lokale_waarde, lokale_waarde_valuta, autofx_kosten"

    def _leeg_op(self):
        with db.db_transactie() as cur:
            for code in (self.CODE, self.ANDERE):
                cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
                cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))

    def setUp(self):
        self._leeg_op()
        self.addCleanup(self._leeg_op)
        with db.db_transactie() as cur:
            for code in (self.CODE, self.ANDERE):
                db.db_maak_portfolio(cur, code, None)

    def _insert(self, cur, code, order_id, *bron):
        db.db_insert_transactie(cur, code, date(2026, 2, 17), "TEST", TAKE_TWO, "NDQ", None,
                                2.0, 163.82, -330.46, order_id, "TEST", None, None, None, None, *bron)

    def _bron(self, code, order_id):
        with db.db_transactie() as cur:
            cur.execute(f"SELECT {self.KOLOMMEN} FROM transacties WHERE code = %s AND order_id = %s",
                        (code, order_id))
            rij = cur.fetchone()
        return tuple(float(w) if w is not None and not isinstance(w, str) else w for w in rij)

    def test_insert_slaat_bronkolommen_op(self):
        with db.db_transactie() as cur:
            self._insert(cur, self.CODE, "A", "XNAS", 193.65, "USD", -387.30, "USD", -0.82)
        self.assertEqual(self._bron(self.CODE, "A"), ("XNAS", 193.65, "USD", -387.30, "USD", -0.82))

    def test_aanvullen_vult_null_kolommen(self):
        with db.db_transactie() as cur:
            self._insert(cur, self.CODE, "A")
            aantal = db.db_vul_bronkolommen_aan(cur, self.CODE, [("A", "XNAS", 193.65, "USD", -387.30, "USD", None)])
        self.assertEqual(aantal, 1)
        self.assertEqual(self._bron(self.CODE, "A"), ("XNAS", 193.65, "USD", -387.30, "USD", None))

    def test_aanvullen_overschrijft_bestaande_waarde_niet(self):
        with db.db_transactie() as cur:
            self._insert(cur, self.CODE, "A", "XNAS", None, None, None, None, None)
            db.db_vul_bronkolommen_aan(cur, self.CODE, [("A", "XAMS", 193.65, "USD", -387.30, "USD", -0.82)])
        self.assertEqual(self._bron(self.CODE, "A"), ("XNAS", 193.65, "USD", -387.30, "USD", -0.82))

    def test_andere_code_blijft_onaangeroerd(self):
        with db.db_transactie() as cur:
            self._insert(cur, self.CODE, "A")
            self._insert(cur, self.ANDERE, "A")
            db.db_vul_bronkolommen_aan(cur, self.CODE, [("A", "XNAS", 193.65, "USD", -387.30, "USD", -0.82)])
        self.assertEqual(self._bron(self.ANDERE, "A"), (None,) * 6)


if __name__ == "__main__":
    unittest.main()
