"""Test voor db.backfill_tijd(), ECHT tegen de database -- zelfde opzet als
tests/test_transactiekosten_db.py (en test_dividend_db.py/test_wijzig_code_db.py):
een aparte, opgeruimde test-code in dezelfde database als DATABASE_URL
aangeeft, overgeslagen als DATABASE_URL niet is ingesteld (bv. GitHub
Actions-CI).

Dekt de backfill-helft van de 'verkoopkoers onbekend bij same-day
transacties'-fix: 'tijd' kwam pas via een latere migratie bij, en de
insert-query in app.py's /upload-route gebruikt ON CONFLICT (code, order_id)
DO NOTHING -- alle vóór-migratie-rijen (elke order_id die al eens eerder is
opgeslagen) bleven daardoor voor altijd tijd=NULL, ook bij een herhaalde
upload van hetzelfde Excel-bestand. Zie db.backfill_tijd voor de fix, en
tests/test_chronologische_sortering.py voor de kern van de bug zelf
(same-day sortering zonder tijd)."""
import os
import sys
import unittest
from datetime import date, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(
    os.environ.get("DATABASE_URL"),
    "DATABASE_URL niet ingesteld -- deze test raakt een echte database aan en wordt overgeslagen "
    "(bv. in CI zonder databasetoegang; draait lokaal wel via de .env)",
)
class TestBackfillTijd(unittest.TestCase):
    TEST_CODE = "TESTTYD"

    def setUp(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        # rij zonder tijd (zoals een vóór-migratie-rij)
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, "
            "order_id, tijd) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2026, 6, 17), "TEST BV", "NL0000000001", "TDG", "TEST.DE",
             12, 49.0, -588.08, "ORD-NULL", None),
        )
        # rij die al een tijd heeft (bv. via een eerdere backfill)
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, "
            "order_id, tijd) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2026, 6, 17), "TEST BV", "NL0000000001", "TDG", "TEST.DE",
             -12, 48.8, 585.81, "ORD-BEKEND", time(13, 41)),
        )
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def _tijd(self, order_id):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT tijd FROM transacties WHERE code = %s AND order_id = %s",
            (self.TEST_CODE, order_id),
        )
        waarde = cur.fetchone()[0]
        cur.close()
        conn.close()
        return waarde

    def test_null_rij_wordt_bijgewerkt(self):
        from db import backfill_tijd

        aantal = backfill_tijd(self.TEST_CODE, [("ORD-NULL", "13:39:00")])
        self.assertEqual(aantal, 1)
        self.assertEqual(self._tijd("ORD-NULL"), time(13, 39))

    def test_bekende_tijd_wordt_niet_overschreven(self):
        from db import backfill_tijd

        # nieuwe upload heeft een ANDER tijdstip voor deze order_id -- moet
        # genegeerd worden, de al opgeslagen waarde blijft leidend
        aantal = backfill_tijd(self.TEST_CODE, [("ORD-BEKEND", "09:00:00")])
        self.assertEqual(aantal, 0)
        self.assertEqual(self._tijd("ORD-BEKEND"), time(13, 41))

    def test_gemengde_upload_werkt_alleen_de_null_rij_bij(self):
        from db import backfill_tijd

        aantal = backfill_tijd(
            self.TEST_CODE, [("ORD-NULL", "13:39:00"), ("ORD-BEKEND", "09:00:00")]
        )
        self.assertEqual(aantal, 1)
        self.assertEqual(self._tijd("ORD-NULL"), time(13, 39))
        self.assertEqual(self._tijd("ORD-BEKEND"), time(13, 41))

    def test_lege_lijst_doet_niets(self):
        from db import backfill_tijd

        aantal = backfill_tijd(self.TEST_CODE, [])
        self.assertEqual(aantal, 0)


if __name__ == "__main__":
    unittest.main()
