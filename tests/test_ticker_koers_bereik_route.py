"""
Route-level tests voor /api/portfolio/<code>/ticker-koers-bereik -- het lui
opgevraagde endpoint achter de "meer historie laden"-knoppen op het 'Per
aandeel aankoop'-tabblad. Geeft extra koersdata terug buiten de standaard-
crop van per_ticker_aankoop, zonder de hoofd-payload aan te raken.

Raakt de echte database aan (net als tests/test_ticker_zekerheid_positie_
route.py), want app.py roept init_db() op moduleniveau aan. Mockt
analysis.get_prices (via app_module.get_prices) zodat er geen echte
yfinance-calls gebeuren.
"""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTickerKoersBereikRoute(unittest.TestCase):
    TEST_CODE = "TESTKB"

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        self._opschonen()

        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.TEST_CODE, "unittest"))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._opschonen()

    def _opschonen(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.TEST_CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def test_geeft_labels_en_koersen_terug(self):
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0, 12.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        with patch.object(self.app_module, "get_prices", return_value=price_data) as mock_get_prices:
            res = self.client.get(
                f"/api/portfolio/{self.TEST_CODE}/ticker-koers-bereik",
                query_string={"ticker": "X", "vanaf": "2023-01-01"},
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2023-01-01", "2023-01-02", "2023-01-03"])
        self.assertEqual(data["koers"], [10.0, 11.0, 12.0])
        self.assertEqual(data["vroegste_beschikbare_datum"], "2023-01-01")
        # 'vanaf' moet ongewijzigd doorgegeven zijn aan get_prices (de
        # startdatum bepaalt hoever de cache/download teruggaat).
        mock_get_prices.assert_called_once_with(["X"], "2023-01-01")

    def test_tot_filtert_de_bovengrens(self):
        price_data = pd.DataFrame(
            {"X": [10.0, 11.0, 12.0]},
            index=pd.to_datetime(["2023-01-01", "2023-01-02", "2023-01-03"]),
        )
        with patch.object(self.app_module, "get_prices", return_value=price_data):
            res = self.client.get(
                f"/api/portfolio/{self.TEST_CODE}/ticker-koers-bereik",
                query_string={"ticker": "X", "vanaf": "2023-01-01", "tot": "2023-01-02"},
            )

        data = res.get_json()
        self.assertEqual(data["labels"], ["2023-01-01", "2023-01-02"])
        self.assertEqual(data["koers"], [10.0, 11.0])

    def test_ontbrekende_ticker_geeft_lege_reeks_met_null_vroegste_datum(self):
        with patch.object(self.app_module, "get_prices", return_value=pd.DataFrame()):
            res = self.client.get(
                f"/api/portfolio/{self.TEST_CODE}/ticker-koers-bereik",
                query_string={"ticker": "ONBEKEND", "vanaf": "2023-01-01"},
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], [])
        self.assertEqual(data["koers"], [])
        self.assertIsNone(data["vroegste_beschikbare_datum"])

    def test_ontbrekend_ticker_of_vanaf_geeft_400(self):
        res = self.client.get(
            f"/api/portfolio/{self.TEST_CODE}/ticker-koers-bereik",
            query_string={"vanaf": "2023-01-01"},
        )
        self.assertEqual(res.status_code, 400)

        res = self.client.get(
            f"/api/portfolio/{self.TEST_CODE}/ticker-koers-bereik",
            query_string={"ticker": "X"},
        )
        self.assertEqual(res.status_code, 400)

    def test_onbekende_code_geeft_404(self):
        res = self.client.get(
            "/api/portfolio/ZZZ/ticker-koers-bereik",
            query_string={"ticker": "X", "vanaf": "2023-01-01"},
        )
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
