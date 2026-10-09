"""Diagnostiek: negatief aantal, verversing, andere beurs, ISIN-override, XIRR, dividend zonder positie en de
Data-checks bij 'niet opslaan'. Offline, geen database."""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie as po
from diagnostiek import CATEGORIE_DATA, CATEGORIE_DIVIDEND, GOED, INFO, LET_OP, haal_meldingen
from diagnostiek_checks import (
    ANDERE_BEURS_PRIJS_KLOPT, MAX_BEVINDINGEN_PER_CHECK, beurs_oordeel, check_dividend_zonder_positie,
    check_isin_override, check_negatief_aantal, check_ontbrekende_kolommen, check_verversing, check_xirr,
    ticker_bevindingen,
)
from statistieken import bereken_statistieken

M = datetime.time(0, 0)


def _rij(datum, aantal, isin="NL0000000001", product="ACME", beurs="EAM", ticker="ACM.AS", tijd=datetime.time(10, 0),
         koers=10.0, transactiekosten=-1.0, waarde_eur=-100.0):
    return {"datum": pd.Timestamp(datum), "tijd": tijd, "product": product, "echte_naam": product, "isin": isin,
            "beurs": beurs, "ticker": ticker, "aantal": float(aantal), "koers": koers,
            "transactiekosten": transactiekosten, "waarde_eur": waarde_eur, "totaal_eur": -aantal * koers}


def _wissel(datum, oud_aantal, nieuw_aantal):
    """ISIN-wissel zoals DeGiro hem boekt: tijd 00:00, geen kosten, koers > 0."""
    return (_rij(datum, -oud_aantal, isin="OLD", product="XELA OUD", ticker="XELA", tijd=M, transactiekosten=None),
            _rij(datum, nieuw_aantal, isin="NEW", product="XELA NIEUW", ticker="XELA", tijd=M, transactiekosten=None))


def _df(*rijen):
    return pd.DataFrame(list(rijen))


class TestNegatiefAantal(unittest.TestCase):
    def test_verkoop_zonder_aankoop(self):
        [b] = check_negatief_aantal(_df(_rij("2021-03-14", -5)))
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "data:negatief_aantal:NL0000000001"))
        self.assertEqual(b["tekst"], "ACME (NL0000000001): op 14-03-2021 worden 5 stuks verkocht terwijl je er volgens "
                                     "de transacties 0 had. Waarschijnlijk begint de export na je eerste aankoop: GAK, "
                                     "rendement en XIRR van deze positie kloppen niet. Exporteer vanaf de openingsdatum "
                                     "van je rekening.")

    def test_meer_verkocht_dan_gekocht(self):
        [b] = check_negatief_aantal(_df(_rij("2021-03-01", 3), _rij("2021-03-14", -5)))
        self.assertIn("worden 5 stuks verkocht terwijl je er volgens de transacties 3 had", b["tekst"])

    def test_volledige_verkoop_geeft_niets(self):
        self.assertEqual(check_negatief_aantal(_df(_rij("2021-03-01", 10), _rij("2021-03-14", -10))), [])

    def test_deelorders_en_volgorde_binnen_een_dag(self):
        # Verkoop staat vóór de aankoop in het bestand en heeft een eerder tijdstip: de eindstand van de dag telt.
        df = _df(_rij("2021-03-01", -4, tijd=datetime.time(9, 0)), _rij("2021-03-01", -6, tijd=datetime.time(9, 0)),
                 _rij("2021-03-01", 10, tijd=datetime.time(15, 0)))
        self.assertEqual(check_negatief_aantal(df), [])

    def test_isin_wissel_geen_vals_alarm(self):
        df = _df(_rij("2021-01-08", 100, isin="OLD", ticker="XELA"), *_wissel("2021-01-26", 100, 10),
                 _rij("2021-01-27", -10, isin="NEW", ticker="XELA"))
        self.assertEqual(check_negatief_aantal(df), [])

    def test_keten_telt_samen_en_meldt_op_eind_isin(self):
        df = _df(_rij("2021-01-08", 100, isin="OLD", ticker="XELA", product="XELA OUD"),
                 *_wissel("2021-01-26", 100, 10), _rij("2021-01-27", -12, isin="NEW", ticker="XELA", product="XELA NIEUW"))
        [b] = check_negatief_aantal(df)
        self.assertEqual(b["sleutel"], "data:negatief_aantal:NEW")
        self.assertIn("XELA NIEUW (NEW): op 27-01-2021 worden 12 stuks verkocht terwijl je er volgens de transacties "
                      "10 had", b["tekst"])

    def test_corporate_action_verkoop_tegen_nul_geen_vals_alarm(self):
        # Split 1:2 als corporate-action-rijen: oude stukken uit tegen 0, nieuwe in.
        df = _df(_rij("2021-01-01", 10),
                 _rij("2021-06-01", -10, beurs="DEG", product="ACME - NON TRADEABLE", ticker=None, koers=0.0, tijd=M),
                 _rij("2021-06-01", 20, beurs="DEG", product="ACME - NON TRADEABLE", ticker=None, koers=0.0, tijd=M),
                 _rij("2021-07-01", -20))
        self.assertEqual(check_negatief_aantal(df), [])

    def test_naam_van_gewone_rij_niet_van_corporate_action(self):
        df = _df(_rij("2021-06-01", -10, beurs="DEG", product="ACME - NON TRADEABLE", ticker=None, koers=0.0, tijd=M),
                 _rij("2021-07-01", 5))
        [b] = check_negatief_aantal(df)
        self.assertTrue(b["tekst"].startswith("ACME (NL0000000001): op 01-06-2021 worden 10 stuks"))

    def test_fractionele_ruis_geeft_niets(self):
        self.assertEqual(check_negatief_aantal(_df(_rij("2021-03-01", 0.3), _rij("2021-03-02", -0.1),
                                                   _rij("2021-03-03", -0.2))), [])

    def test_beperkt_tot_max(self):
        df = _df(*[_rij(f"2021-03-{i + 1:02d}", -1, isin=f"NL{i}") for i in range(MAX_BEVINDINGEN_PER_CHECK + 2)])
        uit = check_negatief_aantal(df)
        self.assertEqual(len(uit), MAX_BEVINDINGEN_PER_CHECK + 1)
        self.assertEqual(uit[0]["sleutel"], "data:negatief_aantal:NL0")
        self.assertEqual((uit[-1]["sleutel"], uit[-1]["tekst"]), ("data:negatief_aantal:meer", "... en 2 meer."))

    def test_lege_df(self):
        self.assertEqual(check_negatief_aantal(pd.DataFrame()), [])


