"""
Golden-master-regressietests voor de performance-fix van
compute_value_over_time() / compute_per_ticker() /
compute_per_ticker_koers_en_aankopen() (analysis.py): de trage
price_data.loc[date, ticker]-scalar-lookups in de dag-loops zijn vervangen
door dict/numpy-array-toegang die vooraf wordt opgebouwd. De rekenlogica
zelf (GAK, crop-range, spike-detector) is bewust ONGEWIJZIGD -- deze tests
bewaken dat de output vóór en ná die wijziging exact gelijk blijft.

De verwachte waarden hieronder zijn 1-op-1 overgenomen uit de output van
de (nog ongewijzigde) functies op de fixture in bouw_fixture(): meerdere
tickers, een gedeeltelijke verkoop, een same-day koop+verkoop (activiteit-
crop-edge-case) en een prijsgat (NaN) voor 1 ticker.

Draait geheel offline: geen database, geen yfinance-calls.
"""
import sys
import os
import random
import time
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import (
    compute_value_over_time,
    compute_per_ticker,
    compute_per_ticker_koers_en_aankopen,
)


def _rij(ticker, datum, tijd, aantal, waarde_eur, totaal_eur, beurs="EAM"):
    return {
        "ticker": ticker, "datum": pd.Timestamp(datum), "tijd": tijd,
        "aantal": aantal, "adj_aantal": aantal, "koers": 0.0,
        "waarde_eur": waarde_eur, "totaal_eur": totaal_eur,
        "beurs": beurs, "product": ticker,
    }


def bouw_fixture():
    """Ticker A: 2 aankopen, 1 gedeeltelijke verkoop, 1 aankoop.
    Ticker B: same-day aankoop + gedeeltelijke verkoop (activiteit-crop-
    edge-case), later een volledige verkoop (nog_in_bezit=False), met een
    prijsgat (NaN) op 2023-01-03."""
    rijen = [
        _rij("A", "2023-01-01", "10:00", 10.0, -100.0, -101.0),
        _rij("A", "2023-01-03", "10:00", 5.0, -55.0, -56.0),
        _rij("A", "2023-01-05", "10:00", -8.0, None, 90.0),
        _rij("A", "2023-01-08", "10:00", 3.0, -33.0, -34.0),
        _rij("B", "2023-01-02", "10:00", 10.0, -50.0, -51.0),
        _rij("B", "2023-01-02", "10:05", -4.0, None, 22.0),
        _rij("B", "2023-01-06", "10:00", -6.0, None, 36.0),
    ]
    df = pd.DataFrame(rijen)

    datums = pd.date_range("2023-01-01", "2023-01-10", freq="D")
    price_data = pd.DataFrame({
        "A": [10.0, 10.5, 11.0, 11.2, 11.5, 11.8, 12.0, 12.2, 12.5, 12.8],
        "B": [20.0, 20.5, float("nan"), 21.0, 21.5, 22.0, 22.5, 23.0, 23.5, 24.0],
    }, index=datums)
    return df, price_data


def bouw_grote_fixture(n_tickers=15, n_dagen=750, seed=42):
    """Grotere, willekeurig gegenereerde fixture voor de performance-test
    (niet voor correctheid -- geen golden-master-assert op deze fixture)."""
    rng = random.Random(seed)
    datums = pd.date_range("2021-01-01", periods=n_dagen, freq="D")

    rijen = []
    prices = {}
    for i in range(n_tickers):
        ticker = f"T{i}"
        prijs_reeks = []
        prijs = rng.uniform(10, 200)
        for d in datums:
            prijs *= 1 + rng.uniform(-0.02, 0.02)
            prijs_reeks.append(round(prijs, 4))
        prices[ticker] = prijs_reeks

        # Een handvol aan- en verkopen verspreid over de periode.
        aantal_lopend = 0.0
        for _ in range(6):
            idx = rng.randint(0, n_dagen - 1)
            if aantal_lopend > 0 and rng.random() < 0.4:
                aantal = -round(rng.uniform(1, aantal_lopend), 2)
            else:
                aantal = round(rng.uniform(1, 20), 2)
            aantal_lopend += aantal
            koers = prices[ticker][idx]
            waarde_eur = -aantal * koers if aantal > 0 else None
            totaal_eur = -aantal * koers
            rijen.append(_rij(ticker, datums[idx], "10:00", aantal, waarde_eur, totaal_eur, beurs="EAM"))

    df = pd.DataFrame(rijen)
    price_data = pd.DataFrame(prices, index=datums)
    return df, price_data


WAARDE_OVER_TIME = {
    "datum": [
        "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05",
        "2023-01-06", "2023-01-07", "2023-01-08", "2023-01-09", "2023-01-10",
    ],
    "waarde": [100.0, 228.0, 165.0, 294.0, 209.5, 82.6, 84.0, 122.0, 125.0, 128.0],
    "geinvesteerd": [101.0, 130.0, 186.0, 186.0, 96.0, 60.0, 60.0, 94.0, 94.0, 94.0],
    "rendement": [-1.0, 98.0, -21.0, 108.0, 113.5, 22.6, 24.0, 28.0, 31.0, 34.0],
}

