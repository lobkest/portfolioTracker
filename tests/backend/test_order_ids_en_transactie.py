"""db_transactie() (commit/rollback/sluiten, zonder database) en de gerichte Order ID-queries achter
find_matching_code() en /bijwerken (echt tegen de database, alleen localhost)."""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


class TestDbTransactie(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        p = patch.object(db, "db_connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)

    def test_succes_commit_en_sluit(self):
        with db.db_transactie() as cur:
            cur.execute("SELECT 1")
        self.conn.commit.assert_called_once()
        self.conn.rollback.assert_not_called()
        self.conn.cursor.return_value.close.assert_called_once()
        self.conn.close.assert_called_once()

    def test_fout_rollback_sluit_en_gooit_door(self):
        with self.assertRaises(RuntimeError):
            with db.db_transactie():
                raise RuntimeError("kapot")
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called_once()
        self.conn.close.assert_called_once()


@vereist_database
class TestOrderIdQueries(unittest.TestCase):
    EIGEN = "tst_oid_eigen"
    ANDERE = "tst_oid_andere"

    def _leeg_op(self):
        with db.db_transactie() as cur:
            for code in (self.EIGEN, self.ANDERE):
                cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
                cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))

    def setUp(self):
        self._leeg_op()
        self.addCleanup(self._leeg_op)
        with db.db_transactie() as cur:
            for code, order_ids in ((self.EIGEN, ["TST-OID-A1", "TST-OID-A2"]), (self.ANDERE, ["TST-OID-B1"])):
                db.db_maak_portfolio(cur, code, None)
                for order_id in order_ids:
                    db.db_insert_transactie(cur, code, date(2024, 1, 2), "TEST", "NL0000000001", "EAM", None,
                                            1.0, 1.0, -1.0, order_id, "TEST", None, None, None, None)

    def test_overlap_geeft_alle_ids_van_alleen_de_overlappende_portfolio(self):
        with db.db_transactie() as cur:
            sets = db.db_get_order_id_sets_met_overlap(cur, {"TST-OID-A1", "TST-OID-ONBEKEND"})
        self.assertEqual(sets, {self.EIGEN: {"TST-OID-A1", "TST-OID-A2"}})

    def test_overlap_zonder_ids_is_leeg(self):
        with db.db_transactie() as cur:
            self.assertEqual(db.db_get_order_id_sets_met_overlap(cur, set()), {})

    def test_order_ids_van_een_portfolio(self):
        with db.db_transactie() as cur:
            self.assertEqual(db.db_get_order_ids(cur, self.EIGEN), {"TST-OID-A1", "TST-OID-A2"})

    def test_ids_bij_andere_portfolios(self):
        with db.db_transactie() as cur:
            gevonden = db.db_get_order_ids_bij_andere_portfolios(
                cur, self.EIGEN, {"TST-OID-A1", "TST-OID-B1", "TST-OID-ONBEKEND"})
        self.assertEqual(gevonden, {"TST-OID-B1"})

    def test_find_matching_code_met_nieuwe_rij(self):
        from portfolio_admin import find_matching_code
        with db.db_transactie() as cur:
            code, ontbrekend = find_matching_code(cur, {"TST-OID-A1", "TST-OID-A2", "TST-OID-NIEUW"})
        self.assertEqual((code, ontbrekend), (self.EIGEN, {"TST-OID-NIEUW"}))


if __name__ == "__main__":
    unittest.main()
