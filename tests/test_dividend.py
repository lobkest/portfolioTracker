"""
Unit tests voor de dividend-verwerking (analysis.verwerk_rekeningoverzicht_df).

Draait geheel offline: geen Excel-bestand, geen database — de tests bouwen
een kleine, handgemaakte DataFrame die exact de kolomvorm nabootst die
verwerk_rekeningoverzicht() na het inlezen/hernoemen van een echt DEGIRO-
rekeningoverzicht doorgeeft (zie analysis.py: Mutatie -> valuta_mutatie,
Unnamed: 8 -> mutatie).
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import verwerk_rekeningoverzicht_df


def _rij(datum, tijd, omschrijving, mutatie, valuta_mutatie, product=None, isin=None):
    return {
        "Datum": pd.Timestamp(datum),
        "Tijd": tijd,
        "Product": product,
        "ISIN": isin,
        "Omschrijving": omschrijving,
        "mutatie": mutatie,
        "valuta_mutatie": valuta_mutatie,
    }


def _df(rijen):
    return pd.DataFrame(rijen)


class TestDividendEurNative(unittest.TestCase):
    def test_simpele_eur_dividendrij(self):
        # Europees aandeel: dividend komt meteen in EUR binnen, geen
        # valutaconversie nodig.
        df = _df([
            _rij("2024-01-15", "10:00", "Dividend", 5.0, "EUR", product="SHELL", isin="NL0000009355"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["netto_eur"], 5.0)
        self.assertAlmostEqual(records[0]["bruto_eur"], 5.0)


class TestDividendValutaconversie(unittest.TestCase):
    def test_usd_dividend_gebruikt_gekoppelde_valuta_creditering(self):
        # $10 dividend, later omgewisseld naar EUR. De gekoppelde Valuta
        # Creditering-rij zegt €9,15 — dat moet het resultaat zijn, NIET
        # een eigen FX-herberekening (die met een andere/afgeronde koers
        # een ander getal zou kunnen geven).
        df = _df([
            _rij("2024-02-10", "08:00", "Dividend", 10.0, "USD", product="APPLE INC", isin="US0378331005"),
            _rij("2024-02-11", "07:00", "Valuta Debitering", -10.0, "USD"),
            _rij("2024-02-11", "07:00", "Valuta Creditering", 9.15, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["netto_eur"], 9.15)

    def test_valutadatum_van_dividendrij_hoeft_niet_te_matchen(self):
        # Regressietest voor de oorspronkelijke bug: de conversie mag een
        # andere (latere) Datum hebben dan de dividendrij zelf — koppeling
        # gaat op valuta+bedrag, niet op datum.
        df = _df([
            _rij("2025-04-03", "08:58", "Dividend", 0.96, "USD", product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-04-04", "07:36", "Valuta Debitering", -0.96, "USD"),
            _rij("2025-04-04", "07:36", "Valuta Creditering", 0.87, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["netto_eur"], 0.87)


class TestDividendCorrectie(unittest.TestCase):
    def test_correctierijen_worden_genet_niet_apart_geteld(self):
        # Precies het patroon uit de opdracht: +0.96 / +0.96 / -1.92 / +0.96
        # USD op dezelfde datum/product -> netto 0.96 USD, GEEN 4 losse
        # uitkeringen. Gekoppeld aan een conversie die €0,87 opleverde.
        df = _df([
            _rij("2025-04-03", "07:32", "Dividend", 0.96, "USD", product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-04-03", "07:34", "Dividend", 0.96, "USD", product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-04-03", "08:49", "Dividend", -1.92, "USD", product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-04-03", "08:58", "Dividend", 0.96, "USD", product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-04-04", "07:36", "Valuta Debitering", -0.96, "USD"),
            _rij("2025-04-04", "07:36", "Valuta Creditering", 0.87, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        # ÉÉN dividendgroep, niet vier
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["netto_eur"], 0.87)


class TestDividendGeenData(unittest.TestCase):
    def test_geen_dividendrijen_geeft_lege_lijst_zonder_crash(self):
        df = _df([
            _rij("2024-01-01", "09:00", "Verkoop 3 @ 100 EUR", -300.0, "EUR", product="IETS ANDERS"),
            _rij("2024-01-02", "09:00", "iDEAL Deposit", 500.0, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(records, [])

    def test_leeg_rekeningoverzicht_geeft_lege_lijst_zonder_crash(self):
        df = _df([])
        # Zonder rijen mist een aantal kolommen (Omschrijving, ISIN, ...) —
        # de functie moet dit toch netjes afhandelen, niet crashen.
        for kolom in ["Datum", "Tijd", "Product", "ISIN", "Omschrijving", "mutatie", "valuta_mutatie"]:
            if kolom not in df.columns:
                df[kolom] = pd.Series(dtype=object)
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
