import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import db_helper
except ImportError:
    from tests import db_helper

URL = "postgresql://user:pw@localhost:5432/test"


class TestDatabaseBeschikbaar(unittest.TestCase):
    def test_geen_url_geeft_false_zonder_verbindingspoging(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}), \
             patch.object(db_helper.psycopg2, "connect") as mock_connect:
            self.assertFalse(db_helper.database_beschikbaar(None))
        mock_connect.assert_not_called()

    def test_geslaagde_verbinding_geeft_true_en_sluit_verbinding(self):
        with patch.object(db_helper.psycopg2, "connect") as mock_connect:
            self.assertTrue(db_helper.database_beschikbaar(URL))
        mock_connect.assert_called_once_with(URL, connect_timeout=10)
        mock_connect.return_value.close.assert_called_once()

    def test_mislukte_verbinding_geeft_false(self):
        with patch.object(db_helper.psycopg2, "connect", side_effect=Exception("verbinding geweigerd")):
            self.assertFalse(db_helper.database_beschikbaar(URL))

    def test_url_uit_omgeving_als_geen_argument(self):
        with patch.dict(os.environ, {"DATABASE_URL": URL}), \
             patch.object(db_helper.psycopg2, "connect") as mock_connect:
            self.assertTrue(db_helper.database_beschikbaar())
        mock_connect.assert_called_once_with(URL, connect_timeout=10)


if __name__ == "__main__":
    unittest.main()
