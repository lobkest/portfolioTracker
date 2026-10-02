"""
Route-level tests voor /upload (niet_opslaan-pad) en
/api/ticker-zekerheid-check (zie CLAUDE.md: Yahoo en tickers): een 'niet opslaan'-analyse van een grotere
portfolio liep vast doordat de dure, prijs-geverifieerde ticker-check altijd
synchroon voor de volle portfolio draaide (zie
tests/test_niet_opslaan_performance.py voor de kwantitatieve bevestiging
daarvan), zonder dat de gebruiker een foutmelding te zien kreeg.

app.py draait init_db() alleen als DATABASE_URL is ingesteld, dus
'import app' werkt zonder database. TestNietOpslaanGebruiktGoedkopeTickerMatch
wordt zonder DATABASE_URL nog overgeslagen: portfolio_orchestratie.get_prices()
is daar niet gemockt en raakt de koerscache en Yahoo.
"""
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from upload_verwerking import VERWACHTE_KOLOMMEN

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


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
    for kolom in VERWACHTE_KOLOMMEN:
        if kolom not in df.columns:
            df[kolom] = None
    buf = BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


BASIS_RESULTAAT = {
    "ticker": "TEST.AS", "zekerheid": "zeker", "waarschuwing": None,
    "land": None, "sector": None, "top_holding_land": None, "valuta": None,
    "fondsfamilie": None, "category": None, "quote_type": None,
    "excel_beurs": "EAM", "yahoo_beurs": None, "beurs_klopt": None,
    "prijs_checks": [], "alternatieven": [],
}


@vereist_database
class TestNietOpslaanGebruiktGoedkopeTickerMatch(unittest.TestCase):
    """Regressietest: het 'niet opslaan'-pad mag de dure,
    prijs-geverifieerde check niet meer synchroon voor de volle portfolio
    aanroepen (zie de niet_opslaan-tak in _upload_impl())."""

    def setUp(self):
        import app as app_module
        import upload_verwerking
        self.app_module = app_module
        self.upload_verwerking = upload_verwerking
        self.client = app_module.app.test_client()

    def test_niet_opslaan_roept_de_dure_check_niet_aan(self):
        with patch.object(self.app_module, "verifieer_tickers_met_prijs_parallel") as mock_dure_check, \
             patch.object(self.upload_verwerking, "basis_ticker_zekerheid_parallel",
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


class TestUploadGeeftNetteFoutrespons(unittest.TestCase):
    """Opdracht 3: een onverwachte fout tijdens de analyse mag nooit een
    kale crash of hangende request opleveren -- altijd een nette JSON-
    foutrespons met status 500 (zie de try/except in upload() rond
    _upload_impl())."""

    def setUp(self):
        import app as app_module
        import upload_verwerking
        self.app_module = app_module
        self.upload_verwerking = upload_verwerking
        self.client = app_module.app.test_client()

    def test_onverwachte_fout_in_niet_opslaan_pad_geeft_500_met_foutmelding(self):
        with patch.object(self.upload_verwerking, "basis_ticker_zekerheid_parallel",
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


class TestTickerZekerheidCheckEndpoint(unittest.TestCase):
    """De losse, door de gebruiker aangevraagde uitgebreide check voor een
    'niet opslaan'-analyse (zie toonInstellingenTickerBasis() in tabs/ticker_zekerheid.js)."""

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
