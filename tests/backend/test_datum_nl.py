import os
import sys
import unittest
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from transactie_utils import formatteer_datum_nl


class TestFormatteerDatumNl(unittest.TestCase):
    def test_date(self):
        self.assertEqual(formatteer_datum_nl(date(2024, 3, 1)), "01-03-2024")

    def test_timestamp_met_tijd(self):
        self.assertEqual(formatteer_datum_nl(pd.Timestamp("2023-12-31 15:30")), "31-12-2023")

    def test_iso_string(self):
        self.assertEqual(formatteer_datum_nl("2026-09-30"), "30-09-2026")


if __name__ == "__main__":
    unittest.main()
