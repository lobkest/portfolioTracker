"""
Route-level tests voor /upload (niet_opslaan-pad) en
/api/ticker-zekerheid-check -- dekt het Statistieken-incident van
2026-08-31 (zie CLAUDE.md): een 'niet opslaan'-analyse van een grotere
portfolio liep vast doordat de dure, prijs-geverifieerde ticker-check altijd
synchroon voor de volle portfolio draaide (zie
tests/test_niet_opslaan_performance.py voor de kwantitatieve bevestiging
daarvan), zonder dat de gebruiker een foutmelding te zien kreeg.

Raakt de echte database aan, want app.py roept init_db() op moduleniveau
aan (buiten if __name__ == '__main__', zie CLAUDE.md) -- 'import app' zou
zonder DATABASE_URL dus al bij de IMPORT crashen. Daarom (net als
tests/test_dividend_db.py) overgeslagen zonder DATABASE_URL, met de import
van 'app' pas binnen setUp() van elke (dan overgeslagen) testklasse, nooit
op moduleniveau.
"""
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# db.py laadt .env ook zelf, maar pas bij de import van app.py binnen setUp()
# -- te laat voor de skipUnless-decorators hieronder, die al bij het
# IMPORTEREN van dit testbestand geëvalueerd worden. Zonder deze eigen
# load_dotenv() zou DATABASE_URL hier nog leeg zijn wanneer dit bestand als
# eerste (of enige) module geïmporteerd wordt, en zouden deze tests dus ten
# onrechte overgeslagen worden ondanks een geldige lokale .env.
load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


def _maak_transacties_excel(n_posities=5):
    """Synthetisch transactiebestand met n_posities verschillende (ISIN,
    Beurs)-groepen, elk met 2 transacties."""
    rijen = []
    for i in range(n_posities):
        for datum in ("2023-01-10", "2023-06-10"):
            rijen.append({
                "Datum": datum, "Tijd": "12:00", "Product": f"TESTFONDS {i}",
                "ISIN": f"NL{i:010d}", "Beurs": "EAM", "Aantal": 10.0,
                "Koers": 100.0, "Totaal EUR": -1000.0, "Order ID": f"SYN-{i}-{datum}",
            })
    df = pd.DataFrame(rijen)
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


BASIS_RESULTAAT = {
    "ticker": "TEST.AS", "zekerheid": "zeker", "waarschuwing": None,
    "land": None, "sector": None, "top_holding_land": None, "valuta": None,
    "fondsfamilie": None, "category": None, "quote_type": None,
    "excel_beurs": "EAM", "yahoo_beurs": None, "beurs_klopt": None,
    "prijs_checks": [], "alternatieven": [], "basis_alleen": True,
}


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestNietOpslaanGebruiktGoedkopeTickerMatch(unittest.TestCase):
    """Regressietest voor het incident: het 'niet opslaan'-pad mag de dure,
    prijs-geverifieerde check niet meer synchroon voor de volle portfolio
    aanroepen (zie de niet_opslaan-tak in _upload_impl())."""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_niet_opslaan_roept_de_dure_check_niet_aan(self):
        with patch.object(self.app_module, "verifieer_tickers_met_prijs_parallel") as mock_dure_check, \
             patch.object(self.app_module, "basis_ticker_zekerheid_parallel",
                           side_effect=lambda posities, **kw: [dict(BASIS_RESULTAAT) for _ in posities]) as mock_basis, \
             patch.object(self.app_module, "get_prices", return_value=pd.DataFrame()):
            excel = _maak_transacties_excel(n_posities=5)
            res = self.client.post(
                "/upload",
                data={"niet_opslaan": "on", "bestand1": (excel, "transacties.xlsx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 200)
        mock_dure_check.assert_not_called()
        mock_basis.assert_called_once()
        self.assertEqual(len(mock_basis.call_args.args[0]), 5)
        data = res.get_json()
        self.assertEqual(len(data["ticker_zekerheid"]), 5)
        self.assertEqual(len(data["ticker_posities_ruw"]), 5)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestUploadGeeftNetteFoutrespons(unittest.TestCase):
    """Opdracht 3: een onverwachte fout tijdens de analyse mag nooit een
    kale crash of hangende request opleveren -- altijd een nette JSON-
    foutrespons met status 500 (zie de try/except in upload() rond
    _upload_impl())."""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_onverwachte_fout_in_niet_opslaan_pad_geeft_500_met_foutmelding(self):
        with patch.object(self.app_module, "basis_ticker_zekerheid_parallel",
                           side_effect=RuntimeError("gesimuleerde Yahoo-storing")):
            excel = _maak_transacties_excel(n_posities=2)
            res = self.client.post(
                "/upload",
                data={"niet_opslaan": "on", "bestand1": (excel, "transacties.xlsx")},
                content_type="multipart/form-data",
            )

        self.assertEqual(res.status_code, 500)
        data = res.get_json()
        self.assertIn("error", data)
        self.assertTrue(data["error"])


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestTickerZekerheidCheckEndpoint(unittest.TestCase):
    """De losse, door de gebruiker aangevraagde uitgebreide check voor een
    'niet opslaan'-analyse (zie toonInstellingenTickerBasis() in app.js)."""

    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_geen_posities_geeft_400(self):
        res = self.client.post("/api/ticker-zekerheid-check", json={"posities": []})
        self.assertEqual(res.status_code, 400)

    def test_succesvolle_check_geeft_posities_met_isin_terug(self):
        with patch.object(
            self.app_module, "verifieer_tickers_met_prijs_parallel",
            return_value=[{"ticker": "AAPL", "zekerheid": "zeker", "alternatieven": [], "prijs_checks": []}],
        ):
            res = self.client.post("/api/ticker-zekerheid-check", json={
                "posities": [{
                    "naam": "APPLE INC", "isin": "US0378331005", "beurs": "NSY",
                    "transacties": [{"datum": "2023-01-10", "koers": 100.0}],
                }],
            })

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["posities"][0]["isin"], "US0378331005")
        self.assertEqual(data["posities"][0]["naam"], "APPLE INC")

    def test_fout_tijdens_verificatie_geeft_500(self):
        with patch.object(self.app_module, "verifieer_tickers_met_prijs_parallel",
                           side_effect=RuntimeError("gesimuleerde timeout")):
            res = self.client.post("/api/ticker-zekerheid-check", json={
                "posities": [{"naam": "APPLE INC", "isin": "US0378331005", "beurs": "NSY", "transacties": []}],
            })

        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())


if __name__ == "__main__":
    unittest.main()
