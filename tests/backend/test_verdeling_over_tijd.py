"""waarde_per_ticker_per_dag(), bereken_verdeling_over_tijd() en de route /verdeling-over-tijd.
Zonder database en zonder Yahoo: kleine handmatige DataFrames, de basis wordt gemockt."""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from portfolio_calc import compute_per_ticker, compute_value_over_time, waarde_per_ticker_per_dag
import portfolio_verdeling
import ticker_classificatie
from portfolio_verdeling import (
    VERDELING_OVERIG_SLEUTEL, bereken_verdeling_over_tijd, compute_beurs_verdeling, compute_valuta_verdeling,
    gewichten_per_beurs, gewichten_per_positie, gewichten_per_valuta,
)


def _rij(datum, ticker, aantal, totaal_eur, beurs="EAM", product=None):
    return {
        "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal, "adj_aantal": aantal,
        "koers": 0.0, "totaal_eur": totaal_eur, "waarde_eur": totaal_eur, "beurs": beurs, "product": product or ticker,
    }


def _voorbeeld():
    """A: 10 stuks, op 4-1 5 verkocht; B: 2 stuks vanaf 3-1, koers op 5-1 ontbreekt."""
    df = pd.DataFrame([
        _rij("2024-01-02", "A", 10.0, -100.0),
        _rij("2024-01-03", "B", 2.0, -40.0),
        _rij("2024-01-04", "A", -5.0, 55.0),
    ])
    dagen = pd.date_range("2024-01-01", "2024-01-08", freq="D")
    price_data = pd.DataFrame({
        "A": [10.0, 10.0, 10.5, 11.0, 11.0, 12.0, 12.0, 13.0],
        "B": [20.0, 20.0, 20.0, 21.0, np.nan, 22.0, 22.0, 23.0],
    }, index=dagen)
    return df, price_data


def _waarde_df(kolommen, start="2024-01-01"):
    lengte = len(next(iter(kolommen.values())))
    return pd.DataFrame(kolommen, index=pd.date_range(start, periods=lengte, freq="D"), dtype=float)


class TestWaardePerTickerPerDag(unittest.TestCase):
    def test_som_gelijk_aan_compute_value_over_time(self):
        df, price_data = _voorbeeld()
        per_ticker = waarde_per_ticker_per_dag(df, price_data)
        totaal = compute_value_over_time(df, price_data)["waarde"]
        np.testing.assert_allclose(per_ticker.sum(axis=1).to_numpy(), totaal.to_numpy())

    def test_met_de_hand(self):
        df, price_data = _voorbeeld()
        per_ticker = waarde_per_ticker_per_dag(df, price_data)
        self.assertEqual(per_ticker.loc["2024-01-01"].tolist(), [0.0, 0.0])
        self.assertEqual(per_ticker.loc["2024-01-03"].tolist(), [105.0, 40.0])
        # 5 stuks A × 11; B heeft geen koers → 0.
        self.assertEqual(per_ticker.loc["2024-01-05"].tolist(), [55.0, 0.0])

    def test_gelijk_aan_compute_per_ticker_binnen_bijgesneden_periode(self):
        df, price_data = _voorbeeld()
        per_ticker = waarde_per_ticker_per_dag(df, price_data)
        for ticker, reeks in compute_per_ticker(df, price_data).items():
            verwacht = per_ticker.loc[pd.to_datetime(reeks["labels"]), ticker].round(2).tolist()
            self.assertEqual(reeks["waarde"], verwacht)

    def test_ticker_zonder_koers_valt_weg(self):
        df, price_data = _voorbeeld()
        df = pd.concat([df, pd.DataFrame([_rij("2024-01-02", "Z", 1.0, -5.0)])])
        self.assertEqual(list(waarde_per_ticker_per_dag(df, price_data).columns), ["A", "B"])


