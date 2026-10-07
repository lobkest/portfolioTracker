"""Alle rijen van het rekeningoverzicht in rekening_regels: regel_id (puur) en opslaan/verwijderen/verhuizen
(echt tegen de database, alleen localhost)."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
from dividend import lees_rekeningoverzicht, bouw_rekening_regels

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database

TESTBESTAND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "test_files", "Account_test.xlsx")


def _lees():
    with open(TESTBESTAND, "rb") as f:
        return lees_rekeningoverzicht(f)


def _ids(df):
    return [regel["regel_id"] for regel in bouw_rekening_regels(df)]


class TestRegelId(unittest.TestCase):
    def setUp(self):
        self.df = _lees()

    def test_een_regel_per_rij_en_unieke_ids(self):
        ids = _ids(self.df)
        self.assertEqual(len(ids), len(self.df))
        self.assertEqual(len(set(ids)), len(ids))

    def test_twee_keer_inlezen_zelfde_ids(self):
        self.assertEqual(_ids(self.df), _ids(_lees()))

    def test_andere_productnaam_zelfde_id(self):
        hernoemd = self.df.copy()
        hernoemd.loc[0, "Product"] = "ASML HOLDING N.V. (NIEUWE NAAM)"
        self.assertEqual(_ids(hernoemd)[0], _ids(self.df)[0])

    def test_alleen_ander_saldo_andere_id(self):
        twee = pd.concat([self.df.iloc[[0]], self.df.iloc[[0]]], ignore_index=True)
        twee.loc[1, "saldo"] = twee.loc[0, "saldo"] + 1.0
        ids = _ids(twee)
        self.assertNotEqual(ids[0].rsplit("-", 1)[0], ids[1].rsplit("-", 1)[0])

    def test_identieke_rijen_krijgen_volgnummer(self):
        twee = pd.concat([self.df.iloc[[0]], self.df.iloc[[0]]], ignore_index=True)
        ids = _ids(twee)
        self.assertEqual((ids[0][-2:], ids[1][-2:]), ("-0", "-1"))
        self.assertEqual(ids[0][:-2], ids[1][:-2])

    def test_kleiner_bestand_geeft_deelverzameling(self):
        helft = self.df.iloc[: len(self.df) // 2].reset_index(drop=True)
        self.assertTrue(set(_ids(helft)) <= set(_ids(self.df)))


@vereist_database
class TestRekeningRegelsDatabase(unittest.TestCase):
    CODE = "TESTREK"
    NIEUWE_CODE = "TESTREK2"

    def _leeg_op(self):
        with db.db_transactie() as cur:
            for code in (self.CODE, self.NIEUWE_CODE):
                cur.execute("DELETE FROM rekening_regels WHERE code = %s", (code,))
                cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))

    def setUp(self):
        self._leeg_op()
        self.addCleanup(self._leeg_op)
        with db.db_transactie() as cur:
            db.db_maak_portfolio(cur, self.CODE, None)
        self.df = _lees()

    def _aantal(self, code):
        with db.db_transactie() as cur:
            cur.execute("SELECT COUNT(*) FROM rekening_regels WHERE code = %s", (code,))
            return cur.fetchone()[0]

    def test_overlappende_uploads_geen_dubbele_rijen(self):
        eerste = bouw_rekening_regels(self.df.iloc[:40].reset_index(drop=True))
        tweede = bouw_rekening_regels(self.df.iloc[20:].reset_index(drop=True))
        with db.db_transactie() as cur:
            self.assertEqual(db.db_save_rekening_regels(cur, self.CODE, eerste), 40)
            self.assertEqual(db.db_save_rekening_regels(cur, self.CODE, tweede), len(self.df) - 40)
        self.assertEqual(self._aantal(self.CODE), len(self.df))

    def test_verwijderen_verwijdert_de_regels(self):
        with db.db_transactie() as cur:
            db.db_save_rekening_regels(cur, self.CODE, bouw_rekening_regels(self.df))
        db.db_delete_portfolio(self.CODE)
        self.assertEqual(self._aantal(self.CODE), 0)

    def test_code_wijzigen_verhuist_de_regels(self):
        with db.db_transactie() as cur:
            db.db_save_rekening_regels(cur, self.CODE, bouw_rekening_regels(self.df))
        gelukt, _ = db.db_wijzig_portfolio_code(self.CODE, self.NIEUWE_CODE)
        self.assertTrue(gelukt)
        self.assertEqual(self._aantal(self.CODE), 0)
        self.assertEqual(self._aantal(self.NIEUWE_CODE), len(self.df))


if __name__ == "__main__":
    unittest.main()