PER_TICKER = {
    "A": {
        "labels": [
            "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05",
            "2023-01-06", "2023-01-07", "2023-01-08", "2023-01-09", "2023-01-10",
        ],
        "waarde": [100.0, 105.0, 165.0, 168.0, 80.5, 82.6, 84.0, 122.0, 125.0, 128.0],
        "geinvesteerd": [100.0, 100.0, 155.0, 155.0, 72.33, 72.33, 72.33, 105.33, 105.33, 105.33],
        "nog_in_bezit": True,
    },
    "B": {
        "labels": [
            "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04",
            "2023-01-05", "2023-01-06", "2023-01-07",
        ],
        "waarde": [0.0, 123.0, 0.0, 126.0, 129.0, 0.0, 0.0],
        "geinvesteerd": [0.0, 30.0, 30.0, 30.0, 30.0, 0.0, 0.0],
        "nog_in_bezit": False,
    },
}

PER_TICKER_KOERS_EN_AANKOPEN = {
    "A": {
        "labels": [
            "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05",
            "2023-01-06", "2023-01-07", "2023-01-08", "2023-01-09", "2023-01-10",
        ],
        "koers": [10.0, 10.5, 11.0, 11.2, 11.5, 11.8, 12.0, 12.2, 12.5, 12.8],
        "holdings": [10.0, 10.0, 15.0, 15.0, 7.0, 7.0, 7.0, 10.0, 10.0, 10.0],
        "aankoop_datums": ["2023-01-01", "2023-01-03", "2023-01-08"],
        "verkoop_datums": ["2023-01-05"],
    },
    "B": {
        "labels": [
            "2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04",
            "2023-01-05", "2023-01-06", "2023-01-07",
        ],
        "koers": [20.0, 20.5, None, 21.0, 21.5, 22.0, 22.5],
        "holdings": [0.0, 6.0, 6.0, 6.0, 6.0, 0.0, 0.0],
        "aankoop_datums": ["2023-01-02"],
        "verkoop_datums": ["2023-01-02", "2023-01-06"],
    },
}


class TestGoldenMasterValueOverTime(unittest.TestCase):
    def test_output_matcht_baseline(self):
        df, price_data = bouw_fixture()
        result = compute_value_over_time(df.copy(), price_data)
        self.assertEqual([d.strftime("%Y-%m-%d") for d in result.index], WAARDE_OVER_TIME["datum"])
        for kolom in ("waarde", "geinvesteerd", "rendement"):
            for verwacht, echt in zip(WAARDE_OVER_TIME[kolom], result[kolom].tolist()):
                self.assertAlmostEqual(verwacht, echt, places=6)


class TestGoldenMasterPerTicker(unittest.TestCase):
    def test_output_matcht_baseline(self):
        df, price_data = bouw_fixture()
        result = compute_per_ticker(df.copy(), price_data)
        self.assertEqual(set(result.keys()), set(PER_TICKER.keys()))
        for ticker, verwacht in PER_TICKER.items():
            echt = result[ticker]
            self.assertEqual(echt["labels"], verwacht["labels"])
            self.assertEqual(echt["nog_in_bezit"], verwacht["nog_in_bezit"])
            for kolom in ("waarde", "geinvesteerd"):
                for v, e in zip(verwacht[kolom], echt[kolom]):
                    self.assertAlmostEqual(v, e, places=6)


class TestGoldenMasterPerTickerKoersEnAankopen(unittest.TestCase):
    def test_output_matcht_baseline(self):
        df, price_data = bouw_fixture()
        result = compute_per_ticker_koers_en_aankopen(df.copy(), price_data)
        self.assertEqual(set(result.keys()), set(PER_TICKER_KOERS_EN_AANKOPEN.keys()))
        for ticker, verwacht in PER_TICKER_KOERS_EN_AANKOPEN.items():
            echt = result[ticker]
            self.assertEqual(echt["labels"], verwacht["labels"])
            self.assertEqual(echt["aankoop_datums"], verwacht["aankoop_datums"])
            self.assertEqual(echt["verkoop_datums"], verwacht["verkoop_datums"])
            self.assertEqual(echt["holdings"], verwacht["holdings"])
            for v, e in zip(verwacht["koers"], echt["koers"]):
                if v is None:
                    self.assertIsNone(e)
                else:
                    self.assertAlmostEqual(v, e, places=6)


class TestPerformanceIndicatie(unittest.TestCase):
    """Geen harde assert op absolute looptijd (omgevingsafhankelijk) --
    puur ter observatie in de testoutput."""

    def test_looptijd_grote_fixture(self):
        df, price_data = bouw_grote_fixture()

        start = time.perf_counter()
        compute_value_over_time(df.copy(), price_data)
        t_waarde = time.perf_counter() - start

        start = time.perf_counter()
        compute_per_ticker(df.copy(), price_data)
        t_per_ticker = time.perf_counter() - start

        start = time.perf_counter()
        compute_per_ticker_koers_en_aankopen(df.copy(), price_data)
        t_koers_aankopen = time.perf_counter() - start

        print(
            f"\n[performance] 15 tickers x 750 dagen -- "
            f"compute_value_over_time: {t_waarde:.3f}s, "
            f"compute_per_ticker: {t_per_ticker:.3f}s, "
            f"compute_per_ticker_koers_en_aankopen: {t_koers_aankopen:.3f}s"
        )


if __name__ == "__main__":
    unittest.main()