class TestBerekenVerdelingOverTijd(unittest.TestCase):
    def test_pct_telt_op_tot_100(self):
        df, price_data = _voorbeeld()
        waarde = waarde_per_ticker_per_dag(df, price_data)
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(waarde.columns), {})
        for i in range(len(res["labels"])):
            self.assertAlmostEqual(sum(r["pct"][i] for r in res["reeksen"]), 100.0, delta=0.05)

    def test_meetpunten_laatste_koersdag_per_week_en_start_bij_eerste_waarde(self):
        # 1-1-2024 is een maandag; wo 3-1 is de eerste dag met waarde.
        waarde = _waarde_df({"A": [0, 0, 10, 10, 10, 10, 10, 10, 10, 10]})
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["A"]), {})
        self.assertEqual(res["labels"], ["2024-01-07", "2024-01-10"])

    def test_laatste_dag_altijd_in_labels(self):
        waarde = _waarde_df({"A": [5.0] * 9}, start="2024-01-01")  # t/m di 9-1
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["A"]), {})
        self.assertEqual(res["labels"][-1], "2024-01-09")

    def test_verkochte_positie_op_nul_na_verkoop(self):
        waarde = _waarde_df({"A": [10.0] * 7 + [0.0] * 7, "B": [10.0] * 14})
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["A", "B"]), {})
        a = next(r for r in res["reeksen"] if r["sleutel"] == "A")
        self.assertEqual(a["pct"], [50.0, 0.0])
        self.assertEqual(a["waarde"], [10.0, 0.0])

    def test_top_n_op_euro_som_niet_op_max_pct(self):
        # Kleine K was in week 1 alleen (100%), daarna komen X en Y met veel meer geld.
        waarde = _waarde_df({
            "K": [1.0] * 21,
            "X": [0.0] * 7 + [100.0] * 14,
            "Y": [0.0] * 7 + [50.0] * 14,
        })
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["K", "X", "Y"]), {}, top_n=2)
        self.assertEqual([r["sleutel"] for r in res["reeksen"]], ["X", "Y", VERDELING_OVERIG_SLEUTEL])
        overig = res["reeksen"][-1]
        self.assertEqual(overig["naam"], "Overig")
        self.assertEqual(overig["pct"][0], 100.0)

    def test_geen_overig_bij_hooguit_n_categorieen(self):
        waarde = _waarde_df({"A": [1.0] * 7, "B": [2.0] * 7})
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["A", "B"]), {}, top_n=2)
        self.assertNotIn(VERDELING_OVERIG_SLEUTEL, [r["sleutel"] for r in res["reeksen"]])

    def test_pct_none_bij_totaal_nul(self):
        waarde = _waarde_df({"A": [10.0] * 7 + [0.0] * 7 + [10.0] * 7})
        res = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(["A"]), {})
        self.assertEqual(res["totaal"], [10.0, 0.0, 10.0])
        self.assertEqual(res["reeksen"][0]["pct"], [100.0, None, 100.0])

    def test_ticker_zonder_gewichten_naar_unknown(self):
        waarde = _waarde_df({"A": [30.0] * 7, "B": [10.0] * 7})
        res = bereken_verdeling_over_tijd(waarde, {"A": {"A": 1.0}}, {"A": "Fonds A"})
        self.assertEqual([(r["sleutel"], r["naam"], r["pct"]) for r in res["reeksen"]],
                         [("A", "Fonds A", [75.0]), ("Unknown", "Unknown", [25.0])])

    def test_fractioneel_gewicht(self):
        waarde = _waarde_df({"ETF": [100.0] * 7, "NL": [50.0] * 7})
        gewichten = {"ETF": {"US": 0.6, "NL": 0.4}, "NL": {"NL": 1.0}}
        res = bereken_verdeling_over_tijd(waarde, gewichten, {"US": "Verenigde Staten"})
        per_sleutel = {r["sleutel"]: (r["naam"], r["waarde"][0], r["pct"][0]) for r in res["reeksen"]}
        self.assertEqual(per_sleutel, {"NL": ("NL", 90.0, 60.0), "US": ("Verenigde Staten", 60.0, 40.0)})
        self.assertEqual(res["totaal"], [150.0])

    def test_leeg(self):
        leeg = {"labels": [], "totaal": [], "reeksen": []}
        self.assertEqual(bereken_verdeling_over_tijd(pd.DataFrame(), {}, {}), leeg)
        self.assertEqual(bereken_verdeling_over_tijd(_waarde_df({"A": [0.0] * 3}), {}, {}), leeg)


