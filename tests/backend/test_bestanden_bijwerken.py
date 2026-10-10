"""Instellingen > Bestanden bijwerken: eigendomscheck (pure functies) en de route POST /api/portfolio/<code>/bijwerken.
De route-tests mocken de database; 'import app' doet zonder DATABASE_URL geen db_init()."""
import io
import os
import sys
import unittest
from contextlib import nullcontext
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_admin import (
    controleer_eigen_transactiebestand, controleer_eigen_rekeningoverzicht,
    FOUT_ANDERE_PORTFOLIO, FOUT_TRANSACTIES_ONTBREKEN, FOUT_GEEN_ORDER_IDS, FOUT_ONBEKENDE_TRANSACTIES,
)
from dividend import lees_rekeningoverzicht, order_ids_uit_rekeningoverzicht_df, MELDING_GEEN_REKENINGOVERZICHT
from upload_verwerking import OngeldigExcelBestand, lees_transacties_excel, voeg_koers_eur_toe, vul_synthetische_order_ids_aan

TEST_FILES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_files")
BESTAND_TRANSACTIES = os.path.join(TEST_FILES, "Transactions_test.xlsx")
BESTAND_REKENING = os.path.join(TEST_FILES, "Account_test.xlsx")

TEST_CODE = "tst_bijwerken"
ANDERE_CODE = "tst_andere"


def _transactie_ids():
    with open(BESTAND_TRANSACTIES, "rb") as f:
        df = lees_transacties_excel(f)
    return set(vul_synthetische_order_ids_aan(voeg_koers_eur_toe(df))["Order ID"])


def _rekening_ids():
    with open(BESTAND_REKENING, "rb") as f:
        return order_ids_uit_rekeningoverzicht_df(lees_rekeningoverzicht(f))


class TestControleerEigenTransactiebestand(unittest.TestCase):
    def test_superset_geeft_alleen_de_nieuwe_ids(self):
        fout, nieuw = controleer_eigen_transactiebestand({"a", "b"}, {"a", "b", "c"}, {"x"})
        self.assertIsNone(fout)
        self.assertEqual(nieuw, {"c"})

    def test_precies_gelijk_is_ok_zonder_nieuwe_ids(self):
        self.assertEqual(controleer_eigen_transactiebestand({"a", "b"}, {"a", "b"}, set()), (None, set()))

    def test_opgeslagen_id_ontbreekt(self):
        fout, _ = controleer_eigen_transactiebestand({"a", "b"}, {"a", "c"}, set())
        self.assertEqual(fout, FOUT_TRANSACTIES_ONTBREKEN)

    def test_id_van_andere_portfolio(self):
        fout, _ = controleer_eigen_transactiebestand({"a"}, {"a", "x"}, {"x", "y"})
        self.assertEqual(fout, FOUT_ANDERE_PORTFOLIO)

    def test_geen_overlap(self):
        fout, _ = controleer_eigen_transactiebestand({"a", "b"}, {"c", "d"}, set())
        self.assertEqual(fout, FOUT_ANDERE_PORTFOLIO)


class TestControleerEigenRekeningoverzicht(unittest.TestCase):
    def test_alle_ids_bekend(self):
        self.assertIsNone(controleer_eigen_rekeningoverzicht({"a", "b"}, {"a", "b", "c"}))

    def test_zonder_order_ids(self):
        self.assertEqual(controleer_eigen_rekeningoverzicht(set(), {"a"}), FOUT_GEEN_ORDER_IDS)

    def test_onbekende_id(self):
        self.assertEqual(controleer_eigen_rekeningoverzicht({"a", "z"}, {"a", "b"}), FOUT_ONBEKENDE_TRANSACTIES)

    def test_gedekt_door_transactiebestand_uit_dezelfde_upload(self):
        opgeslagen, nieuw_uit_bestand1 = {"a"}, {"a", "b"}
        self.assertIsNone(controleer_eigen_rekeningoverzicht({"a", "b"}, opgeslagen | nieuw_uit_bestand1))


class TestOrderIdsUitRekeningoverzicht(unittest.TestCase):
    def test_tien_ids_allemaal_in_het_transactiebestand(self):
        ids = _rekening_ids()
        self.assertEqual(len(ids), 10)
        self.assertLessEqual(ids, _transactie_ids())

    def test_transactiebestand_is_geen_rekeningoverzicht(self):
        with open(BESTAND_TRANSACTIES, "rb") as f:
            with self.assertRaises(OngeldigExcelBestand):
                lees_rekeningoverzicht(f)


class TestBijwerkenRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import upload_verwerking
        self.app_module = app_module
        self.client = app_module.app.test_client()
        self.transactie_ids = _transactie_ids()
        patches = [
            patch.object(app_module, "db_portfolio_bestaat", return_value=True),
            patch.object(app_module, "db_transactie", side_effect=lambda: nullcontext(MagicMock())),
            patch.object(app_module, "reset_yahoo_call_teller"),
            patch.object(app_module, "log_yahoo_call_samenvatting"),
            patch.object(app_module, "meld_valuta_consistentie"),
            patch.object(app_module, "ticker_per_isin_beurs_uit_basis", return_value={}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.mock_opgeslagen = self._start(patch.object(app_module, "db_get_order_ids", return_value=set()))
        self.mock_bij_andere = self._start(
            patch.object(app_module, "db_get_order_ids_bij_andere_portfolios", return_value=set()))
        self.mock_kern = self._start(patch.object(app_module, "_kern_na_opslaan", return_value={}))
        self.mock_insert = self._start(patch.object(app_module, "voeg_nieuwe_transacties_toe"))
        self.mock_aanvullen = self._start(patch.object(app_module, "vul_bronkolommen_aan"))
        self.mock_dividend_opslaan = self._start(patch.object(upload_verwerking, "db_save_dividenden"))
        self.mock_regels_opslaan = self._start(patch.object(upload_verwerking, "db_save_rekening_regels", return_value=0))
        self.mock_bewaar = self._start(patch.object(upload_verwerking, "db_save_upload_meldingen"))
        self._start(patch.object(app_module, "meld_order_ids_rekening"))

    def _start(self, p):
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def _post(self, transacties=True, rekening=False, rekening_pad=BESTAND_REKENING):
        data = {}
        if transacties:
            with open(BESTAND_TRANSACTIES, "rb") as f:
                data["bestand1"] = (io.BytesIO(f.read()), "transacties.xlsx")
        if rekening:
            with open(rekening_pad, "rb") as f:
                data["bestand2"] = (io.BytesIO(f.read()), "rekening.xlsx")
        return self.client.post(f"/api/portfolio/{TEST_CODE}/bijwerken", data=data,
                                content_type="multipart/form-data")

    def _assert_niets_opgeslagen(self):
        self.mock_kern.assert_not_called()
        self.mock_insert.assert_not_called()
        self.mock_dividend_opslaan.assert_not_called()
        self.mock_bewaar.assert_not_called()

    def test_onbekende_code_geeft_404(self):
        self.app_module.db_portfolio_bestaat.return_value = False
        res = self._post()
        self.assertEqual(res.status_code, 404)
        self._assert_niets_opgeslagen()

    def test_zonder_bestanden_400(self):
        res = self._post(transacties=False)
        self.assertEqual(res.status_code, 400)
        self._assert_niets_opgeslagen()

    def test_lege_bestandsnaam_telt_als_geen_bestand(self):
        res = self.client.post(f"/api/portfolio/{TEST_CODE}/bijwerken",
                               data={"bestand1": (io.BytesIO(b""), "")}, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)

    def test_ontbrekende_opgeslagen_transactie_slaat_niets_op(self):
        self.mock_opgeslagen.return_value = self.transactie_ids | {"ONTBREEKT-IN-BESTAND"}
        res = self._post()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], self.app_module.MELDING_TRANSACTIES_ONTBREKEN)
        self._assert_niets_opgeslagen()

    def test_afgekeurd_transactiebestand_verwerkt_ook_het_rekeningoverzicht_niet(self):
        een_id = next(iter(self.transactie_ids))
        self.mock_opgeslagen.return_value = self.transactie_ids - {een_id}
        self.mock_bij_andere.return_value = {een_id}
        res = self._post(rekening=True)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], self.app_module.MELDING_NIET_VAN_DEZE_PORTFOLIO)
        self.assertNotIn(ANDERE_CODE, res.get_data(as_text=True))
        self._assert_niets_opgeslagen()

    def test_rekeningoverzicht_met_onbekende_transacties_slaat_niets_op(self):
        self.mock_opgeslagen.return_value = {"IETS-ANDERS"}
        res = self._post(transacties=False, rekening=True)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], self.app_module.MELDING_REKENING_ONBEKENDE_TRANSACTIES)
        self._assert_niets_opgeslagen()

    def test_transactiebestand_als_rekeningoverzicht_geeft_400_zonder_opslaan(self):
        self.mock_opgeslagen.return_value = self.transactie_ids
        res = self._post(rekening=True, rekening_pad=BESTAND_TRANSACTIES)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], MELDING_GEEN_REKENINGOVERZICHT)
        self._assert_niets_opgeslagen()

    def test_andere_portfolios_alleen_gevraagd_naar_ids_uit_het_bestand(self):
        self.mock_opgeslagen.return_value = self.transactie_ids
        self._post()
        _cur, code, gevraagd = self.mock_bij_andere.call_args.args
        self.assertEqual((code, gevraagd), (TEST_CODE.upper(), self.transactie_ids))

    def test_superset_slaat_alleen_de_nieuwe_rijen_op(self):
        nieuw = set(sorted(self.transactie_ids)[:2])
        self.mock_opgeslagen.return_value = self.transactie_ids - nieuw
        res = self._post(rekening=True)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["bijwerken"], {"nieuwe_transacties": 2, "dividend_verwerkt": True})
        _cur, code, rows_to_insert = self.mock_insert.call_args.args
        self.assertEqual(code, TEST_CODE.upper())
        self.assertEqual(self.mock_insert.call_args.kwargs, {"herbepaal_alle_tickers": False})
        self.assertEqual(set(rows_to_insert["Order ID"]), nieuw)
        self.mock_kern.assert_called_once_with(TEST_CODE.upper())
        code, soort, _meldingen, maximum = self.mock_bewaar.call_args.args
        self.assertEqual((code, soort, maximum), (TEST_CODE.upper(), "bijwerken", 5))

    def test_niets_nieuw_vult_alleen_bronkolommen_aan(self):
        self.mock_opgeslagen.return_value = self.transactie_ids
        res = self._post()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.mock_insert.call_args.args[2].empty)
        _cur, code, df = self.mock_aanvullen.call_args.args
        self.assertEqual((code, set(df["Order ID"])), (TEST_CODE.upper(), self.transactie_ids))
        self.assertEqual(self.app_module.db_transactie.call_count, 2)


if __name__ == "__main__":
    unittest.main()
