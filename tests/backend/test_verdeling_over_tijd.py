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
from portfolio_verdeling import (
    VERDELING_OVERIG_SLEUTEL, bereken_verdeling_over_tijd, gewichten_per_positie,
)


def _rij(datum, ticker, aantal, totaal_eur):
    return {
        "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal, "adj_aantal": aantal,
        "koers": 0.0, "totaal_eur": totaal_eur, "waarde_eur": totaal_eur, "beurs": "EAM", "product": ticker,
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
            for url in ("/api/portfolio/ZZTEST/verdeling-over-tijd?dimensie=land",
                        "/api/portfolio/ZZTEST/verdeling-over-tijd"):
                self.assertEqual(self.client.get(url).status_code, 400)

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
