import json
import unittest
from datetime import date

import numpy as np
import pandas as pd

from backend.koersoverzicht import PERIODES, bouw_koersoverzicht, periode_grens

VANDAAG = date(2024, 3, 6)


def prijzen(kolommen, start="2024-02-26", dagen=10):
    index = pd.date_range(start, periods=dagen)
    return pd.DataFrame(kolommen, index=index)


def transacties(*rijen):
    return pd.DataFrame(rijen, columns=["datum", "ticker", "product", "aantal"]).assign(
        datum=lambda d: pd.to_datetime(d["datum"]))


class PeriodeGrensTests(unittest.TestCase):
    def test_maand_is_de_eerste(self):
        self.assertEqual(periode_grens("maand", date(2024, 3, 17)), date(2024, 3, 1))
        self.assertEqual(periode_grens("maand", date(2024, 3, 1)), date(2024, 3, 1))

    def test_ytd(self):
        self.assertEqual(periode_grens("ytd", date(2024, 8, 9)), date(2024, 1, 1))

    def test_een_maand_terug_klemt_op_laatste_dag(self):
        self.assertEqual(periode_grens("1m", date(2024, 3, 31)), date(2024, 2, 29))
        self.assertEqual(periode_grens("1m", date(2023, 3, 31)), date(2023, 2, 28))

    def test_een_maand_terug_over_jaarwisseling(self):
        self.assertEqual(periode_grens("1m", date(2024, 1, 15)), date(2023, 12, 15))

    def test_een_jaar_terug_van_schrikkeldag(self):
        self.assertEqual(periode_grens("1j", date(2024, 2, 29)), date(2023, 2, 28))

    def test_alles_is_none(self):
        self.assertIsNone(periode_grens("alles", VANDAAG))

    def test_onbekend_geeft_fout(self):
        with self.assertRaises(ValueError):
            periode_grens("kwartaal", VANDAAG)

    def test_perioden_volgorde(self):
        self.assertEqual(PERIODES, ("maand", "ytd", "1m", "1j", "alles"))


class BouwKoersoverzichtTests(unittest.TestCase):
    def bouw(self, tr, pr, splits=None, periode="maand"):
        return bouw_koersoverzicht(tr, pr, splits or {}, periode, VANDAAG)

    def test_split_midden_in_periode_geeft_geen_sprong(self):
        pr = prijzen({"AAA": [400, 400, 100, 100, 110, 110, 110, 110, 110, 110]}, start="2024-03-01")
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1))
        r = self.bouw(tr, pr, {"AAA": {"2024-03-03": 4.0}})["reeksen"][0]
        self.assertEqual(r["pct"][:3], [0.0, 0.0, 0.0])
        self.assertEqual(r["koers_begin"], 100.0)
        self.assertEqual(r["koers_nu"], 110.0)
        self.assertEqual(r["pct"][4], 10.0)

    def test_aankoop_halverwege_zet_in_bezit_om(self):
        pr = prijzen({"AAA": [10.0] * 5 + [11.0] * 5}, start="2024-03-01")
        tr = transacties(("2024-03-04", "AAA", "Aaa", 2))
        r = self.bouw(tr, pr)["reeksen"][0]
        self.assertEqual(r["in_bezit"][:5], [False, False, False, True, True])
        self.assertEqual(r["pct"][0], 0.0)
        self.assertEqual(r["pct"][-1], 10.0)

    def test_gesloten_positie_ontbreekt(self):
        pr = prijzen({"AAA": [10.0] * 10, "BBB": [5.0] * 10}, start="2024-03-01")
        tr = transacties(
            ("2024-01-02", "AAA", "Aaa", 3), ("2024-02-01", "AAA", "Aaa", -3), ("2024-01-02", "BBB", "Bbb", 1))
        res = self.bouw(tr, pr)
        self.assertEqual([r["ticker"] for r in res["reeksen"]], ["BBB"])
        self.assertEqual(res["ontbreken"], [])

    def test_start_bij_eerste_geldige_koers(self):
        pr = prijzen({"AAA": [np.nan, np.nan, 20.0, 22.0, 22.0]}, start="2024-03-01", dagen=5)
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1))
        r = self.bouw(tr, pr)["reeksen"][0]
        self.assertEqual(r["pct"], [None, None, 0.0, 10.0, 10.0])
        self.assertEqual(r["koers_begin"], 20.0)
        self.assertEqual(r["koers_nu"], 22.0)

    def test_koers_begin_en_nu_ontbreken_bij_alleen_nan_in_de_periode(self):
        index = pd.date_range("2024-02-26", periods=10)
        pr = pd.DataFrame({"AAA": [10.0] * 3 + [np.nan] * 7}, index=index)
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1))
        r = self.bouw(tr, pr, periode="maand")["reeksen"][0]
        self.assertIsNone(r["koers_begin"])
        self.assertIsNone(r["koers_nu"])
        self.assertNotIn("eur", r)

    def test_nan_wordt_none_en_json_werkt(self):
        pr = prijzen({"AAA": [10.0, np.nan, 12.0, 12.0, 12.0]}, start="2024-03-01", dagen=5)
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1))
        res = self.bouw(tr, pr)
        self.assertIsNone(res["reeksen"][0]["pct"][1])
        json.dumps(res, allow_nan=False)

    def test_sortering_aflopend_met_none_achteraan(self):
        index = pd.date_range("2024-02-26", periods=10)
        pr = pd.DataFrame({
            "LAAG": [10.0] * 9 + [9.0],
            "HOOG": [10.0] * 9 + [12.0],
            "STIL": [10.0] * 3 + [np.nan] * 7,
        }, index=index)
        tr = transacties(("2024-01-02", "LAAG", "L", 1), ("2024-01-02", "HOOG", "H", 1), ("2024-01-02", "STIL", "S", 1))
        res = self.bouw(tr, pr, periode="maand")
        self.assertEqual([r["ticker"] for r in res["reeksen"]], ["HOOG", "LAAG", "STIL"])
        self.assertEqual([r["pct_periode"] for r in res["reeksen"]], [20.0, -10.0, None])

    def test_positie_zonder_koersdata_komt_in_ontbreken(self):
        pr = prijzen({"AAA": [10.0] * 5}, start="2024-03-01", dagen=5)
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1), ("2024-01-02", "ZZZ", "Zzz", 1))
        res = self.bouw(tr, pr)
        self.assertEqual(res["ontbreken"], [{"ticker": "ZZZ", "naam": "Zzz"}])
        self.assertEqual([r["ticker"] for r in res["reeksen"]], ["AAA"])

    def test_labels_beginnen_op_de_grens(self):
        pr = prijzen({"AAA": [10.0] * 10}, start="2024-02-26")
        tr = transacties(("2024-01-02", "AAA", "Aaa", 1))
        res = self.bouw(tr, pr, periode="maand")
        self.assertEqual(res["vanaf"], "2024-03-01")
        self.assertEqual(res["labels"][0], "2024-03-01")
        self.assertEqual(len(res["reeksen"][0]["pct"]), len(res["labels"]))
        self.assertEqual(self.bouw(tr, pr, periode="alles")["labels"][0], "2024-02-26")


if __name__ == "__main__":
    unittest.main()
