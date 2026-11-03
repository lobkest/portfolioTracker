"""Automatische wissel bij alleen een beurs-mismatch (Berkshire NSY/VIE) via verwerk -> verifieer -> beslis (geen netwerk of DB)."""
import unittest
from unittest.mock import patch

from backend import ticker_matching, ticker_wissel, ticker_zekerheid
from backend.ticker_wissel import verwerk_automatische_wissels

ISIN = "US0846707026"
ZOEKRESULTATEN = [
    {"symbol": "BRKB.VI", "exchange": "VIE"}, {"symbol": "BRK-B", "exchange": "NYQ"},
    {"symbol": "BRKB-USD", "exchange": "CCC"},
]
DETAILS = {"BRKB.VI": {"yahoo_beurs": "VIE", "valuta": "EUR"}, "BRK-B": {"yahoo_beurs": "NYQ", "valuta": "USD"},
           "BRKB-USD": {"yahoo_beurs": "CCC", "valuta": "USD"}, "ASML.AS": {"yahoo_beurs": "AMS", "valuta": "EUR"}}


def _check(ticker, datum, koers):
    return {"yahoo_koers": koers, "bekende_koers": koers, "match": True, "niveau": "ok", "binnen_dagrange": True,
            "afwijking_pct": 0.0, "valuta_omgerekend": "USD" if ticker == "BRK-B" else None, "high": koers, "low": koers}


def _info(ticker="BRKB.VI", beurs="NSY", isin=ISIN, wisselkoers=1.1685):
    return {"naam": "BERKSHIRE HATHAWAY INC", "echte_naam": "BERKSHIRE HATHAWAY INC", "beurs": beurs, "isin": isin,
            "isins": [isin], "ticker": ticker,
            "transacties": [{"datum": "2025-08-28", "koers": 426.55, "wisselkoers": wisselkoers, "koers_valuta": "EUR"}]}


GEZIEN = []


def _verifieer_en_onthoud(*args, **kwargs):
    GEZIEN.append(ticker_zekerheid.verifieer_ticker_met_prijs(*args, **kwargs))
    return GEZIEN[-1]


def _verwerk(info, wissels, waarschuwingen=()):
    GEZIEN.clear()
    with patch.object(ticker_wissel, "db_get_ticker_wissels", return_value=wissels), \
         patch.object(ticker_wissel, "_ticker_details_met_cache", side_effect=lambda t: DETAILS[t]), \
         patch.object(ticker_zekerheid, "_ticker_details_met_cache", side_effect=lambda t: DETAILS[t]), \
         patch.object(ticker_zekerheid, "_yahoo_search", return_value=ZOEKRESULTATEN), \
         patch.object(ticker_zekerheid, "haal_openfigi_resultaten", return_value={"resultaten": [], "fout": None}), \
         patch.object(ticker_zekerheid, "vergelijk_prijs_op_datum",
                      side_effect=lambda t, d, k: _check(t, d, k)), \
         patch.object(ticker_zekerheid, "vergelijk_prijzen_op_datums",
                      side_effect=lambda t, ts: [_check(t, x["datum"], float(x["koers"])) for x in ts]), \
         patch.object(ticker_zekerheid, "classify_ticker", return_value=False), \
         patch.object(ticker_zekerheid, "_land_sector_voor_weergave", return_value=("US", "Financials", None)), \
         patch.object(ticker_wissel, "verifieer_ticker_met_prijs",
                      side_effect=_verifieer_en_onthoud) as verifieer, \
         patch.object(ticker_wissel, "pas_ticker_wissel_toe") as pas:
        ticker_wissel._zonder_wissel_beoordeeld.clear()
        uit = verwerk_automatische_wissels("ABC", list(waarschuwingen), [((info["isin"], info["beurs"]), info)])
    return uit, verifieer, pas