class TestVerversing(unittest.TestCase):
    VRIJDAG = pd.Timestamp("2026-10-02")

    def test_meer_dan_drie_werkdagen(self):
        [b] = check_verversing(self.VRIJDAG, pd.Timestamp("2026-10-09"), True)
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "koersen:verversing"))
        self.assertEqual(b["tekst"], "De laatste koers is van 02-10-2026, 4 werkdagen geleden: het verversen bij Yahoo is "
                                     "waarschijnlijk mislukt. Open het portfolio later opnieuw.")

    def test_drie_werkdagen_en_weekend_nog_goed(self):
        self.assertEqual(check_verversing(self.VRIJDAG, pd.Timestamp("2026-10-08"), True), [])
        self.assertEqual(check_verversing(self.VRIJDAG, pd.Timestamp("2026-10-05"), True), [])

    def test_geen_open_posities(self):
        self.assertEqual(check_verversing(self.VRIJDAG, pd.Timestamp("2026-12-01"), False), [])

    def test_geen_koersdatum(self):
        self.assertEqual(check_verversing(None, pd.Timestamp("2026-12-01"), True), [])


class TestAndereBeurs(unittest.TestCase):
    KLOPT = [{"match": True, "binnen_dagrange": True, "afwijking_pct": 0.5}]
    KLOPT_NIET = [{"match": False, "binnen_dagrange": False, "afwijking_pct": 30.0}]

    def _df(self):
        return _df(_rij("2023-05-01", 10, isin="NL0000226223", product="STELLANTIS", ticker="STLAP.PA"))

    def _details(self):
        return {"STLAP.PA": {"valuta": "EUR", "quote_type": "EQUITY", "yahoo_beurs": "PAR"}}

    def test_oordeel(self):
        self.assertEqual(beurs_oordeel("EAM", "PAR", self.KLOPT), ANDERE_BEURS_PRIJS_KLOPT)
        self.assertEqual(beurs_oordeel("EAM", "PAR", self.KLOPT_NIET), "beurs")
        self.assertEqual(beurs_oordeel("EAM", "PAR", [{"match": None}]), "beurs")
        self.assertEqual(beurs_oordeel("EAM", "AMS", self.KLOPT), "zeker")

    def test_aparte_regel_en_geen_beurswaarschuwing_in_samenvatting(self):
        uit = ticker_bevindingen(self._df(), self._details(), {}, [], {"STLAP.PA": self.KLOPT})
        self.assertEqual([(b["niveau"], b["sleutel"]) for b in uit],
                         [(INFO, "tickers:andere_beurs:STLAP.PA"), (GOED, "tickers:samenvatting")])
        self.assertEqual(uit[0]["tekst"], "STELLANTIS (STLAP.PA): koers van een andere beurs (PAR) dan in Excel (EAM); de "
                                          "transactiekoersen kloppen.")
        self.assertIn("1 posities: 1 zeker, 0 onzeker (beurs niet te controleren), 0 met waarschuwing", uit[1]["tekst"])

    def test_prijs_klopt_niet_blijft_beurswaarschuwing(self):
        [samenvatting] = ticker_bevindingen(self._df(), self._details(), {}, [], {"STLAP.PA": self.KLOPT_NIET})
        self.assertIn("STLAP.PA: beurs", samenvatting["tekst"])


