"""Beslisregel voor automatisch wisselen, Terugzetten, her-upload en de banner (zonder netwerk of database)."""
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

import pandas as pd

from backend import ticker_prijscheck, ticker_wissel, ticker_zekerheid, upload_verwerking
from backend.ticker_wissel import (
    SOORT_AUTO, SOORT_HANDMATIG, actieve_automatische_wissels, beslis_automatische_wissel, laatste_per_positie,
    mag_automatisch_wisselen, verwerk_automatische_wissels, zet_automatische_wissel_terug,
)


def _omgeving(yahoo_koers, high, low, splits, sprong):
    stack = ExitStack()
    for naam, waarde in {
        "db_get_cached_prijscheck": None, "_haal_koers_en_dagrange_op": (yahoo_koers, high, low),
        "_ticker_details_met_cache": {"valuta": "EUR"}, "_haal_splits_op": splits, "_haal_split_sprong": sprong,
    }.items():
        stack.enter_context(patch.object(ticker_prijscheck, naam, return_value=waarde))
    stack.enter_context(patch.object(ticker_prijscheck, "db_save_prijscheck"))
    return stack


def _check(binnen):
    return {"match": True, "niveau": "ok", "binnen_dagrange": binnen, "afwijking_pct": 0.0 if binnen else 9.0}


def _alt(ticker, beurs="AMS", valuta_gelijk=True, matches=3, gecontroleerd=3, **extra):
    return {"ticker": ticker, "beurs": beurs, "valuta": "EUR", "valuta_gelijk": valuta_gelijk,
            "aantal_matches": matches, "aantal_gecontroleerd": gecontroleerd, **extra}


def _resultaat(alternatieven, beurs_klopt=False, checks=None, excel_beurs="EAM"):
    return {"ticker": "OUD.DE", "excel_beurs": excel_beurs, "yahoo_beurs": "GER", "beurs_klopt": beurs_klopt,
            "prijs_checks": checks if checks is not None else [_check(True)] * 3, "alternatieven": alternatieven}


class TestBeslisregel(unittest.TestCase):
    def test_beurs_mismatch_en_een_goede_kandidaat_wisselt(self):
        besluit = beslis_automatische_wissel(_resultaat([_alt("NIEUW.AS")]), "EUR")
        self.assertEqual((besluit["van"], besluit["naar"]), ("OUD.DE", "NIEUW.AS"))
        self.assertIn("beurs GER past niet bij EAM", besluit["reden"])

    def test_prijs_buiten_range_zonder_beursprobleem_wisselt(self):
        r = _resultaat([_alt("NIEUW.AS")], beurs_klopt=True, checks=[_check(True), _check(False), _check(True)])
        self.assertEqual(beslis_automatische_wissel(r, "EUR")["naar"], "NIEUW.AS")

    def test_alles_in_orde_wisselt_niet(self):
        self.assertIsNone(beslis_automatische_wissel(_resultaat([_alt("NIEUW.AS")], beurs_klopt=True), "EUR"))

    def test_berkshire_eur_koers_usd_kandidaat_wisselt_niet(self):
        usd = {"ticker": "BRK-B", "beurs": "NYQ", "valuta": "USD", "valuta_gelijk": False, "prijscheck_nvt": True,
               "aantal_matches": 0, "aantal_gecontroleerd": 0}
        self.assertIsNone(beslis_automatische_wissel(_resultaat([usd], excel_beurs="NSY"), "EUR"))

    def test_twee_geschikte_kandidaten_wisselt_niet(self):
        self.assertIsNone(beslis_automatische_wissel(_resultaat([_alt("A.AS"), _alt("B.AS")]), "EUR"))

    def test_onbekende_koers_valuta_wisselt_niet(self):
        self.assertIsNone(beslis_automatische_wissel(_resultaat([_alt("NIEUW.AS")]), None))

    def test_kandidaat_met_misser_of_andere_beurs_telt_niet(self):
        alts = [_alt("A.AS", matches=2), _alt("B.PA", beurs="PAR"), _alt("C.AS", valuta_gelijk=None),
                _alt("D.AS", matches=0, gecontroleerd=0)]
        self.assertIsNone(beslis_automatische_wissel(_resultaat(alts), "EUR"))

    def test_een_goede_naast_een_foute_kandidaat_wisselt(self):
        alts = [_alt("A.AS", matches=1), _alt("B.AS")]
        self.assertEqual(beslis_automatische_wissel(_resultaat(alts), "EUR")["naar"], "B.AS")


