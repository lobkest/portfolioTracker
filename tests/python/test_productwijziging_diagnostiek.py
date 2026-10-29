"""Een productwijziging (rechten -> aandelen, BYD) is voor Diagnostiek geen gewone transactie, maar blijft echt bezit."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.diagnostiek_checks import (
    check_corporate_action_rijen, check_negatief_aantal, check_ontbrekende_kolommen, check_synthetische_order_ids,
)
from backend.portfolio_calc import holdings_op_datums
from backend.statistieken import bereken_holdings_en_gesloten

BYD = "CNE100000296"
D1, D2 = datetime.date(2025, 6, 10), datetime.date(2025, 7, 31)
MIDDERNACHT, OCHTEND = datetime.time(0, 0), datetime.time(7, 49)


def _rij(datum, tijd, aantal, beurs="TDG", koers=0.0, order_id="SYN-x", ticker="BY6.MU", kosten=None, totaal=0.0,
         product="BYD CO LTD"):
    return {"datum": datum, "tijd": tijd, "product": product, "echte_naam": product, "isin": BYD, "beurs": beurs,
            "ticker": ticker, "aantal": aantal, "koers": koers, "totaal_eur": totaal, "transactiekosten": kosten,
            "waarde_eur": totaal, "order_id": order_id}


def _byd():
    return pd.DataFrame([
        _rij(datetime.date(2024, 6, 21), datetime.time(9, 46), 12.0, koers=28.32, order_id="echt-1", kosten=-3.9,
             totaal=-343.74),
        _rij(D1, MIDDERNACHT, 9.0, beurs="DEG", ticker=None, order_id="SYN-a", product="BYD CO LTD - NON TRADEABLE"),
        _rij(D1, MIDDERNACHT, 14.0, beurs="DEG", ticker=None, order_id="SYN-b", product="BYD CO LTD - NON TRADEABLE"),
        _rij(D2, OCHTEND, -23.0, beurs="DEG", ticker=None, order_id="SYN-c", product="BYD CO LTD - NON TRADEABLE"),
        _rij(D2, OCHTEND, 23.0, order_id="SYN-d"),
        _rij(datetime.date(2026, 2, 16), datetime.time(19, 4), 10.0, koers=10.39, order_id="echt-2", kosten=-3.9,
             totaal=-107.8),
    ])


class TestProductwijzigingHerkend(unittest.TestCase):
    def test_syn_check_telt_de_omzetting_niet_mee(self):
        self.assertEqual(check_synthetische_order_ids(_byd()), [])

    def test_kostencheck_telt_de_omzetting_niet_mee(self):
        self.assertEqual(check_ontbrekende_kolommen(_byd()), [])

    def test_omzetting_staat_bij_de_corporate_action_rijen(self):
        [b] = check_corporate_action_rijen(_byd())
        self.assertIn("4 corporate-action-rijen", b["tekst"])

    def test_zonder_order_id_kolom_werkt_het_op_koers_nul(self):
        self.assertEqual(check_ontbrekende_kolommen(_byd().drop(columns="order_id")), [])


class TestGewoneTradeBlijftGewoon(unittest.TestCase):
    def test_trade_met_echt_order_id_op_hetzelfde_tijdstip(self):
        df = _byd()
        df.loc[4, "order_id"] = "echt-3"
        [b] = check_ontbrekende_kolommen(df)
        self.assertIn("1 transacties hebben geen 'transactiekosten'", b["tekst"])

    def test_trade_met_koers_hetzelfde_tijdstip(self):
        df = _byd()
        df.loc[4, "koers"] = 10.0
        self.assertEqual(len(check_synthetische_order_ids(df)), 1)

    def test_syn_trade_zonder_tegenpartij(self):
        df = _byd().drop(index=3)
        self.assertEqual(len(check_synthetische_order_ids(df)), 1)


class TestBezitBlijftGelijk(unittest.TestCase):
    def test_checks_veranderen_niets_aan_de_data_en_aantallen_en_gak_kloppen(self):
        df = _byd()
        df.loc[[0, 5], "waarde_eur"] = [-339.84, -103.9]
        voor = df.copy()
        check_ontbrekende_kolommen(df), check_synthetische_order_ids(df), check_corporate_action_rijen(df)
        check_negatief_aantal(df)
        pd.testing.assert_frame_equal(df, voor)

        open_posities, gesloten = bereken_holdings_en_gesloten(df)
        self.assertEqual(open_posities["BY6.MU"]["aantal"], 45.0)
        self.assertAlmostEqual(open_posities["BY6.MU"]["gak"], (339.84 + 103.9) / 45, places=4)
        self.assertEqual(gesloten, {})
        trades = df[df["ticker"] == "BY6.MU"]
        self.assertEqual(holdings_op_datums(trades, [pd.Timestamp("2025-07-31"), pd.Timestamp("2026-03-01")]),
                         [35.0, 45.0])


if __name__ == "__main__":
    unittest.main()
