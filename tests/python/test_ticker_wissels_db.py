"""ticker_wissels tegen de lokale testdatabase (alleen in de CI-job met Postgres of lokaal met Docker)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


@vereist_database
class TestTickerWisselsDb(unittest.TestCase):
    CODE = "TESTWISSEL"
    CODE_NIEUW = "TESTWISSEL2"

    def _opruimen(self):
        from backend.db import db_connect
        conn = db_connect()
        cur = conn.cursor()
        for code in (self.CODE, self.CODE_NIEUW):
            cur.execute("DELETE FROM ticker_wissels WHERE code = %s", (code,))
            cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))
        conn.commit()
        cur.close()
        conn.close()

    def setUp(self):
        from backend.db import db_connect, db_init
        db_init()
        self._opruimen()
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.CODE, "unittest"))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._opruimen()

    def _voeg_toe(self, oud, nieuw, soort):
        from backend.db import db_transactie, db_voeg_ticker_wissel_toe
        with db_transactie() as cur:
            db_voeg_ticker_wissel_toe(cur, self.CODE, "IE1", "EAM", oud, nieuw, soort, "reden")

    def test_wissels_komen_oudste_eerst_terug_en_de_laatste_geldt(self):
        from backend.db import db_get_ticker_wissels
        from backend.ticker_wissel import laatste_per_positie
        self._voeg_toe("A", "B", "auto")
        self._voeg_toe("B", "A", "handmatig")
        wissels = db_get_ticker_wissels(self.CODE)
        self.assertEqual([w["soort"] for w in wissels], ["auto", "handmatig"])
        self.assertEqual(laatste_per_positie(wissels)[("IE1", "EAM")]["nieuwe_ticker"], "A")

    def test_portfolio_verwijderen_ruimt_de_wissels_op(self):
        from backend.db import db_delete_portfolio, db_get_ticker_wissels
        self._voeg_toe("A", "B", "auto")
        db_delete_portfolio(self.CODE)
        self.assertEqual(db_get_ticker_wissels(self.CODE), [])

    def test_code_wijzigen_verhuist_de_wissels(self):
        from backend.db import db_get_ticker_wissels, db_wijzig_portfolio_code
        self._voeg_toe("A", "B", "handmatig")
        gelukt, _fout = db_wijzig_portfolio_code(self.CODE, self.CODE_NIEUW)
        self.assertTrue(gelukt)
        self.assertEqual(db_get_ticker_wissels(self.CODE), [])
        self.assertEqual(len(db_get_ticker_wissels(self.CODE_NIEUW)), 1)


if __name__ == "__main__":
    unittest.main()
