"""
Unit tests voor analysis.find_ticker_met_snelle_prijscheck() en
analysis.prijswaarschuwing_voor_ticker() -- de standaard, LICHTE
prijscontrole die nu bij ELKE upload draait (opslaand én 'niet opslaan'),
i.t.t. de volledige verifieer_ticker_met_prijs() die duur is en alleen
lui/on-demand draait op de Ticker-zekerheid-pagina.

Escalatietrapje (zie de docstring van find_ticker_met_snelle_prijscheck):
  1. Alleen de laatste transactiedatum -- 1 call, het gangbare geval.
  2. >6% afwijking -> ook de rest van de steekproef (tot 3 calls totaal).
  3. Nog steeds >10% afwijking (na stap 2) -> ook alternatieve tickers.

Draait geheel offline: find_ticker_detailed en vergelijk_prijs_op_datum
worden gemockt (net als tests/test_ticker_verificatie.py), dus geen echte
yahooquery/yfinance-calls en geen databasetoegang nodig.
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import (
    find_ticker_met_snelle_prijscheck, prijswaarschuwing_voor_ticker,
    ticker_waarschuwingen_voor_transacties, basis_ticker_zekerheid_parallel,
)

# find_ticker_met_snelle_prijscheck() roept sinds de OpenFIGI-root-check
# (zie _voeg_openfigi_check_toe in analysis.py) altijd haal_openfigi_
# resultaten() aan, die zonder deze patch een echte DB/netwerk-call zou
# doen. Module-breed op "geen resultaten" gepatcht zodat de bestaande
# tests hier offline en ongewijzigd blijven -- _openfigi_root_bekend()
# geeft dan None terug (geen oordeel), dus geen effect op deze tests.
_openfigi_patcher = None


def setUpModule():
    global _openfigi_patcher
    _openfigi_patcher = patch.object(
        analysis, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}
    )
    _openfigi_patcher.start()


def tearDownModule():
    _openfigi_patcher.stop()


def _basis_patch(ticker="AAPL", zekerheid="zeker", alternatieven=None):
    return patch.object(
        analysis, "find_ticker_detailed",
        return_value={"ticker": ticker, "zekerheid": zekerheid, "alternatieven": alternatieven or []},
    )


def _prijscheck(afwijking_pct, match=True, yahoo_koers=100.0):
    return {
        "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
        "bekende_koers": 100.0, "afwijking_pct": afwijking_pct, "niveau": None, "match": match,
    }


class TestStap1AlleenLaatsteDatum(unittest.TestCase):
    def test_afwijking_onder_6_procent_precies_1_aanroep_geen_escalatie(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 3, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        call_count = {"n": 0}

        def fake_vergelijk(ticker, datum, bekende_koers):
            call_count["n"] += 1
            return _prijscheck(afwijking_pct=3.0, match=True)

        with _basis_patch(), patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertEqual(call_count["n"], 1)
        self.assertIsNone(resultaat["prijswaarschuwing"])
        self.assertEqual(len(resultaat["prijs_checks"]), 1)
        self.assertEqual(resultaat["zekerheid"], "zeker")

    def test_gecontroleerde_datum_is_de_meest_recente_transactie(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 90.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},  # meest recent
        ]
        gecheckte_datums = []

        def fake_vergelijk(ticker, datum, bekende_koers):
            gecheckte_datums.append(datum)
            return _prijscheck(afwijking_pct=1.0, match=True)

        with _basis_patch(), patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertEqual(gecheckte_datums, [date(2023, 6, 10)])

    def test_geen_transacties_of_geen_ticker_geeft_geen_crash(self):
        with _basis_patch(ticker=None, zekerheid="geen_match"):
            resultaat = find_ticker_met_snelle_prijscheck("ONBEKEND", "XX0000000000", "XYZ", [])
        self.assertIsNone(resultaat["ticker"])
        self.assertEqual(resultaat["prijs_checks"], [])
        self.assertIsNone(resultaat["prijswaarschuwing"])

    def test_corporate_action_rijen_met_koers_0_worden_genegeerd_bij_laatste(self):
        # Een split-/corporate-action-rij (koers 0) die toevallig de meest
        # recente datum heeft mag niet als 'laatste' transactie gekozen
        # worden -- zelfde filter als _kies_steekproef_transacties.
        transacties = [
            {"datum": date(2023, 6, 10), "koers": 100.0},
            {"datum": date(2023, 9, 1), "koers": 0.0},  # corporate action, later
        ]
        gecheckte_koersen = []

        def fake_vergelijk(ticker, datum, bekende_koers):
            gecheckte_koersen.append(bekende_koers)
            return _prijscheck(afwijking_pct=1.0, match=True)

        with _basis_patch(), patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk):
            find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertEqual(gecheckte_koersen, [100.0])


class TestStap2EscaleertNaarSteekproef(unittest.TestCase):
    def test_afwijking_tussen_6_en_10_procent_escaleert_maar_zoekt_geen_alternatieven(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 3, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        call_count = {"n": 0}

        def fake_vergelijk(ticker, datum, bekende_koers):
            call_count["n"] += 1
            return _prijscheck(afwijking_pct=8.0, match=False)  # >6%, <=10%

        with _basis_patch(zekerheid="zeker", alternatieven=[{"symbol": "ALT", "exchange": "NMS"}]), \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(analysis, "_zoek_betere_alternatieven") as mock_alternatieven:
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        # Laatste datum (stap 1) + 2 resterende steekproefdatums (stap 2) = 3.
        self.assertEqual(call_count["n"], 3)
        mock_alternatieven.assert_not_called()
        self.assertIsNotNone(resultaat["prijswaarschuwing"])
        self.assertIn("8.0%", resultaat["prijswaarschuwing"])
        self.assertEqual(resultaat["zekerheid"], "onzeker")
        self.assertNotIn("aanbevolen_alternatief", resultaat)

    def test_grootste_afwijking_over_hele_steekproef_bepaalt_de_waarschuwing(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 3, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},  # laatste, wordt eerst gecheckt
        ]

        def fake_vergelijk(ticker, datum, bekende_koers):
            if datum == date(2023, 6, 10):
                return _prijscheck(afwijking_pct=6.5, match=False)  # net > 6%, triggert stap 2
            if datum == date(2023, 1, 10):
                return _prijscheck(afwijking_pct=9.9, match=False)  # grootste, maar < 10%
            return _prijscheck(afwijking_pct=2.0, match=True)

        with _basis_patch(alternatieven=[]), patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(analysis, "_zoek_betere_alternatieven") as mock_alternatieven:
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertIn("9.9%", resultaat["prijswaarschuwing"])
        mock_alternatieven.assert_not_called()


class TestStap3EscaleertNaarAlternatieven(unittest.TestCase):
    def test_afwijking_boven_10_procent_zoekt_alternatieven_en_geeft_aanbevolen_alternatief(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 3, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]

        def fake_vergelijk(ticker, datum, bekende_koers):
            return _prijscheck(afwijking_pct=15.0, match=False)

        with _basis_patch(zekerheid="zeker", alternatieven=[{"symbol": "ALT", "exchange": "NMS"}]), \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(analysis, "_zoek_betere_alternatieven", return_value=([{"ticker": "ALT"}], "ALT")) as mock_alt:
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        mock_alt.assert_called_once()
        self.assertEqual(resultaat["aanbevolen_alternatief"], "ALT")
        self.assertIn("15.0%", resultaat["prijswaarschuwing"])

    def test_geen_geslaagd_alternatief_laat_aanbevolen_alternatief_weg(self):
        transacties = [{"datum": date(2023, 6, 10), "koers": 100.0}] * 1

        def fake_vergelijk(ticker, datum, bekende_koers):
            return _prijscheck(afwijking_pct=15.0, match=False)

        with _basis_patch(alternatieven=[{"symbol": "ALT", "exchange": "NMS"}]), \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=fake_vergelijk), \
             patch.object(analysis, "_zoek_betere_alternatieven", return_value=([{"ticker": "ALT"}], None)):
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertNotIn("aanbevolen_alternatief", resultaat)


class TestGeenYahooData(unittest.TestCase):
    """Bugfix: geen koersdata bij Yahoo werd behandeld als 'niets te
    controleren, dus geen probleem' en bleef stilzwijgend 'zeker'. Nu
    escaleert dit net als een grote afwijking (zie het G2X.MU/GDX.L-geval
    in tests/test_ticker_verificatie.py)."""

    def test_geen_yahoo_koers_op_geen_enkele_steekproefdatum_escaleert_naar_onzeker(self):
        transacties = [
            {"datum": date(2023, 1, 10), "koers": 100.0},
            {"datum": date(2023, 3, 10), "koers": 100.0},
            {"datum": date(2023, 6, 10), "koers": 100.0},
        ]
        geen_data = {"yahoo_koers": None, "afwijking_pct": None, "match": None, "niveau": None,
                     "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0, "bekende_koers": 100.0}

        with _basis_patch(zekerheid="zeker", alternatieven=[]), \
             patch.object(analysis, "vergelijk_prijs_op_datum", return_value=geen_data):
            resultaat = find_ticker_met_snelle_prijscheck("APPLE INC", "US0378331005", "NASDAQ", transacties)

        self.assertIsNotNone(resultaat["prijswaarschuwing"])
        self.assertIn("Geen koersdata", resultaat["prijswaarschuwing"])
        self.assertEqual(resultaat["zekerheid"], "onzeker")

    def test_geen_koersdata_zoekt_ook_alternatieven_en_geeft_aanbevolen_alternatief(self):
        transacties = [{"datum": date(2023, 6, 10), "koers": 100.0}]
        geen_data = {"yahoo_koers": None, "afwijking_pct": None, "match": None, "niveau": None,
                     "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0, "bekende_koers": 100.0}

        with _basis_patch(zekerheid="zeker", alternatieven=[{"symbol": "GDX.L", "exchange": "LSE"}]), \
             patch.object(analysis, "vergelijk_prijs_op_datum", return_value=geen_data), \
             patch.object(analysis, "_zoek_betere_alternatieven", return_value=([{"ticker": "GDX.L"}], "GDX.L")) as mock_alt:
            resultaat = find_ticker_met_snelle_prijscheck("VANECK GOLD MINERS", "IE00BQQP9F84", "TDG", transacties)

        mock_alt.assert_called_once()
        self.assertEqual(resultaat["aanbevolen_alternatief"], "GDX.L")


class TestPrijswaarschuwingVoorTicker(unittest.TestCase):
    """De cache-only variant voor analyze_transacties() (later bezoek aan
    een opgeslagen portfolio) -- roept BEWUST find_ticker_detailed niet aan."""

    def test_geen_afwijking_geeft_geen_waarschuwing(self):
        with patch.object(analysis, "find_ticker_detailed") as mock_find, \
             patch.object(analysis, "vergelijk_prijs_op_datum", return_value=_prijscheck(afwijking_pct=1.0)):
            boodschap = prijswaarschuwing_voor_ticker("AAPL", [{"datum": date(2023, 6, 10), "koers": 100.0}])

        mock_find.assert_not_called()
        self.assertIsNone(boodschap)

    def test_afwijking_boven_drempel_geeft_boodschap_met_ticker_en_percentage(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=_prijscheck(afwijking_pct=12.3, match=False)):
            boodschap = prijswaarschuwing_voor_ticker("AAPL", [{"datum": date(2023, 6, 10), "koers": 100.0}])

        self.assertIn("AAPL", boodschap)
        self.assertIn("12.3%", boodschap)

    def test_geen_transacties_geeft_geen_waarschuwing(self):
        self.assertIsNone(prijswaarschuwing_voor_ticker("AAPL", []))
        self.assertIsNone(prijswaarschuwing_voor_ticker(None, [{"datum": date(2023, 6, 10), "koers": 100.0}]))


class TestTickerWaarschuwingenVoorTransacties(unittest.TestCase):
    """Integratietest (spec-item 6): een transacties_df met een bewust
    afwijkende testprijs voor 1 ticker levert een melding op voor precies
    die ticker, en niets voor de andere -- dit is de functie die
    analyze_transacties() in app.py aanroept, dus dit dekt zowel het
    opslaande als het 'niet opslaan'-uploadpad (die delen deze functie)."""

    def test_een_afwijkende_ticker_tussen_meerdere_geeft_precies_1_waarschuwing(self):
        transacties_df = pd.DataFrame({
            "ticker": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "datum": [date(2023, 1, 10), date(2023, 6, 10), date(2023, 1, 10), date(2023, 6, 10)],
            "koers": [100.0, 100.0, 200.0, 200.0],
        })
        ticker_namen = {"AAPL": "Apple", "MSFT": "Microsoft"}

        def fake_prijswaarschuwing(ticker, transacties, isin=None):
            if ticker == "AAPL":
                return "Koers van AAPL wijkt 15.0% af van Yahoo — controleer op het Ticker-zekerheid-tabblad."
            return None

        with patch.object(analysis, "prijswaarschuwing_voor_ticker", side_effect=fake_prijswaarschuwing):
            waarschuwingen = ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen)

        self.assertEqual(len(waarschuwingen), 1)
        self.assertEqual(waarschuwingen[0]["ticker"], "AAPL")
        self.assertEqual(waarschuwingen[0]["naam"], "Apple")
        self.assertIn("15.0%", waarschuwingen[0]["boodschap"])

    def test_geen_enkele_afwijking_geeft_lege_lijst(self):
        transacties_df = pd.DataFrame({
            "ticker": ["AAPL"], "datum": [date(2023, 6, 10)], "koers": [100.0],
        })
        with patch.object(analysis, "prijswaarschuwing_voor_ticker", return_value=None):
            waarschuwingen = ticker_waarschuwingen_voor_transacties(transacties_df, {})
        self.assertEqual(waarschuwingen, [])


class TestBasisTickerZekerheidParallel(unittest.TestCase):
    def test_bewaart_volgorde_en_wikkelt_elk_resultaat_in_basis_vorm(self):
        posities = [
            ("FONDS A", "ISINA", "EAM", []),
            ("FONDS B", "ISINB", "EAM", []),
            ("FONDS C", "ISINC", "EAM", []),
        ]

        def fake_find_ticker_detailed(product, isin, beurs):
            return {"ticker": f"TICK-{isin}", "zekerheid": "zeker", "alternatieven": []}

        with patch.object(analysis, "find_ticker_detailed", side_effect=fake_find_ticker_detailed):
            resultaten = basis_ticker_zekerheid_parallel(posities)

        self.assertEqual([r["ticker"] for r in resultaten], ["TICK-ISINA", "TICK-ISINB", "TICK-ISINC"])
        for r in resultaten:
            self.assertTrue(r["basis_alleen"])
            self.assertEqual(r["prijs_checks"], [])


if __name__ == "__main__":
    unittest.main()