class TestGewichtenPerValuta(unittest.TestCase):
    def test_cache_hit_zonder_yahoo_en_normalisatie(self):
        cache = {"A": {"valuta": "GBp", "quote_type": "EQUITY"}, "B": {"valuta": None, "quote_type": "ETF"},
                 "C": {"valuta": "USD", "quote_type": None}}
        with patch.object(ticker_classificatie, "db_get_ticker_details", return_value=cache), \
             patch.object(ticker_classificatie, "get_valuta") as mock_valuta:
            res = gewichten_per_valuta(["A", "B", "C"])
        mock_valuta.assert_not_called()
        self.assertEqual(res, {"A": {"GBP": 1.0}, "B": {"Unknown": 1.0}, "C": {"USD": 1.0}})

    def test_ontbrekende_ticker_via_get_valuta(self):
        with patch.object(ticker_classificatie, "db_get_ticker_details",
                          return_value={"A": {"valuta": "EUR", "quote_type": "ETF"}}), \
             patch.object(ticker_classificatie, "get_valuta", return_value="USD") as mock_valuta:
            res = gewichten_per_valuta(["A", "B"])
        mock_valuta.assert_called_once_with("B")
        self.assertEqual(res, {"A": {"EUR": 1.0}, "B": {"USD": 1.0}})

    def test_normaliseer_valuta(self):
        self.assertEqual(ticker_classificatie.normaliseer_valuta("GBp"), "GBP")
        self.assertEqual(ticker_classificatie.normaliseer_valuta(""), "Unknown")
        self.assertEqual(ticker_classificatie.normaliseer_valuta(None), "Unknown")
        self.assertEqual(ticker_classificatie.normaliseer_valuta("USD"), "USD")


class TestGewichtenPerBeurs(unittest.TestCase):
    def test_eam_en_xams_zijn_een_naam(self):
        df = pd.DataFrame([_rij("2024-01-02", "A", 5.0, -50.0, "EAM"), _rij("2024-01-03", "A", 5.0, -50.0, "XAMS")])
        self.assertEqual(gewichten_per_beurs(df), {"A": {"Euronext Amsterdam": 1.0}})

    def test_euronext_samenvoegen_tradegate_apart(self):
        df = pd.DataFrame([_rij("2024-01-02", "A", 5.0, -50.0, "EAM"), _rij("2024-01-02", "B", 5.0, -50.0, "EPA"),
                           _rij("2024-01-02", "C", 5.0, -50.0, "TDG")])
        self.assertEqual(gewichten_per_beurs(df, euronext_samenvoegen=True),
                         {"A": {"Euronext": 1.0}, "B": {"Euronext": 1.0}, "C": {"Tradegate": 1.0}})
        self.assertEqual(gewichten_per_beurs(df)["B"], {"Euronext Parijs": 1.0})

    def test_twee_beursnamen_naar_gekochte_stuks_ook_als_gesloten(self):
        # 6 op Xetra en 2 op Tradegate gekocht, alles verkocht op Xetra: som van de aantallen 0, gekocht 6 : 2.
        df = pd.DataFrame([_rij("2024-01-02", "A", 6.0, -60.0, "XET"), _rij("2024-01-03", "A", 2.0, -20.0, "TDG"),
                           _rij("2024-01-04", "A", -8.0, 80.0, "XET")])
        self.assertEqual(gewichten_per_beurs(df), {"A": {"Xetra": 0.75, "Tradegate": 0.25}})

    def test_splitboeking_telt_niet_als_aankoop(self):
        df = pd.DataFrame([_rij("2024-01-02", "A", 1.0, -10.0, "XET"),
                           _rij("2024-01-05", "A", 3.0, 0.0, "DEG", product="A NON TRADEABLE")])
        self.assertEqual(gewichten_per_beurs(df), {"A": {"Xetra": 1.0}})

    def test_lege_beurs_onbekend(self):
        df = pd.DataFrame([_rij("2024-01-02", "A", 1.0, -10.0, None)])
        self.assertEqual(gewichten_per_beurs(df), {"A": {"Onbekend": 1.0}})


