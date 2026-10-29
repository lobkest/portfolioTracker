"""Alternatieven op een thuisbeurs (fase A): pure kern, invoer uit de transacties en de twee endpoints.
Verzonnen data, offline: Yahoo en de database zijn gemockt."""
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import beurs_alternatieven as ba
import db
import portfolio_orchestratie as po
from aansluitingskosten import (
    THUISBEURZEN, bouw_aansluitingskosten_samenvatting, bouw_alternatieven_invoer, voeg_beurs_resultaat_toe)
from contextlib import ExitStack
from dividend import ORDER_ID_KOLOM_REKENING, bouw_rekening_regels
from beurs_alternatieven import (
    FOUT_ALGEMEEN, STATUS_FOUT, STATUS_GEVONDEN, STATUS_NIET_GEVONDEN, STATUS_WAARSCHUWING, beoordeel_positie,
    bouw_beurs_alternatieven, haal_alternatieven, onderzoek_isin, thuisnoteringen, valideer_alternatieven_invoer,
    valideer_beurscode, wis_onderzoek_cache)

ISIN_A = "IE00B4L5Y983"
ISIN_B = "US0378331005"


def notering(ticker, beurs_code, koers, beurs="AMS", naam="Euronext Amsterdam"):
    return {"ticker": ticker, "beurs": beurs, "beurs_code": beurs_code, "beurs_naam": naam, "koers_eur": koers}


def positie(ticker="IWDA.DE", isin=ISIN_A, aantal=10.0, waarde=1000.0):
    return {"ticker": ticker, "naam": ticker, "isin": isin, "aantal": aantal, "waarde_eur": waarde}


def invoer(posities=None, verkoop=4.0, thuis=None, isins_thuis=(), jaarkosten=2.5):
    return {
        "beurs": "TDG", "beurs_naam": "Tradegate", "posities": posities if posities is not None else [positie()],
        "jaarkosten_eur": jaarkosten, "betaald_dit_jaar": True,
        "gemiddelde_kosten_per_transactie_eur": verkoop,
        "thuisbeurs_kosten_per_transactie_eur": thuis if thuis is not None else {"EAM": 2.0, "EBR": 3.0},
        "isins_op_thuisbeurs": list(isins_thuis),
    }


def onderzoek(noteringen, is_etf=True):
    return {"is_etf": is_etf, "noteringen": noteringen}


def quote(symbol, exchange, quote_type="ETF"):
    return {"symbol": symbol, "exchange": exchange, "quoteType": quote_type, "longname": "Fonds"}


class TestThuisnoteringen(unittest.TestCase):
    def test_alleen_thuisbeurzen_ams_voor_bru(self):
        quotes = [quote("X.DE", "GER"), quote("X.BR", "BRU"), quote("X.AS", "AMS"), quote("X.L", "LSE")]
        is_etf, gevonden = thuisnoteringen(quotes)
        self.assertTrue(is_etf)
        self.assertEqual([(n["ticker"], n["beurs_code"]) for n in gevonden], [("X.AS", "EAM"), ("X.BR", "EBR")])

    def test_aandeel_is_geen_etf(self):
        is_etf, gevonden = thuisnoteringen([quote("AAPL", "NMS", "EQUITY")])
        self.assertFalse(is_etf)
        self.assertEqual(gevonden, [])

    def test_thuisbeurzen_op_een_plek(self):
        self.assertEqual([t["code"] for t in THUISBEURZEN], ["EAM", "EBR"])
        self.assertEqual([t["yahoo"] for t in THUISBEURZEN], ["AMS", "BRU"])


