"""
db.get_laatste_prijs_update(), ÉCHT tegen de database (zelfde patroon als
tests/test_dividend_db.py) -- t.b.v. de "laatst bijgewerkt"-melding op
Portfolio-home. Test dat MAX(datum)/MAX(bijgewerkt_op) over meerdere
tickers heen klopt, en dat een lege tickerlijst / tickers zonder prijsdata
(None, None) opleveren i.p.v. een crash.

Gebruikt een aparte, opgeruimde test-ticker-prefix in dezelfde database als
DATABASE_URL aangeeft (bewust geen aparte testdatabase, zie test_dividend_db.py).
Wordt overgeslagen als DATABASE_URL niet is ingesteld (bv. in CI).
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "DATABASE_URL niet ingesteld -- deze test raakt een echte database aan en wordt overgeslagen "
    "(bv. in CI zonder databasetoegang; draait lokaal wel via de .env)",
)
class TestLaatstePrijsUpdate(unittest.TestCase):
    TICKER_A = "TESTPRIJSA"
    TICKER_B = "TESTPRIJSB"

    def setUp(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM prijzen WHERE ticker IN (%s, %s)", (self.TICKER_A, self.TICKER_B))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self.setUp()

    def test_geeft_meest_recente_datum_en_ophaalmoment_over_meerdere_tickers(self):
        from db import get_db_connection, get_laatste_prijs_update

        conn = get_db_connection()
        cur = conn.cursor()
        # TICKER_A heeft de meest recente koersdatum, TICKER_B is het meest
        # recent OPGEHAALD (bijgewerkt_op) -- get_laatste_prijs_update moet
        # het maximum van elke kolom apart teruggeven, niet gekoppeld aan
        # dezelfde rij.
        cur.execute(
            "INSERT INTO prijzen (ticker, datum, koers_eur, bijgewerkt_op) VALUES "
            "(%s, %s, %s, %s), (%s, %s, %s, %s)",
            (
                self.TICKER_A, date(2026, 9, 3), 10.0, "2026-09-01 08:00:00",
                self.TICKER_B, date(2026, 9, 1), 20.0, "2026-09-03 18:04:00",
            ),
        )
        conn.commit()
        cur.close()
        conn.close()

        laatste_datum, laatst_opgehaald = get_laatste_prijs_update([self.TICKER_A, self.TICKER_B])
        self.assertEqual(laatste_datum, date(2026, 9, 3))
        self.assertEqual(laatst_opgehaald.isoformat(), "2026-09-03T18:04:00")

    def test_lege_tickerlijst_geeft_none_none(self):
        from db import get_laatste_prijs_update
        self.assertEqual(get_laatste_prijs_update([]), (None, None))

    def test_tickers_zonder_prijsdata_geeft_none_none(self):
        from db import get_laatste_prijs_update
        self.assertEqual(get_laatste_prijs_update(["TESTPRIJS_ONBEKEND"]), (None, None))


if __name__ == "__main__":
    unittest.main()