def _info(ticker="OUD.DE"):
    return {"naam": "Fonds", "echte_naam": "Fonds", "beurs": "EAM", "isin": "IE1", "isins": ["IE1"], "ticker": ticker,
            "transacties": [{"datum": "2024-01-02", "koers": 10.0, "koers_valuta": "EUR"}]}


def _wissel(soort, oud="OUD.DE", nieuw="NIEUW.AS"):
    return {"id": 1, "isin": "IE1", "beurs": "EAM", "oude_ticker": oud, "nieuwe_ticker": nieuw, "soort": soort,
            "reden": "r"}


class TestVastleggenEnTerugzetten(unittest.TestCase):
    def test_eerdere_wissel_blokkeert_nieuwe_automatische_wissel(self):
        self.assertTrue(mag_automatisch_wisselen([]))
        self.assertFalse(mag_automatisch_wisselen([_wissel(SOORT_HANDMATIG)]))
        self.assertFalse(mag_automatisch_wisselen([_wissel(SOORT_AUTO)]))

    def test_terugzetten_herstelt_oude_ticker_als_handmatige_keuze(self):
        with patch.object(ticker_wissel, "pas_ticker_wissel_toe", return_value="Naam") as pas:
            uitkomst = zet_automatische_wissel_terug("ZZTEST", _info("NIEUW.AS"), [_wissel(SOORT_AUTO)])
        self.assertEqual(uitkomst, ("NIEUW.AS", "OUD.DE", "Naam"))
        self.assertEqual(pas.call_args.args[2:4], ("OUD.DE", SOORT_HANDMATIG))

    def test_na_terugzetten_blokkeert_de_handmatige_rij(self):
        wissels = [_wissel(SOORT_AUTO), _wissel(SOORT_HANDMATIG, oud="NIEUW.AS", nieuw="OUD.DE")]
        self.assertFalse(mag_automatisch_wisselen(wissels))
        self.assertEqual(laatste_per_positie(wissels)[("IE1", "EAM")]["soort"], SOORT_HANDMATIG)
        with patch.object(ticker_wissel, "pas_ticker_wissel_toe") as pas:
            self.assertIsNone(zet_automatische_wissel_terug("ZZTEST", _info("OUD.DE"), wissels))
        pas.assert_not_called()

    def test_handmatige_keuze_is_niet_terug_te_zetten(self):
        with patch.object(ticker_wissel, "pas_ticker_wissel_toe") as pas:
            self.assertIsNone(zet_automatische_wissel_terug("ZZTEST", _info("NIEUW.AS"), [_wissel(SOORT_HANDMATIG)]))
        pas.assert_not_called()

    def test_logregel_alleen_voor_actieve_automatische_wissel(self):
        groepen = [(("IE1", "EAM"), _info("NIEUW.AS"))]
        regels = actieve_automatische_wissels([_wissel(SOORT_AUTO)], groepen)
        self.assertEqual([(r["oude_ticker"], r["nieuwe_ticker"]) for r in regels], [("OUD.DE", "NIEUW.AS")])
        self.assertEqual(actieve_automatische_wissels([_wissel(SOORT_HANDMATIG)], groepen), [])


