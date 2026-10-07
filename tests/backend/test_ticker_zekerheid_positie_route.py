"""
Route-level test voor de nieuwe per-positie ticker-zekerheid-route
(/api/portfolio/<code>/ticker-zekerheid/positie). Aanleiding: grote
portfolio's liepen vast op /api/portfolio/<code>/ticker-zekerheid (inmiddels
verwijderd) omdat die
route moet wachten tot ALLE posities klaar zijn (verifieer_tickers_met_
prijs_parallel), waardoor één trage/rate-limited positie de hele opvraag
liet mislukken. De nieuwe route verifieert precies 1 positie en hergebruikt
daarvoor de bestaande ticker_zekerheid.verifieer_ticker_met_prijs() zonder eigen
backend-logica -- deze test bevestigt dat de route exact hetzelfde
resultaat geeft als een directe aanroep van die functie.

Raakt de echte database aan (net als tests/test_dividend_db.py), want
app.py roept db_init() op moduleniveau aan -- 'import app' zou zonder
DATABASE_URL dus al bij de import crashen. Mockt find_ticker_detailed en
vergelijk_prijs_op_datum zodat er geen echte yahooquery/yfinance-calls
gebeuren.
"""
import os
import sys
import unittest
from datetime import date, time
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database

import ticker_zekerheid

# verifieer_ticker_met_prijs() roept sinds de OpenFIGI-root-check (zie
# _voeg_openfigi_check_toe in ticker_zekerheid.py) altijd haal_openfigi_resultaten()
# aan, die zonder deze patch een echte DB/netwerk-call zou doen. Module-breed
# op "geen resultaten" gepatcht zodat deze route-test offline en ongewijzigd
# blijft t.o.v. de directe aanroep waarmee 'ie vergeleken wordt.
_openfigi_patcher = None


def setUpModule():
    global _openfigi_patcher
    _openfigi_patcher = patch.object(
        ticker_zekerheid, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}
    )
    _openfigi_patcher.start()


def tearDownModule():
    _openfigi_patcher.stop()


@vereist_database
class TestTickerZekerheidPositieRoute(unittest.TestCase):
    TEST_CODE = "TESTPOS"
    ISIN = "US0378331005"
    BEURS = "NASDAQ"

    def setUp(self):
        import app as app_module
        import ticker_zekerheid
        self.app_module = app_module
        self.ticker_zekerheid = ticker_zekerheid
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import db_connect
        conn = db_connect()
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
        from db import db_connect
        conn = db_connect()
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

        with patch.object(self.ticker_zekerheid, "find_ticker_detailed",
                           return_value={"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": []}), \
             patch.object(self.ticker_zekerheid, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(self.ticker_zekerheid, "_ticker_details_met_cache", return_value={}), \
             patch.object(self.ticker_zekerheid, "_land_sector_voor_weergave", return_value=(None, None, None)), \
             patch.object(self.ticker_zekerheid, "classify_ticker", return_value=False):
            verwacht = self.ticker_zekerheid.verifieer_ticker_met_prijs("APPLE INC", self.ISIN, self.BEURS, transacties)

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
            "/api/portfolio/ZZZNIETBESTAAND/ticker-zekerheid/positie",
            query_string={"isin": self.ISIN, "beurs": self.BEURS},
        )
        self.assertEqual(res.status_code, 404)


@vereist_database
class TestTickerZekerheidLijstRoute(unittest.TestCase):
    """De lichte lijst-route mag geen prijscontrole doen -- alleen isin/
    beurs/naam per positie, zodat dit vrijwel instant is."""

    TEST_CODE = "TESTLIJST"

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import db_connect
        conn = db_connect()
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
        from db import db_connect
        conn = db_connect()
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



@vereist_database
class TestTickerZekerheidWijzigRoute(unittest.TestCase):
    """Bij een ISIN-wissel (wisselpaar om 00:00, zonder kosten) zet de knop de ticker van beide ISIN's om."""

    TEST_CODE = "TESTWISSEL"
    OUD_ISIN, NIEUW_ISIN, BEURS = "US30162V1026", "US30162V4095", "NDQ"

    def setUp(self):
        import app as app_module
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import db_connect
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        rijen = [
            ("W-1", date(2021, 1, 8), time(17, 57), self.OUD_ISIN, 13, 0.41, -0.54),
            ("W-2", date(2021, 1, 26), time(0, 0), self.OUD_ISIN, -14, 0.72, None),
            ("W-3", date(2021, 1, 26), time(0, 0), self.NIEUW_ISIN, 4, 2.16, None),
            ("W-4", date(2021, 1, 27), time(16, 17), self.NIEUW_ISIN, -4, 1.91, -0.51),
        ]
        for order_id, datum, tijd, isin, aantal, koers, kosten in rijen:
            cur.execute(
                "INSERT INTO transacties (code, datum, tijd, product, isin, beurs, ticker, aantal, koers, totaal_eur, "
                "order_id, echte_naam, transactiekosten) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (self.TEST_CODE, datum, tijd, "EXELA", isin, self.BEURS, "XELA", aantal, koers, -aantal * koers,
                 order_id, "EXELA", kosten),
            )
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._opschonen()

    def _opschonen(self):
        from db import db_connect
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.TEST_CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def test_wijzig_zet_beide_isins_om(self):
        import app as app_module
        with patch.object(app_module, "bijnaam_na_tickerwissel", return_value=None):
            res = self.client.post(
                f"/api/portfolio/{self.TEST_CODE}/ticker-zekerheid/wijzig",
                json={"isin": self.NIEUW_ISIN, "beurs": self.BEURS, "ticker": "XELA.NEW"},
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"ticker": "XELA.NEW", "oude_ticker": "XELA", "bijnaam": None})

        from db import db_connect
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("SELECT isin, ticker FROM transacties WHERE code = %s", (self.TEST_CODE,))
        rijen = cur.fetchall()
        cur.close()
        conn.close()
        self.assertEqual(len(rijen), 4)
        self.assertEqual({ticker for _, ticker in rijen}, {"XELA.NEW"})


if __name__ == "__main__":
    unittest.main()
