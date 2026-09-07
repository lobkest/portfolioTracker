"""
Unit tests voor het "ticker-informatie opnieuw bepalen"-vinkje bij het
ophalen van een portfolio via code (GET /api/portfolio/<code>?herbepaal_
alle_tickers=true) -- hergebruikt dezelfde backfill_verouderde_tickers(
code, forceer=...) als de upload-flow (zie CLAUDE.md/opdracht "vinkje
ticker-informatie opnieuw bepalen ... ook bij Ophalen met code").

Uitgezocht vóór deze wijziging: backfill_verouderde_tickers() werd bij het
ophalen via code NOOIT aangeroepen -- alleen bij /upload naar een
bestaande code (build_portfolio_response() leest alleen de al opgeslagen
tickers). Zonder de nieuwe parameter blijft dat exact zo; met de parameter
komt er een expliciete, opt-in aanroep bij.

Raakt de echte database aan via 'import app' (init_db() draait bij import,
zie CLAUDE.md) -- daarom overgeslagen zonder DATABASE_URL, net als
tests/test_gefaseerd_laden.py. build_portfolio_response() en
backfill_verouderde_tickers() worden gemockt, dus geen echte portfolio
nodig en geen Yahoo-calls.
"""
import os
import sys
import unittest
from unittest.mock import patch

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestHerbepaalAlleTickersBijOphalen(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_zonder_parameter_roept_backfill_niet_aan(self):
        # Standaardgedrag (vóór en na deze wijziging): geen ongevraagde
        # performance-impact voor de normale, meest gebruikte flow.
        with patch.object(self.app_module, "build_portfolio_response", return_value={"code": "ABC"}), \
             patch.object(self.app_module, "backfill_verouderde_tickers") as mock_backfill:
            resp = self.client.get("/api/portfolio/ABC")

        mock_backfill.assert_not_called()
        self.assertEqual(resp.status_code, 200)

    def test_herbepaal_alle_tickers_true_roept_backfill_geforceerd_aan(self):
        with patch.object(self.app_module, "build_portfolio_response", return_value={"code": "ABC"}), \
             patch.object(self.app_module, "backfill_verouderde_tickers") as mock_backfill:
            resp = self.client.get("/api/portfolio/ABC?herbepaal_alle_tickers=true")

        mock_backfill.assert_called_once_with("ABC", forceer=True)
        self.assertEqual(resp.status_code, 200)

    def test_hoofdletters_in_parameterwaarde_werken_ook(self):
        with patch.object(self.app_module, "build_portfolio_response", return_value={"code": "ABC"}), \
             patch.object(self.app_module, "backfill_verouderde_tickers") as mock_backfill:
            self.client.get("/api/portfolio/ABC?herbepaal_alle_tickers=True")

        mock_backfill.assert_called_once_with("ABC", forceer=True)

    def test_verkeerde_of_ontbrekende_waarde_valt_fail_safe_terug_op_uit(self):
        for waarde in ["", "false", "1", "yes", "onwaar", "TrueX"]:
            with self.subTest(waarde=waarde):
                with patch.object(self.app_module, "build_portfolio_response", return_value={"code": "ABC"}), \
                     patch.object(self.app_module, "backfill_verouderde_tickers") as mock_backfill:
                    self.client.get(f"/api/portfolio/ABC?herbepaal_alle_tickers={waarde}")

                mock_backfill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