class TestIsinOverride(unittest.TestCase):
    def test_opgeslagen_ticker_gelijk_aan_override(self):
        [b] = check_isin_override({"BY6.MU": [("CNE100000296", "TDG")]}, {"BY6.MU": "BYD"})
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "tickers:override:BY6.MU"))
        self.assertEqual(b["tekst"], "BYD (BY6.MU): ticker vastgezet via een handmatige override voor (CNE100000296, TDG).")

    def test_andere_ticker_of_andere_beurs_geeft_niets(self):
        self.assertEqual(check_isin_override({"4BY1.F": [("CNE100000296", "TDG")]}, {}), [])
        self.assertEqual(check_isin_override({"BY6.MU": [("CNE100000296", "FRA")]}, {}), [])

    def test_via_ticker_bevindingen(self):
        df = _df(_rij("2023-05-01", 10, isin="IE00B3RBWM25", product="VANGUARD FTSE ALL-WORLD", ticker="VWRL.AS"))
        details = {"VWRL.AS": {"valuta": "EUR", "quote_type": "ETF", "yahoo_beurs": "AMS"}}
        sleutels = [b["sleutel"] for b in ticker_bevindingen(df, details, {}, [])]
        self.assertIn("tickers:override:VWRL.AS", sleutels)


class TestXirr(unittest.TestCase):
    def test_niet_berekend(self):
        [b] = check_xirr(True, None, 2.0, pd.Timestamp("2020-01-01"))
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "plausibel:xirr:niet_berekend"))
        self.assertIn("XIRR kon niet berekend worden", b["tekst"])

    def test_korte_periode(self):
        [b] = check_xirr(False, 31.5, 0.5, pd.Timestamp("2026-04-09"))
        self.assertEqual((b["niveau"], b["sleutel"]), (INFO, "plausibel:xirr:korte_periode"))
        self.assertEqual(b["tekst"], "Het portfolio loopt pas 6 maanden (sinds 09-04-2026): de XIRR (31,5%) is omgerekend "
                                     "naar een jaarrendement en vergroot het rendement van zo'n korte periode sterk uit.")

    def test_lang_genoeg_of_te_weinig_kasstromen_geeft_niets(self):
        self.assertEqual(check_xirr(False, 8.0, 1.0, pd.Timestamp("2020-01-01")), [])
        self.assertEqual(check_xirr(False, None, 0.5, pd.Timestamp("2026-04-09")), [])

    def _stats(self, eindwaarde):
        transacties = _df(_rij("2024-01-01", 10, ticker="TEST"))
        resultaat = pd.DataFrame({"waarde": [eindwaarde], "geinvesteerd": [100.0], "rendement": [eindwaarde - 100.0]},
                                 index=[pd.Timestamp("2024-12-01")])
        prijzen = pd.DataFrame({"TEST": [eindwaarde / 10]}, index=[pd.Timestamp("2024-12-01")])
        return bereken_statistieken(transacties, prijzen, resultaat)["geavanceerd"]

    def test_vlag_uit_bereken_statistieken(self):
        # Alleen een aankoop en eindwaarde 0: geen tekenwissel in de kasstromen, dus geen XIRR.
        self.assertTrue(self._stats(0.0)["xirr_niet_berekend"])
        geavanceerd = self._stats(110.0)
        self.assertFalse(geavanceerd["xirr_niet_berekend"])
        self.assertIsNotNone(geavanceerd["xirr_pct"])


