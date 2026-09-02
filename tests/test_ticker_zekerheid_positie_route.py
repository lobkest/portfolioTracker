"""
Route-level test voor de nieuwe per-positie ticker-zekerheid-route
(/api/portfolio/<code>/ticker-zekerheid/positie) -- opdracht: grote
portfolio's liepen vast op /api/portfolio/<code>/ticker-zekerheid omdat die
route moet wachten tot ALLE posities klaar zijn (verifieer_tickers_met_
prijs_parallel), waardoor één trage/rate-limited positie de hele opvraag
liet mislukken. De nieuwe route verifieert precies 1 positie en hergebruikt
daarvoor de bestaande analysis.verifieer_ticker_met_prijs() zonder eigen
backend-logica -- deze test bevestigt dat de route exact hetzelfde
resultaat geeft als een directe aanroep van die functie.

Raakt de echte database aan (net als tests/test_dividend_db.py), want
app.py roept init_db() op moduleniveau aan -- 'import app' zou zonder
DATABASE_URL dus al bij de import crashen. Mockt find_ticker_detailed en
vergelijk_prijs_op_datum zodat er geen echte yahooquery/yfinance-calls
gebeuren.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTickerZekerheidPositieRoute(unittest.TestCase):
    TEST_CODE = "TESTPOS"
    ISIN = "US0378331005"
    BEURS = "NASDAQ"

    def setUp(self):
        import app as app_module
        import analysis
        self.app_module = app_module
        self.analysis = analysis
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        for order_id, datum in (("POS-1", date(2023, 1, 10)), ("POS-2", date(2023, 6, 10))):
            cur.execute(
                "INSERT INTO transacties (code, datum, product, isin, beurs, aantal, koers, totaal_eur, "
                "order_id, echte_naam) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (self.TEST_CODE, datum, "APPLE INC", self.ISIN, self.BEURS, 10, 100.0, -1000.0, order_id, "APPLE INC"),
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

    def test_verifieer_positie_los(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]

        def fake_vergelijk(ticker, datum, bekende_koers):
            return {
                "yahoo_koers": 101.0, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
                "bekende_koers": bekende_koers, "afwijking_pct": 1.0, "niveau": "ok", "match": True,
                "high": 102.0, "low": 99.0, "binnen_dagrange": True,
            }

        with patch.object(self.analysis, "find_ticker_detailed",
                           return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []}), \
             patch.object(self.analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(self.analysis, "_ticker_details_met_cache", return_value={}), \
             patch.object(self.analysis, "_land_sector_voor_weergave", return_value=(None, None, None)), \
             patch.object(self.analysis, "classify_ticker", return_value=False):
            verwacht = self.analysis.verifieer_ticker_met_prijs("APPLE INC", self.ISIN, self.BEURS, transacties)

            res = self.client.get(
                f"/api/portfolio/{self.TEST_CODE}/ticker-zekerheid/positie",
                query_string={"isin": self.ISIN, "beurs": self.BEURS},
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        # De route voegt alleen isin/naam/echte_naam toe -- alle velden die
        # verifieer_ticker_met_prijs() zelf teruggeeft moeten exact gelijk zijn.
        for key, verwachte_waarde in verwacht.items():
            self.assertEqual(data.get(key), verwachte_waarde, f"veld '{key}' verschilt van de directe aanroep")

        self.assertEqual(data["isin"], self.ISIN)

    def test_onbekende_positie_geeft_404(self):
        res = self.client.get(
            f"/api/portfolio/{self.TEST_CODE}/ticker-zekerheid/positie",
            query_string={"isin": "XX0000000000", "beurs": "XYZ"},
        )
        self.assertEqual(res.status_code, 404)

    def test_onbekende_code_geeft_404(self):
        res = self.client.get(
            "/api/portfolio/ZZZ/ticker-zekerheid/positie",
            query_string={"isin": self.ISIN, "beurs": self.BEURS},
        )
        self.assertEqual(res.status_code, 404)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTickerZekerheidLijstRoute(unittest.TestCase):
    """De lichte lijst-route mag geen prijscontrole doen -- alleen isin/
    beurs/naam per positie, zodat dit vrijwel instant is."""

    TEST_CODE = "TESTLIJST"

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, aantal, koers, totaal_eur, "
            "order_id, echte_naam) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, date(2023, 1, 10), "APPLE INC", "US0378331005", "NASDAQ", 10, 100.0, -1000.0,
             "LIJST-1", "APPLE INC"),
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

    def test_lijst_geeft_posities_zonder_prijscontrole(self):
        with patch.object(self.app_module, "verifieer_tickers_met_prijs_parallel") as mock_dure_check, \
             patch.object(self.app_module, "verifieer_ticker_met_prijs") as mock_positie_check:
            res = self.client.get(f"/api/portfolio/{self.TEST_CODE}/ticker-zekerheid/lijst")

        mock_dure_check.assert_not_called()
        mock_positie_check.assert_not_called()
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["posities"]), 1)
        self.assertEqual(data["posities"][0]["isin"], "US0378331005")
        self.assertEqual(data["posities"][0]["beurs"], "NASDAQ")
        self.assertNotIn("prijs_checks", data["posities"][0])


if __name__ == "__main__":
    unittest.main()
