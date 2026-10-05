"""Pure splitlogica: ruwe koersen, continue reeks en de koppeling van DeGiro-boekingen aan Yahoo-splits."""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from split_correctie import (
    DegiroSplitGebeurtenis,
    SPLIT_KOPPEL_MAX_DAGEN,
    continue_reeks,
    koppel_degiro_aan_yahoo_splits,
    ruwe_koers,
)

XELA_SPLITS = {"2021-01-26": 1 / 3, "2022-07-26": 0.05, "2023-05-15": 0.005}


def _reeks(waarden):
    return pd.Series(list(waarden.values()), index=pd.to_datetime(list(waarden)), dtype=float)


class TestRuweKoers(unittest.TestCase):
    def test_xela_echte_yahoo_waarden(self):
        # Close uit Yahoo (adjusted voor alle 3 splits); latere splits: 0,05 x 0,005 = 0,00025, met de 1:3 erbij 8,33e-5.
        close = _reeks({"2021-01-25": 10484.4043, "2021-01-26": 10040.0, "2021-01-27": 8760.0})
        ruw = ruwe_koers(close, XELA_SPLITS)
        self.assertAlmostEqual(ruw["2021-01-25"], 0.8737, places=4)
        # De splitdag zelf is al post-split: de 1:3 hoort er dan niet meer bij.
        self.assertAlmostEqual(ruw["2021-01-26"], 2.51, places=6)
        self.assertAlmostEqual(ruw["2021-01-27"], 2.19, places=6)

    def test_forward_split_na_verkoop_geen_te_lage_waarde(self):
        # GME-achtig: 4:1 na de verkoop. Yahoo toont 25, de koers van die dag was 100.
        ruw = ruwe_koers(_reeks({"2022-01-10": 25.0}), {"2022-07-22": 4.0})
        self.assertAlmostEqual(ruw["2022-01-10"], 100.0)

    def test_reverse_split_na_verkoop_geen_te_hoge_waarde(self):
        # Tilray-achtig: 1:10 na de verkoop. Yahoo toont 50, de koers van die dag was 5.
        ruw = ruwe_koers(_reeks({"2022-01-10": 50.0}), {"2023-12-01": 0.1})
        self.assertAlmostEqual(ruw["2022-01-10"], 5.0)

    def test_zonder_splits_ongewijzigd_en_nan_blijft_nan(self):
        close = _reeks({"2022-01-10": 12.5, "2022-01-11": float("nan")})
        for splits in (None, {}):
            ruw = ruwe_koers(close, splits)
            self.assertEqual(ruw["2022-01-10"], 12.5)
            self.assertTrue(pd.isna(ruw["2022-01-11"]))

    def test_invoer_wordt_niet_aangepast(self):
        close = _reeks({"2022-01-10": 50.0})
        ruwe_koers(close, {"2023-12-01": 0.1})
        self.assertEqual(close["2022-01-10"], 50.0)

    def test_split_na_de_laatste_koersdatum_telt_niet_mee_vooraf(self):
        # Alleen splits ná een dag corrigeren die dag; een split vóór de reeks verandert niets.
        ruw = ruwe_koers(_reeks({"2022-01-10": 7.0}), {"2020-01-01": 4.0})
        self.assertEqual(ruw["2022-01-10"], 7.0)

    def test_continue_reeks_is_inverse(self):
        close = _reeks({"2021-01-25": 10484.4043, "2021-01-26": 10040.0, "2022-08-01": 1000.0})
        terug = continue_reeks(ruwe_koers(close, XELA_SPLITS), XELA_SPLITS)
        for datum in close.index:
            self.assertAlmostEqual(terug[datum], close[datum], places=2)

    def test_continue_reeks_heeft_geen_sprong_op_de_splitdag(self):
        # Ruw: 3,0 vóór en 9,0 ná een 1:3-split (ratio 1/3); continu moet het verloop gelijk blijven.
        ruw = _reeks({"2022-03-01": 3.0, "2022-03-02": 9.0})
        cont = continue_reeks(ruw, {"2022-03-02": 1 / 3})
        self.assertAlmostEqual(cont["2022-03-01"], 9.0)
        self.assertAlmostEqual(cont["2022-03-02"], 9.0)