class TestWisselBijBeursMismatch(unittest.TestCase):
    def test_mismatch_zonder_koerswaarschuwing_wisselt_naar_brk_b(self):
        (_waarschuwingen, aantal), _verifieer, pas = _verwerk(_info(), [])
        self.assertEqual(aantal, 1)
        self.assertEqual(pas.call_args.args[2:4], ("BRK-B", "auto"))

    def test_handmatige_rij_blokkeert_de_wissel(self):
        handmatig = [{"isin": ISIN, "beurs": "NSY", "oude_ticker": "BRK-B", "nieuwe_ticker": "BRKB.VI",
                      "soort": "handmatig", "reden": None}]
        (_w, aantal), verifieer, pas = _verwerk(_info(), handmatig)
        self.assertEqual(aantal, 0)
        verifieer.assert_not_called()
        pas.assert_not_called()

    def test_eur_aandeel_zonder_mismatch_roept_de_beslisregel_niet_aan(self):
        info = _info(ticker="ASML.AS", beurs="EAM", isin="NL0010273215", wisselkoers=None)
        with patch.object(ticker_wissel, "beslis_automatische_wissel") as beslis:
            (_w, aantal), verifieer, pas = _verwerk(info, [])
        self.assertEqual(aantal, 0)
        verifieer.assert_not_called()
        beslis.assert_not_called()
        pas.assert_not_called()

    def test_alternatieven_tonen_excel_koers_en_valuta_per_datum(self):
        _verwerk(_info(), [])
        brk_b = next(a for a in GEZIEN[0]["alternatieven"] if a["ticker"] == "BRK-B")
        self.assertEqual(brk_b["vergelijkingen"][0]["excel_koers_eur"], 426.55)
        self.assertEqual(brk_b["vergelijkingen"][0]["yahoo_valuta"], "USD")


class TestLuieWisselIndex(unittest.TestCase):
    def _draai(self, groepen, waarschuwingen=()):
        with patch.object(ticker_wissel, "db_get_ticker_wissels", return_value=[]) as db,              patch.object(ticker_wissel, "_ticker_details_met_cache", side_effect=lambda t: DETAILS[t]),              patch.object(ticker_wissel, "_probeer_wissel_voor_positie", return_value=False):
            uit = verwerk_automatische_wissels("ABC", list(waarschuwingen), groepen)
        return uit, db

    def test_geen_kandidaten_geen_db_aanroep(self):
        info = _info(ticker="ASML.AS", beurs="EAM", isin="NL0010273215", wisselkoers=None)
        (_w, aantal), db = self._draai([(("NL0010273215", "EAM"), info)])
        self.assertEqual(aantal, 0)
        db.assert_not_called()

    def test_twee_kandidaten_een_db_aanroep(self):
        a, b = _info(), _info(isin="US0000000002")
        _uit, db = self._draai([((a["isin"], "NSY"), a), ((b["isin"], "NSY"), b)])
        db.assert_called_once_with("ABC")


class TestEersteKoppelingBerkshire(unittest.TestCase):
    def test_zoeken_kiest_brk_b_en_niet_brkb_vi(self):
        with patch.object(ticker_matching, "_yahoo_search", return_value=ZOEKRESULTATEN), \
             patch.object(ticker_matching, "db_get_ticker_details", return_value={}):
            resultaat = ticker_matching.find_ticker_detailed("BERKSHIRE HATHAWAY INC", ISIN, "NSY", in_eur=False)
        self.assertEqual((resultaat["ticker"], resultaat["zekerheid"]), ("BRK-B", "zeker"))

    def test_zonder_valuta_oordeel_wint_de_eerste_beursmatch_niet_vie(self):
        with patch.object(ticker_matching, "_yahoo_search", return_value=ZOEKRESULTATEN), \
             patch.object(ticker_matching, "db_get_ticker_details", return_value={}):
            resultaat = ticker_matching.find_ticker_detailed("BERKSHIRE HATHAWAY INC", ISIN, "NSY", in_eur=None)
        self.assertEqual(resultaat["ticker"], "BRK-B")


if __name__ == "__main__":
    unittest.main()
