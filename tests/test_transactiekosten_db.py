"""Test voor db.backfill_transactiekosten(), ECHT tegen de database -- zelfde
opzet als tests/test_dividend_db.py en tests/test_wijzig_code_db.py: een
aparte, opgeruimde test-code in dezelfde database als DATABASE_URL aangeeft,
overgeslagen als DATABASE_URL niet is ingesteld (bv. GitHub Actions-CI).

Dekt de backfill-bugfix: transactiekosten kwam pas via een latere migratie
bij, en de insert-query in app.py's /upload-route gebruikt ON CONFLICT
(code, order_id) DO NOTHING -- alle vóór-migratie-rijen (elke order_id die al
eens eerder is opgeslagen) bleven daardoor voor altijd transactiekosten=NULL,
ook bij een herhaalde upload van hetzelfde Excel-bestand. Zie
db.backfill_transactiekosten voor de fix."""
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
class TestBackfillTransactiekosten(unittest.TestCase):
    TEST_CODE = "TESTKST"

    def setUp(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        # rij zonder transactiekosten (zoals een vóór-migratie-rij)
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, "
            "order_id, transactiekosten) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2024, 1, 1), "TEST BV", "NL0000000001", "AEB", "TEST.AS",
             10, 5.0, -50.0, "ORD-NULL", None),
        )
        # rij die al een (correcte) waarde heeft
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, "
            "order_id, transactiekosten) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2024, 2, 1), "TEST BV", "NL0000000001", "AEB", "TEST.AS",
             5, 6.0, -30.0, "ORD-BEKEND", -1.5),
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

    def _transactiekosten(self, order_id):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT transactiekosten FROM transacties WHERE code = %s AND order_id = %s",
            (self.TEST_CODE, order_id),
        )
        waarde = cur.fetchone()[0]
        cur.close()
        conn.close()
        return float(waarde) if waarde is not None else None

    def test_null_rij_wordt_bijgewerkt(self):
        from db import backfill_transactiekosten

        aantal = backfill_transactiekosten(self.TEST_CODE, [("ORD-NULL", -2.0)])
        self.assertEqual(aantal, 1)
        self.assertAlmostEqual(self._transactiekosten("ORD-NULL"), -2.0)

    def test_bekende_waarde_wordt_niet_overschreven(self):
        from db import backfill_transactiekosten

        # nieuwe upload heeft een ANDER bedrag voor deze order_id -- moet
        # genegeerd worden, de al opgeslagen waarde blijft leidend
        aantal = backfill_transactiekosten(self.TEST_CODE, [("ORD-BEKEND", -9.9)])
        self.assertEqual(aantal, 0)
        self.assertAlmostEqual(self._transactiekosten("ORD-BEKEND"), -1.5)

    def test_gemengde_upload_werkt_alleen_de_null_rij_bij(self):
        from db import backfill_transactiekosten

        aantal = backfill_transactiekosten(
            self.TEST_CODE, [("ORD-NULL", -2.0), ("ORD-BEKEND", -9.9)]
        )
        self.assertEqual(aantal, 1)
        self.assertAlmostEqual(self._transactiekosten("ORD-NULL"), -2.0)
        self.assertAlmostEqual(self._transactiekosten("ORD-BEKEND"), -1.5)

    def test_lege_lijst_doet_niets(self):
        from db import backfill_transactiekosten

        aantal = backfill_transactiekosten(self.TEST_CODE, [])
        self.assertEqual(aantal, 0)


if __name__ == "__main__":
    unittest.main()
