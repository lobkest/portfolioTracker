"""
Opslaan+ophalen-cyclus voor dividenden, ÉCHT tegen de database (in
tegenstelling tot tests/test_dividend.py, dat bewust database-vrij is en
alleen de berekening zelf test). Dit dekt het end-to-end-pad
save_dividenden() -> DB -> get_dividenden() -> bereken_dividend_samenvatting()
af, waar eerder een upsert-bug zat: ON CONFLICT DO NOTHING liet een
foutieve (bv. NULL) waarde permanent staan zodra dezelfde dividend_id ooit
met die foutieve waarde was opgeslagen, ook na een latere bugfix in de
berekening zelf. Zie db.save_dividenden() voor de uitleg/fix.

Gebruikt een aparte, opgeruimde test-code (TESTDIV) in dezelfde database als
DATABASE_URL aangeeft — bewust GEEN aparte testdatabase, dit project heeft
er geen. Wordt overgeslagen als DATABASE_URL niet is ingesteld (bv. in de
GitHub Actions-CI, die geen databasetoegang heeft) — de andere testbestanden
in tests/ blijven daar wél draaien.
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
class TestDividendOpslaanEnOphalen(unittest.TestCase):
    TEST_CODE = "TESTDIV"

    def setUp(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM dividenden WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM dividenden WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def test_opgeslagen_dividend_komt_terug_in_de_samenvatting(self):
        from db import save_dividenden
        from analysis import bereken_dividend_samenvatting

        save_dividenden(self.TEST_CODE, [{
            "dividend_id": "TEST-EUR-1", "datum": date(2024, 1, 1),
            "product": "TEST BV", "isin": "NL0000000001", "valuta": "EUR",
            "bruto_eur": 5.0, "belasting_eur": 0.0, "netto_eur": 5.0,
        }])

        samenvatting = bereken_dividend_samenvatting(self.TEST_CODE)
        self.assertIsNotNone(samenvatting)
        self.assertAlmostEqual(samenvatting["totaal_netto"], 5.0)

    def test_herhaald_opslaan_met_gecorrigeerde_waarde_overschrijft_de_oude_rij(self):
        # Regressietest voor de upsert-bug: eerst een foutieve waarde
        # opslaan (zoals de oude, pre-fix berekening deed: netto_eur=None
        # omdat de valutaconversie destijds mislukte), dan dezelfde
        # dividend_id opnieuw opslaan met de gecorrigeerde waarde. Met
        # ON CONFLICT DO NOTHING zou de eerste (foutieve) waarde blijven
        # staan; met de upsert-fix moet de tweede (juiste) waarde winnen.
        from db import save_dividenden
        from analysis import bereken_dividend_samenvatting

        foutief = [{
            "dividend_id": "TEST-USD-1", "datum": date(2024, 2, 1),
            "product": "TEST ETF", "isin": "IE0000000002", "valuta": "USD",
            "bruto_eur": None, "belasting_eur": 0.0, "netto_eur": None,
        }]
        save_dividenden(self.TEST_CODE, foutief)

        # Tussentijds al herberekend voor code MOL was dit de daadwerkelijke
        # bug: de samenvatting bleef €0,00 tonen ondanks een correcte
        # herberekening bij een volgende upload.
        tussentijds = bereken_dividend_samenvatting(self.TEST_CODE)
        self.assertAlmostEqual(tussentijds["totaal_netto"], 0.0)

        gecorrigeerd = [{
            "dividend_id": "TEST-USD-1", "datum": date(2024, 2, 1),
            "product": "TEST ETF", "isin": "IE0000000002", "valuta": "USD",
            "bruto_eur": 4.2, "belasting_eur": 0.0, "netto_eur": 4.2,
        }]
        save_dividenden(self.TEST_CODE, gecorrigeerd)

        samenvatting = bereken_dividend_samenvatting(self.TEST_CODE)
        self.assertIsNotNone(samenvatting)
        self.assertAlmostEqual(samenvatting["totaal_netto"], 4.2)

    def test_geen_dividenden_voor_deze_code_geeft_none(self):
        from analysis import bereken_dividend_samenvatting
        # TEST_CODE bestaat als portfolio (zie setUp) maar heeft hier geen
        # dividendrijen -> moet None zijn, niet een lege-maar-beschikbare
        # samenvatting (dat onderscheid bepaalt de "niet geupload"-melding
        # op de Dividend-pagina).
        samenvatting = bereken_dividend_samenvatting(self.TEST_CODE)
        self.assertIsNone(samenvatting)


if __name__ == "__main__":
    unittest.main()