class TestDividendZonderPositie(unittest.TestCase):
    def _dividend(self, isin, datum="2023-06-01", product="UNILEVER"):
        return {"isin": isin, "product": product, "datum": datetime.date.fromisoformat(datum)}

    def test_isin_zonder_transactie(self):
        dividenden = [self._dividend("GB00B10RZP78"), self._dividend("GB00B10RZP78", "2023-09-01"),
                      self._dividend("GB00B10RZP78", "2023-12-01")]
        [b] = check_dividend_zonder_positie(_df(_rij("2023-01-01", 10)), dividenden)
        self.assertEqual((b["niveau"], b["sleutel"]), (LET_OP, "dividend:zonder_positie:GB00B10RZP78"))
        self.assertEqual(b["tekst"], "Dividend van UNILEVER (GB00B10RZP78, 3 uitkering(en), 01-06-2023 t/m 01-12-2023) "
                                     "hoort bij geen enkele transactie: begint het transactiebestand later dan het "
                                     "rekeningoverzicht?")

    def test_dividend_na_verkoop_is_normaal(self):
        df = _df(_rij("2023-01-01", 10), _rij("2023-05-01", -10))
        self.assertEqual(check_dividend_zonder_positie(df, [self._dividend("NL0000000001", "2023-06-01")]), [])

    def test_isin_wissel_geen_vals_alarm(self):
        # Dividend op de oude ISIN; die staat alleen nog in de transacties via de keten.
        df = _df(_rij("2021-01-08", 100, isin="OLD", ticker="XELA"), *_wissel("2021-01-26", 100, 10))
        self.assertEqual(check_dividend_zonder_positie(df, [self._dividend("OLD"), self._dividend("NEW")]), [])

    def test_geen_dividenden_of_isin(self):
        self.assertEqual(check_dividend_zonder_positie(_df(_rij("2023-01-01", 10)), None), [])
        self.assertEqual(check_dividend_zonder_positie(_df(_rij("2023-01-01", 10)), [self._dividend(None)]), [])


class TestNietOpslaan(unittest.TestCase):
    def test_ontbrekende_kolommen_zonder_advies_om_opnieuw_te_uploaden(self):
        df = _df(_rij("2021-03-01", 10, tijd=None))
        [opgeslagen] = check_ontbrekende_kolommen(df)
        [niet_opgeslagen] = check_ontbrekende_kolommen(df, opgeslagen=False)
        self.assertIn("opnieuw te uploaden", opgeslagen["tekst"])
        self.assertEqual(niet_opgeslagen["tekst"], "1 transacties hebben geen 'tijd' (oudste: 01-03-2021). Dit raakt de "
                                                   "volgorde van transacties binnen een dag.")

    def test_analyze_transacties_draait_data_checks_zonder_database(self):
        df = _df(_rij("2021-03-01", 5, tijd=None), _rij("2021-03-14", -8))
        dividenden = [{"isin": "GB00B10RZP78", "product": "UNILEVER", "datum": datetime.date(2023, 6, 1)}]
        with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()), \
                patch.object(po, "db_get_order_id_rijen", side_effect=AssertionError("geen database")), \
                patch.object(po, "analyze_transacties_kern", return_value={"chart_data": None}):
            po.analyze_transacties(df, code=None, naam="Test", box3_dividenden=dividenden)
            meldingen = haal_meldingen()
        sleutels = {(m["categorie"], m["sleutel"]) for m in meldingen}
        self.assertIn((CATEGORIE_DATA, "data:negatief_aantal:NL0000000001"), sleutels)
        self.assertIn((CATEGORIE_DATA, "data:ontbrekend:tijd"), sleutels)
        self.assertIn((CATEGORIE_DIVIDEND, "dividend:zonder_positie:GB00B10RZP78"), sleutels)
        self.assertFalse(any(m["sleutel"].startswith("check_mislukt") for m in meldingen))


class TestMeldfunctiesBrekenNiet(unittest.TestCase):
    def test_fout_wordt_een_melding(self):
        with Flask(__name__).test_request_context():
            po._meld_xirr(pd.DataFrame(), {})
            po._meld_verversing(None, {})
            po._meld_dividend_zonder_positie(pd.DataFrame(), [{"isin": "X", "datum": "geen datum"}])
            sleutels = {m["sleutel"] for m in haal_meldingen()}
        self.assertEqual(sleutels, {"check_mislukt:XIRR", "check_mislukt:Verversing",
                                    "check_mislukt:Dividend zonder positie"})


if __name__ == "__main__":
    unittest.main()