class TestBeoordeelPositie(unittest.TestCase):
    def test_etf_op_ams_en_bru_kiest_ams(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.BR", "EBR", 100.0, "BRU"), notering("X.AS", "EAM", 100.5)]),
                              set())
        # De volgorde van de noteringen bepaalt de voorkeur; thuisnoteringen() levert AMS vóór BRU.
        self.assertEqual(r["status"], STATUS_GEVONDEN)
        _, gevonden = thuisnoteringen([quote("X.BR", "BRU"), quote("X.AS", "AMS")])
        r = beoordeel_positie(positie(), onderzoek([dict(n, koers_eur=100.0) for n in gevonden]), set())
        self.assertEqual(r["voorkeur"]["ticker"], "X.AS")

    def test_koersafwijking_boven_5_procent_is_waarschuwing_zonder_voorkeur(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.AS", "EAM", 106.0)]), set())
        self.assertEqual((r["status"], r["voorkeur"], r["waarschuwing"]), (STATUS_WAARSCHUWING, None, "koers wijkt af"))
        self.assertEqual(r["noteringen"][0]["afwijking_pct"], 6.0)

    def test_afwijking_precies_binnen_de_marge_is_goed(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.AS", "EAM", 104.9)]), set())
        self.assertEqual(r["status"], STATUS_GEVONDEN)

    def test_notering_alleen_via_naam_krijgt_waarschuwing_maar_blijft_bruikbaar(self):
        r = beoordeel_positie(positie(), onderzoek([dict(notering("X.AS", "EAM", 100.0), via_naam=True)]), set())
        self.assertEqual(r["status"], STATUS_GEVONDEN)
        self.assertEqual(r["voorkeur"]["waarschuwing"], "niet_op_isin_bevestigd")

    def test_notering_via_isin_krijgt_die_waarschuwing_niet(self):
        r = beoordeel_positie(positie(), onderzoek([dict(notering("X.AS", "EAM", 100.0), via_naam=False)]), set())
        self.assertIsNone(r["voorkeur"]["waarschuwing"])

    def test_isin_route_wint_van_naam_route(self):
        naam_ams = dict(notering("X.AS", "EAM", 100.0), via_naam=True)
        isin_bru = dict(notering("Y.BR", "EBR", 100.0, "BRU"), via_naam=False)
        r = beoordeel_positie(positie(), onderzoek([naam_ams, isin_bru]), set())
        self.assertEqual(r["voorkeur"]["ticker"], "Y.BR")

    def test_naam_route_met_koersafwijking_is_geen_voorkeur(self):
        r = beoordeel_positie(positie(), onderzoek([dict(notering("X.AS", "EAM", 130.0), via_naam=True)]), set())
        self.assertEqual((r["status"], r["waarschuwing"]), (STATUS_WAARSCHUWING, "koers wijkt af"))

    def test_afwijkende_ams_dan_goede_bru(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.AS", "EAM", 120.0), notering("X.BR", "EBR", 100.0, "BRU")]),
                              set())
        self.assertEqual(r["voorkeur"]["ticker"], "X.BR")

    def test_zonder_koers_geen_voorkeur(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.AS", "EAM", None)]), set())
        self.assertEqual((r["status"], r["waarschuwing"]), (STATUS_WAARSCHUWING, "geen koers om mee te vergelijken"))

    def test_aandeel_zonder_thuisnotering_geen_fase_b(self):
        r = beoordeel_positie(positie("AAPL", ISIN_B), onderzoek([], is_etf=False), set())
        self.assertEqual((r["status"], r["fase_b_mogelijk"]), (STATUS_NIET_GEVONDEN, False))

    def test_etf_zonder_thuisnotering_fase_b_mogelijk(self):
        r = beoordeel_positie(positie(), onderzoek([], is_etf=True), set())
        self.assertEqual((r["status"], r["fase_b_mogelijk"]), (STATUS_NIET_GEVONDEN, True))

    def test_etf_met_thuisnotering_heeft_ook_fase_b(self):
        r = beoordeel_positie(positie(), onderzoek([notering("X.AS", "EAM", 100.0)], is_etf=True), set())
        self.assertEqual((r["status"], r["fase_b_mogelijk"]), (STATUS_GEVONDEN, True))
        r = beoordeel_positie(positie("AAPL", ISIN_B), onderzoek([notering("X.AS", "EAM", 100.0)], is_etf=False), set())
        self.assertFalse(r["fase_b_mogelijk"])

    def test_al_in_bezit_op_thuisbeurs(self):
        n = onderzoek([notering("X.AS", "EAM", 100.0)])
        self.assertTrue(beoordeel_positie(positie(), n, {ISIN_A})["al_in_bezit_op_thuisbeurs"])
        self.assertFalse(beoordeel_positie(positie(), n, {ISIN_B})["al_in_bezit_op_thuisbeurs"])

    def test_fout_per_positie(self):
        r = beoordeel_positie(positie(), {"fout": "kapot"}, set())
        self.assertEqual((r["status"], r["fout"]), (STATUS_FOUT, "kapot"))


class TestBeurs(unittest.TestCase):
    def test_alles_kan_over_besparing_overstapkosten_terugverdientijd(self):
        antwoord = bouw_beurs_alternatieven(invoer(), {ISIN_A: onderzoek([notering("X.AS", "EAM", 100.0)])})
        self.assertFalse(antwoord["blijft_betalen"])
        self.assertEqual(antwoord["besparing_per_jaar_eur"], 2.5)
        # Verkopen op de huidige beurs (4,00) + kopen op Amsterdam (2,00).
        self.assertEqual(antwoord["overstapkosten_schatting_eur"], 6.0)
        self.assertEqual(antwoord["terugverdientijd_jaren"], 2.4)
        self.assertTrue(antwoord["betaald_dit_jaar"])

    def test_een_positie_zonder_alternatief_blijft_betalen(self):
        i = invoer([positie("A", ISIN_A), positie("B", ISIN_B, waarde=500.0, aantal=5.0)])
        antwoord = bouw_beurs_alternatieven(i, {ISIN_A: onderzoek([notering("X.AS", "EAM", 100.0)]),
                                                ISIN_B: onderzoek([], is_etf=False)})
        self.assertTrue(antwoord["blijft_betalen"])
        self.assertEqual(antwoord["besparing_per_jaar_eur"], 0)
        self.assertIsNone(antwoord["terugverdientijd_jaren"])

    def test_overstapkosten_per_positie_en_thuisbeurs(self):
        i = invoer([positie("A", ISIN_A), positie("B", ISIN_B)], thuis={"EAM": 2.0, "EBR": 3.0})
        antwoord = bouw_beurs_alternatieven(i, {ISIN_A: onderzoek([notering("X.AS", "EAM", 100.0)]),
                                                ISIN_B: onderzoek([notering("Y.BR", "EBR", 100.0, "BRU")])})
        self.assertEqual(antwoord["overstapkosten_schatting_eur"], (4.0 + 2.0) + (4.0 + 3.0))

    def test_zonder_eigen_transacties_onbekend(self):
        n = {ISIN_A: onderzoek([notering("X.AS", "EAM", 100.0)])}
        for i in (invoer(verkoop=None), invoer(thuis={"EAM": None, "EBR": None})):
            antwoord = bouw_beurs_alternatieven(i, n)
            self.assertIsNone(antwoord["overstapkosten_schatting_eur"])
            self.assertIsNone(antwoord["terugverdientijd_jaren"])
            self.assertEqual(antwoord["besparing_per_jaar_eur"], 2.5)

    def test_zonder_jaarkosten_geen_besparing_en_geen_verzonnen_bedrag(self):
        antwoord = bouw_beurs_alternatieven(invoer(jaarkosten=None), {ISIN_A: onderzoek([notering("X.AS", "EAM", 100.0)])})
        self.assertIsNone(antwoord["besparing_per_jaar_eur"])
        self.assertIsNone(antwoord["terugverdientijd_jaren"])

    def test_positie_zonder_isin_is_een_fout_en_blijft_betalen(self):
        antwoord = bouw_beurs_alternatieven(invoer([positie(isin=None)]), {})
        self.assertEqual(antwoord["posities"][0]["status"], STATUS_FOUT)
        self.assertTrue(antwoord["blijft_betalen"])


class TestNetwerklaag(unittest.TestCase):
    def setUp(self):
        wis_onderzoek_cache()
        self.addCleanup(wis_onderzoek_cache)
        p = patch.object(ba, "db_get_ticker_details", return_value={})
        self.details = p.start()
        self.addCleanup(p.stop)

    def test_onderzoek_haalt_noteringen_en_koersen_en_onthoudt_per_isin(self):
        quotes = [quote("X.DE", "GER"), quote("X.AS", "AMS")]
        with patch.object(ba, "zoek_gecachet", return_value=quotes) as zoek, \
                patch.object(ba, "noteringen_voor_isin_zoekopdracht", side_effect=lambda q, z: q), \
                patch.object(ba, "haal_koers", return_value=(101.0, "2026-10-27")) as koers:
            eerste = onderzoek_isin(ISIN_A)
            tweede = onderzoek_isin(ISIN_A)
        self.assertEqual(eerste["noteringen"][0]["koers_eur"], 101.0)
        self.assertEqual(eerste, tweede)
        self.assertEqual((zoek.call_count, koers.call_count), (1, 1))

    def test_lege_zoekuitslag_is_een_fout_en_wordt_niet_onthouden(self):
        with patch.object(ba, "zoek_gecachet", return_value=[]) as zoek:
            self.assertIn("fout", onderzoek_isin(ISIN_A))
            onderzoek_isin(ISIN_A)
        self.assertEqual(zoek.call_count, 2)

    def test_yahoo_fout_bij_een_positie_laat_de_rest_werken(self):
        def onderzoeker(isin, naam=None):
            return {"fout": FOUT_ALGEMEEN} if isin == ISIN_B else onderzoek([notering("X.AS", "EAM", 100.0)])
        antwoord = haal_alternatieven(invoer([positie("A", ISIN_A), positie("B", ISIN_B)]), onderzoeker=onderzoeker)
        self.assertEqual([p["status"] for p in antwoord["posities"]], [STATUS_GEVONDEN, STATUS_FOUT])
        self.assertTrue(antwoord["blijft_betalen"])

    def test_alleen_mutualfund_zonder_longname_zoekt_op_productnaam(self):
        stuttgart = [{"symbol": "IE0005B8WVT6.SG", "exchange": "STU", "quoteType": "MUTUALFUND", "shortname": "VanEck"}]
        op_naam = [dict(quote("TRET.AS", "AMS"), longname="VanEck Global Real Estate UCITS ETF"),
                   dict(quote("TRET.L", "LSE"), longname="VanEck Global Real Estate UCITS ETF"),
                   dict(quote("ANDER.AS", "AMS"), longname="Heel Ander Fonds Europe"),
                   dict(quote("AAPL", "NMS", "EQUITY"), longname="VanEck Global Real Estate Inc")]
        zoekopdrachten = []

        def zoek(query):
            zoekopdrachten.append(query)
            return stuttgart if query == ISIN_A else op_naam
        with patch.object(ba, "zoek_gecachet", side_effect=zoek), \
                patch.object(ba, "haal_koers", return_value=(100.0, "2026-10-27")):
            resultaat = onderzoek_isin(ISIN_A, "VANECK GLOBAL REAL ESTATE UCITS ETF")
        self.assertEqual(zoekopdrachten, [ISIN_A, "VANECK GLOBAL REAL ESTATE UCITS ETF"])
        self.assertEqual([n["ticker"] for n in resultaat["noteringen"]], ["TRET.AS"])
        self.assertTrue(resultaat["noteringen"][0]["via_naam"])

    def test_isin_route_markeert_niet_als_via_naam(self):
        quotes = [quote("X.DE", "GER"), quote("X.AS", "AMS")]
        with patch.object(ba, "zoek_gecachet", return_value=quotes), \
                patch.object(ba, "noteringen_voor_isin_zoekopdracht", side_effect=lambda q, z: q), \
                patch.object(ba, "haal_koers", return_value=(100.0, "2026-10-27")):
            resultaat = onderzoek_isin(ISIN_A, "Fonds")
        self.assertFalse(resultaat["noteringen"][0]["via_naam"])

    def test_zonder_productnaam_geen_tweede_zoekopdracht(self):
        stuttgart = [{"symbol": "X.SG", "exchange": "STU", "quoteType": "MUTUALFUND"}]
        with patch.object(ba, "zoek_gecachet", return_value=stuttgart) as zoek:
            resultaat = onderzoek_isin(ISIN_A)
        self.assertEqual(zoek.call_count, 1)
        self.assertEqual(resultaat["noteringen"], [])

    def test_naamzoekopdracht_houdt_alleen_dezelfde_familie_en_naam(self):
        naam_quotes = [dict(quote("A.AS", "AMS", "EQUITY"), longname="Global Real Estate Corp"),
                       dict(quote("B.AS", "AMS"), longname="Global Real Estate UCITS ETF"),
                       dict(quote("B.BR", "BRU"), longname="Global Real Estate UCITS ETF"),
                       dict(quote("C.AS", "AMS"), longname="Real Estate Dividend UCITS ETF")]
        isin_quotes = [{"symbol": "X.SG", "exchange": "STU", "quoteType": "MUTUALFUND"}]
        gevonden = ba.quotes_via_productnaam(isin_quotes, naam_quotes, "Global Real Estate UCITS ETF")
        self.assertEqual([q["symbol"] for q in gevonden], ["B.AS", "B.BR"])
        self.assertEqual(ba.quotes_via_productnaam(isin_quotes, naam_quotes, "Enkel"), [])

    def test_is_etf_uit_classificatie_gaat_voor_op_zoekuitslag(self):
        zoek_zegt_etf = {"is_etf": True, "noteringen": []}
        self.details.return_value = {"IWDA.DE": {"is_etf": False}}
        antwoord = haal_alternatieven(invoer(), onderzoeker=lambda isin, naam=None: zoek_zegt_etf)
        self.assertFalse(antwoord["posities"][0]["fase_b_mogelijk"])
        self.details.return_value = {"IWDA.DE": {"is_etf": True}}
        antwoord = haal_alternatieven(invoer(), onderzoeker=lambda isin, naam=None: {"is_etf": False, "noteringen": []})
        self.assertTrue(antwoord["posities"][0]["fase_b_mogelijk"])

    def test_is_etf_valt_terug_op_zoekuitslag_zonder_classificatie(self):
        antwoord = haal_alternatieven(invoer(), onderzoeker=lambda isin, naam=None: {"is_etf": True, "noteringen": []})
        self.assertTrue(antwoord["posities"][0]["fase_b_mogelijk"])
        self.details.side_effect = RuntimeError("geen database")
        antwoord = haal_alternatieven(invoer(), onderzoeker=lambda isin, naam=None: {"is_etf": True, "noteringen": []})
        self.assertTrue(antwoord["posities"][0]["fase_b_mogelijk"])

    def test_gevulde_uitslag_wordt_onthouden_lege_niet(self):
        quotes = [quote("X.AS", "AMS")]
        with patch.object(ba, "zoek_gecachet", return_value=quotes) as zoek, \
                patch.object(ba, "haal_koers", return_value=(1.0, "2026-10-27")):
            onderzoek_isin(ISIN_A)
            onderzoek_isin(ISIN_A)
        self.assertEqual(zoek.call_count, 2)  # ISIN en longname, de tweede keer uit de cache
        with patch.object(ba, "zoek_gecachet", return_value=[]) as zoek:
            onderzoek_isin(ISIN_B)
            onderzoek_isin(ISIN_B)
        self.assertEqual(zoek.call_count, 2)

    def test_zoekcache_bewaart_alleen_gevulde_uitslagen(self):
        import effect_zoeken
        effect_zoeken.wis_zoek_cache()
        self.addCleanup(effect_zoeken.wis_zoek_cache)
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[]) as zoek:
            effect_zoeken.zoek_gecachet("leeg")
            effect_zoeken.zoek_gecachet("leeg")
        self.assertEqual(zoek.call_count, 2)
        with patch.object(effect_zoeken, "_yahoo_search", return_value=[quote("X.AS", "AMS")]) as zoek:
            effect_zoeken.zoek_gecachet("gevuld")
            effect_zoeken.zoek_gecachet("gevuld")
        self.assertEqual(zoek.call_count, 1)

    def test_uitzondering_in_zoeklaag_wordt_een_nette_fout(self):
        with patch.object(ba, "zoek_gecachet", side_effect=RuntimeError("rate limit")):
            self.assertEqual(onderzoek_isin(ISIN_A), {"fout": FOUT_ALGEMEEN})


def _kostenregels(*jaren, code="TDG"):
    return [{"omschrijving": f"DEGIRO Aansluitingskosten {jaar} (Tradegate AG - {code})", "mutatie_valuta": "EUR",
             "mutatie": -2.5} for jaar in jaren]


def _tx(datum, beurs, ticker, isin, aantal, kosten=-2.0):
    return {"datum": pd.Timestamp(datum), "beurs": beurs, "ticker": ticker, "isin": isin, "aantal": aantal,
            "product": ticker, "transactiekosten": kosten}


class TestInvoerUitTransacties(unittest.TestCase):
    def maak(self, rijen, waarden, huidig_jaar=2026):
        samenvatting = bouw_aansluitingskosten_samenvatting(_kostenregels(2025, 2026))
        stat = {"posities": [{"ticker": t, "huidige_waarde": w, "rendement_eur": 0.0} for t, w in waarden.items()],
                "gesloten_posities": [], "totalen": {}}
        voeg_beurs_resultaat_toe(samenvatting, pd.DataFrame(rijen), stat, huidig_jaar)
        return samenvatting["per_beurs"][0]["alternatieven_invoer"]

    def test_gemiddelde_kosten_isin_op_thuisbeurs_en_posities(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10, -4.0), _tx("2025-02-02", "TDG", "IWDA.DE", ISIN_A, 5, -6.0),
                 _tx("2025-03-02", "EAM", "VWRL.AS", ISIN_B, 2, -1.0)]
        i = self.maak(rijen, {"IWDA.DE": 1500.0, "VWRL.AS": 300.0})
        self.assertEqual(i["gemiddelde_kosten_per_transactie_eur"], 5.0)
        self.assertEqual(i["thuisbeurs_kosten_per_transactie_eur"], {"EAM": 1.0, "EBR": None})
        self.assertEqual(i["isins_op_thuisbeurs"], [ISIN_B])
        self.assertEqual(i["jaarkosten_eur"], 2.5)
        self.assertEqual(i["posities"], [
            {"ticker": "IWDA.DE", "naam": "IWDA.DE", "echte_naam": None, "isin": ISIN_A, "aantal": 15.0, "waarde_eur": 1500.0}])

    def test_geen_open_positie_geen_invoer(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10), _tx("2025-02-02", "TDG", "IWDA.DE", ISIN_A, -10)]
        samenvatting = bouw_aansluitingskosten_samenvatting(_kostenregels(2025))
        voeg_beurs_resultaat_toe(samenvatting, pd.DataFrame(rijen), {"posities": [], "gesloten_posities": [], "totalen": {}}, 2026)
        self.assertIsNone(samenvatting["per_beurs"][0]["alternatieven_invoer"])

    def test_positie_naar_gewicht_bij_twee_beurzen(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10), _tx("2025-02-02", "XET", "IWDA.DE", ISIN_A, 30)]
        i = self.maak(rijen, {"IWDA.DE": 4000.0})
        self.assertEqual((i["posities"][0]["aantal"], i["posities"][0]["waarde_eur"]), (10.0, 1000.0))


