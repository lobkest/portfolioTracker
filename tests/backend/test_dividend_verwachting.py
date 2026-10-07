"""Verwacht dividend (dividend_verwachting.py): venster en split, bronkeuze, koppeling, vergelijking, belasting en de route.
Zonder database en zonder Yahoo: alles gaat via parameters of mocks."""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import dividend_verwachting as dv
from diagnostiek import GOED, INFO, LET_OP
from portfolio_calc import compute_split_adjusted_shares

PEIL = "2026-10-07"
KWARTALEN = ["2025-11-15", "2026-02-15", "2026-05-15", "2026-08-15"]


def _yahoo(bedrag=0.5, datums=KWARTALEN, dividend_rate=None, trailing_rate=None):
    return {"dividenden": {d: bedrag for d in datums}, "dividend_rate": dividend_rate, "trailing_rate": trailing_rate}


def _betaling(ex_iso, bruto, belasting=0.0, dagen_na_ex=16):
    datum = (pd.Timestamp(ex_iso) + pd.Timedelta(days=dagen_na_ex)).date()
    return {"datum": datum, "bruto_eur": bruto, "belasting_eur": belasting}


def _positie(yahoo, eigen=None, valuta="USD", fx=0.9, aantal=10, isin="US0000000001", splitdatum=None,
             dekking=("2025-01-01", PEIL), transactie_datums=()):
    ex_datums = [pd.Timestamp(d) for d in yahoo["dividenden"]] if yahoo else []
    return dv.positie_verwachting(
        isin=isin, ticker="ABC", bijnaam="ABC", aantal=aantal, valuta=valuta, yahoo=yahoo, splitdatum=splitdatum,
        peildatum=PEIL, fx_actueel=fx, eigen_uitkeringen=eigen,
        aantal_per_ex_datum={d: aantal for d in ex_datums},
        fx_per_datum={pd.Timestamp(u["datum"]): fx for u in (eigen or [])},
        dekking=dekking if eigen is not None else None,
        aantal_per_betaaldatum={pd.Timestamp(u["datum"]): aantal for u in (eigen or [])},
        transactie_datums=transactie_datums,
        aantal_bij_eigen_begin=aantal,
    )


def _soorten(bevindingen, niveau=None):
    return {b["sleutel"].split(":")[-1] for b in bevindingen if niveau is None or b["niveau"] == niveau}


class TestVensterEnSplit(unittest.TestCase):
    def test_uitkering_voor_de_split_wordt_genegeerd(self):
        venster, _ = dv.bruikbare_dividenden({"2026-01-01": 1.0, "2026-04-01": 2.0}, "2026-02-01", PEIL)
        self.assertEqual(list(venster), [2.0])

    def test_uitkering_op_de_splitdatum_wordt_genegeerd(self):
        venster, _ = dv.bruikbare_dividenden({"2026-02-01": 1.0, "2026-04-01": 2.0}, "2026-02-01", PEIL)
        self.assertEqual(list(venster), [2.0])

    def test_split_in_het_venster_geeft_geen_volledig_jaar(self):
        _, volledig = dv.bruikbare_dividenden({}, "2026-02-01", PEIL)
        self.assertFalse(volledig)
        _, volledig = dv.bruikbare_dividenden({}, "2025-01-01", PEIL)
        self.assertTrue(volledig)

    def test_split_in_venster_valt_terug_op_dividend_rate_nooit_op_trailing(self):
        self.assertEqual(dv.jaar_dividend_per_aandeel([0.5], False, 1.8, 2.4, False), (1.8, dv.BRON_DIVIDEND_RATE))
        self.assertEqual(dv.jaar_dividend_per_aandeel([0.5], False, None, 2.4, False), (None, dv.BRON_ONBEKEND))

    def test_laatste_splitdatum_uit_yahoo_en_degiro(self):
        self.assertEqual(dv.laatste_splitdatum({"2024-05-01": 2.0}, [pd.Timestamp("2025-03-01")]),
                         pd.Timestamp("2025-03-01"))
        self.assertIsNone(dv.laatste_splitdatum({}, []))


