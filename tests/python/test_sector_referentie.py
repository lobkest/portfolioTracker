"""Sector per bedrijf opzoeken in een iShares-referentiefonds (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_profiel, sector_referentie as sr, ticker_classificatie as tc
from backend.sector_referentie import maak_sector_opzoektabel, normaliseer_ticker, sector_verdeling_via_referentie


def ref(naam, ticker, sector, isin=None):
    rij = {"naam": naam, "gewicht": 0.2, "land": "Unknown", "sector": sector, "ticker": ticker}
    if isin:
        rij["isin"] = isin
    return rij


def holding(naam, gewicht, ticker=None, isin=None):
    rij = {"naam": naam, "gewicht": gewicht, "land": "Unknown", "sector": None, "ticker": ticker}
    if isin:
        rij["isin"] = isin
    return rij


# Uittreksel van de eerste regels van het TDIV-bestand van VanEck (geen sectorkolom, Bloomberg-tickers).
TDIV = [
    holding("Pfizer Inc", 4.48, "PFE US", "US7170811035"), holding("Verizon Communications Inc", 4.43, "VZ US"),
    holding("Nestle Sa", 4.10, "NESN SW"), holding("Shell Plc", 4.04, "SHEL LN"),
    holding("Hsbc Holdings Plc", 3.99, "HSBA LN"), holding("Totalenergies Se", 3.49, "TTE FP"),
    holding("Pepsico Inc", 2.94, "PEP US"), holding("Allianz Se", 2.80, "ALV GR"), holding("Bp Plc", 2.33, "BP/ LN"),
    holding("Intesa Sanpaolo Spa", 2.28, "ISP IM"), holding("Unilever Plc", 2.27, "ULVR LN"),
    holding("Bristol-Myers Squibb Co", 2.22, "BMY US"), holding("Banco Bilbao Vizcaya Argentaria Sa", 2.20, "BBVA SM"),
    holding("Novo Nordisk A/S", 2.10, "NOVOB DC"), holding("Iberdrola Sa", 2.03, "IBE SM"),
]
# Dezelfde bedrijven uit iShares MSCI World (IWDA, Nederlandse sectornamen); Novo Nordisk ontbreekt zoals in het echte bestand.
IWDA = [
    ref("PFIZER", "PFE", "Gezondheidszorg"), ref("VERIZON COMMUNICATIONS INC", "VZ", "Communicatie"),
    ref("NESTLE SA", "NESN", "Basis-consumentengoederen"), ref("SHELL", "SHEL", "Energie"),
    ref("HSBC HOLDINGS PLC", "HSBA", "Financiële waarden"), ref("TOTALENERGIES", "TTE", "Energie"),
    ref("PEPSICO", "PEP", "Basis-consumentengoederen"), ref("ALLIANZ", "ALV", "Financiële waarden"),
    ref("BP PLC", "BP.", "Energie"), ref("INTESA SANPAOLO", "ISP", "Financiële waarden"),
    ref("UNILEVER PLC", "ULVR", "Basis-consumentengoederen"), ref("BRISTOL MYERS SQUIBB", "BMY", "Gezondheidszorg"),
    ref("BANCO BILBAO VIZCAYA ARGENTARIA", "BBVA", "Financiële waarden"), ref("IBERDROLA", "IBE", "Nutsbedrijven"),
    ref("USD CASH", "USD", "Liquide middelen en/of derivaten"),
]


class TestOpzoektabel(unittest.TestCase):
    def test_nl_en_en_sectoren_worden_vertaald_en_cash_blijft_weg(self):
        tabel = maak_sector_opzoektabel(IWDA + [ref("NVIDIA CORP", "NVDA", "Information Technology")])
        self.assertEqual(tabel["ticker"]["HSBA"], "Financial Services")
        self.assertEqual(tabel["ticker"]["NVDA"], "Technology")
        self.assertEqual(tabel["naam"]["iberdrola"], "Utilities")
        self.assertNotIn("USD", tabel["ticker"])

    def test_dubbelzinnige_sleutel_wordt_weggelaten(self):
        tabel = maak_sector_opzoektabel([ref("SANTANDER", "SAN", "Financials"), ref("SANOFI", "SAN", "Health Care"),
                                         ref("ACME", "AC1", "Energy"), ref("ACME", "AC2", "Utilities")])
        self.assertNotIn("SAN", tabel["ticker"])
        self.assertNotIn("acme", tabel["naam"])
        self.assertEqual(tabel["naam"]["santander"], "Financial Services")

    def test_isin_gaat_voor_ticker_en_naam(self):
        tabel = maak_sector_opzoektabel([ref("FOO", "FOO", "Energy", isin="NL0000000001"), ref("BAR", "BAR", "Utilities")])
        uit, dekking = sector_verdeling_via_referentie(
            [holding("Bar", 80.0, "FOO US", "NL0000000001")], tabel)
        self.assertEqual(uit, {"Energy": 0.8})
        self.assertEqual(dekking, 80.0)

    def test_ticker_zonder_beurs_en_landcode(self):
        self.assertEqual([normaliseer_ticker(t) for t in ("PFE US", "ASML.AS", "BRK.B", "BP/ LN", " -- ", None)],
                         ["PFE", "ASML", "BRK.B", "BP/", "", ""])


class TestViaReferentie(unittest.TestCase):
    tabel = maak_sector_opzoektabel(IWDA)

    def test_gevonden_op_ticker_en_op_naam_met_dekking(self):
        uit, dekking = sector_verdeling_via_referentie(
            [holding("Wie Dan Ook", 30.0, "PFE US"), holding("Iberdrola Sa", 25.0, "IBE SM"),
             holding("Verizon Communications Inc", 10.0, "XX"), holding("Onbekend Bv", 35.0, "ZZZ")],
            self.tabel)
        self.assertEqual(uit, {"Healthcare": 0.3, "Utilities": 0.25, "Communication Services": 0.1})
        self.assertEqual(dekking, 65.0)

    def test_onder_de_helft_gevonden_geeft_leeg_maar_meldt_de_dekking(self):
        uit, dekking = sector_verdeling_via_referentie([holding("Pfizer", 49.0, "PFE US"), holding("Onbekend", 51.0)], self.tabel)
        self.assertEqual((uit, dekking), ({}, 49.0))

    def test_hoofdletters_en_rechtsvorm_pfizer_inc(self):
        tabel = maak_sector_opzoektabel([ref("PFIZER INC", "XXX", "Health Care")])
        self.assertEqual(sector_verdeling_via_referentie([holding("Pfizer Inc", 100.0)], tabel)[0], {"Healthcare": 1.0})


class TestIntegratieTdiv(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        tc._sector_opzoektabel_cache[:] = [None, 0.0]
        self.addCleanup(tc.wis_geen_fondsdata_cache)
        self.addCleanup(lambda: tc._sector_opzoektabel_cache.__setitem__(slice(None), [None, 0.0]))

    def draai(self, tickers):
        opgeslagen = {}
        with patch.object(tc, "db_get_cached_etf_sector_verdeling", side_effect=lambda t: opgeslagen.get(t)), \
                patch.object(tc, "db_save_etf_sector_verdeling", side_effect=lambda t, v: opgeslagen.__setitem__(t, dict(v))), \
                patch.object(tc, "_sector_verdeling_van_yahoo", return_value={}), \
                patch.object(tc, "ETF_HOLDINGS_BRON", {}), \
                patch.object(tc, "_haal_vaneck_holdings", return_value=TDIV), \
                patch.object(tc, "_ishares_fonds_voor_isin",
                             side_effect=lambda isin: {"product_url": "x"} if isin in sr.SECTOR_REFERENTIE_ISINS else None), \
                patch.object(tc, "fetch_ishares_holdings_via_productpagina", return_value=IWDA) as download, \
                patch.object(sr, "SECTOR_MIN_HERKEND_PCT", 40.0), \
                patch.object(tc, "dprint"):
            # Het uittreksel is maar 45,7 % van het fonds, vandaar de lagere drempel.
            uit = [tc.get_etf_sector_verdeling(t) for t in tickers]
            bronnen = [tc.get_etf_sector_bron(t) for t in tickers]
        return uit, bronnen, opgeslagen, download

    def test_verdeling_dekking_en_opslag_als_referentie(self):
        (verdeling,), (bron,), opgeslagen, _ = self.draai(["TDIV.AS"])
        verwacht = {"Healthcare": 6.70, "Communication Services": 4.43, "Consumer Defensive": 9.31, "Energy": 9.86,
                    "Financial Services": 11.27, "Utilities": 2.03}
        self.assertEqual(verdeling.keys(), verwacht.keys())
        for sector, pct in verwacht.items():
            self.assertAlmostEqual(verdeling[sector], pct / 100)
        self.assertAlmostEqual(sum(verdeling.values()) * 100, 43.60)
        self.assertEqual(bron, "referentie")
        self.assertIn("_bron:referentie", opgeslagen["TDIV.AS"])

    def test_tweede_fonds_gebruikt_de_geheugentabel(self):
        _, _, _, download = self.draai(["TDIV.AS", "TDIV.DE"])
        download.assert_called_once()

    def test_mislukte_download_wordt_onthouden(self):
        with patch.object(tc, "fetch_ishares_holdings_via_productpagina", return_value=None) as download, \
                patch.object(tc, "_ishares_fonds_voor_isin", return_value={"product_url": "x"}), patch.object(tc, "dprint"):
            self.assertIsNone(tc._sector_opzoektabel())
            self.assertIsNone(tc._sector_opzoektabel())
        download.assert_called_once()


class TestProfielReferentie(unittest.TestCase):
    def test_sector_bron_referentie_met_dekking_en_zonder_waarschuwing(self):
        patches = {
            "get_ticker_details": dict(return_value={"is_etf": True, "long_name": "VanEck Dividend UCITS ETF", "valuta": "EUR"}),
            "get_etf_sector_verdeling": dict(return_value={"Energy": 0.4, "Utilities": 0.2}),
            "get_etf_sector_bron": dict(return_value="referentie"),
            "get_etf_holdings": dict(return_value=[{"holding_naam": "Pfizer", "holding_ticker": None, "gewicht": 1.0,
                                                    "land": "United States", "bron": "provider_csv"}]),
            "get_etf_holdings_voor_isin": dict(return_value=None), "land_proxies_voor_etfs": dict(return_value={}),
            "db_get_isins_voor_ticker": dict(return_value=[]), "zoek_gecachet": dict(return_value=[]),
            "ishares_fondsen": dict(return_value=[]), "haal_koers": dict(return_value=(30.0, "2026-10-28")),
        }
        gestart = [patch.object(effect_profiel, naam, **kw) for naam, kw in patches.items()]
        for p in gestart:
            p.start()
            self.addCleanup(p.stop)
        with patch("backend.effect_dividend.haal_dividend_profiel", return_value={"status": "onbekend"}):
            p = effect_profiel.bouw_effect_profiel("TDIV.AS")
        self.assertEqual((p["sector_bron"], p["sector_dekking"], p["sector_aanbieder"]), ("referentie", 60.0, "iShares MSCI World"))
        self.assertNotIn("geen_sector", p["waarschuwingen"])
        self.assertAlmostEqual(p["sector"]["Unknown"], 0.4)


if __name__ == "__main__":
    unittest.main()
