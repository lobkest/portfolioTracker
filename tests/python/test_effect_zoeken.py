"""Zoeken naar effecten en ISIN-kandidaten: zonder database en zonder netwerk."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import effect_zoeken
from backend.effect_zoeken import (
    groepeer_noteringen, herken_isin, is_ucits_naam, isin_kandidaten, normaliseer_fondsnaam, zoek_effecten, zoek_effecten_gecachet,
    wis_zoek_cache, noteringen_voor_isin_zoekopdracht, bepaal_isin_kandidaten,
)

WERELD = "iShares Core MSCI World UCITS ETF USD (Acc)"


def quote(symbol, exchange, longname=WERELD, quote_type="ETF", shortname=None, exch_disp=None):
    q = {"symbol": symbol, "exchange": exchange, "quoteType": quote_type, "exchDisp": exch_disp or exchange}
    if longname is not None:
        q["longname"] = longname
    if shortname is not None:
        q["shortname"] = shortname
    return q


class TestGroeperen(unittest.TestCase):
    def test_zelfde_longname_en_type_wordt_een_resultaat(self):
        resultaten = groepeer_noteringen([quote("IWDA.AS", "AMS"), quote("EUNL.DE", "GER"), quote("IWDA.L", "LSE")])
        self.assertEqual(len(resultaten), 1)
        self.assertEqual([n["ticker"] for n in resultaten[0]["noteringen"]], ["IWDA.AS", "EUNL.DE", "IWDA.L"])
        self.assertEqual(resultaten[0]["noteringen"][0], {"ticker": "IWDA.AS", "beurs": "AMS", "beurs_naam": "AMS"})

    def test_ander_type_of_andere_naam_blijft_apart_en_volgorde_van_yahoo_blijft(self):
        resultaten = groepeer_noteringen([
            quote("ASML", "NMS", "ASML Holding N.V.", "EQUITY"),
            quote("ASMU", "NGM", "Direxion Daily ASML Bull 2X Shares", "ETF"),
            quote("ASML.AS", "AMS", "ASML Holding N.V.", "EQUITY"),
        ])
        self.assertEqual([r["naam"] for r in resultaten], ["ASML Holding N.V.", "Direxion Daily ASML Bull 2X Shares"])
        self.assertEqual([n["ticker"] for n in resultaten[0]["noteringen"]], ["ASML", "ASML.AS"])

    def test_andere_types_vallen_weg(self):
        resultaten = groepeer_noteringen([quote("^AEX", "AEX", "AEX Index", "INDEX"), quote("X", "AMS", "Fonds", "MUTUALFUND")])
        self.assertEqual(resultaten, [])

    def test_shortname_is_terugval_zonder_longname(self):
        resultaten = groepeer_noteringen([quote("ABC.AS", "AMS", longname=None, shortname="ABC FUND")])
        self.assertEqual(resultaten[0]["naam"], "ABC FUND")

    def test_dezelfde_ticker_wordt_niet_dubbel_toegevoegd(self):
        resultaten = groepeer_noteringen([quote("IWDA.AS", "AMS"), quote("IWDA.AS", "AMS")])
        self.assertEqual(len(resultaten[0]["noteringen"]), 1)

    def test_quote_zonder_symbol_of_naam_wordt_overgeslagen(self):
        self.assertEqual(groepeer_noteringen([{"quoteType": "ETF", "longname": "X"}, {"symbol": "A", "quoteType": "ETF"}]), [])


class TestVoorkeur(unittest.TestCase):
    def voorkeur(self, *quotes):
        return groepeer_noteringen(list(quotes))[0]["voorkeur_ticker"]

    def test_ams_wint_van_ger(self):
        self.assertEqual(self.voorkeur(quote("EUNL.DE", "GER"), quote("IWDA.AS", "AMS")), "IWDA.AS")

    def test_ger_wint_van_lse(self):
        self.assertEqual(self.voorkeur(quote("IWDA.L", "LSE"), quote("EUNL.DE", "GER")), "EUNL.DE")

    def test_zonder_voorkeursbeurs_de_eerste(self):
        self.assertEqual(self.voorkeur(quote("IWDA.L", "LSE"), quote("SWDA.MI", "MIL")), "IWDA.L")


class TestIsinHerkenning(unittest.TestCase):
    def test_isin_wordt_herkend(self):
        self.assertEqual(herken_isin("IE00B4L5Y983"), "IE00B4L5Y983")
        self.assertEqual(herken_isin("  ie00b4l5y983 "), "IE00B4L5Y983")

    def test_ticker_en_naam_zijn_geen_isin(self):
        self.assertIsNone(herken_isin("IWDA.AS"))
        self.assertIsNone(herken_isin("ASML"))
        self.assertIsNone(herken_isin("IE00B4L5Y98"))


class TestNaamnormalisatie(unittest.TestCase):
    def test_acc_dist_valuta_ucits_etf_en_leestekens_vallen_weg(self):
        a = normaliseer_fondsnaam("iShares Core MSCI World UCITS ETF USD (Acc)")
        self.assertEqual(a, normaliseer_fondsnaam("iShares Core MSCI World UCITS ETF"))
        self.assertEqual(a, normaliseer_fondsnaam("iShares Core MSCI World UCITS ETF EUR (Dist)"))
        self.assertEqual(a, "ishares core msci world")

    def test_ander_fonds_blijft_ongelijk(self):
        self.assertNotEqual(normaliseer_fondsnaam("iShares MSCI World UCITS ETF"),
                            normaliseer_fondsnaam("iShares MSCI World Small Cap UCITS ETF"))

    def test_none_geeft_lege_tekst(self):
        self.assertEqual(normaliseer_fondsnaam(None), "")


def ishares(isin, naam=WERELD):
    return {"isin": isin, "naam": naam}


def zoeker_met(per_isin):
    return lambda q: per_isin.get(q, [])


class TestIsUcitsNaam(unittest.TestCase):
    def test_woord_herkend_ongeacht_hoofdletters(self):
        self.assertTrue(is_ucits_naam(WERELD))
        self.assertTrue(is_ucits_naam("iShares msci world ucits etf"))

    def test_zonder_woord_of_als_onderdeel_van_ander_woord(self):
        self.assertFalse(is_ucits_naam("iShares MSCI Brazil ETF"))
        self.assertFalse(is_ucits_naam("iShares MUCITS ETF"))
        self.assertFalse(is_ucits_naam(None))


class TestIsinKandidaten(unittest.TestCase):
    def test_zoekopdracht_is_zeker(self):
        r = isin_kandidaten("IWDA.L", WERELD, "IE00B4L5Y983", ["XX"], [ishares("A"), ishares("B")], zoek=zoeker_met({}))
        self.assertEqual((r["zekerheid"], r["bron"]), ("zeker", "zoekopdracht"))
        self.assertEqual(r["kandidaten"], [{"isin": "IE00B4L5Y983", "naam": WERELD, "voorgeselecteerd": True}])

    def test_eigen_data_met_een_isin_is_zeker(self):
        r = isin_kandidaten("IWDA.AS", WERELD, None, ["IE00B4L5Y983", "IE00B4L5Y983"], [], zoek=zoeker_met({}))
        self.assertEqual((r["zekerheid"], r["bron"]), ("zeker", "eigen_data"))
        self.assertEqual([k["isin"] for k in r["kandidaten"]], ["IE00B4L5Y983"])

    def test_eigen_data_met_twee_isins_is_kiezen_zonder_voorselectie(self):
        r = isin_kandidaten("XELA", "Exela", None, ["US30162V4095", "US30162V1026"], [], zoek=zoeker_met({}))
        self.assertEqual((r["zekerheid"], r["bron"]), ("kiezen", "eigen_data"))
        self.assertEqual([k["isin"] for k in r["kandidaten"]], ["US30162V1026", "US30162V4095"])
        self.assertFalse(any(k["voorgeselecteerd"] for k in r["kandidaten"]))

    def test_ishares_met_een_kandidaat_is_zeker(self):
        r = isin_kandidaten("IWDA.AS", WERELD, None, [], [ishares("IE1"), ishares("IE2", "Ander fonds")], zoek=zoeker_met({}))
        self.assertEqual((r["zekerheid"], r["bron"]), ("zeker", "ishares_naam"))
        self.assertEqual([k["isin"] for k in r["kandidaten"]], ["IE1"])

    def test_ishares_met_drie_en_een_rootmatch_selecteert_die_voor(self):
        fondsen = [ishares("IE1"), ishares("IE2"), ishares("IE3")]
        zoek = zoeker_met({"IE1": [{"symbol": "SWDA.L"}], "IE2": [{"symbol": "IWDA.L"}], "IE3": []})
        r = isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen, zoek=zoek)
        self.assertEqual((r["zekerheid"], r["bron"]), ("kiezen", "ishares_naam"))
        self.assertEqual({k["isin"]: k["voorgeselecteerd"] for k in r["kandidaten"]},
                         {"IE1": False, "IE2": True, "IE3": False})

    def test_ishares_zonder_rootmatch_of_met_twee_selecteert_niets_voor(self):
        fondsen = [ishares("IE1"), ishares("IE2"), ishares("IE3")]
        for naam, per_isin in (("geen", {"IE1": [{"symbol": "AAA.L"}]}),
                               ("twee", {"IE1": [{"symbol": "IWDA.L"}], "IE2": [{"symbol": "IWDA.MI"}]})):
            with self.subTest(naam):
                r = isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen, zoek=zoeker_met(per_isin))
                self.assertEqual(r["zekerheid"], "kiezen")
                self.assertEqual(len(r["kandidaten"]), 3)
                self.assertFalse(any(k["voorgeselecteerd"] for k in r["kandidaten"]))

    def test_geen_kandidaten_is_geen(self):
        for fondsen in ([], None, [ishares("IE1", "Totaal ander fonds")]):
            with self.subTest(fondsen=fondsen):
                r = isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen, zoek=zoeker_met({}))
                self.assertEqual(r, {"zekerheid": "geen", "bron": None, "kandidaten": []})

    def test_amerikaanse_etf_koppelt_niet_aan_de_ierse_ucits_isin(self):
        fondsen = [ishares("IE00B0M63516", "iShares MSCI Brazil UCITS ETF USD (Dist)")]
        r = isin_kandidaten("EWZ", "iShares MSCI Brazil ETF", None, [], fondsen, zoek=zoeker_met({}))
        self.assertEqual(r, {"zekerheid": "geen", "bron": None, "kandidaten": []})

    def test_ucits_naam_vindt_nog_steeds_zijn_ishares_fonds(self):
        r = isin_kandidaten("IWDA.AS", WERELD, None, [], [ishares("IE1", WERELD)], zoek=zoeker_met({}))
        self.assertEqual((r["zekerheid"], [k["isin"] for k in r["kandidaten"]]), ("zeker", ["IE1"]))

    def test_acc_en_dist_ucits_geven_kiezen_met_voorselectie(self):
        fondsen = [ishares("IE1", WERELD), ishares("IE2", WERELD.replace("Acc", "Dist"))]
        zoek = zoeker_met({"IE1": [{"symbol": "IWDA.L"}], "IE2": []})
        r = isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen, zoek=zoek)
        self.assertEqual(r["zekerheid"], "kiezen")
        self.assertEqual({k["isin"]: k["voorgeselecteerd"] for k in r["kandidaten"]}, {"IE1": True, "IE2": False})

    def test_lege_naam_matcht_geen_ishares_fonds(self):
        r = isin_kandidaten("X", "", None, [], [ishares("IE1", "")], zoek=zoeker_met({}))
        self.assertEqual(r["zekerheid"], "geen")


class TestZoekEffecten(unittest.TestCase):
    def test_gewone_zoekopdracht_roept_yahoo_een_keer_aan(self):
        zoek = unittest.mock.Mock(return_value=[quote("IWDA.AS", "AMS")])
        resultaten = zoek_effecten("iShares Core MSCI World", zoek=zoek)
        zoek.assert_called_once_with("iShares Core MSCI World")
        self.assertEqual(resultaten[0]["voorkeur_ticker"], "IWDA.AS")

    def test_isin_zoekopdracht_voegt_noteringen_met_dezelfde_longname_toe(self):
        antwoorden = {
            "IE00B4L5Y983": [quote("IWDA.L", "LSE")],
            WERELD: [quote("IWDA.AS", "AMS"), quote("EUNL.DE", "GER"), quote("IWDA.L", "LSE"),
                     quote("IWDD.L", "LSE", "iShares Core MSCI World UCITS ETF USD (Dist)")],
        }
        resultaten = zoek_effecten("IE00B4L5Y983", zoek=lambda q: antwoorden[q])
        self.assertEqual(len(resultaten), 1)
        self.assertEqual([n["ticker"] for n in resultaten[0]["noteringen"]], ["IWDA.L", "IWDA.AS", "EUNL.DE"])
        self.assertEqual(resultaten[0]["voorkeur_ticker"], "IWDA.AS")

    def test_isin_zonder_resultaat_geeft_lege_lijst_zonder_tweede_call(self):
        zoek = unittest.mock.Mock(return_value=[])
        self.assertEqual(zoek_effecten("IE00B4L5Y983", zoek=zoek), [])
        self.assertEqual(zoek.call_count, 1)

    def test_noteringen_voor_isin_zoekopdracht_zonder_etf_of_equity_laat_quotes_staan(self):
        zoek = unittest.mock.Mock()
        quotes = [quote("X", "AMS", "Index", "INDEX")]
        self.assertEqual(noteringen_voor_isin_zoekopdracht(quotes, zoek), quotes)
        zoek.assert_not_called()


class TestCache(unittest.TestCase):
    def setUp(self):
        wis_zoek_cache()
        self.addCleanup(wis_zoek_cache)

    def test_tweede_zoekopdracht_binnen_de_cacheduur_gebruikt_de_cache(self):
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[quote("IWDA.AS", "AMS")]) as yahoo:
            zoek_effecten_gecachet("IWDA", klok=lambda: 0)
            zoek_effecten_gecachet("  iwda ", klok=lambda: effect_zoeken.ZOEK_CACHE_SECONDEN - 1)
        self.assertEqual(yahoo.call_count, 1)

    def test_na_de_cacheduur_wordt_opnieuw_gezocht(self):
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[quote("IWDA.AS", "AMS")]) as yahoo:
            zoek_effecten_gecachet("IWDA", klok=lambda: 0)
            zoek_effecten_gecachet("IWDA", klok=lambda: effect_zoeken.ZOEK_CACHE_SECONDEN)
        self.assertEqual(yahoo.call_count, 2)

    def test_lege_uitslag_wordt_niet_onthouden(self):
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[]) as yahoo:
            zoek_effecten_gecachet("IWDA", klok=lambda: 0)
            zoek_effecten_gecachet("IWDA", klok=lambda: 1)
        self.assertEqual(yahoo.call_count, 2)

    def test_cache_blijft_begrensd(self):
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[quote("A", "AMS")]), \
                patch.object(effect_zoeken, "ZOEK_CACHE_MAX_ITEMS", 3):
            for i in range(5):
                zoek_effecten_gecachet(f"q{i}", klok=lambda i=i: i)
        self.assertEqual(len(effect_zoeken._zoek_cache), 3)
        self.assertNotIn("q0", effect_zoeken._zoek_cache)


class TestVoorselectieViaNoteringen(unittest.TestCase):
    """Yahoo gemockt op zoekopdracht; isin_kandidaten gebruikt hier de standaardzoeker (met cache)."""
    ACC = "iShares Core S&P 500 UCITS ETF USD (Acc)"
    DIST = "iShares Core S&P 500 UCITS ETF USD (Dist)"

    def setUp(self):
        wis_zoek_cache()
        self.addCleanup(wis_zoek_cache)

    def draai(self, ticker, naam, fondsen, antwoorden):
        gezocht = []

        def yahoo(q):
            gezocht.append(q)
            return antwoorden.get(q, [])

        with patch.object(effect_zoeken, "_yahoo_search", side_effect=yahoo):
            r = isin_kandidaten(ticker, naam, None, [], fondsen)
        return r, gezocht

    def gekozen(self, r):
        return [k["isin"] for k in r["kandidaten"] if k["voorgeselecteerd"]]

    def test_sxr8_exacte_ticker_via_de_naam_kiest_alleen_de_juiste(self):
        fondsen = [ishares("A", "iShares Core S&P 500 UCITS ETF"), ishares("B", "iShares Core S&P 500 UCITS ETF"),
                   ishares("D", self.DIST)]
        antwoorden = {
            "A": [quote("CSSPX.MI", "MIL", self.ACC)],
            self.ACC: [quote("CSSPX.MI", "MIL", self.ACC), quote("SXR8.DE", "GER", self.ACC)],
            "B": [quote("GSPX.L", "LSE", "iShares Core S&P 500 UCITS ETF USD (Acc) B")],
            "D": [quote("IDUS.L", "LSE", self.DIST)],
            self.DIST: [quote("IDUS.L", "LSE", self.DIST), quote("IUSA.AS", "AMS", self.DIST)],
        }
        r, _ = self.draai("SXR8.DE", self.ACC, fondsen, antwoorden)
        self.assertEqual(r["zekerheid"], "kiezen")
        self.assertEqual(self.gekozen(r), ["A"])

    def test_emim_exacte_ticker_via_de_naam(self):
        naam = "iShares Core MSCI EM IMI UCITS ETF USD (Acc)"
        fondsen = [ishares("A", "iShares Core MSCI EM IMI UCITS ETF"), ishares("B", "iShares Core MSCI EM IMI UCITS ETF")]
        antwoorden = {
            "A": [quote("EIMI.L", "LSE", naam)],
            naam: [quote("EIMI.L", "LSE", naam), quote("EMIM.AS", "AMS", naam)],
            "B": [quote("EIMU.L", "LSE", "iShares Core MSCI EM IMI UCITS ETF USD (Dist)")],
        }
        r, _ = self.draai("EMIM.AS", naam, fondsen, antwoorden)
        self.assertEqual(self.gekozen(r), ["A"])

    def test_exacte_ticker_is_hoofdletterongevoelig(self):
        fondsen = [ishares("A"), ishares("B")]
        r, _ = self.draai("iwda.as", WERELD, fondsen, {"A": [quote("IWDA.AS", "AMS")], "B": []})
        self.assertEqual(self.gekozen(r), ["A"])

    def test_twee_exacte_matches_geven_geen_voorselectie_en_geen_root_terugval(self):
        fondsen = [ishares("A"), ishares("B"), ishares("C")]
        antwoorden = {"A": [quote("IWDA.AS", "AMS")], "B": [quote("IWDA.AS", "AMS")], "C": [quote("IWDA.L", "LSE")]}
        r, _ = self.draai("IWDA.AS", WERELD, fondsen, antwoorden)
        self.assertEqual(self.gekozen(r), [])

    def test_geen_exacte_match_maar_een_root_match_valt_terug_op_de_root(self):
        fondsen = [ishares("A"), ishares("B")]
        r, _ = self.draai("IWDA.AS", WERELD, fondsen, {"A": [quote("IWDA.L", "LSE", "Andere naam")], "B": [quote("XXXX.L", "LSE", "Nog een naam")]})
        self.assertEqual(self.gekozen(r), ["A"])

    def test_geen_exacte_en_geen_root_match_selecteert_niets(self):
        fondsen = [ishares("A"), ishares("B")]
        r, _ = self.draai("IWDA.AS", WERELD, fondsen, {"A": [quote("AAAA.L", "LSE", "N1")], "B": []})
        self.assertEqual(self.gekozen(r), [])

    def test_yahoo_exceptie_telt_als_geen_match(self):
        fondsen = [ishares("A"), ishares("B")]
        with patch.object(effect_zoeken, "_yahoo_search", side_effect=RuntimeError("weg")):
            r = isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen)
        self.assertEqual((r["zekerheid"], self.gekozen(r)), ("kiezen", []))

    def test_alleen_de_eerste_n_kandidaten_worden_opgezocht_maar_alle_staan_in_de_lijst(self):
        aantal = effect_zoeken.MAX_KANDIDATEN_VOORSELECTIE
        isins = [f"IE{i:010d}" for i in range(aantal + 3)]
        r, gezocht = self.draai("IWDA.AS", WERELD, [ishares(i) for i in isins], {})
        self.assertEqual([k["isin"] for k in r["kandidaten"]], isins)
        self.assertEqual(sorted(gezocht), sorted(isins[:aantal]))

    def test_tweede_voorselectie_doet_geen_nieuwe_yahoo_calls(self):
        fondsen = [ishares("A"), ishares("B")]
        # Lege uitslagen worden bewust niet onthouden, dus elke zoekopdracht geeft hier iets terug.
        antwoorden = {"A": [quote("IWDA.AS", "AMS")], "B": [quote("XXXX.L", "LSE", "Ander")], WERELD: [quote("IWDA.AS", "AMS")],
                      "Ander": [quote("XXXX.L", "LSE", "Ander")]}
        with patch.object(effect_zoeken, "_yahoo_search", side_effect=lambda q: antwoorden.get(q, [])) as yahoo:
            isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen)
            na_eerste = yahoo.call_count
            isin_kandidaten("IWDA.AS", WERELD, None, [], fondsen)
        self.assertEqual(yahoo.call_count, na_eerste)


class TestBepaalIsinKandidaten(unittest.TestCase):
    def test_bronfouten_tellen_als_niets(self):
        with patch.object(effect_zoeken, "db_get_isins_voor_ticker", side_effect=RuntimeError("db weg")), \
                patch.object(effect_zoeken, "ishares_fondsen", side_effect=RuntimeError("cache weg")):
            r = bepaal_isin_kandidaten("IWDA.AS", WERELD, "")
        self.assertEqual(r["zekerheid"], "geen")

    def test_isin_uit_de_aanvraag_wordt_gevalideerd(self):
        with patch.object(effect_zoeken, "db_get_isins_voor_ticker", return_value=[]), \
                patch.object(effect_zoeken, "ishares_fondsen", return_value=[]):
            self.assertEqual(bepaal_isin_kandidaten("T", "N", "IE00B4L5Y983")["bron"], "zoekopdracht")
            self.assertEqual(bepaal_isin_kandidaten("T", "N", "onzin")["zekerheid"], "geen")


if __name__ == "__main__":
    unittest.main()
