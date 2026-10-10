"""Upload-meldingen bewaren per portfolio: filter en API-vorm (puur), de route (gemockt) en opslaan, maximum,
verwijderen en code wijzigen (echt tegen de database, alleen localhost)."""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
import upload_verwerking as uv
from diagnostiek import (
    CATEGORIE_DATA, CATEGORIE_DIVIDEND, CATEGORIE_KOERSEN, CATEGORIE_LAADTIJDEN, CATEGORIE_OPSLAAN, CATEGORIE_ORDER_IDS,
    CATEGORIE_REKENINGOVERZICHT, CATEGORIE_TICKERS, CATEGORIE_WISSELKOERSEN, GOED, INFO, LET_OP, meld,
    upload_meldingen_om_te_bewaren,
)

try:
    from db_helper import vereist_database
except ImportError:
    from tests.db_helper import vereist_database


def _m(categorie, sleutel="s", niveau=INFO):
    return {"categorie": categorie, "niveau": niveau, "tekst": f"{categorie} {sleutel}", "sleutel": sleutel}


class TestFilterOpCategorie(unittest.TestCase):
    def test_alleen_de_upload_categorieen(self):
        bewaard = [_m(c) for c in (CATEGORIE_OPSLAAN, CATEGORIE_ORDER_IDS, CATEGORIE_WISSELKOERSEN, CATEGORIE_TICKERS,
                                   CATEGORIE_DIVIDEND, CATEGORIE_REKENINGOVERZICHT)]
        niet = [_m(c) for c in (CATEGORIE_DATA, CATEGORIE_KOERSEN, CATEGORIE_LAADTIJDEN, "Plausibiliteit", "Splits")]
        self.assertEqual(upload_meldingen_om_te_bewaren(niet + bewaard), bewaard)

    def test_leeg_of_none(self):
        self.assertEqual(upload_meldingen_om_te_bewaren(None), [])
        self.assertEqual(upload_meldingen_om_te_bewaren([]), [])


class TestApiVorm(unittest.TestCase):
    def test_tijdstip_als_utc_iso(self):
        uit = uv.upload_meldingen_voor_api([
            {"soort": "upload", "geupload_op": datetime.datetime(2026, 10, 10, 12, 5, 30), "meldingen": [_m("Opslaan")]},
        ])
        self.assertEqual(uit, [{"soort": "upload", "geupload_op": "2026-10-10T12:05:30Z", "meldingen": [_m("Opslaan")]}])


class TestBewaarZonderDatabase(unittest.TestCase):
    def _bewaar(self, **patch_kwargs):
        with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()) as uitvoer, \
                patch.object(uv, "db_save_upload_meldingen", **patch_kwargs) as opslaan:
            meld(CATEGORIE_OPSLAAN, GOED, "3 transacties opgeslagen.", sleutel="insert_opgeslagen")
            meld(CATEGORIE_LAADTIJDEN, INFO, "Koersen ophalen: 1,0 s.", sleutel="basis_koersen_ophalen")
            uv.bewaar_upload_meldingen("ZZTEST", uv.UPLOAD_SOORT_UPLOAD)
        return opslaan, uitvoer.getvalue()

    def test_bewaart_alleen_upload_categorieen_met_maximum(self):
        opslaan, _ = self._bewaar()
        code, soort, meldingen, maximum = opslaan.call_args.args
        self.assertEqual((code, soort, maximum), ("ZZTEST", "upload", 5))
        self.assertEqual([m["sleutel"] for m in meldingen], ["insert_opgeslagen"])

    def test_fout_wordt_alleen_een_logregel(self):
        _, uitvoer = self._bewaar(side_effect=RuntimeError("db weg"))
        self.assertIn("[upload] WARN upload-meldingen niet opgeslagen (RuntimeError", uitvoer)


class TestRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_geeft_uploads_nieuwste_eerst(self):
        uploads = [{"soort": "bijwerken", "geupload_op": datetime.datetime(2026, 10, 10, 12, 0), "meldingen": []},
                   {"soort": "upload", "geupload_op": datetime.datetime(2026, 10, 1, 9, 30), "meldingen": [_m("Opslaan")]}]
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "db_get_upload_meldingen", return_value=uploads) as ophalen:
            res = self.client.get("/api/portfolio/abc/upload-meldingen")
        ophalen.assert_called_once_with("ABC")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([u["geupload_op"] for u in res.get_json()["uploads"]],
                         ["2026-10-10T12:00:00Z", "2026-10-01T09:30:00Z"])

    def test_onbekende_code_404(self):
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=False):
            res = self.client.get("/api/portfolio/XYZ/upload-meldingen")
        self.assertEqual(res.status_code, 404)


