"""Procesbrede connection pool in db.py (gemockt, geen database)."""
import os
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
def _nep_verbinding():
    conn = MagicMock()
    conn.closed = 0
    return conn


class PoolTestCase(unittest.TestCase):
    def setUp(self):
        self.verbindingen = []

        def maak(*args, **kwargs):
            conn = _nep_verbinding()
            self.verbindingen.append(conn)
            return conn

        patchers = [
            patch("psycopg2.connect", side_effect=maak),
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}),
            patch.object(db, "_pool", None),
            patch.dict(db._laatst_gebruikt, clear=True),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        db.db_reset_verbinding_teller()


class TestHergebruik(PoolTestCase):
    def test_tweede_connect_na_close_hergebruikt(self):
        eerste = db._open_verbinding()
        eerste.close()
        tweede = db._open_verbinding()
        self.assertEqual(len(self.verbindingen), 1)
        self.assertIs(tweede._verbinding, self.verbindingen[0])
        self.assertEqual(db.db_verbinding_teller_stand()[0], 1)
        self.assertEqual(db._verbinding_teller["hergebruikt"], 1)

    def test_dubbel_close_is_veilig(self):
        conn = db._open_verbinding()
        conn.close()
        conn.close()
        self.assertTrue(conn.closed)
        self.assertEqual(len(db._pool._used), 0)

    def test_niet_gesloten_wrapper_gaat_terug_bij_opruimen(self):
        conn = db._open_verbinding()
        del conn
        self.assertEqual(len(db._pool._used), 0)


class TestClose(PoolTestCase):
    def test_rollback_bij_close(self):
        conn = db._open_verbinding()
        conn.close()
        self.verbindingen[0].rollback.assert_called()

    def test_kapotte_verbinding_wordt_weggegooid(self):
        conn = db._open_verbinding()
        self.verbindingen[0].rollback.side_effect = Exception("weg")
        conn.close()
        db._open_verbinding()
        self.assertEqual(len(self.verbindingen), 2)

    def test_gesloten_verbinding_wordt_weggegooid(self):
        conn = db._open_verbinding()
        self.verbindingen[0].closed = 2
        conn.close()
        db._open_verbinding()
        self.assertEqual(len(self.verbindingen), 2)


class TestVerouderd(PoolTestCase):
    def test_falende_controle_geeft_nieuwe_verbinding(self):
        db._open_verbinding().close()
        db._laatst_gebruikt[id(self.verbindingen[0])] -= db.POOL_CONTROLE_NA_SECONDEN + 1
        self.verbindingen[0].cursor.side_effect = Exception("dood")
        nieuw = db._open_verbinding()
        self.assertEqual(len(self.verbindingen), 2)
        self.assertIs(nieuw._verbinding, self.verbindingen[1])

    def test_goede_controle_hergebruikt(self):
        db._open_verbinding().close()
        db._laatst_gebruikt[id(self.verbindingen[0])] -= db.POOL_CONTROLE_NA_SECONDEN + 1
        conn = db._open_verbinding()
        self.assertIs(conn._verbinding, self.verbindingen[0])
        self.verbindingen[0].cursor.return_value.execute.assert_called_with("SELECT 1")

    def test_verse_verbinding_krijgt_geen_controle(self):
        db._open_verbinding().close()
        db._open_verbinding()
        self.verbindingen[0].cursor.assert_not_called()


class TestPoolVol(PoolTestCase):
    def test_losse_verbinding_en_geen_exception(self):
        with patch.object(db, "POOL_MAX_VERBINDINGEN", 2):
            a, b = db._open_verbinding(), db._open_verbinding()
            c = db._open_verbinding()
            self.assertIsNone(c._pool)
            c.close()
            self.verbindingen[2].close.assert_called_once()
            a.close()
            b.close()
            self.assertEqual(len(db._pool._pool), 2)


class TestThreads(PoolTestCase):
    def test_acht_threads_geen_fouten_en_hoogstens_maxconn_connects(self):
        fouten = []

        def werk():
            try:
                for _ in range(20):
                    conn = db._open_verbinding()
                    conn.close()
            except Exception as e:
                fouten.append(e)

        threads = [threading.Thread(target=werk) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(fouten, [])
        self.assertLessEqual(len(self.verbindingen), db.POOL_MAX_VERBINDINGEN)
        self.assertEqual(len(db._pool._used), 0)


if __name__ == "__main__":
    unittest.main()
