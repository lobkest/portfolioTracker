"""
Unit tests voor de dagrange-bewuste escalatiepoort (opdracht A).

Achtergrond: find_ticker_met_snelle_prijscheck() (stap 1) escaleerde alleen
naar de duurdere steekproef/alternatieven-check bij een ruwe %-afwijking
> PRIJSCHECK_DREMPEL_WAARSCHUWING (6%) op de laatste transactiedatum. De
rest van de app (_prijscheck_is_probleem(), bv. de bovenste
waarschuwingsbalk) geeft voorrang aan de dagrange-check (valt de Excel-
koers buiten Yahoo's intraday-high/low), met de %-drempel alleen als
terugval. Bevestigde praktijkgevallen (BYD, VWCE.AS) hadden een lage
%-afwijking (<6%) maar vielen wel buiten de dagrange, en escaleerden dus
ten onrechte niet. Deze tests dekken zowel find_ticker_met_snelle_
prijscheck() als _ticker_heeft_prijsprobleem() (gebruikt door
backfill_verouderde_tickers()), die hetzelfde criterium moeten gebruiken.

Draait geheel offline: find_ticker_detailed, vergelijk_prijs_op_datum en
_zoek_betere_alternatieven worden gemockt, dus geen echte yahooquery/
yfinance-calls.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis


class TestEscalatiepoortDagrangeBewust(unittest.TestCase):
    def setUp(self):
        self.transacties = [{"datum": "2026-02-16", "koers": 10.39}]

    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_escaleert_nu_bij_dagrange_probleem_met_lage_pct(self, mock_find, mock_vergelijk):
        # BYD-achtig geval: afwijking maar 3.7% (< 6%-drempel), maar WEL
        # buiten de dagrange -- moet nu escaleren voorbij stap 1. Stap 3
        # (kandidaten doorrekenen) blijft ongewijzigd gated op > 10%
        # (buiten scope van deze opdracht), dus die wordt hier bewust NIET
        # bereikt met 3.7% -- de escalatie voorbij stap 1 (zekerheid wordt
        # "onzeker", i.p.v. de vroege "OK"-return die basis ongemoeid laat)
        # is het bewijs dat de dagrange-bewuste poort werkt.
        mock_find.return_value = {"ticker": "BY6.MU", "zekerheid": "zeker", "alternatieven": []}
        mock_vergelijk.return_value = {
            "afwijking_pct": 3.7, "match": False, "binnen_dagrange": False,
            "yahoo_koers": 10.0, "high": 10.0, "low": 9.9,
        }
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "BYD Company Limited", "CNE100000296", "TDG", self.transacties
        )
        self.assertIsNotNone(resultaat["prijswaarschuwing"])
        self.assertEqual(resultaat["zekerheid"], "onzeker")

    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_escaleert_niet_bij_lage_pct_en_binnen_dagrange(self, mock_find, mock_vergelijk):
        # VUSA.AS-achtig geval: laatste transactie oprecht in orde -- moet
        # NIET escaleren (ongewijzigd gedrag).
        mock_find.return_value = {"ticker": "VUSA.AS", "zekerheid": "zeker", "alternatieven": []}
        mock_vergelijk.return_value = {
            "afwijking_pct": 1.1, "match": True, "binnen_dagrange": True,
            "yahoo_koers": 110.0, "high": 111.2, "low": 110.3,
        }
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "Vanguard S&P 500 UCITS ETF USD Dis", "IE00B3XXRP09", "EAM", self.transacties
        )
        self.assertIsNone(resultaat["prijswaarschuwing"])

    @patch("analysis._zoek_betere_alternatieven")
    @patch("analysis.vergelijk_prijs_op_datum")
    @patch("analysis.find_ticker_detailed")
    def test_ontbrekende_koersdata_escaleert_nog_steeds(self, mock_find, mock_vergelijk, mock_alt):
        # Regressietest: het G2X.MU-geval (geen koersdata = verdacht) mag
        # niet stuklopen door deze wijziging.
        mock_find.return_value = {"ticker": "ONBEKEND.XX", "zekerheid": "zeker", "alternatieven": []}
        mock_vergelijk.return_value = {
            "afwijking_pct": None, "match": None, "binnen_dagrange": None,
            "yahoo_koers": None, "high": None, "low": None,
        }
        mock_alt.return_value = ([], None)
        resultaat = analysis.find_ticker_met_snelle_prijscheck(
            "Onbekend Fonds", "XX0000000000", "TDG", self.transacties
        )
        self.assertIsNotNone(resultaat["prijswaarschuwing"])


class TestTickerHeeftPrijsprobleemDagrangeBewust(unittest.TestCase):
    @patch("analysis.vergelijk_prijs_op_datum")
    def test_dagrange_probleem_met_lage_pct_telt_als_probleem(self, mock_vergelijk):
        mock_vergelijk.return_value = {
            "afwijking_pct": 3.7, "match": False, "binnen_dagrange": False,
        }
        self.assertTrue(
            analysis._ticker_heeft_prijsprobleem("BY6.MU", [{"datum": "2026-02-16", "koers": 10.39}])
        )

    @patch("analysis.vergelijk_prijs_op_datum")
    def test_binnen_dagrange_telt_niet_als_probleem(self, mock_vergelijk):
        mock_vergelijk.return_value = {
            "afwijking_pct": 1.1, "match": True, "binnen_dagrange": True,
        }
        self.assertFalse(
            analysis._ticker_heeft_prijsprobleem("VUSA.AS", [{"datum": "2025-12-02", "koers": 111.78}])
        )


if __name__ == "__main__":
    unittest.main()