@vereist_database
class TestUploadMeldingenDatabase(unittest.TestCase):
    CODE = "TESTUPL"
    NIEUWE_CODE = "TESTUPL2"

    def _leeg_op(self):
        with db.db_transactie() as cur:
            for code in (self.CODE, self.NIEUWE_CODE):
                cur.execute("DELETE FROM upload_meldingen WHERE code = %s", (code,))
                cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))

    def setUp(self):
        self._leeg_op()
        self.addCleanup(self._leeg_op)
        with db.db_transactie() as cur:
            db.db_maak_portfolio(cur, self.CODE, "Test")

    def _bewaar(self, nummer, soort="upload"):
        melding = {**_m(CATEGORIE_ORDER_IDS, f"m{nummer}", LET_OP),
                   "tabel": {"kolommen": ["Datum"], "rijen": [["01-01-2024"]]}}
        db.db_save_upload_meldingen(self.CODE, soort, [melding], 5)

    def test_opslaan_en_ophalen_nieuwste_eerst(self):
        self._bewaar(1)
        self._bewaar(2, soort="bijwerken")
        uploads = db.db_get_upload_meldingen(self.CODE)
        self.assertEqual([u["soort"] for u in uploads], ["bijwerken", "upload"])
        self.assertEqual(uploads[0]["meldingen"][0]["sleutel"], "m2")
        self.assertEqual(uploads[0]["meldingen"][0]["tabel"]["rijen"], [["01-01-2024"]])
        self.assertIsInstance(uploads[0]["geupload_op"], datetime.datetime)

    def test_maximaal_vijf_oudste_weg(self):
        for nummer in range(7):
            self._bewaar(nummer)
        uploads = db.db_get_upload_meldingen(self.CODE)
        self.assertEqual([u["meldingen"][0]["sleutel"] for u in uploads], ["m6", "m5", "m4", "m3", "m2"])

    def test_verwijderen_neemt_ze_mee(self):
        self._bewaar(1)
        db.db_delete_portfolio(self.CODE)
        self.assertEqual(db.db_get_upload_meldingen(self.CODE), [])

    def test_code_wijzigen_verhuist_ze(self):
        self._bewaar(1)
        gelukt, fout = db.db_wijzig_portfolio_code(self.CODE, self.NIEUWE_CODE)
        self.assertTrue(gelukt, fout)
        self.assertEqual(db.db_get_upload_meldingen(self.CODE), [])
        self.assertEqual(len(db.db_get_upload_meldingen(self.NIEUWE_CODE)), 1)

    def test_mislukt_opslaan_breekt_niets_en_slaat_niets_op(self):
        # code NULL schendt NOT NULL: een echte databasefout, die bewaar_upload_meldingen() moet opvangen.
        with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()) as uitvoer:
            meld(CATEGORIE_OPSLAAN, GOED, "3 transacties opgeslagen.", sleutel="insert_opgeslagen")
            uv.bewaar_upload_meldingen(None, uv.UPLOAD_SOORT_UPLOAD)
        self.assertIn("WARN upload-meldingen niet opgeslagen", uitvoer.getvalue())
        self.assertEqual(db.db_get_upload_meldingen(self.CODE), [])

    def test_bewaar_via_upload_verwerking(self):
        with Flask(__name__).test_request_context():
            meld(CATEGORIE_OPSLAAN, GOED, "3 transacties opgeslagen.", sleutel="insert_opgeslagen")
            meld(CATEGORIE_LAADTIJDEN, INFO, "Koersen ophalen: 1,0 s.", sleutel="basis_koersen_ophalen")
            uv.bewaar_upload_meldingen(self.CODE, uv.UPLOAD_SOORT_BIJWERKEN)
        [upload] = db.db_get_upload_meldingen(self.CODE)
        self.assertEqual(upload["soort"], "bijwerken")
        self.assertEqual([m["sleutel"] for m in upload["meldingen"]], ["insert_opgeslagen"])


if __name__ == "__main__":
    unittest.main()
