"""
Unit tests voor diagnostiek.py (meldingen per laadbeurt, Instellingen >
Diagnostiek) en de Wisselkoersen-meldingen die erop aansluiten:
- Excel-bron in _normaliseer_transactie_kolommen() (upload_verwerking.py)
- Yahoo-FX-reeks in _fx_prijzen_serie() (prijzen.py)
- meldingen bewaren/opnieuw meegeven bij een cache-hit in
  _haal_portfolio_basis() (portfolio_orchestratie.py)

De valuta-meldingen van _haal_valuta_op()/_converteer_naar_eur() staan in
tests/test_valuta_waarschuwing.py.

Draait geheel offline: database en Yahoo worden gemockt, app.py wordt niet
geïmporteerd (een losse Flask-app levert de request-context).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnostiek
from diagnostiek import meld, haal_meldingen, CATEGORIE_WISSELKOERSEN, GOED, INFO, LET_OP, FOUT


class TestMeld(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_buiten_context_stille_no_op(self):
        meld(CATEGORIE_WISSELKOERSEN, GOED, "tekst")  # mag niet crashen
        self.assertEqual(haal_meldingen(), [])

    def test_verzamelt_binnen_request_in_volgorde(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "eerste")
            meld(CATEGORIE_WISSELKOERSEN, FOUT, "tweede", sleutel="k2")
            self.assertEqual(haal_meldingen(), [
                {"categorie": CATEGORIE_WISSELKOERSEN, "niveau": GOED, "tekst": "eerste", "sleutel": "eerste"},
                {"categorie": CATEGORIE_WISSELKOERSEN, "niveau": FOUT, "tekst": "tweede", "sleutel": "k2"},
            ])

    def test_nieuwe_request_begint_leeg(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "a")
        with self.app.test_request_context():
            self.assertEqual(haal_meldingen(), [])

    def test_zelfde_categorie_en_sleutel_vervangt_op_dezelfde_plek(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "oud", sleutel="USDEUR=X")
            meld(CATEGORIE_WISSELKOERSEN, INFO, "ander", sleutel="GBPEUR=X")
            meld(CATEGORIE_WISSELKOERSEN, FOUT, "nieuw", sleutel="USDEUR=X")
            meldingen = haal_meldingen()
        self.assertEqual([m["tekst"] for m in meldingen], ["nieuw", "ander"])
        self.assertEqual(meldingen[0]["niveau"], FOUT)

    def test_zelfde_sleutel_andere_categorie_blijft_apart(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "a", sleutel="x")
            meld("Andere", GOED, "b", sleutel="x")
            self.assertEqual(len(haal_meldingen()), 2)

    def test_ongeldig_niveau_wordt_info(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, "ONZIN", "tekst")
            self.assertEqual(haal_meldingen()[0]["niveau"], INFO)

    def test_haal_meldingen_geeft_kopie(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "tekst")
            kopie = haal_meldingen()
            kopie[0]["tekst"] = "gewijzigd"
            kopie.append({"x": 1})
            self.assertEqual(haal_meldingen()[0]["tekst"], "tekst")
            self.assertEqual(len(haal_meldingen()), 1)

    def test_meldingen_sinds_en_meld_opnieuw(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, GOED, "al bekend")
            voor = haal_meldingen()
            meld(CATEGORIE_WISSELKOERSEN, GOED, "nieuw")
            nieuw = diagnostiek.meldingen_sinds(voor)
        self.assertEqual([m["tekst"] for m in nieuw], ["nieuw"])
        with self.app.test_request_context():
            diagnostiek.meld_opnieuw(nieuw)
            diagnostiek.meld_opnieuw(nieuw)  # ontdubbeld
            diagnostiek.meld_opnieuw(None)
            self.assertEqual(haal_meldingen(), nieuw)


class TestVoegDiagnostiekToe(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_voegt_sleutel_toe_aan_response(self):
        with self.app.test_request_context():
            meld(CATEGORIE_WISSELKOERSEN, LET_OP, "tekst")
            resultaat = diagnostiek.voeg_diagnostiek_toe({"code": "ABC"})
        self.assertEqual(resultaat["code"], "ABC")
        self.assertEqual([m["tekst"] for m in resultaat["diagnostiek"]], ["tekst"])

    def test_zonder_meldingen_lege_lijst(self):
        with self.app.test_request_context():
            self.assertEqual(diagnostiek.voeg_diagnostiek_toe({})["diagnostiek"], [])

    def test_none_blijft_none(self):
        with self.app.test_request_context():
            self.assertIsNone(diagnostiek.voeg_diagnostiek_toe(None))


class TestExcelWisselkoersMelding(unittest.TestCase):
    """_normaliseer_transactie_kolommen(): juiste melding, en _koers_eur
    identiek aan vóór de wijziging (Koers / Wisselkoers, anders Koers)."""

    def setUp(self):
        import upload_verwerking
        self.uv = upload_verwerking
        self.app = Flask(__name__)

    def _df(self, wisselkoersen=None):
        df = pd.DataFrame({
            "ISIN": ["US1", "NL1", "US2"], "Beurs": ["NDQ", "EAM", "NSY"],
            "Koers": [110.0, 50.0, 22.0],
        })
        if wisselkoersen is not None:
            df["Wisselkoers"] = wisselkoersen
        return df

    def _normaliseer(self, df):
        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            uit = self.uv._normaliseer_transactie_kolommen(df)
            return uit, haal_meldingen()

    def test_kolom_met_waarden_geeft_goed_en_zelfde_koers_eur(self):
        uit, meldingen = self._normaliseer(self._df([1.1, None, 1.1]))
        # 110 / 1,1 = 100; EUR-rij ongewijzigd 50; 22 / 1,1 = 20
        self.assertEqual(uit["_koers_eur"].round(6).tolist(), [100.0, 50.0, 20.0])
        self.assertEqual(len(meldingen), 1)
        self.assertEqual(meldingen[0]["niveau"], GOED)
        self.assertEqual(meldingen[0]["categorie"], CATEGORIE_WISSELKOERSEN)
        self.assertEqual(meldingen[0]["tekst"], "Wisselkoers uit Excel gebruikt voor 2 van 3 transacties.")

    def test_kolom_zonder_waarden_geeft_info(self):
        uit, meldingen = self._normaliseer(self._df([None, None, 0]))
        self.assertEqual(uit["_koers_eur"].tolist(), [110.0, 50.0, 22.0])
        self.assertEqual([m["niveau"] for m in meldingen], [INFO])
        self.assertIn("aanwezig, maar geen enkele transactie", meldingen[0]["tekst"])

    def test_kolom_ontbreekt_geeft_let_op(self):
        uit, meldingen = self._normaliseer(self._df())
        self.assertEqual(uit["_koers_eur"].tolist(), [110.0, 50.0, 22.0])
        self.assertEqual([m["niveau"] for m in meldingen], [LET_OP])
        self.assertIn("ontbreekt in het Excel-bestand", meldingen[0]["tekst"])


class TestFxReeksMelding(unittest.TestCase):
    """_fx_prijzen_serie() met get_prices gemockt."""

    def setUp(self):
        import prijzen
        self.prijzen = prijzen
        self.app = Flask(__name__)

    @patch("prijzen.get_prices")
    def test_lege_serie_geeft_fout(self, mock_get_prices):
        mock_get_prices.return_value = pd.DataFrame()
        with self.app.test_request_context():
            reeks = self.prijzen._fx_prijzen_serie("USD")
            meldingen = haal_meldingen()
        self.assertTrue(reeks.empty)
        self.assertEqual(len(meldingen), 1)
        self.assertEqual(meldingen[0]["niveau"], FOUT)
        self.assertEqual(meldingen[0]["sleutel"], "USDEUR=X")
        self.assertIn("USD -> EUR: geen koersdata van Yahoo (USDEUR=X)", meldingen[0]["tekst"])

    @patch("prijzen.get_prices")
    def test_gevulde_serie_geeft_goed(self, mock_get_prices):
        index = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        mock_get_prices.return_value = pd.DataFrame({"USDEUR=X": [0.91, None, 0.92]}, index=index)
        with self.app.test_request_context():
            self.prijzen._noteer_fx_bron("USDEUR=X", self.prijzen.FX_BRON_GEDOWNLOAD)
            self.prijzen._fx_prijzen_serie("USD")
            meldingen = haal_meldingen()
        self.assertEqual(len(meldingen), 1)
        self.assertEqual(meldingen[0]["niveau"], GOED)
        self.assertEqual(
            meldingen[0]["tekst"],
            "USD -> EUR via Yahoo (USDEUR=X): 2 koersen, vanaf 2024-01-02; gedownload.",
        )

    @patch("prijzen.get_prices")
    def test_buiten_context_geen_crash(self, mock_get_prices):
        mock_get_prices.return_value = pd.DataFrame()
        self.assertTrue(self.prijzen._fx_prijzen_serie("USD").empty)

    def test_noteer_fx_bron_negeert_gewone_tickers(self):
        with self.app.test_request_context():
            self.prijzen._noteer_fx_bron("AAPL", self.prijzen.FX_BRON_CACHE)
            self.prijzen._noteer_fx_bron("GBPEUR=X", self.prijzen.FX_BRON_VERVERST)
            self.assertIsNone(self.prijzen._fx_bron("AAPL"))
            self.assertEqual(self.prijzen._fx_bron("GBPEUR=X"), self.prijzen.FX_BRON_VERVERST)


# Kolomvolgorde zoals de SELECT in _haal_portfolio_basis().
TRANSACTIE_RIJ = ("2024-01-01", "TEST", "US0000000001", "NDQ", "TEST", 1.0, 100.0, -100.0, "TEST", None, None, None)
TEST_CODE = "ZZTESTDIAGNOSTIEK"


def _fake_conn():
    cur = MagicMock()
    cur.fetchone.return_value = ("Testportfolio",)
    cur.fetchall.return_value = [TRANSACTIE_RIJ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestBasisCacheMeldingen(unittest.TestCase):
    """Bij een cache-hit slaat _haal_portfolio_basis() get_prices() over;
    de meldingen van de miss moeten toch opnieuw meekomen."""

    def setUp(self):
        import portfolio_orchestratie
        self.po = portfolio_orchestratie
        self.app = Flask(__name__)
        self.po._wis_portfolio_basis_cache(TEST_CODE)

    def tearDown(self):
        self.po._wis_portfolio_basis_cache(TEST_CODE)

    @patch("portfolio_orchestratie.get_prices")
    @patch("portfolio_orchestratie.get_db_connection")
    def test_hit_geeft_meldingen_opnieuw_mee(self, mock_conn, mock_get_prices):
        mock_conn.side_effect = lambda: _fake_conn()

        def fake_get_prices(tickers, start_date, verversen=True):
            meld(CATEGORIE_WISSELKOERSEN, GOED, "USD -> EUR via Yahoo", sleutel="USDEUR=X")
            return pd.DataFrame()
        mock_get_prices.side_effect = fake_get_prices

        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            meld(CATEGORIE_WISSELKOERSEN, GOED, "van voor de basis", sleutel="excel")
            self.po._haal_portfolio_basis(TEST_CODE)
            eerste = haal_meldingen()

        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            self.po._haal_portfolio_basis(TEST_CODE)
            tweede = haal_meldingen()

        self.assertEqual(mock_get_prices.call_count, 1)  # tweede keer was een hit
        # Sinds de Laadtijden-categorie meldt meet_tijd() ook de basis-fasen.
        self.assertEqual(len([m for m in eerste if m["categorie"] == CATEGORIE_WISSELKOERSEN]), 2)
        self.assertIn(diagnostiek.CATEGORIE_LAADTIJDEN, [m["categorie"] for m in eerste])
        # Alleen wat tijdens de basis ontstond, niet de eerdere Excel-melding,
        # en geen laadtijden (die tijd is bij een hit niet besteed).
        self.assertEqual([m["sleutel"] for m in tweede], ["USDEUR=X"])


if __name__ == "__main__":
    unittest.main()
