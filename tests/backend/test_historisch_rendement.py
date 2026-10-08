"""Historisch rendement (historisch_rendement.py): CAGR, rollende perioden, portfolio-index, bootstrap, waarschuwingen.
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


class TestCagr(unittest.TestCase):
    def test_verdubbeling_in_10_jaar_is_7_177_procent(self):
        reeks = pd.Series([100.0, 200.0], index=pd.to_datetime(["2016-01-01", "2026-01-01"]))
        self.assertAlmostEqual(hr.cagr(reeks) * 100, 7.177, places=2)

    def test_korter_dan_een_jaar_geeft_none(self):
        self.assertIsNone(hr.cagr(_groei("2026-01-01", "2026-09-01")))

    def test_begin_nul_geeft_none(self):
        reeks = pd.Series([0.0, 200.0], index=pd.to_datetime(["2016-01-01", "2026-01-01"]))
        self.assertIsNone(hr.cagr(reeks))

    def test_venster_houdt_alle_historie_tot_en_met_peildatum(self):
        reeks, jaren = hr.venster(_groei("1990-01-01", "2026-12-31"), PEIL)
        self.assertEqual(reeks.index[0], pd.Timestamp("1990-01-01"))
        self.assertEqual(reeks.index[-1], pd.Timestamp(PEIL))
        self.assertGreater(jaren, 36)


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

    def _lang_kort_nieuw(self):
        lang = pd.Series(100.0, index=pd.date_range("2020-01-01", "2026-01-01"))
        # Kort: begint 01-01-2023, stijgt de dag erna 10% en blijft dan gelijk.
        kort = pd.Series(110.0, index=pd.date_range("2023-01-01", "2026-01-01"))
        kort.iloc[0] = 100.0
        # Minder dan een jaar: telt niet mee, ondanks de sprong.
        nieuw = pd.Series(100.0, index=pd.date_range("2025-06-01", "2025-12-01"))
        nieuw.iloc[-30:] = 500.0
        return {"LANG": lang, "KORT": kort, "NIEUW": nieuw}

    def test_portfolio_index_korte_ticker_telt_vanaf_eerste_koers_en_gewichten_herschaald(self):
        index, niet_compleet, dekking = hr.portfolio_index(
            self._lang_kort_nieuw(), {"LANG": 0.5, "KORT": 0.25, "NIEUW": 0.25})
        self.assertEqual(index.index[0], pd.Timestamp("2020-01-01"))
        self.assertEqual(index.iloc[0], 1.0)
        self.assertEqual(index[pd.Timestamp("2023-01-01")], 1.0)
        # Op 02-01-2023: (0 x 0,5 + 10% x 0,25) / 0,75 = 3,33%.
        self.assertAlmostEqual(index[pd.Timestamp("2023-01-02")], 1 + 0.1 / 3)
        self.assertAlmostEqual(index.iloc[-1], 1 + 0.1 / 3)
        self.assertAlmostEqual(niet_compleet, 3.0, places=2)
        self.assertEqual(dekking, 0.5)

    def test_index_begint_pas_bij_minstens_50_procent_dekking(self):
        index, niet_compleet, dekking = hr.portfolio_index(
            self._lang_kort_nieuw(), {"LANG": 0.25, "KORT": 0.25, "NIEUW": 0.5})
        # LANG alleen dekt 25%; pas met KORT erbij (01-01-2023) is het 50%.
        self.assertEqual(index.index[0], pd.Timestamp("2023-01-01"))
        self.assertEqual(index.iloc[0], 1.0)
        self.assertAlmostEqual(index[pd.Timestamp("2023-01-02")], 1.05)
        self.assertEqual(dekking, 0.5)
        self.assertEqual(niet_compleet, 0.0)

    def test_nooit_genoeg_dekking_geeft_lege_index(self):
        index, _, dekking = hr.portfolio_index(self._lang_kort_nieuw(), {"LANG": 0.2, "KORT": 0.2, "NIEUW": 0.6})
        self.assertTrue(index.empty)
        self.assertEqual(dekking, 0.0)

    def test_maandrendementen_op_maandeinde(self):
        index = pd.Series([1.0, 1.1, 1.21, 1.21, 0.968],
                          index=pd.to_datetime(["2024-01-15", "2024-01-31", "2024-02-29", "2024-03-10", "2024-03-29"]))
        # Maandeinden 1,1 -> 1,21 -> 0,968: +10% en -20%; januari zelf heeft geen vorige maand.
        # 29-03-2024 is de laatste handelsdag (31-03 is een zondag): maart telt mee.
        np.testing.assert_allclose(hr.maandrendementen(index), [0.1, -0.2])
        self.assertEqual(len(hr.maandrendementen(pd.Series(dtype=float))), 0)

    def test_index_die_halverwege_een_maand_eindigt_geeft_die_maand_niet(self):
        index = pd.Series([1.0, 1.1, 1.21, 0.5],
                          index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-29", "2024-04-15"]))
        # April loopt maar tot de 15e: alleen februari (+10%) en maart (+10%), niet de -59% van half april.
        np.testing.assert_allclose(hr.maandrendementen(index), [0.1, 0.1])


SLEUTELS = ("p10", "p25", "p50", "p75", "p90")


def _maandelijks(jaarfactor, jaren):
    return [jaarfactor ** (1 / 12) - 1] * (12 * jaren)


class TestBootstrap(unittest.TestCase):
    def test_constante_maandrendementen_geven_vijf_gelijke_paden(self):
        paden = hr.bootstrap_percentielpaden([0.01] * 60, 24, n_paden=200)
        self.assertEqual(tuple(paden), SLEUTELS)
        verwacht = 1.01 ** np.arange(25)
        for sleutel in SLEUTELS:
            np.testing.assert_allclose(paden[sleutel], verwacht, err_msg=sleutel)

    def test_zelfde_seed_geeft_zelfde_uitkomst(self):
        rendementen = np.random.default_rng(7).normal(0.005, 0.04, 120)
        a = hr.bootstrap_percentielpaden(rendementen, 60, n_paden=500, seed=3)
        b = hr.bootstrap_percentielpaden(rendementen, 60, n_paden=500, seed=3)
        for sleutel in SLEUTELS:
            np.testing.assert_array_equal(a[sleutel], b[sleutel])

    def test_percentielen_op_volgorde_en_band_wordt_breder(self):
        rendementen = np.random.default_rng(7).normal(0.005, 0.04, 120)
        paden = hr.bootstrap_percentielpaden(rendementen, 120)
        for laag, hoog in zip(SLEUTELS, SLEUTELS[1:]):
            self.assertTrue(np.all(paden[laag] <= paden[hoog]), (laag, hoog))
        band = paden["p90"] - paden["p10"]
        self.assertEqual(band[0], 0.0)
        self.assertGreater(band[120], band[12])

    def test_crashjaar_drukt_p10_na_een_jaar(self):
        zonder = _maandelijks(1.10, 10)
        met = _maandelijks(1.10, 4) + _maandelijks(0.70, 1) + _maandelijks(1.10, 5)
        p10_zonder = hr.bootstrap_percentielpaden(zonder, 12)["p10"][12]
        p10_met = hr.bootstrap_percentielpaden(met, 12)["p10"][12]
        self.assertAlmostEqual(p10_zonder, 1.10)
        # Ruim 10% van de startmaanden pakt een groot deel van het crashjaar mee.
        self.assertLess(p10_met, 1.0)

    def test_horizonnen_geannualiseerd_uit_paden(self):
        maanden = np.arange(37)
        paden = {"p10": np.ones(37), "p50": 1.1 ** (maanden / 12), "p90": np.ones(37)}
        # p90 na 2 jaar 1,44 = 1,2^2, na 3 jaar 1,331 = 1,1^3.
        paden["p90"][24], paden["p90"][36] = 1.44, 1.331
        horizonnen = hr.horizonnen_uit_paden(paden, 3)
        self.assertEqual(sorted(horizonnen), [1, 2, 3])
        self.assertEqual(horizonnen[2], {"p10": 0.0, "p50": 10.0, "p90": 20.0})
        self.assertEqual(horizonnen[3], {"p10": 0.0, "p50": 10.0, "p90": 10.0})
        self.assertEqual(horizonnen[1]["p50"], 10.0)

    def test_positie_statistiek(self):
        stat = hr.positie_statistiek(_groei("2024-10-07", PEIL), PEIL)
        self.assertEqual(stat["beschikbare_jaren"], 2.0)
        self.assertEqual(stat["historie_vanaf"], 2024)
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
    def _bereken(self, abc_vanaf):
        echt = _groei(abc_vanaf, PEIL)
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
        return data, get_prices

    def test_minder_dan_36_maanden_niet_beschikbaar(self):
        data, _ = self._bereken("2024-01-01")
        self.assertFalse(data["beschikbaar"])
        self.assertIn("36 maanden", data["melding"])
        self.assertIsNone(data["paden"])
        self.assertEqual(data["horizonnen"], {})
        self.assertLess(data["aantal_maanden"], hr.HISTORIE_MIN_MAANDEN)

    def test_gebruikt_de_continue_reeks_en_antwoordvorm(self):
        data, get_prices = self._bereken("2016-10-07")

        self.assertEqual(get_prices.call_args[0][1], hr.HISTORIE_VROEGSTE_START)
        abc = next(p for p in data["posities"] if p["ticker"] == "ABC")
        self.assertEqual(abc["status"], hr.STATUS_OK)
        self.assertAlmostEqual(abc["cagr_pct"], 5.0, places=1)
        nieuw_pos = next(p for p in data["posities"] if p["ticker"] == "NIEUW.AS")
        self.assertEqual(nieuw_pos["status"], hr.STATUS_TE_KORT)
        # Gewicht = huidige waarde: 10 x 105/4 tegenover 5 x 20.
        self.assertAlmostEqual(abc["gewicht"] + nieuw_pos["gewicht"], 1.0, places=3)
        self.assertGreater(abc["gewicht"], nieuw_pos["gewicht"])

        self.assertTrue(data["beschikbaar"])
        self.assertIsNone(data["melding"])
        self.assertFalse(data["onvolledig"])
        self.assertEqual(data["historie_start"], "2016-10-07")
        self.assertEqual(data["historie_jaren"], 10.0)
        # ABC heeft koers vanaf het begin, NIEUW.AS telt niet mee: ABC dekt bij de start zijn eigen gewicht.
        self.assertEqual(data["dekking_bij_start"], abc["gewicht"])
        # Maandeinden okt 2016 t/m sep 2026 (okt 2026 loopt maar tot de 7e en valt weg): 120 punten, 119 rendementen.
        self.assertEqual(data["aantal_maanden"], 119)
        maanden = 12 * hr.HISTORIE_MAX_HORIZON_JAREN + 1
        self.assertEqual({k: len(v) for k, v in data["paden"].items()}, {k: maanden for k in SLEUTELS})
        self.assertEqual(data["paden"]["p50"][0], 1.0)
        self.assertEqual(sorted(data["horizonnen"], key=int),
                         [str(h) for h in range(1, hr.HISTORIE_MAX_HORIZON_JAREN + 1)])
        # Constante groei: elk blok van 12 maanden is een kalenderjaar met 5%.
        self.assertAlmostEqual(data["horizonnen"]["5"]["p50"], 5.0, places=1)
        self.assertAlmostEqual(data["horizonnen"]["5"]["p10"], 5.0, places=1)
        self.assertIsNone(data["horizonnen"]["5"]["waarschuwing"])
        self.assertTrue(any("Telt niet mee" in w for w in data["waarschuwingen"]))
        for sleutel in ("peildatum", "jaren_niet_compleet"):
            self.assertIn(sleutel, data)
        self.assertEqual(set(abc), {"isin", "ticker", "bijnaam", "gewicht", "beschikbare_jaren", "historie_vanaf",
                                    "cagr_pct", "laag_1j_pct", "hoog_1j_pct", "te_kort", "kort", "status"})
        self.assertEqual(abc["historie_vanaf"], 2016)


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
                             return_value={"beschikbaar": True, "horizonnen": {"1": {"p50": 5.0}}}):
            data = self.client.get("/api/portfolio/ABC/historisch-rendement").get_json()
        self.assertEqual(data["horizonnen"]["1"]["p50"], 5.0)
        self.assertIn("diagnostiek", data)
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "bereken_historisch_rendement", side_effect=RuntimeError("stuk")):
            res = self.client.get("/api/portfolio/ABC/historisch-rendement")
        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())


if __name__ == "__main__":
    unittest.main()
