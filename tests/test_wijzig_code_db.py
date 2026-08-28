"""Test voor wijzig_portfolio_code() (db.py), ECHT tegen de database --
zelfde opzet als tests/test_dividend_db.py: een aparte, opgeruimde
test-code in dezelfde database als DATABASE_URL aangeeft, overgeslagen
als DATABASE_URL niet is ingesteld (bv. GitHub Actions-CI).

Dekt de "Code wijzigen"-feature op Instellingen: de portfolios-rij zelf
hernoemen kan niet zomaar, want transacties.code heeft een FK naar
portfolios(code) zonder ON UPDATE CASCADE (zie db.wijzig_portfolio_code
voor de uitleg/aanpak)."""
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
class TestWijzigPortfolioCode(unittest.TestCase):
    OUD = "TSO"
    NIEUW = "TSN"
    BESTAAND = "TSB"

    def _leeg_op(self, code):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM dividenden WHERE code = %s", (code,))
        cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))
        conn.commit()
        cur.close()
        conn.close()

    def setUp(self):
        for code in (self.OUD, self.NIEUW, self.BESTAAND):
            self._leeg_op(code)

        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.OUD, "unittest"))
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.OUD, date(2024, 1, 1), "TEST BV", "NL0000000001", "AEB", "TEST.AS", 10, 5.0, 50.0, None),
        )
        cur.execute(
            "INSERT INTO dividenden (code, dividend_id, datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.OUD, "TEST-EUR-1", date(2024, 2, 1), "TEST BV", "NL0000000001", "EUR", 5.0, 0.0, 5.0),
        )
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        for code in (self.OUD, self.NIEUW, self.BESTAAND):
            self._leeg_op(code)

    def test_wijzigen_naar_vrije_code_slaagt(self):
        from db import wijzig_portfolio_code, get_db_connection

        success, foutmelding = wijzig_portfolio_code(self.OUD, self.NIEUW)
        self.assertTrue(success)
        self.assertIsNone(foutmelding)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT naam FROM portfolios WHERE code = %s", (self.NIEUW,))
        self.assertEqual(cur.fetchone()[0], "unittest")
        cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (self.OUD,))
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

    def test_gekoppelde_transacties_en_dividenden_volgen_de_nieuwe_code(self):
        from db import wijzig_portfolio_code, get_db_connection
        from analysis import bereken_dividend_samenvatting

        success, _ = wijzig_portfolio_code(self.OUD, self.NIEUW)
        self.assertTrue(success)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT isin FROM transacties WHERE code = %s", (self.NIEUW,))
        self.assertEqual(cur.fetchone()[0], "NL0000000001")
        cur.execute("SELECT 1 FROM transacties WHERE code = %s", (self.OUD,))
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

        samenvatting = bereken_dividend_samenvatting(self.NIEUW)
        self.assertIsNotNone(samenvatting)
        self.assertAlmostEqual(samenvatting["totaal_netto"], 5.0)
        self.assertIsNone(bereken_dividend_samenvatting(self.OUD))

    def test_wijzigen_naar_bestaande_code_faalt_zonder_overschrijven(self):
        from db import get_db_connection, wijzig_portfolio_code

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.BESTAAND, "bestaat-al"))
        conn.commit()
        cur.close()
        conn.close()

        success, foutmelding = wijzig_portfolio_code(self.OUD, self.BESTAAND)
        self.assertFalse(success)
        self.assertIn(self.BESTAAND, foutmelding)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT naam FROM portfolios WHERE code = %s", (self.BESTAAND,))
        self.assertEqual(cur.fetchone()[0], "bestaat-al")
        cur.execute("SELECT naam FROM portfolios WHERE code = %s", (self.OUD,))
        self.assertEqual(cur.fetchone()[0], "unittest")
        cur.execute("SELECT 1 FROM transacties WHERE code = %s", (self.OUD,))
        self.assertIsNotNone(cur.fetchone())
        cur.close()
        conn.close()


if __name__ == "__main__":
    unittest.main()
