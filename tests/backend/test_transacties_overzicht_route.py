"""
Route-level tests voor /api/portfolio/<code>/transacties -- het Transacties-
overzichtstabblad (datum, tijd, product, aantal, koers, totaal_eur,
transactiekosten, resultaat en dividend per aankoop; geen sortering/paginering
server-side, dat gebeurt client-side) -- en transacties_lijst bij 'niet opslaan'.

De route met code raakt de echte database aan (net als tests/test_ticker_koers_bereik_route.py);
koersen worden gemockt. 'Niet opslaan' draait zonder database.
"""
import os
import sys
import unittest
from datetime import date, time
from unittest.mock import patch

import pandas as pd


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


def _koersen():
    return pd.DataFrame({"OUD.AS": [5.5, 6.0], "NIEUW.AS": [100.0, 90.0]},
                        index=[pd.Timestamp("2024-06-01"), pd.Timestamp("2024-06-02")])


@vereist_database
class TestTransactiesOverzichtRoute(unittest.TestCase):
    TEST_CODE = "TESTTX"

    def setUp(self):
        import app as app_module
        import portfolio_orchestratie
        self.client = app_module.app.test_client()
        koers_patch = patch.object(portfolio_orchestratie, "get_prices", return_value=_koersen())
        koers_patch.start()
        self.addCleanup(koers_patch.stop)
        self._opschonen()

        from db import db_connect
        conn = db_connect()
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
        cur.execute(
            "INSERT INTO dividenden (code, dividend_id, datum, product, isin, valuta, netto_eur, herinvesteerd) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (self.TEST_CODE, "div-1", date(2024, 3, 1), "OUDE BV", "NL0000000001", "EUR", 3.0, False),
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
        cur.execute("DELETE FROM dividenden WHERE code = %s", (self.TEST_CODE,))
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

    def test_resultaat_en_dividend_per_aankoop(self):
        data = self.client.get(f"/api/portfolio/{self.TEST_CODE}/transacties").get_json()
        oud = next(r for r in data["lijst"] if r["product"] == "OUDE BV")
        nieuw = next(r for r in data["lijst"] if r["product"] == "NIEUWE BV")
        # OUDE BV: waarde_eur NULL -> 10 x 5,00 = 50; laatste koers 6,00 -> +10 (20%). Dividend 3 / 50 = 6%.
        self.assertAlmostEqual(oud["resultaat_eur"], 10.0)
        self.assertAlmostEqual(oud["resultaat_pct"], 20.0)
        self.assertEqual(oud["status"], "open")
        self.assertAlmostEqual(oud["dividend_eur"], 3.0)
        self.assertAlmostEqual(oud["dividend_pct"], 6.0)
        # NIEUWE BV: 3 x (90 - 100) = -30 (-10%); geen dividend voor deze ISIN.
        self.assertAlmostEqual(nieuw["resultaat_eur"], -30.0)
        self.assertAlmostEqual(nieuw["resultaat_pct"], -10.0)
        self.assertAlmostEqual(nieuw["dividend_eur"], 0.0)


class TestTransactiesLijstNietOpslaan(unittest.TestCase):
    """Zonder database: app.py draait db_init() alleen met DATABASE_URL, Yahoo en de database zijn gemockt."""

    def _transacties_df(self):
        return pd.DataFrame([{
            "datum": pd.Timestamp("2024-01-01"), "tijd": "10:00", "product": "OUDE BV", "echte_naam": "OUDE BV",
            "isin": "NL0000000001", "beurs": "AEB", "ticker": "OUD.AS", "aantal": 10.0, "koers": 5.0,
            "totaal_eur": -51.0, "waarde_eur": -50.0, "transactiekosten": -1.0, "wisselkoers": float("nan"),
            "autofx_kosten": None,
        }])

    def test_zelfde_velden_dividend_altijd_none(self):
        import app as am
        import portfolio_orchestratie as po
        dividenden = [{"datum": date(2024, 3, 1), "isin": "NL0000000001", "netto_eur": 3.0, "herinvesteerd": False}]
        with am.app.test_request_context(),              patch.object(am, "ticker_resolutie_niet_opslaan", return_value=({}, [], [])),              patch.object(am, "bepaal_product_per_ticker", return_value={}),              patch.object(am, "bouw_transacties_df_niet_opslaan", return_value=self._transacties_df()),              patch.object(am, "bereken_kassaldo", return_value=None),              patch.object(am, "verwerk_dividend_zonder_opslaan", return_value=dividenden),              patch.object(am, "bouw_rekening_regels", return_value=[]),              patch.object(am, "meld_valuta_consistentie"),              patch.object(am, "meld_order_ids_rekening_uit_bestanden"),              patch.object(am, "bouw_dividend_samenvatting", return_value=None),              patch.object(po, "get_prices", return_value=_koersen()),              patch.object(po, "pas_effectieve_datums_toe", side_effect=lambda df: df),              patch.object(po, "analyze_transacties_kern", return_value={"chart_data": None}):
            result = am._analyseer_zonder_opslaan(pd.DataFrame(), pd.DataFrame({"x": [1]}), "")
        [rij] = result["transacties_lijst"]
        self.assertEqual(rij["product"], "OUDE BV")
        self.assertAlmostEqual(rij["resultaat_eur"], 10.0)
        self.assertAlmostEqual(rij["resultaat_pct"], 20.0)
        self.assertEqual(rij["status"], "open")
        self.assertIsNone(rij["dividend_eur"])
        self.assertIsNone(rij["dividend_pct"])


if __name__ == "__main__":
    unittest.main()