class TestBouwAlternatievenInvoer(unittest.TestCase):
    def beurs(self, code="TDG"):
        return bouw_aansluitingskosten_samenvatting(_kostenregels(2025, 2026, code=code))["per_beurs"][0]

    def test_open_positie(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10, -4.0)]
        i = bouw_alternatieven_invoer(self.beurs(), pd.DataFrame(rijen), {"IWDA.DE": 1000.0}, {"IWDA.DE": "World"}, 2026)
        self.assertEqual(i["posities"], [
            {"ticker": "IWDA.DE", "naam": "World", "echte_naam": None, "isin": ISIN_A, "aantal": 10.0, "waarde_eur": 1000.0}])
        self.assertEqual((i["beurs"], i["jaarkosten_eur"], i["betaald_dit_jaar"]), ("TDG", 2.5, True))
        self.assertEqual(i["gemiddelde_kosten_per_transactie_eur"], 4.0)

    def test_gesloten_positie_geen_invoer(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10), _tx("2025-02-02", "TDG", "IWDA.DE", ISIN_A, -10)]
        self.assertIsNone(bouw_alternatieven_invoer(self.beurs(), pd.DataFrame(rijen), {}, {}, 2026))

    def test_beurs_zonder_open_positie_geen_invoer(self):
        rijen = [_tx("2025-01-02", "XET", "IWDA.DE", ISIN_A, 10)]
        self.assertIsNone(bouw_alternatieven_invoer(self.beurs(), pd.DataFrame(rijen), {"IWDA.DE": 1.0}, {}, 2026))

    def test_onbekende_beurs_geen_invoer(self):
        beurs = bouw_aansluitingskosten_samenvatting([{
            "omschrijving": "DEGIRO Aansluitingskosten Voorbeeldbeurs", "mutatie_valuta": "EUR", "mutatie": -2.5}])["per_beurs"][0]
        rijen = [_tx("2025-01-02", "XET", "IWDA.DE", ISIN_A, 10)]
        self.assertIsNone(bouw_alternatieven_invoer(beurs, pd.DataFrame(rijen), {"IWDA.DE": 1.0}, {}, 2026))

    def test_positie_zonder_koers_heeft_waarde_none(self):
        rijen = [_tx("2025-01-02", "TDG", "IWDA.DE", ISIN_A, 10)]
        i = bouw_alternatieven_invoer(self.beurs(), pd.DataFrame(rijen), {}, {}, 2026)
        self.assertIsNone(i["posities"][0]["waarde_eur"])