class TestAutomatischeWisselInBanner(unittest.TestCase):
    WAARSCHUWING = [{"ticker": "OUD.DE", "naam": "Fonds", "boodschap": "x", "redenen": ["koers"]}]

    def _verwerk(self, wissels, besluit):
        with patch.object(ticker_wissel, "db_get_ticker_wissels", return_value=wissels), \
             patch.object(ticker_wissel, "verifieer_ticker_met_prijs", return_value={}), \
             patch.object(ticker_wissel, "beslis_automatische_wissel", return_value=besluit), \
             patch.object(ticker_wissel, "pas_ticker_wissel_toe") as pas:
            ticker_wissel._zonder_wissel_beoordeeld.clear()
            uit = verwerk_automatische_wissels("ZZTEST", list(self.WAARSCHUWING), [(("IE1", "EAM"), _info())])
        return uit, pas

    def test_wissel_geeft_logregel_en_geen_banner(self):
        (waarschuwingen, aantal), pas = self._verwerk([], {"van": "OUD.DE", "naar": "NIEUW.AS", "reden": "r"})
        self.assertEqual((waarschuwingen, aantal), ([], 1))
        self.assertEqual(pas.call_args.args[2:5], ("NIEUW.AS", SOORT_AUTO, "r"))

    def test_zonder_besluit_blijft_de_banner(self):
        (waarschuwingen, aantal), pas = self._verwerk([], None)
        self.assertEqual((len(waarschuwingen), aantal), (1, 0))
        pas.assert_not_called()

    def test_handmatige_keuze_wordt_niet_overschreven(self):
        (waarschuwingen, aantal), pas = self._verwerk(
            [_wissel(SOORT_HANDMATIG)], {"van": "OUD.DE", "naar": "NIEUW.AS", "reden": "r"})
        self.assertEqual(aantal, 0)
        pas.assert_not_called()


class TestHerupload(unittest.TestCase):
    def test_handmatige_keuze_gaat_voor_bij_herupload(self):
        excel = pd.DataFrame({
            "Datum": pd.to_datetime(["2024-01-02"]), "Tijd": ["10:00"], "Product": ["Fonds"], "ISIN": ["IE1"],
            "Beurs": ["EAM"], "Aantal": [1], "_koers_eur": [10.0], upload_verwerking.KOSTEN_KOLOM: [-1.0],
        })

        def fake_check(posities, bekende_tickers):
            return [{"ticker": bekende_tickers.get((isin, beurs), "ZOEK")} for _, isin, beurs, _t in posities]

        with patch.object(upload_verwerking, "db_get_bekende_tickers", return_value={("IE1", "EAM"): "OUD.DE"}), \
             patch.object(upload_verwerking, "db_get_ticker_wissels", return_value=[_wissel(SOORT_HANDMATIG)]), \
             patch.object(upload_verwerking, "vind_tickers_met_snelle_prijscheck_parallel", side_effect=fake_check), \
             patch.object(upload_verwerking, "_meld_zoekstappen"):
            tickers = upload_verwerking._ticker_resolutie_opslaan(None, "ZZTEST", excel, True)
        self.assertEqual(tickers[("IE1", "EAM")], "NIEUW.AS")


class TestBannerOordeel(unittest.TestCase):
    def _tekst(self, uitkomsten):
        transacties = [{"datum": date(2024, m, 2), "koers": 10.0} for m in range(1, len(uitkomsten) + 1)]
        with patch.object(ticker_zekerheid, "vergelijk_prijzen_op_datums", return_value=[_check(u) for u in uitkomsten]):
            return ticker_zekerheid._koers_banner_tekst("T", transacties)

    def test_zeker_geeft_geen_banner(self):
        self.assertIsNone(self._tekst([True, True, True]))

    def test_alleen_een_opmerking_geeft_geen_banner(self):
        self.assertIsNone(self._tekst([True, False, True]))

    def test_onzeker_geeft_wel_banner(self):
        self.assertTrue(self._tekst([False, False, True]))

    def test_unilever_na_splitvariant_geeft_geen_banner(self):
        transacties = [{"datum": date(2025, 6, m), "koers": 54.48} for m in (2, 3, 4)]
        with _omgeving(55.66, 56.2, 54.8, {"2025-12-06": 0.888889}, (55.0, 55.2)):
            checks = [ticker_prijscheck.vergelijk_prijs_op_datum("UNA.AS", t["datum"], t["koers"]) for t in transacties]
        with patch.object(ticker_zekerheid, "vergelijk_prijzen_op_datums", return_value=checks):
            self.assertIsNone(ticker_zekerheid._koers_banner_tekst("UNA.AS", transacties))


if __name__ == "__main__":
    unittest.main()
