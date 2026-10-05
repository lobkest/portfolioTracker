"""check_koers_stilstand (diagnostiek_checks.py): forward-filled koersen tijdens een open positie. Offline, geen database."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from diagnostiek import LET_OP
from diagnostiek_checks import MAX_BEVINDINGEN_PER_CHECK, MAX_FORWARD_FILL_DAGEN, check_koers_stilstand

DAGEN = pd.bdate_range("2024-01-01", "2024-03-29")


def _df(rijen):
    df = pd.DataFrame(rijen, columns=["datum", "product", "echte_naam", "ticker", "aantal"])
    df["datum"] = pd.to_datetime(df["datum"])
    return df


def _bewegend(stil_vanaf=None, stil_dagen=None, ticker="ACM"):
    """Elke dag een andere koers; vanaf `stil_vanaf` `stil_dagen` handelsdagen dezelfde (None = tot het einde)."""
    koers = pd.Series([10.0 + i * 0.01 for i in range(len(DAGEN))], index=DAGEN)
    if stil_vanaf is not None:
        start = DAGEN.get_loc(pd.Timestamp(stil_vanaf))
        eind = len(DAGEN) if stil_dagen is None else start + stil_dagen + 1
        koers.iloc[start:eind] = koers.iloc[start]
    return pd.DataFrame({ticker: koers})


AANKOOP = ("2024-01-02", "ACME", "ACME CORP", "ACM", 10)


class TestKoersStilstand(unittest.TestCase):
    def test_gedelist_tijdens_open_positie(self):
        [b] = check_koers_stilstand(_df([AANKOOP]), _bewegend(stil_vanaf="2024-03-01"))
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "koers_stilstand:ACM"))
        for stuk in ("ACME CORP (ACM)", "20 handelsdagen", "01-03-2024 t/m 29-03-2024", "gedelist"):
            self.assertIn(stuk, b["tekst"])

    def test_gat_midden_in_de_reeks(self):
        [b] = check_koers_stilstand(_df([AANKOOP]), _bewegend(stil_vanaf="2024-02-01", stil_dagen=12))
        self.assertIn("12 handelsdagen", b["tekst"])
        self.assertIn("01-02-2024 t/m 19-02-2024", b["tekst"])
        self.assertIn("ontbreken", b["tekst"])

    def test_grens_precies_op_max_geeft_niets(self):
        df = _df([AANKOOP])
        self.assertEqual(check_koers_stilstand(df, _bewegend("2024-02-01", MAX_FORWARD_FILL_DAGEN)), [])
        self.assertEqual(len(check_koers_stilstand(df, _bewegend("2024-02-01", MAX_FORWARD_FILL_DAGEN + 1))), 1)

    def test_stilstand_na_volledige_verkoop_telt_niet(self):
        df = _df([AANKOOP, ("2024-02-15", "ACME", "ACME CORP", "ACM", -10)])
        self.assertEqual(check_koers_stilstand(df, _bewegend(stil_vanaf="2024-02-16")), [])

    def test_stilstand_voor_de_aankoop_telt_niet(self):
        df = _df([("2024-03-01", "ACME", "ACME CORP", "ACM", 10)])
        self.assertEqual(check_koers_stilstand(df, _bewegend(stil_vanaf="2024-01-02", stil_dagen=30)), [])

    def test_ticker_zonder_koerskolom_en_lege_invoer(self):
        self.assertEqual(check_koers_stilstand(_df([AANKOOP]), _bewegend(ticker="ANDER")), [])
        self.assertEqual(check_koers_stilstand(_df([]), pd.DataFrame()), [])

    def test_beperkt_tot_max_langste_eerst(self):
        aantal = MAX_BEVINDINGEN_PER_CHECK + 1
        rijen = [("2024-01-02", f"P{i}", f"P{i}", f"T{i}", 1) for i in range(aantal)]
        koersen = pd.concat([_bewegend("2024-02-01", 11 + i, ticker=f"T{i}") for i in range(aantal)], axis=1)
        uit = check_koers_stilstand(_df(rijen), koersen)
        self.assertEqual(len(uit), MAX_BEVINDINGEN_PER_CHECK + 1)
        self.assertEqual(uit[0]["sleutel"], f"koers_stilstand:T{aantal - 1}")
        self.assertIn("en 1 meer", uit[-1]["tekst"])


if __name__ == "__main__":
    unittest.main()
