import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import upload_verwerking


def _excel():
    return pd.DataFrame({
        "Datum": pd.to_datetime(["2024-01-10", "2024-02-10", "2024-03-10"]),
        "Tijd": [None, None, None],
        "Product": ["FONDS A", "STAR", "FONDS C"],
        "ISIN": ["ISINA", "ISINB", "ISINC"],
        "Beurs": ["EAM", "EAM", "EAM"],
        "Aantal": [1, 2, 3],
        "_koers_eur": [10.0, 20.0, 30.0],
        upload_verwerking.KOSTEN_KOLOM: [-1.0, -1.0, -1.0],
    })


class TestTweedePogingVangnet(unittest.TestCase):
    def test_fout_in_tweede_poging_houdt_andere_tickers(self):
        def fake_parallel(posities, bekende_tickers):
            return [{"ticker": None if isin == "ISINB" else f"T-{isin}"} for _, isin, _b, _t in posities]

        with patch.object(upload_verwerking, "vind_tickers_met_snelle_prijscheck_parallel", side_effect=fake_parallel), \
             patch.object(upload_verwerking, "_probeer_andere_productnamen",
                          side_effect=AttributeError("'NoneType' object has no attribute 'items'")), \
             Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()):
            tickers = upload_verwerking._ticker_resolutie_opslaan(None, "ZZTEST", _excel(), True)

        self.assertEqual(tickers[("ISINA", "EAM")], "T-ISINA")
        self.assertEqual(tickers[("ISINC", "EAM")], "T-ISINC")
        self.assertIsNone(tickers[("ISINB", "EAM")])


if __name__ == "__main__":
    unittest.main()
