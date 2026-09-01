"""
Unit + route-tests voor Opdracht 2: NON TRADEABLE-/corporate-action-rijen
mogen nooit als eigen "positie" in de ticker-analyse verschijnen, ook niet
als zo'n rij toevallig NIET op beurs "DEG" staat (zoals bij BYD).

Achtergrond: analysis._is_corporate_action_row() (beurs == "DEG" OF "NON
TRADEABLE" in product) was de volledige, correcte check, maar
find_ticker_detailed() had een eigen, onvolledige inline-versie
(uitsluitend beurs == "DEG") en de GET /api/portfolio/<code>/ticker-
zekerheid-route filterde helemaal niet voordat er per (ISIN, Beurs)
gegroepeerd werd. Twee losse fixes, één test per fix:

1. find_ticker_detailed() hergebruikt nu _is_corporate_action_row().
2. De ticker-zekerheid-route filtert nu voordat er gegroepeerd wordt.

Draait geheel offline voor de find_ticker_detailed-tests (geen yahooquery/
netwerk nodig: een corporate-action-rij retourneert al vóór er gezocht
wordt). De route-test raakt de echte database aan (net als
tests/test_wijzig_code_db.py) en wordt overgeslagen zonder DATABASE_URL.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

import analysis
from analysis import find_ticker_detailed, _is_corporate_action_row


class TestIsCorporateActionRow(unittest.TestCase):
    def test_beurs_deg_is_corporate_action(self):
        self.assertTrue(_is_corporate_action_row({"beurs": "DEG", "product": "GEWOON PRODUCT"}))

    def test_non_tradeable_in_product_op_andere_beurs_is_corporate_action(self):
        # Het BYD-geval: NON TRADEABLE-rij, maar niet op beurs "DEG".
        self.assertTrue(_is_corporate_action_row({"beurs": "TDG", "product": "BYD CO LTD - NON TRADEABLE"}))

    def test_normale_rij_is_geen_corporate_action(self):
        self.assertFalse(_is_corporate_action_row({"beurs": "EAM", "product": "AKZO NOBEL NV"}))


def _geen_netwerk_toegestaan(*args, **kwargs):
    raise AssertionError("find_ticker_detailed had voor een corporate-action-rij nooit mogen gaan zoeken")


class TestFindTickerDetailedFiltertCorporateActionRijen(unittest.TestCase):
    """find_ticker_detailed() moet een corporate-action-rij meteen als
    ticker=None/geen_match teruggeven, zonder een yahooquery-zoekopdracht te
    starten -- of het nu via beurs=DEG is, of via 'NON TRADEABLE' in de
    productnaam op een andere beurs (het BYD-geval)."""

    def setUp(self):
        patcher1 = patch.object(analysis, "_zoek_product_progressief", side_effect=_geen_netwerk_toegestaan)
        patcher1.start()
        self.addCleanup(patcher1.stop)
        patcher2 = patch.object(analysis, "_yahoo_search", side_effect=_geen_netwerk_toegestaan)
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def test_beurs_deg_geeft_geen_match_zonder_te_zoeken(self):
        resultaat = find_ticker_detailed("SPLIT BOEKING", "NL0000000001", "DEG")
        self.assertIsNone(resultaat["ticker"])
        self.assertEqual(resultaat["zekerheid"], "geen_match")
        self.assertEqual(resultaat["alternatieven"], [])

    def test_non_tradeable_op_niet_deg_beurs_geeft_geen_match_zonder_te_zoeken(self):
        # Reproductie van het BYD-geval: NON TRADEABLE, maar de beurskolom
        # was hier kennelijk niet "DEG".
        resultaat = find_ticker_detailed("BYD CO LTD - NON TRADEABLE", "CNE100000296", "TDG")
        self.assertIsNone(resultaat["ticker"])
        self.assertEqual(resultaat["zekerheid"], "geen_match")
        self.assertEqual(resultaat["alternatieven"], [])


SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTickerZekerheidRouteFiltertCorporateActionRijen(unittest.TestCase):
    """GET /api/portfolio/<code>/ticker-zekerheid mag een NON TRADEABLE-rij
    (ook op een niet-DEG-beurs) nooit als eigen positie teruggeven."""

    CODE = "TCA"

    def _leeg_op(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def setUp(self):
        self._leeg_op()
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.CODE, "unittest"))
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.CODE, date(2023, 1, 10), "AKZO NOBEL NV", "NL0013267909", "EAM", "AKZA.AS", 10, 50.0, -500.0,
             "ORDER-1", "AKZO NOBEL NV"),
        )
        # Het BYD-geval: NON TRADEABLE, maar niet op beurs "DEG".
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.CODE, date(2023, 6, 1), "BYD CO LTD - NON TRADEABLE", "CNE100000296", "TDG", None, 5, 0.0, 0.0,
             "ORDER-2", "BYD CO LTD - NON TRADEABLE"),
        )
        conn.commit()
        cur.close()
        conn.close()

        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def tearDown(self):
        self._leeg_op()

    def test_non_tradeable_rij_verschijnt_niet_als_eigen_positie(self):
        with patch.object(
            self.app_module, "verifieer_tickers_met_prijs_parallel",
            side_effect=lambda posities: [
                {"ticker": "AKZA.AS", "zekerheid": "zeker", "alternatieven": [], "prijs_checks": []}
                for _ in posities
            ],
        ) as mock_check:
            res = self.client.get(f"/api/portfolio/{self.CODE}/ticker-zekerheid")

        self.assertEqual(res.status_code, 200)
        # Maar 1 (ISIN, Beurs)-groep had verifieer_tickers_met_prijs_parallel
        # in mogen gaan -- de NON TRADEABLE-rij is er vóóraf uitgefilterd.
        self.assertEqual(len(mock_check.call_args.args[0]), 1)
        data = res.get_json()
        self.assertEqual(len(data["posities"]), 1)
        self.assertEqual(data["posities"][0]["isin"], "NL0013267909")

    def test_regressie_normale_corporate_action_rij_op_beurs_deg_blijft_gefilterd(self):
        cur_code = self.CODE
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cur_code, date(2023, 6, 2), "GEWONE SPLIT BOEKING", "NL0013267909", "DEG", None, 5, 0.0, 0.0,
             "ORDER-3", "GEWONE SPLIT BOEKING"),
        )
        conn.commit()
        cur.close()
        conn.close()

        with patch.object(
            self.app_module, "verifieer_tickers_met_prijs_parallel",
            side_effect=lambda posities: [
                {"ticker": "AKZA.AS", "zekerheid": "zeker", "alternatieven": [], "prijs_checks": []}
                for _ in posities
            ],
        ) as mock_check:
            res = self.client.get(f"/api/portfolio/{self.CODE}/ticker-zekerheid")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mock_check.call_args.args[0]), 1)
        data = res.get_json()
        self.assertEqual(len(data["posities"]), 1)


if __name__ == "__main__":
    unittest.main()
