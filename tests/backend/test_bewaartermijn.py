"""Bewaartermijn: verloopgrens, opruimen met de één-keer-per-uur-rem, en het bijwerken van laatst_gebruikt per route.
Alles zonder database: db_connect en de db_-functies zijn gemockt."""
import io
import os
import sys
import unittest
from contextlib import contextmanager, nullcontext, redirect_stdout
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import bewaartermijn
import db
from bewaartermijn import verloopgrens, ruim_verlopen_portfolios_op, OPRUIM_MIN_INTERVAL_SECONDEN

NU = datetime(2026, 10, 16, 12, 0, 0)


class TestVerloopgrens(unittest.TestCase):
    def test_zes_maanden_terug(self):
        self.assertEqual(verloopgrens(NU), datetime(2026, 4, 16, 12, 0, 0))

    def test_maandeinde_wordt_afgekapt_op_de_laatste_dag(self):
        self.assertEqual(verloopgrens(datetime(2026, 8, 31)), datetime(2026, 2, 28))

    def test_query_telt_alleen_strikt_oudere_portfolios_als_verlopen(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchall.return_value = [("AAA",), ("BBB",)]
        grens = verloopgrens(NU)
        with patch.object(db, "db_connect", return_value=conn):
            codes = db.db_get_verlopen_codes(grens)
        sql, params = cur.execute.call_args.args
        self.assertIn("laatst_gebruikt < %s", sql)
        self.assertEqual(params, (grens,))
        self.assertEqual(codes, ["AAA", "BBB"])


class _MetSchoneRem(unittest.TestCase):
    def setUp(self):
        bewaartermijn._laatste_opruiming["tijdstip"] = None
        self.addCleanup(bewaartermijn._laatste_opruiming.update, tijdstip=None)


class TestRuimOp(_MetSchoneRem):
    def test_verwijdert_alle_verlopen_codes_met_de_juiste_grens(self):
        with patch.object(bewaartermijn, "db_get_verlopen_codes", return_value=["AAA", "BBB"]) as haal, \
                patch.object(bewaartermijn, "db_delete_portfolio") as verwijder:
            aantal = ruim_verlopen_portfolios_op(nu=NU)
        haal.assert_called_once_with(verloopgrens(NU))
        self.assertEqual([c.args[0] for c in verwijder.call_args_list], ["AAA", "BBB"])
        self.assertEqual(aantal, 2)

    def test_logt_alleen_het_aantal_nooit_de_codes(self):
        uitvoer = io.StringIO()
        with patch.object(bewaartermijn, "db_get_verlopen_codes", return_value=["QXZ"]), \
                patch.object(bewaartermijn, "db_delete_portfolio"), redirect_stdout(uitvoer):
            ruim_verlopen_portfolios_op(nu=NU)
        self.assertIn("1 verlopen", uitvoer.getvalue())
        self.assertNotIn("QXZ", uitvoer.getvalue())

    def test_een_fout_breekt_niets_en_logt_geen_details(self):
        uitvoer = io.StringIO()
        with patch.object(bewaartermijn, "db_get_verlopen_codes", side_effect=RuntimeError("code QXZ")), \
                redirect_stdout(uitvoer):
            self.assertIsNone(ruim_verlopen_portfolios_op(nu=NU))
        self.assertNotIn("QXZ", uitvoer.getvalue())


class TestEenKeerPerUur(_MetSchoneRem):
    def _ruim_op(self, klok):
        with patch.object(bewaartermijn, "db_get_verlopen_codes", return_value=[]) as haal:
            resultaat = ruim_verlopen_portfolios_op(nu=NU, klok=klok)
        return resultaat, haal

    def test_tweede_aanroep_binnen_het_uur_doet_niets(self):
        self.assertEqual(self._ruim_op(lambda: 1000.0)[0], 0)
        resultaat, haal = self._ruim_op(lambda: 1000.0 + OPRUIM_MIN_INTERVAL_SECONDEN - 1)
        self.assertIsNone(resultaat)
        haal.assert_not_called()

    def test_na_een_uur_wordt_er_weer_opgeruimd(self):
        self._ruim_op(lambda: 1000.0)
        resultaat, haal = self._ruim_op(lambda: 1000.0 + OPRUIM_MIN_INTERVAL_SECONDEN)
        self.assertEqual(resultaat, 0)
        haal.assert_called_once()


class _MetApp(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        patcher = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_app(self, naam, **kwargs):
        p = patch.object(self.m, naam, **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock


class TestOphalenMarkeertGebruikt(_MetApp):
    def setUp(self):
        super().setUp()
        self.markeer = self.patch_app("_markeer_gebruikt")
        self.patch_app("log_yahoo_call_samenvatting")
        self.patch_app("db_log_verbinding_samenvatting")

    def test_bestaande_code_wordt_gemarkeerd(self):
        self.patch_app("build_portfolio_response", return_value={"code": "ABC"})
        self.assertEqual(self.client.get("/api/portfolio/ABC").status_code, 200)
        self.markeer.assert_called_once_with("ABC")

    def test_onbekende_code_wordt_niet_gemarkeerd(self):
        self.patch_app("build_portfolio_response", return_value=None)
        self.assertEqual(self.client.get("/api/portfolio/ABC").status_code, 404)
        self.markeer.assert_not_called()

    def test_luie_endpoints_markeren_niet(self):
        self.patch_app("laad_transacties_en_resultaat", return_value=(pd.DataFrame(), None))
        self.client.get("/api/portfolio/ABC/rendement-over-tijd")
        self.markeer.assert_not_called()


class TestUploadMarkeertGebruikt(_MetApp):
    def _upload_opslaan(self, bestaand):
        cur = MagicMock()

        @contextmanager
        def transactie():
            yield cur

        self.patch_app("db_transactie", side_effect=transactie)
        self.patch_app("vul_synthetische_order_ids_aan", side_effect=lambda df: df)
        self.patch_app("vind_of_maak_portfolio", return_value=("ABC", bestaand, pd.DataFrame()))
        markeer = self.patch_app("db_markeer_portfolio_gebruikt")
        for naam in ("voeg_nieuwe_transacties_toe", "vul_bronkolommen_aan", "_meld_valuta_na_opslaan",
                     "meld_order_ids_rekening", "bewaar_upload_meldingen"):
            self.patch_app(naam)
        self.patch_app("_kern_na_opslaan", return_value={})
        self.patch_app("db_deel_verbinding", side_effect=nullcontext)
        self.m._upload_opslaan(pd.DataFrame(), None, "", False)
        return markeer, cur

    def test_nieuwe_portfolio_wordt_gemarkeerd(self):
        markeer, cur = self._upload_opslaan(bestaand=False)
        markeer.assert_called_once_with(cur, "ABC")

    def test_bestaande_portfolio_wordt_gemarkeerd(self):
        markeer, cur = self._upload_opslaan(bestaand=True)
        markeer.assert_called_once_with(cur, "ABC")


class TestUploadRuimtOp(_MetApp):
    def test_upload_start_het_opruimen(self):
        self.patch_app("_upload_impl", side_effect=lambda: self.m.jsonify({"ok": True}))
        opruimen = self.patch_app("ruim_verlopen_portfolios_op")
        res = self.client.post("/upload", data={"bestand1": (io.BytesIO(b"x"), "a.xlsx")},
                               content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        opruimen.assert_called_once_with()


class TestWijzigCodeNeemtLaatstGebruiktMee(unittest.TestCase):
    def test_insert_krijgt_de_oude_waarde(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        gebruikt = datetime(2026, 1, 2)
        cur.fetchone.return_value = ("naam", datetime(2025, 1, 1), gebruikt)
        with patch.object(db, "db_connect", return_value=conn):
            self.assertEqual(db.db_wijzig_portfolio_code("AAA", "BBB"), (True, None))
        insert = next(c for c in cur.execute.call_args_list if c.args[0].startswith("INSERT INTO portfolios"))
        self.assertEqual(insert.args[1][-1], gebruikt)


if __name__ == "__main__":
    unittest.main()
