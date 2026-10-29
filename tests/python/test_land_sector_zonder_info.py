"""Land en sector van een aandeel zonder bruikbare Yahoo-.info (alles gemockt, geen netwerk of DB)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_profiel, ticker_classificatie as tc
from backend.sector_referentie import maak_land_opzoektabel, maak_sector_opzoektabel, zoek_land_sector

UITGEKLEED = {"trailingPegRatio": None}
BRUIKBAAR = {"quoteType": "EQUITY", "currency": "EUR", "country": "Germany", "sector": "Industrials"}


def ref(naam, ticker, sector, land, isin=None):
    rij = {"naam": naam, "gewicht": 0.2, "land": land, "sector": sector, "ticker": ticker}
    if isin:
        rij["isin"] = isin
    return rij


REFERENTIE = [ref("ASML HOLDING NV", "ASML", "Information Technology", "Netherlands"),
              ref("SAP", "SAP", "Information Technology", "Germany", "DE0007164600")]


def opzoektabel(holdings=REFERENTIE):
    tabel = maak_sector_opzoektabel(holdings)
    tabel["land"] = maak_land_opzoektabel(holdings)
    return tabel


class TestZoekLandSector(unittest.TestCase):
    def test_asml_op_ticker_en_op_naam(self):
        tabel = opzoektabel([ref("ASML HOLDING NV", "ASML", "Information Technology", "Netherlands")])
        self.assertEqual(zoek_land_sector(tabel, "ASML.AS", None, None), ("Netherlands", "Technology"))
        self.assertEqual(zoek_land_sector(tabel, "XXX", "ASML Holding", None), ("Netherlands", "Technology"))

    def test_onbekend_land_en_dubbelzinnig_land_vallen_weg(self):
        tabel = opzoektabel([ref("A", "AAA", "Energy", "-"), ref("B", "BBB", "Energy", "Spain"),
                             ref("B", "BBB", "Energy", "France")])
        self.assertEqual(zoek_land_sector(tabel, "AAA", None, None), (None, "Energy"))
        self.assertEqual(zoek_land_sector(tabel, "BBB", None, None), (None, "Energy"))

    def test_zonder_landtabel_alleen_sector(self):
        self.assertEqual(zoek_land_sector(maak_sector_opzoektabel(REFERENTIE), "SAP", None, None), (None, "Technology"))


class GeheugenSchoon(unittest.TestCase):
    def setUp(self):
        tc.wis_geen_fondsdata_cache()
        self.addCleanup(tc.wis_geen_fondsdata_cache)
        self.opgeslagen = []
        patches = [
            patch.object(tc, "db_save_land_sector", side_effect=lambda *a: self.opgeslagen.append(a)),
            patch.object(tc, "get_ticker_details", return_value={"long_name": "ASML HOLDING"}),
            patch.object(tc, "_sector_opzoektabel", return_value=opzoektabel()),
            patch.object(tc, "dprint"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def draai(self, ticker="ASML.AS", info=UITGEKLEED, cache=None, **kw):
        with patch.object(tc, "db_get_cached_land_sector", return_value=cache or {}), \
                patch.object(tc, "_fetch_yf_info", return_value=info) as yahoo:
            return tc.get_land_sector(ticker, **kw), yahoo


class TestGetLandSector(GeheugenSchoon):
    def test_uitgeklede_info_met_referentie_geeft_land_en_sector_en_slaat_op(self):
        (uit, _) = self.draai()
        self.assertEqual(uit, ("Netherlands", "Technology"))
        self.assertEqual(self.opgeslagen, [("ASML.AS", "Netherlands", "Technology")])

    def test_zonder_hit_maar_met_isin_geeft_land_uit_isin(self):
        with patch.object(tc, "get_ticker_details", return_value={"long_name": "Onbekend Bv"}):
            (uit, _) = self.draai("ONB.AS", isin="NL0000000001")
        self.assertEqual(uit, ("Netherlands", "Unknown"))
        self.assertEqual(self.opgeslagen, [("ONB.AS", "Netherlands", None)])

    def test_alles_onbekend_slaat_niets_op_en_onthoudt_in_geheugen(self):
        with patch.object(tc, "get_ticker_details", return_value={"long_name": "Onbekend Bv"}):
            (uit, yahoo) = self.draai("ONB.AS")
            self.assertEqual(uit, ("Unknown", "Unknown"))
            self.assertEqual(self.opgeslagen, [])
            tweede, yahoo2 = self.draai("ONB.AS")
        self.assertEqual(tweede, ("Unknown", "Unknown"))
        yahoo.assert_called_once()
        yahoo2.assert_not_called()

    def test_info_none_is_ook_geen_info(self):
        (uit, _) = self.draai(info=None)
        self.assertEqual(uit, ("Netherlands", "Technology"))

    def test_bruikbare_info_werkt_zoals_voorheen(self):
        (uit, _) = self.draai("SAP.DE", info=BRUIKBAAR)
        self.assertEqual(uit, ("Germany", "Industrials"))
        self.assertEqual(self.opgeslagen, [("SAP.DE", "Germany", "Industrials")])

    def test_naam_uit_aanroeper_gaat_voor_ticker_details(self):
        with patch.object(tc, "get_ticker_details") as details:
            (uit, _) = self.draai("XYZ", naam="asml holding")
        details.assert_not_called()
        self.assertEqual(uit, ("Netherlands", "Technology"))


class TestVervuildeCacheRij(GeheugenSchoon):
    def test_null_null_is_een_miss_en_wordt_overschreven(self):
        (uit, yahoo) = self.draai(cache={"ASML.AS": (None, None)})
        yahoo.assert_called_once()
        self.assertEqual(uit, ("Netherlands", "Technology"))
        self.assertEqual(self.opgeslagen, [("ASML.AS", "Netherlands", "Technology")])

    def test_tweede_aanroep_binnen_geheugentermijn_probeert_niet_opnieuw(self):
        vervuild = {"ONB.AS": (None, None)}
        with patch.object(tc, "get_ticker_details", return_value={}):
            _, yahoo = self.draai("ONB.AS", cache=vervuild)
            _, yahoo2 = self.draai("ONB.AS", cache=vervuild)
        yahoo.assert_called_once()
        yahoo2.assert_not_called()

    def test_gedeeltelijke_cache_rij_blijft_een_hit(self):
        (uit, yahoo) = self.draai(cache={"ASML.AS": ("Netherlands", None)})
        yahoo.assert_not_called()
        self.assertEqual(uit, ("Netherlands", "Unknown"))


class TestProfielAandeelZonderCache(GeheugenSchoon):
    def test_profiel_toont_land_en_sector_uit_de_referentie(self):
        details = {"is_etf": False, "long_name": "ASML HOLDING", "valuta": "EUR"}
        patches = [
            patch.object(effect_profiel, "get_ticker_details", return_value=details),
            patch.object(effect_profiel, "haal_koers", return_value=(700.0, "2026-10-28")),
            patch.object(effect_profiel, "verifieer_isin_bij_ticker", return_value=True),
            patch.object(effect_profiel, "get_land_sector", side_effect=tc.get_land_sector),
            patch("backend.effect_dividend.haal_dividend_profiel", return_value={"status": "onbekend"}),
            patch.object(tc, "db_get_cached_land_sector", return_value={}),
            patch.object(tc, "_fetch_yf_info", return_value=UITGEKLEED),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        profiel = effect_profiel.bouw_effect_profiel("ASML.AS", "NL0010273215")
        self.assertEqual((profiel["land"], profiel["sector"]), ({"Netherlands": 1.0}, {"Technology": 1.0}))


if __name__ == "__main__":
    unittest.main()
