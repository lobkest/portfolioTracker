import unittest
from unittest.mock import patch

import pandas as pd

from backend import db, effect_koers
from backend.effect_koers import (
    beschikbaar_sinds, dagpunten, dividenden_in_eur, rendementen, uitleg_uit_dagkoersen, weekpunten,
)
from backend.split_correctie import continue_reeks, ruwe_koers

START = pd.Timestamp("2024-01-01")  # maandag


def reeks(waarden, begin="2024-01-01"):
    """Waarden op opeenvolgende werkdagen (ma-vr)."""
    return pd.Series(waarden, index=pd.bdate_range(begin, periods=len(waarden)), dtype=float)


def dividend(**per_datum):
    return pd.Series({pd.Timestamp(d[1:].replace("_", "-")): b for d, b in per_datum.items()})


class BenaderdTestCase(unittest.TestCase):
    def assertLijstBenaderd(self, gevonden, verwacht):
        self.assertEqual(len(gevonden), len(verwacht))
        for g, v in zip(gevonden, verwacht):
            self.assertAlmostEqual(g, v, places=6)


class TestDagpunten(BenaderdTestCase):
    def test_zonder_dividend_zijn_de_indexen_gelijk(self):
        p = dagpunten(reeks([100, 110, 90]), None, START)
        self.assertLijstBenaderd(list(p["koers_index"]), [100, 110, 90])
        self.assertLijstBenaderd(list(p["totaal_index"]), list(p["koers_index"]))

    def test_dividend_verhoogt_het_totaal_vanaf_de_ex_datum(self):
        p = dagpunten(reeks([100, 100, 100, 100]), dividend(d2024_01_03=5.0), START)
        self.assertLijstBenaderd(list(p["koers_index"]), [100, 100, 100, 100])
        self.assertLijstBenaderd(list(p["totaal_index"]), [100, 100, 105, 105])

    def test_dividend_voor_de_start_telt_niet_mee(self):
        p = dagpunten(reeks([100, 100]), dividend(d2023_12_15=5.0), START)
        self.assertLijstBenaderd(list(p["totaal_index"]), [100, 100])

    def test_start_op_weekend_begint_op_de_eerste_handelsdag(self):
        p = dagpunten(reeks([50, 100]), None, pd.Timestamp("2023-12-31"))
        self.assertEqual(p.index[0], pd.Timestamp("2024-01-01"))
        self.assertEqual(p["koers_index"].iloc[0], 100)

    def test_dividenden_van_dezelfde_dag_worden_opgeteld(self):
        p = dagpunten(reeks([100, 100]), dividend(d2024_01_02=2.0).add(dividend(d2024_01_02=3.0), fill_value=0), START)
        self.assertEqual(p["totaal_index"].iloc[-1], 105)


class TestWeekpunten(BenaderdTestCase):
    def test_weekgemiddelde_van_vijf_dagen(self):
        p = weekpunten(reeks([100, 102, 104, 106, 108]), None, START)
        self.assertEqual(len(p), 1)
        self.assertEqual(p["koers_eur"].iloc[0], 104)
        self.assertEqual(p["koers_index"].iloc[0], 100)

    def test_week_met_feestdag_gebruikt_vier_dagen(self):
        koersen = reeks([100] * 5 + [110, 110, 120, 120, 120])
        koersen = koersen.drop(pd.Timestamp("2024-01-08"))
        p = weekpunten(koersen, None, START)
        self.assertEqual(p["koers_eur"].iloc[1], (110 + 120 + 120 + 120) / 4)

    def test_datum_is_de_laatste_handelsdag_van_de_week(self):
        koersen = reeks([100] * 10).drop(pd.Timestamp("2024-01-05"))
        p = weekpunten(koersen, None, START)
        self.assertEqual(list(p.index), [pd.Timestamp("2024-01-04"), pd.Timestamp("2024-01-12")])

    def test_dividend_telt_pas_vanaf_de_week_erna(self):
        koersen = reeks([100] * 15)
        p = weekpunten(koersen, dividend(d2024_01_03=10.0), START)
        self.assertLijstBenaderd(list(p["totaal_index"]), [100, 110, 110])

    def test_dividend_voor_de_start_telt_niet_mee(self):
        p = weekpunten(reeks([100] * 10), dividend(d2023_12_20=10.0), START)
        self.assertLijstBenaderd(list(p["totaal_index"]), [100, 100])

    def test_eerste_week_deels_voor_de_start_wordt_afgekapt(self):
        p = weekpunten(reeks([100] * 5), None, pd.Timestamp("2024-01-03"))
        self.assertEqual(p["koers_eur"].iloc[0], 100)
        self.assertEqual(len(p), 1)


