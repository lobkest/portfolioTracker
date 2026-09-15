"""
Route-level tests voor /api/portfolio/<code>/transacties -- het Transacties-
overzichtstabblad (datum, tijd, product, aantal, koers, totaal_eur,
transactiekosten per rij, geen sortering/paginering server-side, dat gebeurt
client-side).

Raakt de echte database aan (net als tests/test_ticker_koers_bereik_route.py),
want app.py roept init_db() op moduleniveau aan.
"""
import os
import sys
import unittest
from datetime import date, time

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTransactiesOverzichtRoute(unittest.TestCase):
    TEST_CODE = "TESTTX"

    def setUp(self):
        import app as app_module
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, tijd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2024, 1, 1), "OUDE BV", "NL0000000001", "AEB", "OUD.AS", 10, 5.0, 50.0, "ord-1", time(9, 0)),
        )
        # NIEUWE BV heeft ook transactiekosten gevuld, om te verifiëren dat de
        # route dat veld meestuurt naast tijd; OUDE BV laat transactiekosten
        # bewust NULL (simuleert een oudere, niet opnieuw geüploade transactie).
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, tijd, transactiekosten) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2024, 6, 1), "NIEUWE BV", "NL0000000002", "AEB", "NIEUW.AS", 3, 100.0, 300.0, "ord-2", time(10, 30), -2.5),
        )
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._opschonen()

    def _opschonen(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def test_geeft_alle_rijen_terug_meest_recent_eerst(self):
        res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/transacties")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["lijst"]), 2)
        # Standaard-sortering vanuit de DB: datum aflopend.
        self.assertEqual(data["lijst"][0]["product"], "NIEUWE BV")
        self.assertEqual(data["lijst"][1]["product"], "OUDE BV")

    def test_velden_zijn_floats_geen_decimal_strings(self):
        res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/transacties")
        data = res.get_json()
        rij = next(r for r in data["lijst"] if r["product"] == "NIEUWE BV")
        self.assertEqual(rij["datum"], "2024-06-01")
        self.assertEqual(rij["aantal"], 3.0)
        self.assertEqual(rij["koers"], 100.0)
        self.assertEqual(rij["totaal_eur"], 300.0)
        self.assertEqual(rij["tijd"], "10:30")
        self.assertEqual(rij["transactiekosten"], -2.5)

    def test_ontbrekende_transactiekosten_geeft_none_niet_nul(self):
        res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/transacties")
        data = res.get_json()
        rij = next(r for r in data["lijst"] if r["product"] == "OUDE BV")
        self.assertIsNone(rij["transactiekosten"])
        self.assertEqual(rij["tijd"], "09:00")

    def test_onbekende_code_geeft_404(self):
        res = self.client.get("/api/portfolio/ZZZNIETBESTAAND/transacties")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
