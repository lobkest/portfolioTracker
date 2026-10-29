"""Verbroken databaseverbinding en onverwachte fouten bij /upload (gemockt, geen database)."""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend.debug_utils import maak_foutcode, FOUTCODE_LENGTE
from backend.voortgang import Voortgang, zet_voortgang, reset_voortgang, meld_stap, huidige_stap_tekst, STAPPEN_UPLOAD_OPSLAAN


def _nep_verbinding(gesloten=0):
    conn = MagicMock()
    conn.closed = gesloten
    return conn


class TestDbTransactieRollback(unittest.TestCase):
    def test_kapotte_rollback_vervangt_de_oorspronkelijke_fout_niet(self):
        conn = _nep_verbinding()
        conn.rollback.side_effect = psycopg2.InterfaceError("connection already closed")
        with patch.object(db, "db_connect", return_value=conn):
            with self.assertRaises(psycopg2.OperationalError) as ctx:
                with db.db_transactie():
                    raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")
        self.assertIn("SSL connection", str(ctx.exception))
        conn.close.assert_called_once()

    def test_gesloten_verbinding_slaat_de_rollback_over(self):
        conn = _nep_verbinding(gesloten=2)
        with patch.object(db, "db_connect", return_value=conn):
            with self.assertRaises(ValueError):
                with db.db_transactie():
                    raise ValueError("x")
        conn.rollback.assert_not_called()
        conn.close.assert_called_once()

    def test_gewone_fout_doet_nog_steeds_een_rollback(self):
        conn = _nep_verbinding()
        with patch.object(db, "db_connect", return_value=conn):
            with self.assertRaises(ValueError):
                with db.db_transactie():
                    raise ValueError("x")
        conn.rollback.assert_called_once()

    def test_kapotte_verbinding_gaat_niet_terug_de_pool_in(self):
        with patch("psycopg2.connect", return_value=_nep_verbinding()), \
                patch.dict(os.environ, {"DATABASE_URL": "postgresql://x"}), \
                patch.object(db, "_pool", None), patch.dict(db._laatst_gebruikt, clear=True):
            verbinding = db._open_verbinding()
            verbinding._verbinding.rollback.side_effect = psycopg2.InterfaceError("kapot")
            verbinding.close()
            self.assertEqual(len(db._pool._used), 0)
            self.assertEqual(len(db._pool._pool), 0)


class TestIsVerbindingsfout(unittest.TestCase):
    def test_soorten(self):
        self.assertTrue(db.is_verbindingsfout(psycopg2.OperationalError()))
        self.assertTrue(db.is_verbindingsfout(psycopg2.InterfaceError()))
        self.assertFalse(db.is_verbindingsfout(psycopg2.IntegrityError()))
        self.assertFalse(db.is_verbindingsfout(ValueError()))


class TestFoutcode(unittest.TestCase):
    def test_lengte_en_tekens(self):
        for _ in range(50):
            code = maak_foutcode()
            self.assertEqual(len(code), FOUTCODE_LENGTE)
            self.assertEqual(FOUTCODE_LENGTE, 6)
            self.assertTrue(code.isalnum() and code.upper() == code)


class TestHuidigeStapTekst(unittest.TestCase):
    def test_zonder_voortgang_geen_tekst(self):
        self.assertIsNone(huidige_stap_tekst())

    def test_laatst_gestarte_stap(self):
        token = zet_voortgang(Voortgang(STAPPEN_UPLOAD_OPSLAAN))
        try:
            self.assertIsNone(huidige_stap_tekst())
            meld_stap("tickers_zoeken")
            self.assertEqual(huidige_stap_tekst(), "Tickers zoeken")
        finally:
            reset_voortgang(token)


class TestUploadUitvoerenFouten(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as app_module
        cls.m = app_module

    def _draai(self, fout, stap=None):
        token = zet_voortgang(Voortgang(STAPPEN_UPLOAD_OPSLAAN))
        try:
            if stap:
                meld_stap(stap)
            with self.m.app.test_request_context("/upload", method="POST"), \
                    patch.object(self.m, "_upload_impl", side_effect=fout), \
                    patch.object(self.m, "db_log_verbinding_samenvatting"), \
                    patch.object(self.m, "print_traceback"):
                response, status = self.m._upload_uitvoeren({})
                return status, response.get_json()["error"]
        finally:
            reset_voortgang(token)

    def test_verbindingsfout_geeft_503_met_databasemelding(self):
        status, melding = self._draai(psycopg2.OperationalError("SSL connection has been closed unexpectedly"),
                                      "portfolio_zoeken")
        self.assertEqual(status, 503)
        self.assertEqual(melding, self.m.MELDING_DB_ONDERBROKEN)

    def test_interfaceerror_geeft_ook_503(self):
        status, melding = self._draai(psycopg2.InterfaceError("connection already closed"))
        self.assertEqual(status, 503)
        self.assertEqual(melding, self.m.MELDING_DB_ONDERBROKEN)

    def test_andere_fout_geeft_500_met_stap_en_foutcode(self):
        with patch("builtins.print") as geprint:
            status, melding = self._draai(KeyError("geheim"), "portfolio_zoeken")
        self.assertEqual(status, 500)
        self.assertIn("'Controleren of deze portfolio al bestaat'", melding)
        self.assertRegex(melding, r"foutcode [A-Z0-9]{6}\.$")
        self.assertNotIn("duurde te lang", melding)
        self.assertNotIn("geheim", melding)
        foutcode = melding.rsplit(" ", 1)[1].rstrip(".")
        logregels = [str(c.args[0]) for c in geprint.call_args_list if c.args]
        self.assertTrue(any("[upload] ONVERWACHTE FOUT" in r and foutcode in r for r in logregels))

    def test_zonder_actieve_stap_geen_stapnaam(self):
        status, melding = self._draai(KeyError("x"))
        self.assertEqual(status, 500)
        self.assertNotIn("bij de stap", melding)
        self.assertRegex(melding, r"foutcode [A-Z0-9]{6}")

    def test_fallbackmelding_noemt_geen_timeout(self):
        self.assertNotIn("duurde te lang", self.m.MELDING_UPLOAD_MISLUKT)


if __name__ == "__main__":
    unittest.main()
