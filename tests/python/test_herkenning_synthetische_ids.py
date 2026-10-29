"""Portfolio-herkenning op echte Order ID's: een veranderde SYN-ID mag een herupload niet breken of dupliceren."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import date, time
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import portfolio_admin as pa
from backend import upload_verwerking as uv
from backend.portfolio_admin import (
    FOUT_ANDERE_PORTFOLIO, FOUT_TRANSACTIES_ONTBREKEN, UploadHoortBijBestaandePortfolio,
    controleer_eigen_transactiebestand, find_matching_code,
)


class TestFindMatchingCode(unittest.TestCase):
    def _zoek(self, bestaand, nieuw):
        with patch.object(pa, "db_get_order_id_sets_met_overlap", return_value=bestaand):
            return find_matching_code(None, nieuw)

    def test_herkend_als_alleen_een_syn_id_verschilt(self):
        code, nieuw = self._zoek({"ABC": {"a", "b", "SYN-oud"}}, {"a", "b", "c", "SYN-nieuw"})
        self.assertEqual(code, "ABC")
        self.assertEqual(nieuw, {"c", "SYN-nieuw"})

    def test_ontbrekend_echt_id_blijft_geen_match(self):
        self.assertEqual(self._zoek({"ABC": {"a", "b"}}, {"a", "c"}), (None, None))

    def test_deelverzameling_negeert_syn_ids(self):
        with self.assertRaises(UploadHoortBijBestaandePortfolio):
            self._zoek({"ABC": {"a", "b", "SYN-oud"}}, {"a", "SYN-nieuw"})

    def test_portfolio_met_alleen_syn_ids_houdt_het_oude_gedrag(self):
        self.assertEqual(self._zoek({"ABC": {"SYN-1"}}, {"SYN-1", "SYN-2"}), ("ABC", {"SYN-2"}))
        self.assertEqual(self._zoek({"ABC": {"SYN-1"}}, {"SYN-2"}), (None, None))

    def test_upload_met_alleen_syn_ids_is_geen_deelverzameling_van_een_echt_portfolio(self):
        self.assertEqual(self._zoek({"ABC": {"a", "SYN-1"}}, {"SYN-1"}), (None, None))


class TestControleerEigenTransactiebestand(unittest.TestCase):
    def test_andere_syn_id_is_geen_ontbrekende_transactie(self):
        fout, toe = controleer_eigen_transactiebestand({"a", "SYN-oud"}, {"a", "SYN-nieuw"}, set())
        self.assertIsNone(fout)
        self.assertEqual(toe, {"SYN-nieuw"})

    def test_ontbrekend_echt_id_blijft_fout(self):
        fout, _ = controleer_eigen_transactiebestand({"a", "b", "SYN-1"}, {"a", "SYN-1"}, set())
        self.assertEqual(fout, FOUT_TRANSACTIES_ONTBREKEN)

    def test_syn_id_bij_andere_portfolio_telt_niet(self):
        fout, _ = controleer_eigen_transactiebestand({"a"}, {"a", "SYN-x"}, {"SYN-x"})
        self.assertIsNone(fout)

    def test_echt_id_bij_andere_portfolio_blijft_fout(self):
        fout, _ = controleer_eigen_transactiebestand({"a"}, {"a", "x"}, {"x"})
        self.assertEqual(fout, FOUT_ANDERE_PORTFOLIO)

    def test_portfolio_met_alleen_syn_ids_houdt_het_oude_gedrag(self):
        fout, _ = controleer_eigen_transactiebestand({"SYN-1"}, {"SYN-2"}, set())
        self.assertEqual(fout, FOUT_ANDERE_PORTFOLIO)


def _rijen(*rijen):
    return pd.DataFrame(
        [{"Order ID": oid, "Datum": pd.Timestamp(d), "Tijd": t, "ISIN": isin, "Aantal": aantal}
         for oid, d, t, isin, aantal in rijen])


class TestGeenDubbeleInsert(unittest.TestCase):
    def _filter(self, opgeslagen, nieuw, upload_ids):
        with patch.object(uv, "db_get_synthetische_transacties", return_value=opgeslagen), \
                Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()):
            return uv.sluit_hernoemde_synthetische_rijen_uit(None, "ABC", nieuw, upload_ids)

    def test_zelfde_transactie_met_andere_syn_id_wordt_overgeslagen(self):
        opgeslagen = [("SYN-oud", date(2025, 7, 31), time(7, 49), "CNE1", 23.0)]
        nieuw = _rijen(("SYN-nieuw", "2025-07-31", "07:49", "CNE1", 23.0), ("echt-2", "2025-08-01", "10:00", "NL1", 5.0))
        uit = self._filter(opgeslagen, nieuw, {"SYN-nieuw", "echt-2"})
        self.assertEqual(uit["Order ID"].tolist(), ["echt-2"])

    def test_nu_wel_een_echt_id_voor_dezelfde_transactie(self):
        opgeslagen = [("SYN-oud", date(2025, 7, 31), time(7, 49), "CNE1", 23.0)]
        nieuw = _rijen(("echt-1", "2025-07-31", "07:49", "CNE1", 23.0))
        self.assertTrue(self._filter(opgeslagen, nieuw, {"echt-1"}).empty)

    def test_andere_transactie_wordt_wel_ingevoegd(self):
        opgeslagen = [("SYN-oud", date(2025, 7, 31), time(7, 49), "CNE1", 23.0)]
        nieuw = _rijen(("SYN-x", "2025-07-31", "07:49", "CNE1", 24.0))
        self.assertEqual(len(self._filter(opgeslagen, nieuw, {"SYN-x"})), 1)

    def test_opgeslagen_syn_rij_die_nog_in_de_upload_staat_absorbeert_niets(self):
        opgeslagen = [("SYN-1", date(2025, 7, 31), time(7, 49), "CNE1", 23.0)]
        nieuw = _rijen(("echt-9", "2025-07-31", "07:49", "CNE1", 23.0))
        self.assertEqual(len(self._filter(opgeslagen, nieuw, {"SYN-1", "echt-9"})), 1)

    def test_een_verdwenen_rij_absorbeert_maar_een_nieuwe_rij(self):
        opgeslagen = [("SYN-oud", date(2025, 7, 31), time(7, 49), "CNE1", 23.0)]
        nieuw = _rijen(("SYN-a", "2025-07-31", "07:49", "CNE1", 23.0), ("SYN-b", "2025-07-31", "07:49", "CNE1", 23.0))
        self.assertEqual(len(self._filter(opgeslagen, nieuw, {"SYN-a", "SYN-b"})), 1)

    def test_zonder_verdwenen_rijen_blijft_alles(self):
        nieuw = _rijen(("SYN-a", "2025-07-31", "07:49", "CNE1", 23.0))
        self.assertEqual(len(self._filter([], nieuw, {"SYN-a"})), 1)


if __name__ == "__main__":
    unittest.main()
