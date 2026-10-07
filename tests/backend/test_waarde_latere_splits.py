"""Waarde na splits, van Yahoo-download tot waarde-reeks (zonder database en zonder netwerk).

Yahoo's Close (auto_adjust=False) is achteraf voor alle latere splits gecorrigeerd. get_prices() rekent dat terug naar de
koers van die dag (ruw) en slaat koersen en splits samen op; het aantal stuks komt uit de ruwe transacties.
Gemockt: de Yahoo-download, de koersencache-functies in prijzen.py, de splitlezer in portfolio_orchestratie.py en de
datakwaliteitscheck.
"""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie
import prijzen
from portfolio_calc import compute_value_over_time

TICKER = "TST"
ISIN = "XX0000000001"
D = datetime.date
# Volgorde van TRANSACTIE_KOLOMMEN in db.py.
KOLOMMEN = ("datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur",
            "echte_naam", "transactiekosten", "waarde_eur", "tijd", "wisselkoers", "autofx_kosten")


def _rij(datum, aantal, koers, totaal, beurs="NDQ", ticker=TICKER, isin=ISIN):
    return (datum, "Test", isin, beurs, ticker, float(aantal), float(koers), float(totaal), "Test", 0.0, float(totaal), None, None, None)


class _Omgeving:
    """Context met alle mocks; `opgeslagen_koersen` en `opgeslagen_splits` laten zien wat naar de cache zou gaan."""

    def __init__(self, transacties, yahoo_close, yahoo_splits, dagen):
        self.transacties, self.yahoo_close, self.yahoo_splits, self.dagen = transacties, yahoo_close, yahoo_splits, dagen
        self.opgeslagen_koersen, self.opgeslagen_splits = [], {}

    def _download(self, ticker_of_pair, vanaf, **_):
        tickers = [ticker_of_pair] if isinstance(ticker_of_pair, str) else list(ticker_of_pair)
        close = pd.DataFrame({t: self.yahoo_close[t] for t in tickers if t in self.yahoo_close}, index=self.dagen)
        return close, {t: self.yahoo_splits[t] for t in tickers if t in self.yahoo_splits}

    def _bewaar(self, rijen, splits):
        self.opgeslagen_koersen.extend(rijen)
        self.opgeslagen_splits.update(splits)

    def _lees_splits(self, tickers):
        return {t: self.opgeslagen_splits[t] for t in tickers if t in self.opgeslagen_splits}

    def __enter__(self):
        self.patches = [
            patch.object(portfolio_orchestratie, "db_get_portfolio_naam_en_transacties", return_value=("Test", self.transacties)),
            patch.object(portfolio_orchestratie, "_meld_datakwaliteit"),
            patch.object(portfolio_orchestratie, "db_get_koers_splits", side_effect=self._lees_splits),
            patch.object(prijzen, "db_get_gecachte_koersen", return_value=({}, {}, [])),
            patch.object(prijzen, "db_save_koersen", side_effect=self._bewaar),
            patch.object(prijzen, "download_koersen_met_retry", side_effect=self._download),
            patch.object(prijzen, "_valuta_per_ticker", side_effect=lambda tickers: {t: "EUR" for t in tickers}),
        ]
        for p in self.patches:
            p.start()
        self.uitvoer = redirect_stdout(io.StringIO())
        self.uitvoer.__enter__()
        self.app_context = Flask(__name__).test_request_context()
        self.app_context.__enter__()
        return self

    def __exit__(self, *args):
        self.app_context.__exit__(*args)
        self.uitvoer.__exit__(*args)
        for p in self.patches:
            p.stop()

    def waarde(self):
        _naam, transacties_df, prijs_data = portfolio_orchestratie.haal_portfolio_basis("ZZTESTSPLIT", forceer_vers=True)
        portfolio_orchestratie._basis_cache.pop("ZZTESTSPLIT", None)
        return compute_value_over_time(transacties_df, prijs_data), transacties_df, prijs_data


