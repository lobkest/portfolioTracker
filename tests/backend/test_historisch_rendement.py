"""Historisch rendement (historisch_rendement.py): CAGR, rollende perioden, portfolio-index, horizonnen, waarschuwingen.
Zonder database en zonder Yahoo."""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import historisch_rendement as hr
import portfolio_orchestratie
from portfolio_calc import compute_split_adjusted_shares
from split_correctie import continue_reeks

PEIL = "2026-10-07"


def _groei(begin, eind, jaarpct=5.0, start=100.0):
    """Dagelijkse reeks (kalenderdagen) met constante groei per jaar."""
    datums = pd.date_range(begin, eind, freq="D")
    jaren = (datums - datums[0]).days / hr.DAGEN_PER_JAAR
    return pd.Series(start * (1 + jaarpct / 100) ** jaren, index=datums)


def _random_walk(begin, eind, seed=1):
    datums = pd.bdate_range(begin, eind)
    stappen = np.random.default_rng(seed).normal(0.0003, 0.01, len(datums))
    return pd.Series(100 * np.exp(np.cumsum(stappen)), index=datums)


class TestCagr(unittest.TestCase):
    def test_verdubbeling_in_10_jaar_is_7_177_procent(self):
        reeks = pd.Series([100.0, 200.0], index=pd.to_datetime(["2016-01-01", "2026-01-01"]))
        self.assertAlmostEqual(hr.cagr(reeks) * 100, 7.177, places=2)

    def test_korter_dan_een_jaar_geeft_none(self):
        self.assertIsNone(hr.cagr(_groei("2026-01-01", "2026-09-01")))

    def test_begin_nul_geeft_none(self):
        reeks = pd.Series([0.0, 200.0], index=pd.to_datetime(["2016-01-01", "2026-01-01"]))
        self.assertIsNone(hr.cagr(reeks))

    def test_venster_knipt_op_10_jaar(self):
        reeks, jaren = hr.venster(_groei("2010-01-01", PEIL), PEIL)
        self.assertAlmostEqual(jaren, 10.0, places=2)
        self.assertGreaterEqual(reeks.index[0], pd.Timestamp("2016-10-06"))


class TestSplit(unittest.TestCase):
    def test_continue_reeks_geeft_cagr_zonder_splitsprong(self):
        echt = _groei("2016-10-07", PEIL)
        # Ruwe koers: na een 1:4-split noteert het aandeel een kwart.
        ruw = echt.where(echt.index < "2021-06-01", echt / 4)
        self.assertLess(hr.cagr(ruw), 0)
        self.assertAlmostEqual(hr.cagr(continue_reeks(ruw, {"2021-06-01": 4.0})) * 100, 5.0, places=3)


class TestRollendEnIndex(unittest.TestCase):
    def test_constante_groei_geeft_overal_5_procent(self):
        for horizon in (1, 3):
            waarden = hr.rollende_cagrs(_groei("2016-10-07", PEIL), horizon)
            self.assertGreater(len(waarden), 100)
            # Kalenderjaren: een schrikkeljaar telt 366 dagen, dus hooguit ~0,01 procentpunt verschil.
            self.assertTrue(all(abs(w * 100 - 5.0) < 0.02 for w in waarden), horizon)

    def test_te_weinig_data_geeft_lege_lijst(self):
        self.assertEqual(hr.rollende_cagrs(_groei("2026-01-01", PEIL), 1), [])

    def test_portfolio_index_korte_ticker_telt_vanaf_eerste_koers_en_gewichten_herschaald(self):
        lang = pd.Series(100.0, index=pd.date_range("2020-01-01", "2026-01-01"))
        # Kort: begint 01-01-2023, stijgt de dag erna 10% en blijft dan gelijk.
        kort = pd.Series(110.0, index=pd.date_range("2023-01-01", "2026-01-01"))
        kort.iloc[0] = 100.0
        # Minder dan een jaar: telt niet mee, ondanks de sprong.
        nieuw = pd.Series(100.0, index=pd.date_range("2025-06-01", "2025-12-01"))
        nieuw.iloc[-30:] = 500.0
        index, niet_compleet = hr.portfolio_index(
            {"LANG": lang, "KORT": kort, "NIEUW": nieuw}, {"LANG": 0.25, "KORT": 0.25, "NIEUW": 0.5})
        self.assertEqual(index.iloc[0], 1.0)
        self.assertEqual(index[pd.Timestamp("2023-01-01")], 1.0)
        # Op 02-01-2023: (0 x 0,25 + 10% x 0,25) / 0,5 = 5%.
        self.assertAlmostEqual(index[pd.Timestamp("2023-01-02")], 1.05)
        self.assertAlmostEqual(index.iloc[-1], 1.05)
        self.assertAlmostEqual(niet_compleet, 3.0, places=2)


