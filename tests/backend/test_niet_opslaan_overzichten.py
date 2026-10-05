"""Transacties- en dividendoverzicht voor 'Niet opslaan': pure functies, zonder database."""
import datetime
import unittest
from decimal import Decimal

import pandas as pd

from dividend import bouw_dividend_samenvatting
from transactie_utils import formatteer_transacties_overzicht, transacties_overzicht_uit_df


class TestFormatteerTransactiesOverzicht(unittest.TestCase):
    def test_none_blijft_none_en_tijd_wordt_hh_mm(self):
        rows = [
            (datetime.date(2024, 3, 5), datetime.time(9, 7, 30), "ASML", Decimal("2"), Decimal("700.5"), Decimal("-1401"), Decimal("1"), "NL0010273215"),
            (datetime.date(2024, 3, 4), None, "ASML", 1, None, 10, None, None),
        ]
        a, b = formatteer_transacties_overzicht(rows)
        self.assertEqual(a, {"datum": "2024-03-05", "tijd": "09:07", "product": "ASML", "isin": "NL0010273215", "aantal": 2.0,
                             "koers": 700.5, "totaal_eur": -1401.0, "transactiekosten": 1.0})
        self.assertIsNone(b["tijd"])
        self.assertIsNone(b["koers"])
        self.assertIsNone(b["transactiekosten"])


class TestTransactiesUitDf(unittest.TestCase):
    def _df(self, rijen):
        df = pd.DataFrame(rijen, columns=["datum", "tijd", "product", "aantal", "koers", "totaal_eur", "transactiekosten"])
        df["echte_naam"] = df["product"]
        df["isin"] = "NL0000000001"
        return df

    def test_sorteert_aflopend_op_datum_en_tijd(self):
        df = self._df([
            (pd.Timestamp("2024-01-01"), "10:00", "A", 1.0, 1.0, 1.0, 0.0),
            (pd.Timestamp("2024-01-02"), "09:00", "B", 1.0, 1.0, 1.0, 0.0),
            (pd.Timestamp("2024-01-02"), "13:39", "C", 1.0, 1.0, 1.0, 0.0),
        ])
        self.assertEqual([r["product"] for r in transacties_overzicht_uit_df(df)], ["C", "B", "A"])

    def test_tijd_als_string_time_en_datetime(self):
        df = self._df([
            (pd.Timestamp("2024-01-01"), "13:39", "A", 1.0, 1.0, 1.0, 0.0),
            (pd.Timestamp("2024-01-01"), "08:15:30", "B", 1.0, 1.0, 1.0, 0.0),
            (pd.Timestamp("2024-01-01"), datetime.time(7, 5), "C", 1.0, 1.0, 1.0, 0.0),
            (pd.Timestamp("2024-01-01"), datetime.datetime(1900, 1, 1, 6, 1), "D", 1.0, 1.0, 1.0, 0.0),
        ])
        lijst = transacties_overzicht_uit_df(df)
        self.assertEqual([(r["product"], r["tijd"]) for r in lijst],
                         [("A", "13:39"), ("B", "08:15"), ("C", "07:05"), ("D", "06:01")])

    def test_nan_wordt_none(self):
        df = self._df([(pd.Timestamp("2024-01-01"), float("nan"), "A", 1.0, float("nan"), 5.0, float("nan"))])
        r = transacties_overzicht_uit_df(df)[0]
        self.assertIsNone(r["tijd"])
        self.assertIsNone(r["koers"])
        self.assertIsNone(r["transactiekosten"])
        self.assertEqual(r["datum"], "2024-01-01")

    def test_geen_nan_in_json(self):
        import json
        df = self._df([(pd.Timestamp("2024-01-01"), None, "A", 1.0, float("nan"), 5.0, float("nan"))])
        json.dumps(transacties_overzicht_uit_df(df), allow_nan=False)


class TestBouwDividendSamenvatting(unittest.TestCase):
    ROWS = [("US1", "AAPL", "Apple")]

    def _rec(self, datum, netto, bruto=None, belasting=None, isin="US1"):
        return {"datum": datum, "product": "APPLE INC", "isin": isin, "valuta": "USD",
                "bruto_eur": bruto, "belasting_eur": belasting, "netto_eur": netto, "herinvesteerd": False}

    def test_floats(self):
        recs = [self._rec(datetime.date(2024, 1, 10), 1.5, 2.0, 0.5), self._rec(datetime.date(2024, 4, 10), 2.0, 2.0, 0.0)]
        s = bouw_dividend_samenvatting(recs, self.ROWS)
        self.assertEqual(s["totaal_netto"], 3.5)
        self.assertEqual(s["per_ticker"], [{"ticker": "AAPL", "bijnaam": "Apple", "totaal_netto": 3.5}])
        self.assertEqual(s["cumulatief"]["datums"], ["2024-01-10", "2024-04-10"])
        self.assertEqual(s["cumulatief"]["per_ticker"]["AAPL"], [1.5, 3.5])
        self.assertEqual([r["datum"] for r in s["lijst"]], ["2024-04-10", "2024-01-10"])

    def test_decimals_geven_dezelfde_uitkomst_als_floats(self):
        floats = [self._rec(datetime.date(2024, 1, 10), 1.5, 2.0, 0.5)]
        decimals = [self._rec(datetime.date(2024, 1, 10), Decimal("1.5"), Decimal("2.0"), Decimal("0.5"))]
        self.assertEqual(bouw_dividend_samenvatting(decimals, self.ROWS), bouw_dividend_samenvatting(floats, self.ROWS))

    def test_netto_none_blijft_in_lijst_maar_telt_niet_mee(self):
        recs = [self._rec(datetime.date(2024, 1, 10), None), self._rec(datetime.date(2024, 2, 10), 1.0, 1.0, 0.0)]
        s = bouw_dividend_samenvatting(recs, self.ROWS)
        self.assertEqual(s["totaal_netto"], 1.0)
        self.assertEqual(len(s["lijst"]), 2)
        none_rij = [r for r in s["lijst"] if r["datum"] == "2024-01-10"][0]
        self.assertIsNone(none_rij["netto_eur"])
        self.assertIsNone(none_rij["bruto_eur"])

    def test_onbekende_isin_valt_terug_op_isin(self):
        s = bouw_dividend_samenvatting([self._rec(datetime.date(2024, 1, 10), 1.0, isin="XX9")], self.ROWS)
        self.assertEqual(s["per_ticker"][0]["ticker"], "XX9")

    def test_lege_lijst_geeft_none(self):
        self.assertIsNone(bouw_dividend_samenvatting([], self.ROWS))
        self.assertIsNone(bouw_dividend_samenvatting(None, self.ROWS))


if __name__ == "__main__":
    unittest.main()
