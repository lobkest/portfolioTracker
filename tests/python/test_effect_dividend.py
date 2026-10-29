"""Verwacht dividend van een gezocht effect: jaarbedrag, netto, yield, status en wat-als. Alles gemockt, geen netwerk of DB."""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_dividend
from backend.effect_dividend import dividend_profiel, dividend_voor_aantal, haal_dividend_profiel

PEIL = date(2026, 10, 29)
US_ISIN = "US0378331005"
IE_ISIN = "IE00B5BMR087"
VIER_UITKERINGEN = {"2025-12-01": 0.5, "2026-03-01": 0.5, "2026-06-01": 0.5, "2026-09-01": 0.5}


def yahoo(dividenden, dividend_rate=None, trailing_rate=None):
    return {"dividenden": dividenden, "dividend_rate": dividend_rate, "trailing_rate": trailing_rate, "info_mislukt": False}


def profiel(y, splits=None, valuta="EUR", isin=US_ISIN, koers=100.0, fx=1.0):
    return dividend_profiel(y, splits or {}, valuta, isin, koers, fx, PEIL)


class TestJaarBedrag(unittest.TestCase):
    def test_vol_jaar_met_vier_uitkeringen_geeft_bruto_netto_en_yield(self):
        p = profiel(yahoo(VIER_UITKERINGEN))
        self.assertEqual((p["per_stuk_bruto_eur"], p["per_stuk_netto_eur"]), (2.0, 1.7))
        self.assertAlmostEqual(p["yield_bruto"], 0.02)
        self.assertAlmostEqual(p["yield_netto"], 0.017)
        self.assertEqual((p["bron"], p["uitkeringen_12m"], p["laatste_ex_datum"], p["status"]),
                         ("yahoo_reeks", 4, "2026-09-01", "keert_uit"))
        self.assertEqual((p["belasting_fractie"], p["belasting_bron"]), (0.15, "land"))

    def test_info_mislukt_maar_reeks_werkt_dan_komt_het_bedrag_uit_de_reeks(self):
        y = dict(yahoo(VIER_UITKERINGEN), info_mislukt=True)
        p = profiel(y)
        self.assertEqual((p["per_stuk_bruto_eur"], p["bron"]), (2.0, "yahoo_reeks"))

    def test_geen_uitkering_in_twee_jaar_keert_niet_uit(self):
        p = profiel(yahoo({"2024-06-01": 0.5}))
        self.assertEqual((p["status"], p["bron"], p["per_stuk_bruto_eur"], p["yield_bruto"]), ("keert_niet_uit", "geen_uitkeringen", 0.0, 0.0))
        self.assertEqual(profiel(yahoo({}))["status"], "keert_niet_uit")

    def test_uitkering_voor_de_laatste_split_telt_niet_mee(self):
        dividenden = {"2026-03-01": 0.5, "2026-09-01": 0.5}
        p = profiel(yahoo(dividenden, dividend_rate=3.0), splits={"2026-04-01": 2.0})
        self.assertEqual(p["uitkeringen_12m"], 1)
        self.assertEqual((p["bron"], p["per_stuk_bruto_eur"]), ("dividend_rate", 3.0))

    def test_split_in_het_jaar_zonder_rates_is_onbekend(self):
        p = profiel(yahoo({"2026-03-01": 0.5, "2026-09-01": 0.5}), splits={"2026-04-01": 2.0})
        self.assertEqual((p["status"], p["per_stuk_bruto_eur"], p["per_stuk_netto_eur"], p["yield_bruto"]), ("onbekend", None, None, None))

    def test_gbp_pence_wordt_naar_euro_omgerekend(self):
        pence = {"2025-12-01": 25.0, "2026-03-01": 25.0, "2026-06-01": 25.0, "2026-09-01": 25.0}
        p = profiel(yahoo(pence), valuta="GBp", isin=None, koers=10.0, fx=1.15)
        self.assertAlmostEqual(p["per_stuk_bruto_eur"], 1.15)
        self.assertAlmostEqual(p["yield_bruto"], 0.115)

    def test_onbekende_wisselkoers_geeft_onbekend(self):
        self.assertEqual(profiel(yahoo(VIER_UITKERINGEN), valuta="USD", fx=None)["status"], "onbekend")


class TestBelasting(unittest.TestCase):
    def test_ierse_isin_geeft_geen_belasting(self):
        p = profiel(yahoo(VIER_UITKERINGEN), isin=IE_ISIN)
        self.assertEqual((p["belasting_fractie"], p["belasting_bron"], p["per_stuk_netto_eur"]), (0.0, "land", 2.0))

    def test_zonder_isin_geldt_de_standaardfractie(self):
        p = profiel(yahoo(VIER_UITKERINGEN), isin=None)
        self.assertEqual((p["belasting_fractie"], p["belasting_bron"], p["per_stuk_netto_eur"]), (0.15, "standaard", 1.7))


class TestOphalen(unittest.TestCase):
    def haal(self, **mocks):
        standaard = {"haal_yahoo_dividenden": dict(return_value=yahoo(VIER_UITKERINGEN)),
                     "_haal_splits_op": dict(return_value={}), "_fx_actueel": dict(return_value=1.0)}
        patchers = [patch.object(effect_dividend, naam, **{**standaard[naam], **mocks.get(naam, {})}) for naam in standaard]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        waarschuwingen = []
        return haal_dividend_profiel("ABC.AS", "EUR", US_ISIN, 100.0, waarschuwingen), waarschuwingen

    def test_gelukt_geeft_geen_waarschuwing(self):
        p, waarschuwingen = self.haal()
        self.assertEqual((p["status"], waarschuwingen), ("keert_uit", []))

    def test_none_van_yahoo_geeft_waarschuwing_en_onbekend(self):
        p, waarschuwingen = self.haal(haal_yahoo_dividenden=dict(return_value=None))
        self.assertEqual((p["status"], waarschuwingen), ("onbekend", ["geen_dividend_data"]))

    def test_exceptie_bij_ophalen_is_geen_crash(self):
        p, waarschuwingen = self.haal(haal_yahoo_dividenden=dict(side_effect=RuntimeError("boem")),
                                      _haal_splits_op=dict(side_effect=RuntimeError("boem")))
        self.assertEqual((p["status"], waarschuwingen), ("onbekend", ["geen_dividend_data"]))


class TestWatAls(unittest.TestCase):
    def test_aantal_maal_per_stuk(self):
        p = profiel(yahoo(VIER_UITKERINGEN))
        self.assertEqual(dividend_voor_aantal(p, 10), (20.0, 17.0))

    def test_onbekend_bedrag_geeft_none(self):
        p = profiel(None)
        self.assertEqual(dividend_voor_aantal(p, 10), (None, None))
        self.assertEqual(dividend_voor_aantal(None, 10), (None, None))

    def test_keert_niet_uit_geeft_nul(self):
        self.assertEqual(dividend_voor_aantal(profiel(yahoo({})), 10), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