class TestHorizonnen(unittest.TestCase):
    def test_10_jaar_data_geeft_horizon_1_tot_en_met_9(self):
        index, jaren = hr.venster(_random_walk("2016-10-01", PEIL), PEIL)
        verdeling = hr.horizon_verdeling(index, jaren)
        self.assertEqual(sorted(verdeling), list(range(1, 10)))
        for horizon, v in verdeling.items():
            with self.subTest(horizon=horizon):
                self.assertLessEqual(v["laag"], v["midden"])
                self.assertLessEqual(v["midden"], v["hoog"])
                self.assertGreater(v["aantal_vensters"], 0)

    def test_2_5_jaar_data_geeft_alleen_horizon_1(self):
        index, jaren = hr.venster(_random_walk("2024-04-07", PEIL), PEIL)
        self.assertEqual(sorted(hr.horizon_verdeling(index, jaren)), [1])

    def test_positie_statistiek(self):
        stat = hr.positie_statistiek(_groei("2024-10-07", PEIL), PEIL)
        self.assertEqual(stat["beschikbare_jaren"], 2.0)
        self.assertEqual(stat["cagr_pct"], 5.0)
        self.assertEqual((stat["laag_1j_pct"], stat["hoog_1j_pct"]), (5.0, 5.0))
        self.assertFalse(stat["te_kort"])
        self.assertTrue(stat["kort"])


class TestWaarschuwingen(unittest.TestCase):
    def _positie(self, naam, jaren, status=hr.STATUS_OK):
        return {"bijnaam": naam, "beschikbare_jaren": jaren, "kort": jaren < 3, "te_kort": jaren < 1, "status": status}

    def test_positie_van_2_jaar(self):
        teksten = hr.waarschuwingen([self._positie("Nieuw BV", 2.0), self._positie("Oud BV", 9.5)], 6.0, 0.0, False)
        self.assertEqual(len(teksten), 1)
        self.assertIn("Nieuw BV (2 jaar)", teksten[0])
        self.assertNotIn("Oud BV", teksten[0])

    def test_positie_van_half_jaar_telt_niet_mee(self):
        teksten = hr.waarschuwingen([self._positie("IPO NV", 0.5, hr.STATUS_TE_KORT)], 6.0, 0.0, False)
        self.assertEqual(len(teksten), 1)
        self.assertIn("Telt niet mee", teksten[0])
        self.assertIn("IPO NV (0,5 jaar)", teksten[0])

    def test_midden_boven_12_procent(self):
        self.assertEqual(hr.waarschuwingen([], 12.0, 0.0, False), [])
        teksten = hr.waarschuwingen([], 12.1, 0.0, False)
        self.assertIn("overschat de toekomst", teksten[0])

    def test_niet_compleet_en_onvolledig(self):
        teksten = hr.waarschuwingen([], 6.0, 3.0, True)
        self.assertEqual(len(teksten), 2)
        self.assertIn("eerste 3 jaar", teksten[0])
        self.assertIn("klik opnieuw", teksten[1])


def _transacties():
    rijen = [
        ("2015-01-05", datetime.time(10, 0), "US0000000001", "ABC", 10, 50.0, -1.0, "ABC INC"),
        ("2026-04-07", datetime.time(10, 0), "NL0000000002", "NIEUW.AS", 5, 20.0, -1.0, "NIEUW NV"),
    ]
    df = pd.DataFrame(rijen, columns=["datum", "tijd", "isin", "ticker", "aantal", "koers", "transactiekosten", "product"])
    df["datum"] = pd.to_datetime(df["datum"])
    df["beurs"] = "NSY"
    return compute_split_adjusted_shares(df)


