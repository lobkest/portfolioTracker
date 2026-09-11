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

from analysis import verwerk_rekeningoverzicht_df, _koppel_valutaconversie_paren


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


class TestDividendGepooldeConversie(unittest.TestCase):
    def test_twee_dividenden_zelfde_dag_gepoold_in_een_conversie(self):
        # Precies het Vanguard-voorbeeld uit de opdracht (2025-10-02/03):
        # twee ETF-dividenden dezelfde dag/valuta, DeGiro wisselt ze samen
        # in ÉÉN conversie ($18.37 + $4.23 = $22.60 -> €19.24). De 1-op-1
        # match (per ISIN) vindt niets; STAP A (pooling) moet dit alsnog
        # koppelen en het EUR-bedrag proportioneel verdelen.
        df = _df([
            _rij("2025-10-02", "08:00", "Dividend", 18.37, "USD",
                 product="VANGUARD S&P 500", isin="IE00B3XXRP09"),
            _rij("2025-10-02", "08:05", "Dividend", 4.23, "USD",
                 product="VANGUARD FTSE ALL-WORLD", isin="IE00BK5BQT80"),
            _rij("2025-10-03", "07:30", "Valuta Debitering", -22.60, "USD"),
            _rij("2025-10-03", "07:30", "Valuta Creditering", 19.24, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertIsNotNone(r["netto_eur"])
        totaal = sum(r["netto_eur"] for r in records)
        self.assertAlmostEqual(totaal, 19.24, places=6)
        # Proportioneel: 18.37/22.60 van €19.24, resp. 4.23/22.60 van €19.24
        per_isin = {r["isin"]: r["netto_eur"] for r in records}
        self.assertAlmostEqual(per_isin["IE00B3XXRP09"], 19.24 * (18.37 / 22.60), places=6)
        self.assertAlmostEqual(per_isin["IE00BK5BQT80"], 19.24 * (4.23 / 22.60), places=6)

    def test_dividenden_te_ver_uit_elkaar_worden_niet_gepoold(self):
        # Twee ongematchte USD-dividenden meer dan DIVIDEND_POOL_MAX_DAGEN_
        # VERSCHIL dagen uit elkaar mogen NIET samengevoegd worden, ook al
        # zou hun som toevallig een conversiepaar matchen.
        df = _df([
            _rij("2025-01-01", "08:00", "Dividend", 5.00, "USD",
                 product="A", isin="US0000000001"),
            _rij("2025-01-20", "08:00", "Dividend", 3.00, "USD",
                 product="B", isin="US0000000002"),
            _rij("2025-01-21", "07:00", "Valuta Debitering", -8.00, "USD"),
            _rij("2025-01-21", "07:00", "Valuta Creditering", 7.30, "EUR"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertIsNone(r["netto_eur"])


class TestDividendHerinvestering(unittest.TestCase):
    def test_dividend_herinvestering_wordt_meegeteld(self):
        # BYD-voorbeeld uit de opdracht (2025-08-04): het volledige bruto
        # dividend wordt automatisch herbelegd (Dividend Herinvestering
        # heft het Dividend-bedrag exact op), alleen de belasting is echt
        # cash afgeschreven. netto_ruw moet dus (10.47 - 10.47) - 1.05 =
        # -1.05 HKD zijn, met herinvesteerd=True.
        df = _df([
            _rij("2025-08-04", "09:00", "Dividend", 10.47, "HKD",
                 product="BYD CO LTD", isin="CNE100000296"),
            _rij("2025-08-04", "09:00", "Dividend Herinvestering", -10.47, "HKD",
                 product="BYD CO LTD", isin="CNE100000296"),
            _rij("2025-08-04", "09:01", "Dividendbelasting", -1.05, "HKD",
                 product="BYD CO LTD", isin="CNE100000296"),
            _rij("2025-08-05", "06:00", "Valuta Debitering", -0.12, "EUR"),
            _rij("2025-08-05", "06:00", "Valuta Creditering", 1.05, "HKD"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertTrue(r["herinvesteerd"])
        self.assertIsNotNone(r["netto_eur"])
        self.assertAlmostEqual(r["netto_eur"], -0.12, places=6)

    def test_geen_herinvestering_rij_geeft_herinvesteerd_false(self):
        df = _df([
            _rij("2024-01-15", "10:00", "Dividend", 5.0, "EUR",
                 product="SHELL", isin="NL0000009355"),
        ])
        records = verwerk_rekeningoverzicht_df(df)
        self.assertFalse(records[0]["herinvesteerd"])


class TestDividendConversieRichting(unittest.TestCase):
    def test_eur_naar_vreemd_conversie_wordt_herkend(self):
        # Het BYD-belasting-voorbeeld uit de opdracht: Debitering is EUR,
        # Creditering is de vreemde valuta (DeGiro wisselt EUR náár HKD om
        # de belasting te dekken) — het omgekeerde van de normale richting.
        df = _df([
            _rij("2025-08-05", "06:00", "Valuta Debitering", -0.12, "EUR"),
            _rij("2025-08-05", "06:00", "Valuta Creditering", 1.05, "HKD"),
        ])
        paren = _koppel_valutaconversie_paren(df)
        self.assertEqual(len(paren), 1)
        paar = paren[0]
        self.assertEqual(paar["valuta"], "HKD")
        self.assertAlmostEqual(paar["vreemd_bedrag"], 1.05, places=6)
        self.assertAlmostEqual(paar["eur_bedrag"], -0.12, places=6)

    def test_normale_richting_blijft_positief(self):
        df = _df([
            _rij("2024-02-11", "07:00", "Valuta Debitering", -10.0, "USD"),
            _rij("2024-02-11", "07:00", "Valuta Creditering", 9.15, "EUR"),
        ])
        paren = _koppel_valutaconversie_paren(df)
        self.assertEqual(len(paren), 1)
        paar = paren[0]
        self.assertEqual(paar["valuta"], "USD")
        self.assertAlmostEqual(paar["eur_bedrag"], 9.15, places=6)


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
