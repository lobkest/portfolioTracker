"""Meting per voortgangsstap: stapduur/tellerverschil (nep-standen) en de worker-thread-teller van db.py (geen database)."""
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
from voortgang import Voortgang, stand_verschil, stap_logregel

STAPPEN = (("a", "A", 10), ("b", "B", 90))


class TestStandVerschil(unittest.TestCase):
    def test_verschil_en_niet_negatief(self):
        self.assertEqual(stand_verschil((2, 1.0, 5), (24, 7.0, 17)), (22, 6.0, 12))
        self.assertEqual(stand_verschil((9, 3.0, 9), (1, 0.5, 2)), (0, 0, 0))

    def test_logregel(self):
        regel = stap_logregel("tickers", 4.12, (2, 1.0, 5), (24, 7.01, 17))
        self.assertEqual(regel, "[voortgang] stap tickers: 4.12s, 22 DB-verbinding(en) (6.01s), 12 Yahoo-call(s)")


class TestVoortgangLogt(unittest.TestCase):
    def test_stappen_en_totaal(self):
        standen = iter([(0, 0.0, 0), (0, 0.0, 0), (3, 1.5, 4), (3, 1.5, 4), (5, 2.0, 4), (5, 2.0, 4)])
        v = Voortgang(STAPPEN, lambda: next(standen), "test")
        uit = StringIO()
        with redirect_stdout(uit):
            v.start_stap("a")
            v.start_stap("b")
            v.klaar(200, {})
        regels = uit.getvalue().splitlines()
        self.assertEqual(len(regels), 3)
        self.assertIn("stap a:", regels[0])
        self.assertIn("3 DB-verbinding(en) (1.50s), 4 Yahoo-call(s)", regels[0])
        self.assertIn("stap b:", regels[1])
        self.assertIn("2 DB-verbinding(en) (0.50s), 0 Yahoo-call(s)", regels[1])
        self.assertIn("stap totaal (test):", regels[2])

    def test_zonder_stand_geen_log(self):
        v = Voortgang(STAPPEN)
        uit = StringIO()
        with redirect_stdout(uit):
            v.start_stap("a")
            v.klaar(200, {})
        self.assertEqual(uit.getvalue(), "")


class TestWorkerVerbindingTeller(unittest.TestCase):
    def setUp(self):
        db.db_reset_verbinding_teller()

    def test_alleen_buiten_app_context(self):
        with patch("db.psycopg2.connect"), patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}):
            threading.Thread(target=db._open_verbinding).start()
            for t in threading.enumerate():
                if t is not threading.current_thread():
                    t.join(5)
            self.assertEqual(db._verbinding_teller["worker"], 1)
            with patch("db.has_app_context", return_value=True):
                db._open_verbinding()
        self.assertEqual(db._verbinding_teller["aantal"], 2)
        self.assertEqual(db._verbinding_teller["worker"], 1)

    def test_samenvatting_noemt_worker(self):
        db._verbinding_teller["aantal"], db._verbinding_teller["worker"] = 28, 25
        uit = StringIO()
        with redirect_stdout(uit):
            db.db_log_verbinding_samenvatting()
        self.assertIn("28 (waarvan 25 in worker-threads)", uit.getvalue())


if __name__ == "__main__":
    unittest.main()