class TestBerekenHistorischRendement(unittest.TestCase):
    def test_gebruikt_de_continue_reeks_en_antwoordvorm(self):
        echt = _groei("2016-10-07", PEIL)
        # Ruwe koers met een 1:4-split; zonder continue reeks zou de CAGR negatief zijn.
        ruw = echt.where(echt.index < "2021-06-01", echt / 4)
        nieuw = pd.Series(20.0, index=pd.date_range("2026-04-07", PEIL))
        koersen = pd.DataFrame({"ABC": ruw, "NIEUW.AS": nieuw})
        koersen.attrs["koersen_onvolledig"] = []
        resultaat = pd.DataFrame({"waarde": [100.0]}, index=pd.to_datetime([PEIL]))
        with patch.object(hr, "laad_transacties_en_resultaat", return_value=(_transacties(), resultaat)), \
                patch.object(hr, "get_prices", return_value=koersen) as get_prices, \
                patch.object(portfolio_orchestratie, "db_get_koers_splits",
                             return_value={"ABC": {"2021-06-01": 4.0}}):
            data = hr.bereken_historisch_rendement("TEST_HR")

        # 10 x 365,25 dagen terug.
        self.assertEqual(get_prices.call_args[0][1].date(), datetime.date(2016, 10, 6))
        abc = next(p for p in data["posities"] if p["ticker"] == "ABC")
        self.assertEqual(abc["status"], hr.STATUS_OK)
        self.assertAlmostEqual(abc["cagr_pct"], 5.0, places=1)
        nieuw_pos = next(p for p in data["posities"] if p["ticker"] == "NIEUW.AS")
        self.assertEqual(nieuw_pos["status"], hr.STATUS_TE_KORT)
        # Gewicht = huidige waarde: 10 x 105/4 tegenover 5 x 20.
        self.assertAlmostEqual(abc["gewicht"] + nieuw_pos["gewicht"], 1.0, places=3)
        self.assertGreater(abc["gewicht"], nieuw_pos["gewicht"])

        self.assertTrue(data["beschikbaar"])
        self.assertFalse(data["onvolledig"])
        self.assertEqual(data["max_horizon"], 9)
        self.assertEqual(sorted(data["horizonnen"], key=int), [str(h) for h in range(1, 10)])
        self.assertAlmostEqual(data["horizonnen"]["5"]["midden"], 5.0, places=1)
        self.assertIsNone(data["horizonnen"]["5"]["waarschuwing"])
        self.assertTrue(any("Telt niet mee" in w for w in data["waarschuwingen"]))
        for sleutel in ("peildatum", "terugkijk_jaren", "jaren_niet_compleet"):
            self.assertIn(sleutel, data)
        self.assertEqual(set(abc), {"isin", "ticker", "bijnaam", "gewicht", "beschikbare_jaren", "cagr_pct",
                                    "laag_1j_pct", "hoog_1j_pct", "te_kort", "kort", "status"})


class TestHistorischRendementRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_onbekende_code_geeft_404(self):
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=False):
            res = self.client.get("/api/portfolio/zzz/historisch-rendement")
        self.assertEqual(res.status_code, 404)

    def test_antwoord_met_diagnostiek_en_nette_fout(self):
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "bereken_historisch_rendement",
                             return_value={"beschikbaar": True, "horizonnen": {"1": {"midden": 5.0}}}):
            data = self.client.get("/api/portfolio/ABC/historisch-rendement").get_json()
        self.assertEqual(data["horizonnen"]["1"]["midden"], 5.0)
        self.assertIn("diagnostiek", data)
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "bereken_historisch_rendement", side_effect=RuntimeError("stuk")):
            res = self.client.get("/api/portfolio/ABC/historisch-rendement")
        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())


if __name__ == "__main__":
    unittest.main()
