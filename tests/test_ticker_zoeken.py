"""
Unit tests voor het progressief-inkorten van de productnaam bij het zoeken
van een Yahoo-ticker (analysis._zoek_product_progressief).

Draait geheel offline: analysis._yahoo_search wordt gemockt, dus geen
echte yahooquery/netwerk-calls nodig. Regressietest voor de bug waarbij een
mislukte exacte-beurs-match blind terugviel op de eerste (mogelijk
verkeerde) kandidaat van de volledige-naam-zoekopdracht, i.p.v. de
productnaam in te korten en opnieuw te proberen — zie
GDX.L/VEF5.MU-voorbeeld (VanEck Gold Miners, ISIN IE00BQQP9F84, beurs TDG).
"""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import _zoek_product_progressief, _woorden_varianten, BEURS_MAP


def _quote(symbol, exchange):
    return {"symbol": symbol, "exchange": exchange}


class TestWoordenVarianten(unittest.TestCase):
    def test_kort_stap_voor_stap_in_tot_ondergrens(self):
        varianten = _woorden_varianten("VANECK GOLD MINERS UCITS ETF USD A", min_woorden=2)
        self.assertEqual(varianten, [
            "VANECK GOLD MINERS UCITS ETF USD A",
            "VANECK GOLD MINERS UCITS ETF USD",
            "VANECK GOLD MINERS UCITS ETF",
            "VANECK GOLD MINERS UCITS",
            "VANECK GOLD MINERS",
            "VANECK GOLD",
        ])

    def test_korte_naam_wordt_niet_verder_ingekort(self):
        # Al op/onder de ondergrens -> maar 1 variant (de naam zelf), geen
        # poging om tot een lege/onzinnige query in te korten.
        self.assertEqual(_woorden_varianten("SHELL PLC", min_woorden=2), ["SHELL PLC"])
        self.assertEqual(_woorden_varianten("SHELL", min_woorden=2), ["SHELL"])


class TestZoekProductProgressief(unittest.TestCase):
    TARGETS = BEURS_MAP["TDG"]  # ['GER', 'MUN', 'FRA']

    def test_geen_match_op_7_of_4_woorden_wel_op_3_woorden(self):
        # Zelfde patroon als het echte VanEck Gold Miners-geval: de volle
        # naam en de 4-woorden-variant vinden niets bruikbaars, pas de
        # 3-woorden-variant levert een kandidaat op de juiste beurs op.
        def fake_search(query):
            if query == "VANECK GOLD MINERS UCITS ETF USD A":
                return []
            if query == "VANECK GOLD MINERS UCITS ETF USD":
                return []
            if query == "VANECK GOLD MINERS UCITS ETF":
                return []
            if query == "VANECK GOLD MINERS UCITS":
                return [_quote("GDX.L", "LSE"), _quote("GDXZ.XC", "CXE")]
            if query == "VANECK GOLD MINERS":
                return [_quote("GDX", "PCX"), _quote("GDX.L", "LSE"), _quote("VEF5.MU", "MUN")]
            raise AssertionError(f"onverwachte query: {query!r}")

        with patch.object(analysis, "_yahoo_search", side_effect=fake_search):
            symbol, zekerheid, alternatieven = _zoek_product_progressief(
                "VANECK GOLD MINERS UCITS ETF USD A", "TDG", self.TARGETS,
            )

        self.assertEqual(symbol, "VEF5.MU")
        self.assertEqual(zekerheid, "zeker")
        # GDX en GDX.L (de foute eerste-kandidaat-fallback) horen als
        # alternatief mee, niet als het gekozen resultaat.
        self.assertIn({"symbol": "GDX", "exchange": "PCX"}, alternatieven)
        self.assertIn({"symbol": "GDX.L", "exchange": "LSE"}, alternatieven)

    def test_directe_match_op_volledige_naam_geen_verdere_inkorting(self):
        # Regressietest: als de volledige naam al een beurs-match oplevert,
        # mag er niet verder ingekort/gezocht worden (geen onnodige extra
        # yahooquery-calls).
        aantal_calls = []

        def fake_search(query):
            aantal_calls.append(query)
            return [_quote("VEF5.MU", "MUN"), _quote("GDX.L", "LSE")]

        with patch.object(analysis, "_yahoo_search", side_effect=fake_search):
            symbol, zekerheid, alternatieven = _zoek_product_progressief(
                "VANECK GOLD MINERS UCITS ETF USD A", "TDG", self.TARGETS,
            )

        self.assertEqual(symbol, "VEF5.MU")
        self.assertEqual(zekerheid, "zeker")
        self.assertEqual(aantal_calls, ["VANECK GOLD MINERS UCITS ETF USD A"])

    def test_geen_enkele_poging_vindt_beurs_match_valt_terug_op_eerste_resultaat(self):
        # Geen enkele variant (tot de ondergrens) staat op de verwachte
        # beurs -> terugvallen op het eerste resultaat van de eerste query
        # die uberhaupt iets opleverde, gemarkeerd als 'onzeker'.
        def fake_search(query):
            if query == "VANECK GOLD MINERS UCITS ETF USD A":
                return []
            if query == "VANECK GOLD MINERS UCITS ETF USD":
                return [_quote("GDX.L", "LSE"), _quote("GDX.SW", "EBS")]
            # kortere varianten geven dezelfde (foute) kandidaten
            return [_quote("GDX.L", "LSE"), _quote("GDX.SW", "EBS")]

        with patch.object(analysis, "_yahoo_search", side_effect=fake_search):
            symbol, zekerheid, alternatieven = _zoek_product_progressief(
                "VANECK GOLD MINERS UCITS ETF USD A", "TDG", self.TARGETS,
            )

        self.assertEqual(symbol, "GDX.L")
        self.assertEqual(zekerheid, "onzeker")
        self.assertIn({"symbol": "GDX.SW", "exchange": "EBS"}, alternatieven)

    def test_niets_gevonden_geeft_geen_match(self):
        with patch.object(analysis, "_yahoo_search", return_value=[]):
            symbol, zekerheid, alternatieven = _zoek_product_progressief(
                "COMPLEET ONBEKEND FONDS NAAM HIER", "TDG", self.TARGETS,
            )
        self.assertIsNone(symbol)
        self.assertIsNone(zekerheid)
        self.assertEqual(alternatieven, [])

    def test_ondergrens_wordt_gerespecteerd(self):
        # min_woorden=3: mag niet doorzoeken tot 2 of 1 woord.
        gezochte_queries = []

        def fake_search(query):
            gezochte_queries.append(query)
            return []  # nooit iets vinden, dwingt door tot de ondergrens

        with patch.object(analysis, "_yahoo_search", side_effect=fake_search):
            _zoek_product_progressief(
                "VANECK GOLD MINERS UCITS ETF USD A", "TDG", self.TARGETS, min_woorden=3,
            )

        kortste_query = min(gezochte_queries, key=lambda q: len(q.split()))
        self.assertEqual(len(kortste_query.split()), 3)
        self.assertNotIn("VANECK GOLD", gezochte_queries)  # 2 woorden, te kort


if __name__ == "__main__":
    unittest.main()