class TestInvoerEndpointGelijkAanKern(unittest.TestCase):
    """bouw_beurs_alternatieven_invoer() echt uitgevoerd; alleen haal_portfolio_basis en de rekeningregels zijn gemockt."""

    REGELS = [
        ("2024-01-02", "09:00", "iDEAL storting", "EUR", 1000.0),
        ("2024-02-03", "09:00", "DEGIRO Aansluitingskosten 2024 (Tradegate AG - TDG)", "EUR", -2.5),
        ("2025-02-03", "09:00", "DEGIRO Aansluitingskosten 2025 (Tradegate AG - TDG)", "EUR", -2.5),
    ]

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        dagen = pd.bdate_range("2024-01-02", "2025-06-02")
        self.price_data = pd.DataFrame({"AAA": [50.0 + 0.01 * i for i in range(len(dagen))]}, index=dagen)
        for naam, waarde in [("get_prices", self.price_data), ("db_get_koers_splits", {}),
                             ("db_get_laatste_koers_update", (None, None)),
                             ("ticker_waarschuwingen_voor_transacties", ([], {})), ("analyze_transacties_verrijking", {}),
                             ("db_get_ticker_details", {}), ("db_get_cached_openfigi_voor_isins", {})]:
            stack.enter_context(patch.object(po, naam, return_value=waarde))
        po.wis_portfolio_basis_cache("TESTALT")
        self.addCleanup(po.wis_portfolio_basis_cache, "TESTALT")
        rekening = pd.DataFrame([{
            "Datum": pd.Timestamp(d), "Tijd": t, "Valutadatum": pd.Timestamp(d), "Product": None, "ISIN": None,
            "Omschrijving": o, "FX": None, "valuta_mutatie": v, "mutatie": m, "valuta_saldo": "EUR", "saldo": None,
            ORDER_ID_KOLOM_REKENING: None} for d, t, o, v, m in self.REGELS])
        self.regels = [r for r in bouw_rekening_regels(rekening) if r["mutatie_valuta"] == "EUR" and r["mutatie"] is not None]
        rijen = [dict(_tx("2024-01-02", "TDG", "AAA", ISIN_A, 10.0, -4.0), koers=50.0, totaal_eur=-504.0, echte_naam="AAA",
                      waarde_eur=-500.0, tijd="10:00", wisselkoers=None, autofx_kosten=None),
                 dict(_tx("2024-03-01", "TDG", "AAA", ISIN_A, 2.0, -2.0), koers=55.0, totaal_eur=-112.0, echte_naam="AAA",
                      waarde_eur=-110.0, tijd="10:00", wisselkoers=None, autofx_kosten=None),
                 dict(_tx("2024-04-01", "EAM", "AAA", ISIN_A, 1.0, -1.0), koers=55.0, totaal_eur=-56.0, echte_naam="AAA",
                      waarde_eur=-55.0, tijd="10:00", wisselkoers=None, autofx_kosten=None)]
        self.df = pd.DataFrame(rijen, columns=db.TRANSACTIE_KOLOMMEN)

    def test_zelfde_invoer_als_in_het_kernantwoord(self):
        kern = po.analyze_transacties(self.df.copy(), code=None, naam="Test", rekening_regels=self.regels)
        verwacht = next(b for b in kern["aansluitingskosten"]["per_beurs"] if b["code"] == "TDG")["alternatieven_invoer"]
        self.assertIsNotNone(verwacht)
        with patch.object(po, "haal_portfolio_basis", return_value=("Test", self.df.copy(), self.price_data)),                 patch.object(po, "db_get_rekening_regels_eur", return_value=self.regels):
            self.assertEqual(po.bouw_beurs_alternatieven_invoer("TESTALT", "TDG"), verwacht)

    def test_onbekende_code_en_beurs_zonder_kosten(self):
        with patch.object(po, "haal_portfolio_basis", return_value=(None, None, None)):
            self.assertIsNone(po.bouw_beurs_alternatieven_invoer("TESTALT", "TDG"))
        with patch.object(po, "haal_portfolio_basis", return_value=("Test", self.df.copy(), self.price_data)),                 patch.object(po, "db_get_rekening_regels_eur", return_value=self.regels):
            with self.assertRaises(po.OnbekendeBeurs):
                po.bouw_beurs_alternatieven_invoer("TESTALT", "XET")