class TestGesloten(unittest.TestCase):
    def test_reverse_split_na_verkoop_tegen_echte_koers(self):
        # 1:20 in juni 2021 na de verkoop. Echte koers 1 euro; Yahoo toont (x20) 20 voor alle dagen.
        dagen = pd.bdate_range("2021-01-04", "2021-06-30")
        transacties = [_rij(D(2021, 1, 4), 10, 1.0, -10.0), _rij(D(2021, 1, 8), -10, 1.0, 10.0)]
        with _Omgeving(transacties, {TICKER: pd.Series(20.0, index=dagen)}, {TICKER: {"2021-06-01": 0.05}}, dagen) as omg:
            resultaat, _, prijs_data = omg.waarde()
        self.assertAlmostEqual(prijs_data.loc["2021-01-06", TICKER], 1.0)
        self.assertAlmostEqual(prijs_data.loc["2021-06-02", TICKER], 20.0)  # na de split noteert hij echt 20
        self.assertAlmostEqual(resultaat.loc["2021-01-06", "waarde"], 10.0)
        self.assertAlmostEqual(resultaat.loc["2021-01-11", "waarde"], 0.0)
        self.assertEqual(omg.opgeslagen_splits, {TICKER: {"2021-06-01": 0.05}})
        self.assertAlmostEqual(dict(((t, d), k) for t, d, k in omg.opgeslagen_koersen)[(TICKER, D(2021, 1, 6))], 1.0)

    def test_forward_split_na_verkoop_geen_te_lage_waarde(self):
        # GME-achtig: 4:1 in juli 2022 na de verkoop. Echte koers 100; Yahoo toont (/4) 25.
        dagen = pd.bdate_range("2022-01-03", "2022-08-31")
        transacties = [_rij(D(2022, 1, 3), 10, 100.0, -1000.0), _rij(D(2022, 1, 14), -10, 100.0, 1000.0)]
        with _Omgeving(transacties, {TICKER: pd.Series(25.0, index=dagen)}, {TICKER: {"2022-07-22": 4.0}}, dagen) as omg:
            resultaat, _, _ = omg.waarde()
        self.assertAlmostEqual(resultaat.loc["2022-01-10", "waarde"], 1000.0)

    def test_reverse_split_tilray_achtig_niet_te_hoog(self):
        # 1:10 (ratio 0,1) na de verkoop. Echte koers 5; Yahoo toont (x10) 50.
        dagen = pd.bdate_range("2022-01-03", "2023-12-29")
        transacties = [_rij(D(2022, 1, 3), 100, 5.0, -500.0), _rij(D(2022, 1, 14), -100, 5.0, 500.0)]
        with _Omgeving(transacties, {TICKER: pd.Series(50.0, index=dagen)}, {TICKER: {"2023-12-01": 0.1}}, dagen) as omg:
            resultaat, _, _ = omg.waarde()
        self.assertAlmostEqual(resultaat.loc["2022-01-10", "waarde"], 500.0)


