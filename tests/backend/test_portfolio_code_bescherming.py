import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from psycopg2 import errors as pg_errors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import db
import portfolio_admin
from portfolio_admin import (
    generate_code, maak_portfolio_met_nieuwe_code, find_matching_code, GeenVrijeCode,
    UploadHoortBijBestaandePortfolio, valideer_bijnaam, MAX_CODE_POGINGEN, MAX_BIJNAAM_LENGTE, is_geldige_code,
    MELDING_UPLOAD_HOORT_BIJ_BESTAANDE,
)


class TestGenerateCode(unittest.TestCase):
    def test_geeft_geldige_code(self):
        with patch.object(portfolio_admin, "db_portfolio_bestaat_met_cursor", return_value=False):
            self.assertTrue(is_geldige_code(generate_code(MagicMock())))

    def test_gebruikt_secrets(self):
        with patch.object(portfolio_admin, "db_portfolio_bestaat_met_cursor", return_value=False), \
                patch.object(portfolio_admin.secrets, "choice", return_value="Q") as choice:
            self.assertEqual(generate_code(MagicMock()), "QQQ")
        self.assertEqual(choice.call_count, portfolio_admin.CODE_LENGTH)

    def test_stopt_na_het_maximum_met_nette_fout(self):
        with patch.object(portfolio_admin, "db_portfolio_bestaat_met_cursor", return_value=True) as bestaat:
            with self.assertRaisesRegex(GeenVrijeCode, "geen vrije code"):
                generate_code(MagicMock())
        self.assertEqual(bestaat.call_count, MAX_CODE_POGINGEN)

    def test_uitgesloten_codes_worden_overgeslagen(self):
        with patch.object(portfolio_admin, "db_portfolio_bestaat_met_cursor", return_value=False), \
                patch.object(portfolio_admin.secrets, "choice", side_effect=list("AAABBB")):
            self.assertEqual(generate_code(MagicMock(), uitgesloten={"AAA"}), "BBB")


class TestMaakPortfolioMetNieuweCode(unittest.TestCase):
    def test_race_verliezer_probeert_andere_code(self):
        with patch.object(portfolio_admin, "generate_code", side_effect=["AAA", "BBB"]), \
                patch.object(portfolio_admin, "db_probeer_portfolio_te_maken", side_effect=[False, True]) as probeer:
            self.assertEqual(maak_portfolio_met_nieuwe_code(MagicMock(), "naam"), "BBB")
        self.assertEqual([c.args[1] for c in probeer.call_args_list], ["AAA", "BBB"])

    def test_geeft_op_na_het_maximum(self):
        with patch.object(portfolio_admin, "generate_code", return_value="AAA"), \
                patch.object(portfolio_admin, "db_probeer_portfolio_te_maken", return_value=False) as probeer:
            with self.assertRaises(GeenVrijeCode):
                maak_portfolio_met_nieuwe_code(MagicMock(), None)
        self.assertEqual(probeer.call_count, MAX_CODE_POGINGEN)

    def test_ingenomen_codes_gaan_als_uitgesloten_mee(self):
        with patch.object(portfolio_admin, "generate_code", side_effect=["AAA", "BBB"]) as gen, \
                patch.object(portfolio_admin, "db_probeer_portfolio_te_maken", side_effect=[False, True]):
            maak_portfolio_met_nieuwe_code(MagicMock(), None)
        self.assertEqual(gen.call_args_list[1].kwargs["uitgesloten"], {"AAA"})


class TestDbProbeerPortfolioTeMaken(unittest.TestCase):
    def test_gelukt_geeft_savepoint_vrij(self):
        cur = MagicMock()
        self.assertTrue(db.db_probeer_portfolio_te_maken(cur, "ABC", "naam"))
        sql = [c.args[0] for c in cur.execute.call_args_list]
        self.assertEqual(sql[0], "SAVEPOINT portfolio_code")
        self.assertEqual(sql[-1], "RELEASE SAVEPOINT portfolio_code")

    def test_unique_violation_rolt_alleen_het_savepoint_terug(self):
        cur = MagicMock()

        def execute(sql, params=None):
            if sql.startswith("INSERT"):
                raise pg_errors.UniqueViolation()
        cur.execute.side_effect = execute
        self.assertFalse(db.db_probeer_portfolio_te_maken(cur, "ABC", None))
        self.assertEqual(cur.execute.call_args_list[-1].args[0], "ROLLBACK TO SAVEPOINT portfolio_code")


class TestFindMatchingCode(unittest.TestCase):
    def _zoek(self, bestaand, upload):
        with patch.object(portfolio_admin, "db_get_order_id_sets_met_overlap", return_value=bestaand):
            return find_matching_code(MagicMock(), upload)

    def test_nieuwere_export_vindt_de_code_met_de_nieuwe_ids(self):
        self.assertEqual(self._zoek({"ABC": {"1", "2"}}, {"1", "2", "3"}), ("ABC", {"3"}))

    def test_identieke_export_vindt_de_code_zonder_nieuwe_ids(self):
        self.assertEqual(self._zoek({"ABC": {"1", "2"}}, {"1", "2"}), ("ABC", set()))

    def test_deelverzameling_van_bestaande_portfolio_geeft_geen_code(self):
        with self.assertRaises(UploadHoortBijBestaandePortfolio) as ctx:
            self._zoek({"ABC": {"1", "2", "3"}}, {"1", "2"})
        self.assertEqual(str(ctx.exception), MELDING_UPLOAD_HOORT_BIJ_BESTAANDE)
        self.assertNotIn("ABC", str(ctx.exception))

    def test_deels_overlappend_is_een_nieuwe_portfolio(self):
        self.assertEqual(self._zoek({"ABC": {"1", "9"}}, {"1", "2"}), (None, None))

    def test_geen_overlap_is_een_nieuwe_portfolio(self):
        self.assertEqual(self._zoek({}, {"1"}), (None, None))

    def test_superset_gaat_voor_op_een_portfolio_waar_de_upload_in_past(self):
        bestaand = {"AAA": {"1", "2", "3", "4"}, "BBB": {"1"}}
        self.assertEqual(self._zoek(bestaand, {"1", "2"}), ("BBB", {"2"}))


class TestBijnaamLengte(unittest.TestCase):
    def test_op_het_maximum_mag(self):
        self.assertIsNone(valideer_bijnaam("a" * MAX_BIJNAAM_LENGTE))

    def test_erboven_is_een_fout(self):
        self.assertIn(str(MAX_BIJNAAM_LENGTE), valideer_bijnaam("a" * (MAX_BIJNAAM_LENGTE + 1)))


class TestWijzigCodeUniqueViolation(unittest.TestCase):
    def test_code_al_in_gebruik_geeft_nette_fout_en_rollback(self):
        conn, cur = MagicMock(), MagicMock()
        conn.cursor.return_value = cur
        cur.fetchone.return_value = ("naam", None)

        def execute(sql, params=None):
            if sql.startswith("INSERT INTO portfolios"):
                raise pg_errors.UniqueViolation()
        cur.execute.side_effect = execute
        with patch.object(db, "db_connect", return_value=conn):
            gelukt, melding = db.db_wijzig_portfolio_code("AAA", "BBB")
        self.assertFalse(gelukt)
        self.assertIn("al in gebruik", melding)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