class TestValidatie(unittest.TestCase):
    def test_beurscode(self):
        self.assertEqual(valideer_beurscode(" tdg "), ("TDG", None))
        for slecht in (None, "", "T D", "x" * 11, "TDG;DROP", 5):
            self.assertIsNotNone(valideer_beurscode(slecht)[1], slecht)
        self.assertIsNotNone(valideer_beurscode("EAM")[1])
        self.assertIsNotNone(valideer_beurscode("ebr")[1])

    def test_geldige_invoer_komt_schoon_terug(self):
        schoon, fout = valideer_alternatieven_invoer(invoer())
        self.assertIsNone(fout)
        self.assertEqual(schoon["beurs"], "TDG")
        self.assertEqual(schoon["posities"][0]["isin"], ISIN_A)

    def test_ongeldige_invoer(self):
        goed = invoer()
        slecht = [
            None, [], {}, dict(goed, beurs="EAM"), dict(goed, beurs=None), dict(goed, posities=[]),
            dict(goed, posities="x"), dict(goed, posities=[dict(positie(), isin="onzin")]),
            dict(goed, posities=[dict(positie(), aantal=0)]), dict(goed, posities=[dict(positie(), aantal="5")]),
            dict(goed, posities=[dict(positie(), waarde_eur=-1)]), dict(goed, posities=[dict(positie(), ticker="T" * 21)]),
            dict(goed, posities=[dict(positie(), aantal=float("nan"))]), dict(goed, jaarkosten_eur=-1),
            dict(goed, thuisbeurs_kosten_per_transactie_eur="x"), dict(goed, isins_op_thuisbeurs="x"),
            dict(goed, isins_op_thuisbeurs=["onzin"]),
        ]
        for i, s in enumerate(slecht):
            self.assertIsNotNone(valideer_alternatieven_invoer(s)[1], i)


