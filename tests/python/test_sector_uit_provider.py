"""Sectorverdeling uit de holdings van de fondsmaker: vertaling, opslaan bij het ophalen en de terugval (alles gemockt)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_profiel, etf_holdings_provider as ehp, ticker_classificatie as tc, wat_als
from backend.etf_holdings_provider import sector_verdeling_uit_holdings
from backend.portfolio_verdeling import sector_pct_met_restant

# Uittreksels uit echte iShares-bestanden (CSPX.AS in het Engels, IWDA.AS in het Nederlands).
ISHARES_EN = (
    b'Fund Holdings as of,"Oct 28, 2026"\nInception Date,"May 19, 2010"\n'
    b"Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,Exchange,Market Currency\n"
    b'NVDA,NVIDIA CORP,Information Technology,Equity,"1","30.00","1","1","1",United States,NASDAQ,USD\n'
    b'JPM,JPMORGAN CHASE & CO,Financials,Equity,"1","20.00","1","1","1",United States,NYSE,USD\n'
    b'UNH,UNITEDHEALTH GROUP INC,Health Care,Equity,"1","10.00","1","1","1",United States,NYSE,USD\n'
    b'XTSLA,USD CASH,Cash and/or Derivatives,Cash,"1","40.00","1","1","1",United States,-,USD\n'
)
ISHARES_NL = (
    "Fund Holdings as of,\"28 okt 2026\"\nInception Date,\"mei 2010\"\n"
    "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,Exchange,Market Currency\n"
    "NVDA,NVIDIA CORP,IT,Aandelen,\"1,00\",\"30,00\",\"1,00\",\"1,00\",\"1,00\",Verenigde Staten,NASDAQ,USD\n"
    "JPM,JPMORGAN CHASE & CO,Financiële waarden,Aandelen,\"1,00\",\"20,00\",\"1,00\",\"1,00\",\"1,00\",Verenigde Staten,NYSE,USD\n"
    "UNH,UNITEDHEALTH GROUP INC,Gezondheidszorg,Aandelen,\"1,00\",\"10,00\",\"1,00\",\"1,00\",\"1,00\",Verenigde Staten,NYSE,USD\n"
    "XTSLA,USD CASH,Liquide middelen en/of derivaten,Geldmarkt,\"1,00\",\"40,00\",\"1,00\",\"1,00\",\"1,00\",Verenigde Staten,-,USD\n"
).encode("utf-8")
VERWACHT = {"Technology": 0.3, "Financial Services": 0.2, "Healthcare": 0.1}


def rij(gewicht, sector):
    return {"naam": "X", "gewicht": gewicht, "land": "Unknown", "sector": sector, "ticker": None}


class TestSectorVerdelingUitHoldings(unittest.TestCase):
    def test_engels_en_nederlands_geven_dezelfde_app_namen(self):
        en = sector_verdeling_uit_holdings(ehp._parse_ishares_holdings(ISHARES_EN, locale="en"))
        nl = sector_verdeling_uit_holdings(ehp._parse_ishares_holdings(ISHARES_NL, locale="nl"))
        self.assertEqual(en.keys(), VERWACHT.keys())
        for sector, fractie in VERWACHT.items():
            self.assertAlmostEqual(en[sector], fractie)
            self.assertAlmostEqual(nl[sector], fractie)

    def test_gewichten_worden_per_sector_opgeteld_als_fractie(self):
        uit = sector_verdeling_uit_holdings([rij(30.0, "Financials"), rij(25.0, "Financiële waarden"), rij(5.0, "Energy")])
        self.assertAlmostEqual(uit["Financial Services"], 0.55)
        self.assertAlmostEqual(uit["Energy"], 0.05)

    def test_cash_streepje_en_leeg_komen_niet_in_de_uitvoer(self):
        uit = sector_verdeling_uit_holdings(
            [rij(60.0, "Energy"), rij(10.0, "Cash and/or Derivatives"), rij(5.0, "-"), rij(5.0, None), rij(5.0, "")])
        self.assertEqual(list(uit), ["Energy"])
        self.assertNotIn("Unknown", uit)

    def test_minder_dan_de_helft_herkend_geeft_leeg(self):
        self.assertEqual(sector_verdeling_uit_holdings([rij(49.0, "Energy"), rij(51.0, "Cash and/or Derivatives")]), {})

    def test_zonder_sectorkolom_geeft_leeg(self):
        self.assertEqual(sector_verdeling_uit_holdings([rij(50.0, None), rij(50.0, None)]), {})

    def test_onbekende_sectornaam_geeft_een_logregel(self):
        ehp._gelogde_onbekende_sectoren.clear()
        with patch.object(ehp, "dprint") as log:
            sector_verdeling_uit_holdings([rij(60.0, "Energy"), rij(5.0, "Ruimtevaart"), rij(5.0, "Ruimtevaart")])
            sector_verdeling_uit_holdings([rij(60.0, "Energy"), rij(5.0, "Ruimtevaart")])
        log.assert_called_once()
        self.assertIn("Ruimtevaart", log.call_args.args[0])

    def test_vanek_zonder_sectorkolom_zoals_tdiv_geeft_leeg(self):
        self.assertEqual(sector_verdeling_uit_holdings([rij(4.48, None), rij(4.43, None)]), {})


class TestOpslaanBijHetOphalen(unittest.TestCase):
    PROVIDER = [rij(60.0, "Energy"), rij(40.0, "Utilities")]

    def ophalen(self, yahoo_cache):
        with patch.object(tc, "db_get_cached_etf_holdings", return_value=None), \
                patch.object(tc, "db_save_etf_holdings"), \
                patch.object(tc, "fetch_provider_holdings", return_value=self.PROVIDER), \
                patch.object(tc, "ETF_HOLDINGS_BRON", {"EUEA.AS": {}}), \
                patch.object(tc, "db_get_cached_etf_sector_verdeling", return_value=yahoo_cache), \
                patch.object(tc, "db_save_etf_sector_verdeling") as bewaar:
            tc.get_etf_holdings("EUEA.AS")
        return bewaar

    def test_provider_holdings_met_sectoren_slaan_een_verdeling_op(self):
        bewaar = self.ophalen(None)
        bewaar.assert_called_once()
        self.assertEqual(bewaar.call_args.args[0], "EUEA.AS")
        self.assertAlmostEqual(bewaar.call_args.args[1]["Energy"], 0.6)

    def test_bestaande_yahoo_cache_wordt_niet_overschreven(self):
        self.ophalen({"technology": 1.0}).assert_not_called()


class TestTerugval(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)

    def sector(self, provider_holdings, bron=None):
        opgeslagen = {}

        def lees(ticker):
            return opgeslagen.get(ticker)

        def sla_op(ticker, verdeling):
            opgeslagen[ticker] = dict(verdeling)

        with patch.object(tc, "db_get_cached_etf_sector_verdeling", side_effect=lees), \
                patch.object(tc, "db_save_etf_sector_verdeling", side_effect=sla_op) as bewaar, \
                patch.object(tc, "_yq_sector_verdeling", return_value={}), \
                patch.object(tc.yf, "Ticker", side_effect=RuntimeError("401")), \
                patch.object(tc, "ETF_HOLDINGS_BRON", {"TDT.AS": {}} if bron is None else bron), \
                patch.object(tc, "fetch_provider_holdings", return_value=provider_holdings) as haal, \
                patch.object(tc, "dprint"):
            eerste = tc.get_etf_sector_verdeling("TDT.AS")
            tweede = tc.get_etf_sector_verdeling("TDT.AS")
        return eerste, tweede, haal, bewaar

    def test_een_provider_call_opslaan_en_tweede_aanroep_uit_cache(self):
        eerste, tweede, haal, bewaar = self.sector([rij(70.0, "Energy"), rij(30.0, "Industrials")])
        self.assertAlmostEqual(eerste["Energy"], 0.7)
        self.assertEqual(eerste, tweede)
        haal.assert_called_once()
        bewaar.assert_called_once()

    def test_mislukking_wordt_onthouden(self):
        eerste, tweede, haal, bewaar = self.sector([rij(100.0, None)])
        self.assertEqual((eerste, tweede), ({}, {}))
        haal.assert_called_once()
        bewaar.assert_not_called()


class TestProfielEnWatAls(unittest.TestCase):
    def profiel(self, holdings_bron):
        holdings = [{"holding_naam": "Pfizer", "holding_ticker": None, "gewicht": 1.0, "land": "United States",
                     "bron": holdings_bron}]
        patches = {
            "get_ticker_details": dict(return_value={"is_etf": True, "long_name": "VanEck Morningstar Dev Mkts Div Leaders UCITS ETF",
                                                     "valuta": "EUR"}),
            "get_etf_sector_verdeling": dict(return_value={"Technology": 0.6}),
            "get_etf_holdings": dict(return_value=holdings),
            "get_etf_holdings_voor_isin": dict(return_value=None),
            "land_proxies_voor_etfs": dict(return_value={}),
            "db_get_isins_voor_ticker": dict(return_value=[]),
            "zoek_gecachet": dict(return_value=[]),
            "ishares_fondsen": dict(return_value=[]),
            "haal_koers": dict(return_value=(30.0, "2026-10-28")),
        }
        gestart = []
        try:
            for naam, kwargs in patches.items():
                p = patch.object(effect_profiel, naam, **kwargs)
                p.start()
                gestart.append(p)
            with patch("backend.effect_dividend.haal_dividend_profiel", return_value={"status": "onbekend"}):
                return effect_profiel.bouw_effect_profiel("TDIV.AS")
        finally:
            for p in gestart:
                p.stop()

    def test_provider_holdings_geven_sector_bron_provider_zonder_waarschuwing(self):
        p = self.profiel("provider_csv")
        self.assertEqual((p["sector_bron"], p["sector_aanbieder"]), ("provider", "VanEck"))
        self.assertNotIn("geen_sector", p["waarschuwingen"])
        self.assertAlmostEqual(p["sector"]["Technology"], 0.6)

    def test_yahoo_top10_geeft_sector_bron_yahoo(self):
        p = self.profiel("yfinance_top10")
        self.assertEqual((p["sector_bron"], p["sector_aanbieder"]), ("yahoo", None))

    def test_wat_als_sector_is_niet_meer_volledig_onbekend(self):
        sector = sector_pct_met_restant({"Technology": 0.6})
        self.assertFalse(wat_als._volledig_onbekend(sector))
        self.assertTrue(wat_als._volledig_onbekend(sector_pct_met_restant({})))


if __name__ == "__main__":
    unittest.main()