class TestWaakhondPerDimensie(unittest.TestCase):
    """Elke dimensie verdeelt dezelfde euro's: som van de reeksen = totaal = totaal van 'positie'."""

    def _df_en_koersen(self):
        df = pd.DataFrame([
            _rij("2024-01-02", "A", 10.0, -100.0, "EAM"),
            _rij("2024-01-03", "B", 2.0, -40.0, "XET"),
            _rij("2024-01-03", "B", 1.0, -20.0, "TDG"),
            _rij("2024-01-04", "C", 4.0, -40.0, "EPA"),
            _rij("2024-01-10", "A", -5.0, 55.0, "EAM"),
        ])
        dagen = pd.date_range("2024-01-01", "2024-01-20", freq="D")
        koersen = pd.DataFrame({"A": np.linspace(10, 13, 20), "B": np.linspace(20, 18, 20),
                                "C": np.linspace(10, 11, 20)}, index=dagen)
        return df, koersen

    def _check(self, res, referentie):
        for i in range(len(res["labels"])):
            som = sum(r["waarde"][i] for r in res["reeksen"])
            self.assertAlmostEqual(som, res["totaal"][i], delta=0.05)
        self.assertEqual(res["totaal"], referentie["totaal"])
        self.assertEqual(res["labels"], referentie["labels"])

    def test_valuta_en_beurs(self):
        df, koersen = self._df_en_koersen()
        waarde = waarde_per_ticker_per_dag(df, koersen)
        positie = bereken_verdeling_over_tijd(waarde, gewichten_per_positie(waarde.columns), {})
        with patch.object(portfolio_verdeling, "get_valutas", return_value={"A": "EUR", "B": "USD", "C": "EUR"}):
            valuta = bereken_verdeling_over_tijd(waarde, gewichten_per_valuta(waarde.columns), {})
        self._check(valuta, positie)
        for samenvoegen in (False, True):
            self._check(bereken_verdeling_over_tijd(waarde, gewichten_per_beurs(df, samenvoegen), {}), positie)

    def test_laatste_meetpunt_gelijk_aan_huidige_verdeling(self):
        df, koersen = self._df_en_koersen()
        waarde = waarde_per_ticker_per_dag(df, koersen)
        valutas = {"A": "EUR", "B": "USD", "C": "EUR"}

        def laatste(res):
            return {r["sleutel"]: r["waarde"][-1] for r in res["reeksen"] if r["waarde"][-1]}

        with patch.object(portfolio_verdeling, "get_valutas", side_effect=lambda t: {x: valutas[x] for x in t}):
            over_tijd = laatste(bereken_verdeling_over_tijd(waarde, gewichten_per_valuta(waarde.columns), {}))
            nu = compute_valuta_verdeling(df, koersen)["valuta"]
        self.assertEqual(over_tijd, {k: round(v, 2) for k, v in nu.items()})

        nu_beurs = compute_beurs_verdeling(df, koersen)
        for samenvoegen, sleutel in ((False, "beurs"), (True, "beurs_euronext")):
            over_tijd = laatste(bereken_verdeling_over_tijd(waarde, gewichten_per_beurs(df, samenvoegen), {}))
            self.assertEqual(over_tijd, {k: round(v, 2) for k, v in nu_beurs[sleutel].items()})


class TestVerdelingOverTijdRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        import portfolio_orchestratie
        self.orchestratie = portfolio_orchestratie
        self.client = app_module.app.test_client()

    def _basis(self):
        df, price_data = _voorbeeld()
        return patch.object(self.orchestratie, "haal_portfolio_basis", return_value=("Test", df, price_data))

    def test_onbekende_dimensie_400(self):
        with self._basis():
            for url in ("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=bedrijven",
                        "/api/portfolio/ZZTEST/verdeling-over-tijd"):
                self.assertEqual(self.client.get(url).status_code, 400)

    def test_valuta_en_beurs_200(self):
        with self._basis(), patch.object(portfolio_verdeling, "get_valutas",
                                         side_effect=lambda t: {x: "EUR" for x in t}):
            valuta = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=valuta")
            beurs = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=beurs&samenvoegen=1")
        self.assertEqual(valuta.status_code, 200)
        self.assertEqual([r["sleutel"] for r in valuta.get_json()["reeksen"]], ["EUR"])
        self.assertEqual(beurs.status_code, 200)
        self.assertEqual([r["sleutel"] for r in beurs.get_json()["reeksen"]], ["Euronext"])

    def test_onbekende_code_404(self):
        with patch.object(self.orchestratie, "haal_portfolio_basis", return_value=(None, None, None)):
            res = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=positie")
        self.assertEqual(res.status_code, 404)

    def test_positie(self):
        with self._basis():
            res = self.client.get("/api/portfolio/zztest/verdeling-over-tijd?dimensie=positie")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2024-01-07", "2024-01-08"])
        self.assertEqual([r["sleutel"] for r in data["reeksen"]], ["A", "B"])

    def test_lege_koersdata(self):
        with patch.object(self.orchestratie, "haal_portfolio_basis",
                          return_value=("Test", _voorbeeld()[0], pd.DataFrame())):
            res = self.client.get("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=positie")
        self.assertEqual(res.get_json(), {"labels": [], "totaal": [], "reeksen": []})


if __name__ == "__main__":
    unittest.main()