class TestEndpoints(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.m = app_module
        self.client = app_module.app.test_client()
        app_module.toegangslimiet.wis()
        self.addCleanup(app_module.toegangslimiet.wis)
        p = patch.object(db, "db_connect", side_effect=AssertionError("geen database in deze test"))
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(ba, "db_get_ticker_details", return_value={})
        p.start()
        self.addCleanup(p.stop)

    def patch_app(self, naam, **kwargs):
        p = patch.object(self.m, naam, **kwargs)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    def test_ongeldige_beurs_geeft_400(self):
        basis = self.patch_app("bouw_beurs_alternatieven_invoer")
        for beurs in ("", "EAM", "a b", "x" * 11):
            self.assertEqual(self.client.get(f"/api/portfolio/ABC/beurs-alternatieven?beurs={beurs}").status_code, 400)
        self.assertEqual(self.client.get("/api/portfolio/ABC/beurs-alternatieven").status_code, 400)
        basis.assert_not_called()

    def test_onbekende_code_is_404(self):
        self.patch_app("bouw_beurs_alternatieven_invoer", return_value=None)
        res = self.client.get("/api/portfolio/ABC/beurs-alternatieven?beurs=TDG")
        self.assertEqual(res.status_code, 404)
        self.assertIn("Geen portfolio", res.get_json()["error"])

    def test_beurs_zonder_open_positie_is_404(self):
        self.patch_app("bouw_beurs_alternatieven_invoer", side_effect=self.m.OnbekendeBeurs("TDG"))
        self.assertEqual(self.client.get("/api/portfolio/ABC/beurs-alternatieven?beurs=TDG").status_code, 404)

    def test_antwoord_met_code(self):
        self.patch_app("bouw_beurs_alternatieven_invoer", return_value=invoer())
        self.patch_app("haal_alternatieven", side_effect=lambda i: haal_alternatieven(
            i, onderzoeker=lambda isin, naam=None: onderzoek([notering("X.AS", "EAM", 100.0)])))
        res = self.client.get("/api/portfolio/ABC/beurs-alternatieven?beurs=tdg")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.get_json()["blijft_betalen"])

    def test_onverwachte_fout_is_nette_json(self):
        self.patch_app("bouw_beurs_alternatieven_invoer", side_effect=RuntimeError("kapot"))
        res = self.client.get("/api/portfolio/ABC/beurs-alternatieven?beurs=TDG")
        self.assertEqual(res.status_code, 500)
        self.assertIn("error", res.get_json())

    def test_niet_opslaan_valideert_en_antwoordt(self):
        for body in (None, {}, {"invoer": {}}, {"invoer": dict(invoer(), beurs="EAM")}):
            self.assertEqual(self.client.post("/api/beurs-alternatieven", json=body).status_code, 400)
        self.assertEqual(self.client.post("/api/beurs-alternatieven", data="geen json").status_code, 400)
        self.patch_app("haal_alternatieven", side_effect=lambda i: haal_alternatieven(
            i, onderzoeker=lambda isin, naam=None: onderzoek([])))
        res = self.client.post("/api/beurs-alternatieven", json={"invoer": invoer()})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["blijft_betalen"])


if __name__ == "__main__":
    unittest.main()