class TestBronkeuze(unittest.TestCase):
    def test_volledig_jaar_geeft_som_van_de_reeks(self):
        self.assertEqual(dv.jaar_dividend_per_aandeel([0.5, 0.5, 0.25], True, 9.0, 9.0, False),
                         (1.25, dv.BRON_YAHOO_REEKS))

    def test_reeks_ooit_leeg_geeft_geen_uitkeringen(self):
        self.assertEqual(dv.jaar_dividend_per_aandeel([], True, None, None, True), (0.0, dv.BRON_GEEN_UITKERINGEN))

    def test_niets_bruikbaars_geeft_onbekend(self):
        self.assertEqual(dv.jaar_dividend_per_aandeel([0.5], False, None, None, False), (None, dv.BRON_ONBEKEND))


class TestKoppeling(unittest.TestCase):
    def test_binnen_60_dagen_gekoppeld_61_niet(self):
        ex = [pd.Timestamp("2026-01-01")]
        gekoppeld, los = dv.koppel_eigen_aan_ex_datums([{"datum": datetime.date(2026, 3, 2)}], ex)  # 60 dagen
        self.assertEqual(len(gekoppeld), 1)
        self.assertEqual(los, [])
        gekoppeld, los = dv.koppel_eigen_aan_ex_datums([{"datum": datetime.date(2026, 3, 3)}], ex)  # 61 dagen
        self.assertEqual(gekoppeld, [])
        self.assertEqual(len(los), 1)

    def test_twee_uitkeringen_niet_aan_dezelfde_ex_datum(self):
        eigen = [{"datum": datetime.date(2026, 1, 20)}, {"datum": datetime.date(2026, 1, 25)}]
        gekoppeld, los = dv.koppel_eigen_aan_ex_datums(eigen, [pd.Timestamp("2026-01-01")])
        self.assertEqual(len(gekoppeld), 1)
        self.assertEqual(len(los), 1)

    def test_elke_betaling_bij_haar_eigen_ex_datum(self):
        eigen = [{"datum": datetime.date(2026, 2, 1)}, {"datum": datetime.date(2026, 2, 15)}]
        gekoppeld, _ = dv.koppel_eigen_aan_ex_datums(eigen, ["2026-01-01", "2026-01-20"])
        self.assertEqual([ex for _, ex in gekoppeld], [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-20")])


class TestVergelijking(unittest.TestCase):
    def test_gelijk_bedrag_geeft_geen_let_op(self):
        # 10 stuks x 0,50 USD x 0,9 = 4,50; 4,80 is 6,7% meer.
        eigen = [_betaling(d, 4.8, -0.72) for d in KWARTALEN]
        bevindingen = dv.dividend_bevindingen([_positie(_yahoo(), eigen)])
        self.assertEqual([b["niveau"] for b in bevindingen], [GOED])

    def test_20_procent_verschil_geeft_let_op(self):
        eigen = [_betaling(d, 5.4) for d in KWARTALEN]
        positie = _positie(_yahoo(), eigen)
        self.assertAlmostEqual(positie["afwijking_fractie"], 0.2)
        bevindingen = dv.dividend_bevindingen([positie])
        self.assertEqual(_soorten(bevindingen, LET_OP), {"uitkering", "totaal"})
        tabel = next(b["tabel"] for b in bevindingen if b["sleutel"].endswith(":uitkering"))
        self.assertEqual(len(tabel["rijen"]), 4)

    def test_factor_100_geeft_pence_melding(self):
        # Yahoo zegt 0,50 GBp (pence); DeGiro betaalde 0,50 GBP per stuk.
        eigen = [_betaling(d, 10 * 0.5 * 1.2) for d in KWARTALEN]
        positie = _positie(_yahoo(), eigen, valuta="GBp", fx=1.2, isin="GB0000000001")
        bevindingen = dv.dividend_bevindingen([positie])
        pence = [b for b in bevindingen if b["sleutel"].endswith(":pence")]
        self.assertEqual(len(pence), 1)
        self.assertIn("waarschijnlijk pence vs. pond (Yahoo-valuta GBp)", pence[0]["tekst"])
        self.assertNotIn("uitkering", _soorten(bevindingen))

    def test_gemiste_uitkering_alleen_binnen_de_dekking(self):
        eigen = [_betaling(d, 4.5) for d in KWARTALEN if d != "2026-02-15"]
        self.assertIn("gemist", _soorten(dv.dividend_bevindingen([_positie(_yahoo(), eigen)]), LET_OP))
        buiten = _positie(_yahoo(), eigen, dekking=("2026-03-01", PEIL))
        bevindingen = dv.dividend_bevindingen([buiten])
        self.assertNotIn("gemist", _soorten(bevindingen))
        self.assertIn("dekking", _soorten(bevindingen, INFO))
        self.assertIsNone(buiten["afwijking_fractie"])

    def test_nog_niet_betaalde_uitkering_is_niet_gemist(self):
        # Ex-datum 15-08, rekeningoverzicht tot 01-09: de betaling kan nog komen.
        eigen = [_betaling(d, 4.5) for d in KWARTALEN[:3]]
        positie = _positie(_yahoo(), eigen, dekking=("2025-01-01", "2026-09-01"))
        self.assertNotIn("gemist", _soorten(dv.dividend_bevindingen([positie])))

    def test_eigen_uitkering_zonder_ex_datum(self):
        eigen = [_betaling(d, 4.5) for d in KWARTALEN] + [{"datum": datetime.date(2026, 7, 1), "bruto_eur": 1.0,
                                                           "belasting_eur": 0.0}]
        self.assertIn("zonder_ex", _soorten(dv.dividend_bevindingen([_positie(_yahoo(), eigen)]), LET_OP))

    def test_vergelijk_uitkeringen_slaat_aantal_0_over(self):
        ex = pd.Timestamp("2026-01-01")
        gekoppeld = [({"datum": datetime.date(2026, 1, 20), "bruto_eur": 5.0}, ex)]
        self.assertEqual(dv.vergelijk_uitkeringen(gekoppeld, {ex: 0.0}, {ex: 0.5}, {}, "EUR"), [])
        rij = dv.vergelijk_uitkeringen(gekoppeld, {ex: 10.0}, {ex: 0.5}, {pd.Timestamp("2026-01-20"): 1.0}, "EUR")[0]
        self.assertAlmostEqual(rij["eigen_per_aandeel_eur"], 0.5)
        self.assertAlmostEqual(rij["afwijking_fractie"], 0.0)


class TestBelasting(unittest.TestCase):
    def test_volgorde_eigen_land_standaard(self):
        eigen = [{"bruto_eur": 10.0, "belasting_eur": -1.5}, {"bruto_eur": 10.0, "belasting_eur": -0.5}]
        self.assertEqual(dv.belasting_fractie(eigen, "NL0000000001"), (0.1, dv.BELASTING_BRON_EIGEN))
        self.assertEqual(dv.belasting_fractie([], "NL0000000001"), (0.15, dv.BELASTING_BRON_LAND))
        self.assertEqual(dv.belasting_fractie([], "FR0000000001"), (0.15, dv.BELASTING_BRON_STANDAARD))

    def test_ierse_isin_zonder_eigen_data_geeft_0(self):
        self.assertEqual(dv.belasting_fractie([], "IE00B4L5Y983"), (0.0, dv.BELASTING_BRON_LAND))


class TestPositieVerwachting(unittest.TestCase):
    def test_netto_voorbeeld(self):
        # 4 x 0,50 USD = 2,00 per stuk x 10 stuks x 0,9 = 18,00 bruto; eigen belasting 15% -> 15,30 netto.
        eigen = [_betaling(d, 4.5, -0.675) for d in KWARTALEN]
        p = _positie(_yahoo(), eigen)
        self.assertEqual(p["bron"], dv.BRON_YAHOO_REEKS)
        self.assertAlmostEqual(p["per_aandeel_jaar"], 2.0)
        self.assertAlmostEqual(p["bruto_eur_jaar"], 18.0)
        self.assertAlmostEqual(p["belasting_fractie"], 0.15)
        self.assertEqual(p["belasting_bron"], dv.BELASTING_BRON_EIGEN)
        self.assertAlmostEqual(p["netto_eur_jaar"], 15.3)
        self.assertAlmostEqual(p["eigen_bruto_eur_jaar"], 18.0)
        self.assertTrue(p["meegeteld"])

    def test_gbp_pence_gedeeld_door_100(self):
        # 4 x 50 GBp = 200 pence x 10 stuks x 1,2 / 100 = 24,00 bruto; GB -> standaard 15% -> 20,40 netto.
        p = _positie(_yahoo(bedrag=50), valuta="GBp", fx=1.2, isin="GB0000000001")
        self.assertAlmostEqual(p["fx"], 0.012)
        self.assertAlmostEqual(p["bruto_eur_jaar"], 24.0)
        self.assertAlmostEqual(p["netto_eur_jaar"], 20.4)
        self.assertIsNone(p["eigen_bruto_eur_jaar"])

    def test_valuta_zonder_fx_wordt_niet_meegeteld(self):
        p = _positie(_yahoo(), valuta="CHF", fx=None)
        self.assertFalse(p["meegeteld"])
        self.assertIsNone(p["netto_eur_jaar"])
        self.assertIn("niet_meegeteld", _soorten(dv.dividend_bevindingen([p]), LET_OP))

    def test_split_in_venster_geeft_info(self):
        p = _positie(_yahoo(dividend_rate=1.6), splitdatum="2026-03-01")
        self.assertEqual(p["bron"], dv.BRON_DIVIDEND_RATE)
        self.assertIn("split", _soorten(dv.dividend_bevindingen([p]), INFO))


class _NepTicker:
    def __init__(self, dividenden=None, info=None, dividenden_fout=None, info_fout=None):
        self._dividenden, self._info = dividenden, info
        self._dividenden_fout, self._info_fout = dividenden_fout, info_fout

    @property
    def dividends(self):
        if self._dividenden_fout:
            raise self._dividenden_fout
        return self._dividenden

    @property
    def info(self):
        if self._info_fout:
            raise self._info_fout
        return self._info


class TestOphalen(unittest.TestCase):
    def _haal(self, nep):
        with patch.object(dv, "db_get_cached_ticker_dividenden", return_value=None), \
                patch.object(dv, "db_save_ticker_dividenden") as opslaan, \
                patch.object(dv.yf, "Ticker", return_value=nep):
            return dv.haal_yahoo_dividenden("ASML.AS"), opslaan

    def test_info_faalt_reeks_wordt_wel_gecachet_met_rates_none(self):
        reeks = pd.Series([1.6, 2.7], index=pd.to_datetime(["2026-02-09", "2026-04-24"]))
        # 401 telt als rate limit; met één poging volgt er geen wachttijd.
        data, opslaan = self._haal(_NepTicker(dividenden=reeks, info_fout=Exception("HTTP Error 401: Invalid Crumb")))
        self.assertEqual(data["dividenden"], {"2026-02-09": 1.6, "2026-04-24": 2.7})
        self.assertIsNone(data["dividend_rate"])
        self.assertTrue(data["info_mislukt"])
        opslaan.assert_called_once_with("ASML.AS", {"2026-02-09": 1.6, "2026-04-24": 2.7}, None, None)

    def test_dividends_faalt_geeft_none_en_geen_cache(self):
        data, opslaan = self._haal(_NepTicker(dividenden_fout=ValueError("kapot"), info={"dividendRate": 7.0}))
        self.assertIsNone(data)
        opslaan.assert_not_called()

    def test_info_mislukt_geeft_een_info_melding(self):
        bevindingen = dv.dividend_bevindingen([], info_mislukt=3)
        melding = next(b for b in bevindingen if b["sleutel"] == "div_verwachting:info_mislukt")
        self.assertEqual(melding["niveau"], INFO)
        self.assertIn("3 ticker(s)", melding["tekst"])


class TestBronvolgorde(unittest.TestCase):
    def test_alle_zes_bronnen(self):
        # (venster, volledig_jaar, dividend_rate, trailing_rate, geen_uitkeringen, eigen) -> bron
        gevallen = [
            (([0.5, 0.5], True, 9.0, 9.0, False, 3.0), (1.0, dv.BRON_YAHOO_REEKS)),
            (([0.5], False, 9.0, 9.0, False, 3.0), (3.0, dv.BRON_EIGEN_DATA)),
            (([0.5], False, 9.0, 8.0, False, None), (9.0, dv.BRON_DIVIDEND_RATE)),
            (([], True, None, 8.0, True, None), (0.0, dv.BRON_GEEN_UITKERINGEN)),
            (([0.5], False, None, 8.0, False, None), (None, dv.BRON_ONBEKEND)),
        ]
        for invoer, verwacht in gevallen:
            with self.subTest(verwacht=verwacht[1]):
                self.assertEqual(dv.jaar_dividend_per_aandeel(*invoer), verwacht)

    def test_trailing_rate_wint_niet_van_reeks_of_geen_uitkeringen(self):
        # Zonder split wint de reeks (of geen_uitkeringen); met een split in het venster telt trailing niet.
        for invoer in (([0.5], True, None, 8.0, False), ([], True, None, 8.0, True), ([0.5], False, None, 8.0, False)):
            with self.subTest(invoer=invoer):
                self.assertNotEqual(dv.jaar_dividend_per_aandeel(*invoer)[1], dv.BRON_TRAILING_RATE)

    def test_lege_reeks_geeft_geen_uitkeringen_met_en_zonder_rates(self):
        for dividend_rate, trailing_rate in ((None, None), (1.5, 0.0)):
            with self.subTest(dividend_rate=dividend_rate):
                p = _positie(_yahoo(datums=[], dividend_rate=dividend_rate, trailing_rate=trailing_rate))
                self.assertEqual(p["bron"], dv.BRON_GEEN_UITKERINGEN)
                self.assertEqual(p["per_aandeel_jaar"], 0.0)

    def test_enkel_oud_artefact_keert_niet_uit(self):
        # Take-Two: Yahoo heeft één 'uitkering' van $0,001 uit 2008.
        self.assertTrue(dv.keert_niet_uit({"2008-09-25": 0.001}, PEIL))
        p = _positie(_yahoo(bedrag=0.001, datums=["2008-09-25"], trailing_rate=0.0))
        self.assertEqual(p["bron"], dv.BRON_GEEN_UITKERINGEN)
        self.assertFalse(dv.keert_niet_uit({d: 0.5 for d in KWARTALEN}, PEIL))


# Rekeningoverzicht tot 31-05-2026: het eigen venster is (31-05-2025, 31-05-2026], niet tot de peildatum.
PER_DATUM = "2026-05-31"
EIGEN_KWARTALEN = ["2025-08-01", "2025-11-01", "2026-02-01", "2026-04-15"]


class TestEigenVenster(unittest.TestCase):
    def test_eigen_venster_eindigt_op_per_datum(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN] + [_betaling("2025-05-04", 9.0)]
        p = _positie(_yahoo(datums=EIGEN_KWARTALEN), eigen, dekking=("2024-01-01", PER_DATUM))
        # Met een venster tot de peildatum (07-10-2026) zouden alleen de laatste drie meetellen.
        self.assertEqual(p["eigen_aantal_uitkeringen"], 4)
        self.assertAlmostEqual(p["eigen_bruto_eur_jaar"], 18.0)
        self.assertEqual(len(p["_controle"]["vergelijking"]), 4)

    def test_yahoo_ex_datum_na_per_datum_is_niet_gemist(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN]
        p = _positie(_yahoo(datums=EIGEN_KWARTALEN + ["2026-08-15"]), eigen, dekking=("2024-01-01", PER_DATUM))
        self.assertEqual(p["_controle"]["gemist"], [])
        self.assertNotIn("gemist", _soorten(dv.dividend_bevindingen([p])))


class TestTerugvalEigenData(unittest.TestCase):
    def test_verwachting_uit_eigen_data_als_dividends_faalt(self):
        # 4 x 4,50 bruto op 10 stuks = 1,80 per stuk; eigen belasting 15% -> 18,00 bruto, 15,30 netto, in EUR.
        eigen = [_betaling(d, 4.5, -0.675) for d in EIGEN_KWARTALEN]
        p = _positie(None, eigen, valuta="USD", fx=None, dekking=("2024-01-01", PER_DATUM))
        self.assertEqual(p["bron"], dv.BRON_EIGEN_DATA)
        self.assertAlmostEqual(p["per_aandeel_jaar"], 1.8)
        self.assertEqual(p["valuta"], "EUR")
        self.assertAlmostEqual(p["bruto_eur_jaar"], 18.0)
        self.assertAlmostEqual(p["netto_eur_jaar"], 15.3)
        self.assertTrue(p["meegeteld"])

    def test_uitkering_met_transactie_kort_ervoor_overgeslagen_en_geteld(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN]
        transactie = pd.Timestamp(eigen[1]["datum"]) - pd.Timedelta(days=10)
        p = _positie(None, eigen, dekking=("2024-01-01", PER_DATUM), transactie_datums=[transactie])
        self.assertEqual(p["eigen_aantal_uitkeringen"], 3)
        self.assertAlmostEqual(p["per_aandeel_jaar"], 1.35)
        self.assertEqual(p["_controle"]["overgeslagen"], {"transactie": 1, "geen_bedrag": 0})
        melding = next(b for b in dv.dividend_bevindingen([p]) if b["sleutel"].endswith(":overgeslagen"))
        self.assertEqual(melding["niveau"], INFO)
        self.assertIn("1 uitkering(en) niet meegeteld", melding["tekst"])

    def test_bruto_none_overgeslagen(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN]
        eigen[2]["bruto_eur"] = None
        som, meegeteld, overgeslagen = dv.eigen_jaar_per_aandeel(
            dv._per_betaaldatum(eigen), {pd.Timestamp(u["datum"]): 10 for u in eigen}, [], None, PER_DATUM)
        self.assertEqual(meegeteld, 3)
        self.assertAlmostEqual(som, 1.35)
        self.assertEqual(overgeslagen["geen_bedrag"], 1)

    def test_uitkering_voor_de_laatste_split_overgeslagen(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN]
        # Geschatte ex-datum van de tweede betaling (17-11-2025) is 18-10-2025: op of vóór de split telt niet.
        som, meegeteld, _ = dv.eigen_jaar_per_aandeel(
            eigen, {pd.Timestamp(u["datum"]): 10 for u in eigen}, [], "2025-10-18", PER_DATUM)
        self.assertEqual(meegeteld, 2)
        self.assertAlmostEqual(som, 0.9)

    def test_aantal_0_telt_niet_mee(self):
        eigen = [_betaling(d, 4.5) for d in EIGEN_KWARTALEN]
        aantallen = {pd.Timestamp(u["datum"]): 10 for u in eigen}
        aantallen[pd.Timestamp(eigen[0]["datum"])] = 0
        _, meegeteld, _ = dv.eigen_jaar_per_aandeel(eigen, aantallen, [], None, PER_DATUM)
        self.assertEqual(meegeteld, 3)


def _transacties():
    rijen = [
        # ISIN-wissel bij een 3:1-split: oud uit, nieuw in (tijd 00:00, geen kosten).
        ("2024-01-10", datetime.time(10, 0), "US0000000001", "ABC", 10, 100.0, -1.0),
        ("2025-03-01", datetime.time(0, 0), "US0000000001", "ABC", -10, 100.0, 0.0),
        ("2025-03-01", datetime.time(0, 0), "US0000000002", "ABC", 30, 33.33, 0.0),
        ("2024-02-01", datetime.time(11, 0), "NL0000000003", "XYZ.AS", 5, 20.0, -1.0),
    ]
    df = pd.DataFrame(rijen, columns=["datum", "tijd", "isin", "ticker", "aantal", "koers", "transactiekosten"])
    df["datum"] = pd.to_datetime(df["datum"])
    df["product"] = df["ticker"].map({"ABC": "ABC INC", "XYZ.AS": "XYZ NV"})
    df["beurs"] = df["ticker"].map({"ABC": "NSY", "XYZ.AS": "EAM"})
    return compute_split_adjusted_shares(df)


class TestBerekenDividendVerwachting(unittest.TestCase):
    def test_isin_wissel_is_een_positie_en_antwoordvorm(self):
        resultaat = pd.DataFrame({"waarde": [900.0, 1000.0]}, index=pd.to_datetime(["2026-10-06", PEIL]))
        yahoo = {
            "ABC": (_yahoo(bedrag=0.25), {"2025-03-01": 3.0}),
            "XYZ.AS": ({"dividenden": {}, "dividend_rate": None, "trailing_rate": None}, {}),
        }
        fx = pd.Series(0.9, index=pd.date_range("2024-01-01", PEIL))
        with patch.object(dv, "laad_transacties_en_resultaat", return_value=(_transacties(), resultaat)), \
                patch.object(dv, "haal_yahoo_data_parallel", return_value=yahoo), \
                patch.object(dv, "_valuta_per_ticker", return_value={"ABC": "USD", "XYZ.AS": "EUR"}), \
                patch.object(dv, "_fx_prijzen_serie", return_value=fx), \
                patch.object(dv, "db_get_kassaldo", return_value=None):
            data = dv.bereken_dividend_verwachting("TEST_DV")

        self.assertEqual(len(data["posities"]), 2)
        abc = next(p for p in data["posities"] if p["ticker"] == "ABC")
        self.assertEqual(abc["isin"], "US0000000002")
        self.assertEqual(abc["aantal"], 30)
        # 4 x 0,25 = 1,00 USD x 30 x 0,9 = 27,00 bruto; US 15% -> 22,95 netto.
        self.assertEqual(abc["bruto_eur_jaar"], 27.0)
        self.assertEqual(abc["netto_eur_jaar"], 22.95)
        self.assertEqual(abc["laatste_split"], "2025-03-01")
        xyz = next(p for p in data["posities"] if p["ticker"] == "XYZ.AS")
        self.assertEqual(xyz["bron"], dv.BRON_GEEN_UITKERINGEN)

        self.assertEqual(data["peildatum"], PEIL)
        self.assertEqual(data["huidige_waarde_eur"], 1000.0)
        self.assertEqual(data["totaal_netto_eur_jaar"], 22.95)
        self.assertAlmostEqual(data["yield_netto"], 0.02295)
        self.assertFalse(data["eigen_data"])
        verwacht = {"isin", "ticker", "bijnaam", "aantal", "valuta", "per_aandeel_jaar", "bron", "laatste_split",
                    "volledig_jaar", "fx", "bruto_eur_jaar", "belasting_fractie", "belasting_bron", "netto_eur_jaar",
                    "eigen_bruto_eur_jaar", "afwijking_fractie", "meegeteld"}
        self.assertTrue(verwacht <= set(abc))
        self.assertFalse(any(k.startswith("_") for k in abc))


class TestDividendVerwachtingRoute(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()

    def test_onbekende_code_geeft_404(self):
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=False):
            res = self.client.get("/api/portfolio/zzz/dividend-verwachting")
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.get_json())

    def test_antwoord_met_diagnostiek(self):
        antwoord = {"beschikbaar": True, "posities": [], "totaal_netto_eur_jaar": 0.0}
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "bereken_dividend_verwachting", return_value=antwoord):
            res = self.client.get("/api/portfolio/ABC/dividend-verwachting")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["beschikbaar"])
        self.assertIn("diagnostiek", data)

    def test_zonder_koersdata_niet_beschikbaar(self):
        with patch.object(self.app_module, "db_portfolio_bestaat", return_value=True), \
                patch.object(self.app_module, "bereken_dividend_verwachting", return_value=None):
            res = self.client.get("/api/portfolio/ABC/dividend-verwachting")
        self.assertFalse(res.get_json()["beschikbaar"])


if __name__ == "__main__":
    unittest.main()