class TestKoppelDegiroAanYahoo(unittest.TestCase):
    def _gebeurtenis(self, datum, oud, nieuw):
        return DegiroSplitGebeurtenis(pd.Timestamp(datum), oud, nieuw)

    def test_xela_echte_rijen_zelfde_dag(self):
        # 14 stuks uit, 4 stuks in (14/3 = 4,67 afgerond naar beneden, rest in contanten).
        g = self._gebeurtenis("2021-01-26", 14, 4)
        gekoppeld, geen_g, geen_s = koppel_degiro_aan_yahoo_splits([g], XELA_SPLITS)
        self.assertEqual(len(gekoppeld), 1)
        self.assertEqual(gekoppeld[0].yahoo_datum, pd.Timestamp("2021-01-26"))
        self.assertAlmostEqual(gekoppeld[0].yahoo_ratio, 1 / 3)
        self.assertEqual(geen_g, [])
        # De splits van 2022 en 2023 hebben geen DeGiro-boeking (positie was al gesloten).
        self.assertEqual([d for d, _ in geen_s], [pd.Timestamp("2022-07-26"), pd.Timestamp("2023-05-15")])

    def test_een_dag_verschil_koppelt_in_beide_richtingen(self):
        for boekdatum in ("2021-01-27", "2021-01-25"):
            gekoppeld, geen_g, _ = koppel_degiro_aan_yahoo_splits(
                [self._gebeurtenis(boekdatum, 14, 4)], {"2021-01-26": 1 / 3})
            self.assertEqual(len(gekoppeld), 1, boekdatum)
            self.assertEqual(gekoppeld[0].yahoo_datum, pd.Timestamp("2021-01-26"))
            self.assertEqual(geen_g, [])

    def test_rand_van_het_venster(self):
        splits = {"2021-01-26": 1 / 3}
        op_de_rand = pd.Timestamp("2021-01-26") + pd.Timedelta(days=SPLIT_KOPPEL_MAX_DAGEN)
        een_dag_verder = op_de_rand + pd.Timedelta(days=1)
        gekoppeld, _, _ = koppel_degiro_aan_yahoo_splits([self._gebeurtenis(op_de_rand, 14, 4)], splits)
        self.assertEqual(len(gekoppeld), 1)
        gekoppeld, geen_g, geen_s = koppel_degiro_aan_yahoo_splits([self._gebeurtenis(een_dag_verder, 14, 4)], splits)
        self.assertEqual(gekoppeld, [])
        self.assertEqual(len(geen_g), 1)
        self.assertEqual(len(geen_s), 1)

    def test_ratio_die_niet_past_koppelt_niet(self):
        # 14 -> 7 hoort bij 1:2, niet bij 1:3 (verwacht 4,67, verschil 2,33 stuks).
        gekoppeld, geen_g, _ = koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2021-01-26", 14, 7)], {"2021-01-26": 1 / 3})
        self.assertEqual(gekoppeld, [])
        self.assertEqual(len(geen_g), 1)

    def test_afronding_floor_min_een_tot_ceil(self):
        # Verwacht 40 (4:1 op 10 stuks): toegestaan 39..40. Verwacht 4,667 (1:3 op 14): 3..5.
        splits = {"2022-03-01": 4.0}
        for nieuw, verwacht_match in ((40, True), (39, True), (38, False), (41, False)):
            gekoppeld = koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2022-03-01", 10, nieuw)], splits)[0]
            self.assertEqual(len(gekoppeld), 1 if verwacht_match else 0, nieuw)
        for nieuw, verwacht_match in ((5, True), (4, True), (3, True), (2, False), (6, False)):
            gekoppeld = koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2021-01-26", 14, nieuw)], {"2021-01-26": 1 / 3})[0]
            self.assertEqual(len(gekoppeld), 1 if verwacht_match else 0, nieuw)

    def test_grote_positie_krijgt_geen_relatieve_ruimte(self):
        # 1000 stuks x 4 = 4000: 3900 (2,5% eronder) is bij 5% marge een match geweest, nu niet.
        splits = {"2022-03-01": 4.0}
        self.assertEqual(len(koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2022-03-01", 1000, 3999)], splits)[0]), 1)
        self.assertEqual(len(koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2022-03-01", 1000, 3900)], splits)[0]), 0)

    def test_float_ruis_in_verwacht_aantal_verschuift_de_grens_niet(self):
        # 15 x (1/3) is in float 5,000000000000001 (ceil zou 6 geven): moet gewoon 5 zijn, dus 4..5 toegestaan.
        gekoppeld = koppel_degiro_aan_yahoo_splits([self._gebeurtenis("2021-01-26", 15, 6)], {"2021-01-26": 1 / 3})[0]
        self.assertEqual(gekoppeld, [])

    def test_elke_yahoo_split_hoogstens_een_keer(self):
        splits = {"2022-03-01": 4.0}
        goed = self._gebeurtenis("2022-03-01", 10, 40)
        dezelfde_dag_slechter = self._gebeurtenis("2022-03-02", 10, 39)
        gekoppeld, geen_g, geen_s = koppel_degiro_aan_yahoo_splits([dezelfde_dag_slechter, goed], splits)
        self.assertEqual([k.gebeurtenis for k in gekoppeld], [goed])
        self.assertEqual(geen_g, [dezelfde_dag_slechter])
        self.assertEqual(geen_s, [])

    def test_twee_splits_vlak_na_elkaar_kiezen_elk_hun_partner(self):
        splits = {"2022-03-01": 4.0, "2022-03-04": 0.5}
        vooruit = self._gebeurtenis("2022-03-01", 10, 40)
        achteruit = self._gebeurtenis("2022-03-04", 40, 20)
        gekoppeld, geen_g, geen_s = koppel_degiro_aan_yahoo_splits([achteruit, vooruit], splits)
        paren = {(k.gebeurtenis, k.yahoo_ratio) for k in gekoppeld}
        self.assertEqual(paren, {(vooruit, 4.0), (achteruit, 0.5)})
        self.assertEqual((geen_g, geen_s), ([], []))

    def test_zonder_gebeurtenissen_of_splits(self):
        self.assertEqual(koppel_degiro_aan_yahoo_splits([], {}), ([], [], []))
        g = self._gebeurtenis("2022-03-01", 10, 40)
        self.assertEqual(koppel_degiro_aan_yahoo_splits([g], None), ([], [g], []))
        self.assertEqual(koppel_degiro_aan_yahoo_splits([], {"2022-03-01": 4.0}), ([], [], [(pd.Timestamp("2022-03-01"), 4.0)]))


if __name__ == "__main__":
    unittest.main()