class TestDoorDegiroGeboekteSplit(unittest.TestCase):
    """Echte BYD-vorm (tests/test_files/Transactions_test.xlsx): DEG +14 en +9 (nieuwe, nog niet verhandelbare stukken)
    op 10-06-2025, de splitdatum bij Yahoo; pas op 31-07 DEG -23 en een conversierij +23 op de gewone beurs.
    Open positie van 14 stuks."""

    RATIO = 37 / 14

    def _transacties(self):
        return [
            _rij(D(2025, 5, 2), 14, 10.0, -140.0, beurs="TDG"),
            _rij(D(2025, 6, 10), 14, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 6, 10), 9, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 7, 31), -23, 0.0, 0.0, beurs="DEG", ticker=None),
            _rij(D(2025, 7, 31), 23, 0.0, 0.0, beurs="TDG"),
        ]

    def _waarde(self):
        dagen = pd.bdate_range("2025-05-02", "2025-08-29")
        # Echte koers 10 vóór de splitdag, daarna 10 / RATIO (waardeneutrale split); Yahoo toont overal 10 / RATIO.
        close = pd.Series(10 / self.RATIO, index=dagen)
        with _Omgeving(self._transacties(), {TICKER: close}, {TICKER: {"2025-06-10": self.RATIO}}, dagen) as omg:
            return omg.waarde()

    def test_voor_de_conversie_gelijk_aan_de_oude_methode(self):
        resultaat, transacties_df, prijs_data = self._waarde()
        oud_aantal = transacties_df.dropna(subset=["ticker"]).set_index(pd.to_datetime(transacties_df.dropna(subset=["ticker"])["datum"]))["adj_aantal"]
        for dag in ("2025-05-05", "2025-06-09"):
            # Oud: adj_aantal (14 x 37/14 = 37) x Yahoo's gecorrigeerde koers (10 / RATIO).
            oud = oud_aantal[oud_aantal.index <= pd.Timestamp(dag)].sum() * (10 / self.RATIO)
            self.assertAlmostEqual(resultaat.loc[dag, "waarde"], oud, places=3, msg=dag)
            self.assertAlmostEqual(resultaat.loc[dag, "waarde"], 140.0, places=3, msg=dag)

    def test_na_de_conversie_klopt_het_aantal_met_statistieken(self):
        resultaat, transacties_df, prijs_data = self._waarde()
        echte = transacties_df.dropna(subset=["ticker"])
        werkelijk_aantal = echte["aantal"].sum()  # zo telt Statistieken de huidige positie
        self.assertEqual(werkelijk_aantal, 37.0)
        koers = prijs_data.loc["2025-08-04", TICKER]
        self.assertAlmostEqual(resultaat.loc["2025-08-04", "waarde"], werkelijk_aantal * koers, places=3)
        # De waarde blijft over de split heen gelijk: een bonusuitgifte is waardeneutraal.
        self.assertAlmostEqual(resultaat.loc["2025-08-04", "waarde"], 140.0, places=3)

    def test_tussen_bijschrijving_en_conversie_geen_dip(self):
        # De conversierij van 31-07 telt mee vanaf Yahoo's splitdatum 10-06: anders 14 stuks x de post-split koers.
        resultaat, _, _ = self._waarde()
        for dag in ("2025-06-10", "2025-07-01", "2025-07-30", "2025-07-31"):
            self.assertAlmostEqual(resultaat.loc[dag, "waarde"], 140.0, places=3, msg=dag)

    def test_oude_methode_telde_na_de_conversie_te_veel_stukken(self):
        # Vastgelegd verschil met vóór de splitfix: adj_aantal (37) + conversierij (23) = 60 stuks, echt zijn het er 37.
        _, transacties_df, _ = self._waarde()
        self.assertEqual(transacties_df.dropna(subset=["ticker"])["adj_aantal"].sum(), 60.0)


class TestOnvolledigeOpbouw(unittest.TestCase):
    def _get_prices(self, tickers):
        dagen = pd.bdate_range("2024-01-02", "2024-01-31")
        close = {t: pd.Series(10.0, index=dagen) for t in tickers}
        omg = _Omgeving([], close, {t: {} for t in tickers}, dagen)
        with omg:
            return prijzen.get_prices(tickers, "2024-01-02"), omg

    def test_geen_splitlijst_dan_niets_opslaan(self):
        dagen = pd.bdate_range("2024-01-02", "2024-01-31")
        omg = _Omgeving([], {TICKER: pd.Series(10.0, index=dagen)}, {}, dagen)  # Close wel, splits niet
        with omg:
            resultaat = prijzen.get_prices([TICKER], "2024-01-02")
        self.assertEqual(omg.opgeslagen_koersen, [])
        self.assertTrue(resultaat.empty)

    def test_tijdbudget_op_laat_de_rest_voor_de_volgende_opening(self):
        tickers = ["AA", "BB", "CC"]
        dagen = pd.bdate_range("2024-01-02", "2024-01-31")
        omg = _Omgeving([], {t: pd.Series(10.0, index=dagen) for t in tickers}, {t: {} for t in tickers}, dagen)
        # begin=0; groepje 1 mag (0 s), daarna is het budget op (100 s).
        klok = iter([0.0, 0.0, 100.0, 100.0, 100.0])
        with omg, patch.multiple(prijzen, KOERS_DOWNLOAD_GROEPJE=1, KOERS_TIJDBUDGET_SECONDEN=20), \
                patch.object(prijzen.time, "monotonic", side_effect=lambda: next(klok)):
            resultaat = prijzen.get_prices(tickers, "2024-01-02")
        self.assertEqual(list(resultaat.columns), ["AA"])
        self.assertEqual(resultaat.attrs["koersen_onvolledig"], ["BB", "CC"])

    def test_compleet_geeft_lege_lijst(self):
        resultaat, _ = self._get_prices(["AA", "BB"])
        self.assertEqual(resultaat.attrs["koersen_onvolledig"], [])


if __name__ == "__main__":
    unittest.main()
