"""
Unit test voor db.upsert_prices() -- t.b.v. de fix "koersen bij élke
portfolio-opening verversen" (zie CLAUDE.md). save_prices() gebruikt
ON CONFLICT ... DO NOTHING (juist voor historische koersen, die nooit meer
veranderen); upsert_prices() moet voor de dagverse-koers-refresh in
get_prices() (analysis.py) juist WEL overschrijven (DO UPDATE), anders
blijft een eerder op dezelfde dag gecachete (mogelijk tussentijdse) koers
voor de rest van de dag stilzwijgend staan.

Raakt de database NIET aan -- get_db_connection wordt gemockt.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class TestUpsertPrices(unittest.TestCase):
    @patch("db.execute_values")
    @patch("db.get_db_connection")
    def test_upsert_prices_gebruikt_do_update_niet_do_nothing(self, mock_get_conn, mock_execute_values):
        """Losse, expliciete waakhond: upsert_prices() moet een bestaande
        rij overschrijven -- regressie naar save_prices()-gedrag (DO
        NOTHING) zou deze hele fix stilzwijgend weer teniet doen.

        execute_values() zelf gemockt (i.p.v. alleen de cursor) -- de echte
        implementatie roept cur.mogrify()/cur.connection.encoding aan, wat
        een kale MagicMock-cursor niet zinvol kan beantwoorden."""
        db.upsert_prices([("TEST.AS", "2026-09-07", 99.99)])

        mock_execute_values.assert_called_once()
        sql = mock_execute_values.call_args[0][1]
        self.assertIn("DO UPDATE", sql)
        self.assertNotIn("DO NOTHING", sql)
        self.assertEqual(mock_execute_values.call_args[1]["template"], "(%s, %s, %s, NOW())")
        mock_get_conn.return_value.commit.assert_called_once()

    @patch("db.get_db_connection")
    def test_lege_lijst_doet_niets(self, mock_get_conn):
        db.upsert_prices([])
        mock_get_conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