class TestRendementen(unittest.TestCase):
    def test_dagdata_en_weekdata_geven_hetzelfde_rendement(self):
        koersen = reeks([100, 104, 98, 101, 120, 90, 95, 130])
        divs = dividend(d2024_01_03=2.0, d2024_01_09=3.0)
        koers, totaal = rendementen(koersen, divs, START)
        self.assertAlmostEqual(koers, 30.0)
        self.assertAlmostEqual(totaal, 35.0)
        week = weekpunten(koersen, divs, START)
        self.assertNotAlmostEqual(week["totaal_index"].iloc[-1] - 100, totaal)

    def test_zonder_koersen_none(self):
        self.assertEqual(rendementen(pd.Series(dtype=float), None, START), (None, None))


class TestUitleg(unittest.TestCase):
    def test_begin_en_eind_uit_de_dagkoersen_en_dividendsom_alleen_in_de_periode(self):
        koersen = reeks([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
        divs = dividend(d2023_12_01=5.0, d2024_01_01=2.0, d2024_01_04=1.0, d2024_01_10=0.5, d2024_02_01=7.0)
        self.assertEqual(uitleg_uit_dagkoersen(koersen, divs, START), {
            "begin_datum": "2024-01-01", "eind_datum": "2024-01-12", "koers_begin_eur": 100.0, "koers_eind_eur": 109.0,
            "dividend_som_eur": 1.5})

    def test_klopt_met_de_rendementen(self):
        koersen = reeks([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
        divs = dividend(d2024_01_04=1.0)
        u = uitleg_uit_dagkoersen(koersen, divs, START)
        koers, totaal = rendementen(koersen, divs, START)
        self.assertAlmostEqual((u["koers_eind_eur"] / u["koers_begin_eur"] - 1) * 100, koers)
        self.assertAlmostEqual(((u["koers_eind_eur"] + u["dividend_som_eur"]) / u["koers_begin_eur"] - 1) * 100, totaal)

    def test_zonder_koersen_none_en_zonder_dividend_nul(self):
        self.assertIsNone(uitleg_uit_dagkoersen(pd.Series(dtype=float), None, START))
        self.assertEqual(uitleg_uit_dagkoersen(reeks([100, 110]), None, START)["dividend_som_eur"], 0.0)


class TestBeschikbaarSinds(unittest.TestCase):
    def test_none_als_de_data_vanaf_de_start_beschikbaar_is(self):
        self.assertIsNone(beschikbaar_sinds(reeks([1, 2]), START))
        self.assertIsNone(beschikbaar_sinds(reeks([1, 2], begin="2024-01-08"), START))

    def test_datum_als_de_reeks_later_begint(self):
        self.assertEqual(beschikbaar_sinds(reeks([1, 2], begin="2024-03-04"), START).isoformat(), "2024-03-04")


class TestSplit(unittest.TestCase):
    def test_split_geeft_continue_index_zonder_sprong(self):
        # Yahoo-Close is al gecorrigeerd; ruw springt van 400 naar 100 op de splitdag (4:1).
        gecorrigeerd = reeks([100, 101, 102, 103])
        splits = {"2024-01-03": 4.0}
        ruw = ruwe_koers(gecorrigeerd, splits)
        self.assertEqual(ruw.iloc[1], 404)
        continu = continue_reeks(ruw, splits)
        p = dagpunten(continu, None, START)
        self.assertEqual([round(x, 2) for x in p["koers_index"]], [100, 101, 102, 103])


class TestDividendenInEur(unittest.TestCase):
    FX = pd.Series([0.9, 0.8], index=pd.to_datetime(["2024-01-01", "2024-01-10"]))

    def test_eur_blijft_gelijk(self):
        self.assertEqual(dividenden_in_eur({"2024-01-05": 1.5}, "EUR", None).iloc[0], 1.5)

    def test_usd_met_fx_op_de_ex_datum(self):
        self.assertAlmostEqual(dividenden_in_eur({"2024-01-12": 2.0}, "USD", self.FX).iloc[0], 1.6)

    def test_gbp_pence_wordt_gedeeld_door_honderd(self):
        self.assertAlmostEqual(dividenden_in_eur({"2024-01-05": 50.0}, "GBp", self.FX).iloc[0], 50.0 * 0.9 / 100)

    def test_zonder_fx_voor_die_datum_weggelaten(self):
        self.assertTrue(dividenden_in_eur({"2023-12-01": 2.0}, "USD", self.FX).empty)
        self.assertTrue(dividenden_in_eur({"2024-01-05": 2.0}, "USD", None).empty)

    def test_niet_ondersteunde_valuta_weggelaten(self):
        self.assertTrue(dividenden_in_eur({"2024-01-05": 2.0}, "CHF", self.FX).empty)


class TestRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)
        self.koersen = reeks([100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
        for naam, waarde in {
            "download_koersen_met_retry": (pd.DataFrame(), {}),
            "_ruwe_koersen_in_eur": (pd.DataFrame({"X": self.koersen}), {"X": {}}),
            "haal_yahoo_dividenden": {"dividenden": {"2024-01-04": 1.0}},
            "_valuta_per_ticker": {"X": "EUR"},
            "valuta_voor_effect": "EUR",
        }.items():
            mock = patch.object(effect_koers, naam, return_value=waarde)
            setattr(self, naam, mock.start())
            self.addCleanup(mock.stop)
        patch_start = patch.object(effect_koers, "periode_start", return_value=START.date())
        patch_start.start()
        self.addCleanup(patch_start.stop)

    def vraag(self, **q):
        return self.client.get("/api/effect-koers", query_string=dict({"ticker": "X"}, **q))

    def test_200_met_de_juiste_vorm(self):
        res = self.vraag(periode="3j", per="week")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(set(data), {"periode", "per", "punten", "koers_rendement", "totaal_rendement",
                                     "beschikbaar_sinds", "heeft_dividend", "uitleg"})
        self.assertEqual(data["per"], "week")
        self.assertEqual(len(data["punten"]), 2)
        self.assertEqual(set(data["punten"][0]), {"datum", "koers_eur", "koers_index", "totaal_index"})
        self.assertTrue(data["heeft_dividend"])
        self.assertEqual(data["koers_rendement"], 9.0)

    def test_uitleg_is_bij_week_gelijk_aan_de_daguitkomst(self):
        dag = self.vraag(periode="3j", per="dag").get_json()["uitleg"]
        week = self.vraag(periode="3j", per="week").get_json()["uitleg"]
        self.assertEqual(dag, week)
        self.assertEqual(dag, {"begin_datum": "2024-01-01", "eind_datum": "2024-01-12", "koers_begin_eur": 100.0,
                               "koers_eind_eur": 109.0, "dividend_som_eur": 1.0})

    def test_1j_is_altijd_per_dag(self):
        data = self.vraag(periode="1j", per="week").get_json()
        self.assertEqual(data["per"], "dag")
        self.assertEqual(len(data["punten"]), 10)

    def test_standaard_is_1j(self):
        self.assertEqual(self.vraag().get_json()["periode"], "1j")

    def test_ongeldige_invoer_geeft_400(self):
        for q in ({"periode": "5j"}, {"per": "maand"}, {"ticker": ""}, {"ticker": "T" * 21}):
            self.assertEqual(self.vraag(**q).status_code, 400, q)

    def test_geen_koersen_geeft_422(self):
        self._ruwe_koersen_in_eur.return_value = (pd.DataFrame(), {})
        res = self.vraag()
        self.assertEqual(res.status_code, 422)
        self.assertIn("error", res.get_json())

    def test_onbekende_valuta_geeft_422_zonder_download(self):
        self.valuta_voor_effect.return_value = None
        res = self.vraag()
        self.assertEqual(res.status_code, 422)
        self.assertIn("Valuta van dit effect onbekend", res.get_json()["error"])
        self.download_koersen_met_retry.assert_not_called()

    def test_schrijft_niets_naar_de_database(self):
        with patch.object(db, "db_save_koersen", create=True) as opslaan:
            self.assertEqual(self.vraag().status_code, 200)
        opslaan.assert_not_called()
        self.assertFalse(hasattr(effect_koers, "get_prices"))

    def test_valt_onder_de_externe_bron_limiet(self):
        from backend.toegangslimiet import REGELS_EXTERNE_BRON
        statussen = [self.client.get("/api/effect-koers", query_string={"ticker": "X"},
                                     environ_base={"REMOTE_ADDR": "4.4.4.4"}).status_code
                     for _ in range(REGELS_EXTERNE_BRON[0].maximum + 1)]
        self.assertEqual(statussen[-1], 429)


if __name__ == "__main__":
    unittest.main()
