"""'Niet opslaan' geeft met hetzelfde rekeningoverzicht dezelfde cijfers als de opgeslagen route: promotiekorting en
dividend in Statistieken. Offline: importeert app niet, database en Yahoo zijn gemockt."""
import os
import sys
import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import db
from backend import portfolio_orchestratie as po
from backend.db import TRANSACTIE_KOLOMMEN
from backend.dividend import ORDER_ID_KOLOM_REKENING, bouw_rekening_regels, promotie_bedragen
from backend.rekening_regelsoorten import OMSCHRIJVINGEN_PROMOTIE

TEST_CODE = "test_niet_opslaan_promo"
PROMOTIE = OMSCHRIJVINGEN_PROMOTIE[0]

# Promotie van 3,00 op 01-03-2024: de eerste aankoop (kosten 2,00) volledig, de tweede voor 1,00.
PROMOTIES = [(date(2024, 3, 1), 3.0)]
KASSALDO = {"saldo_eur": 10.0, "netto_gestort_eur": 1000.0, "per_datum": date(2024, 6, 1)}
DIVIDENDEN = [
    {"datum": date(2024, 4, 15), "product": "AAA", "isin": "XX0000000001", "valuta": "EUR",
     "bruto_eur": 5.0, "belasting_eur": -0.75, "netto_eur": 4.25, "herinvesteerd": False},
    {"datum": date(2024, 5, 15), "product": "AAA", "isin": "XX0000000001", "valuta": "EUR",
     "bruto_eur": 2.0, "belasting_eur": -0.3, "netto_eur": 1.7, "herinvesteerd": False},
]


def _transacties_df():
    def rij(datum, tijd, ticker, isin, aantal, koers, kosten):
        waarde = -aantal * koers
        return {"datum": pd.Timestamp(datum), "product": ticker, "isin": isin, "beurs": "EAM", "ticker": ticker,
                "aantal": aantal, "koers": koers, "totaal_eur": waarde + kosten, "echte_naam": ticker,
                "transactiekosten": kosten, "waarde_eur": waarde, "tijd": tijd, "wisselkoers": None,
                "autofx_kosten": None}
    return pd.DataFrame([
        rij("2024-01-02", "10:00", "AAA", "XX0000000001", 10.0, 50.0, -2.0),
        rij("2024-02-01", "10:00", "BBB", "XX0000000002", 5.0, 100.0, -3.0),
        rij("2024-04-02", "10:00", "AAA", "XX0000000001", 2.0, 55.0, -2.0),
    ], columns=TRANSACTIE_KOLOMMEN)


def _price_data():
    dagen = pd.bdate_range("2024-01-02", "2024-06-03")
    return pd.DataFrame({
        "AAA": [50.0 + 0.1 * i for i in range(len(dagen))],
        "BBB": [100.0 - 0.05 * i for i in range(len(dagen))],
    }, index=dagen)


def _faalt(naam):
    def aanroep(*args, **kwargs):
        raise AssertionError(f"{naam} aangeroepen bij 'niet opslaan'")
    return aanroep


class _Basis(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.stack = stack
        for naam, waarde in [
            ("get_prices", _price_data()),
            ("db_get_koers_splits", {}),
            ("db_get_laatste_koers_update", (None, None)),
            ("ticker_waarschuwingen_voor_transacties", ([], {})),
            ("analyze_transacties_verrijking", {}),
            ("db_get_ticker_details", {}),
            ("db_get_cached_openfigi_voor_isins", {}),
        ]:
            stack.enter_context(patch.object(po, naam, return_value=waarde))
        po.wis_portfolio_basis_cache(TEST_CODE)
        self.addCleanup(po.wis_portfolio_basis_cache, TEST_CODE)

    def opgeslagen(self, promoties=PROMOTIES, dividenden=DIVIDENDEN, kassaldo=KASSALDO):
        df = _transacties_df()
        rijen = list(df.itertuples(index=False, name=None))
        with ExitStack() as stack:
            for naam, waarde in [
                ("db_get_portfolio_naam_en_transacties", ("Test", rijen)),
                ("db_get_rekening_regels_eur", [{"datum": d, "tijd": None, "omschrijving": PROMOTIE,
                                                 "mutatie_valuta": "EUR", "mutatie": b} for d, b in promoties]),
                ("db_get_rekening_koop_verkoop", []),
                ("db_get_dividenden", dividenden),
                ("db_get_isin_ticker_product", po.isin_ticker_product_rijen(df)),
                ("db_get_kassaldo", kassaldo),
                ("db_get_order_id_rijen", []),
            ]:
                stack.enter_context(patch.object(po, naam, return_value=waarde))
            return po.build_portfolio_response(TEST_CODE)

    def niet_opslaan(self, promoties=PROMOTIES, dividenden=DIVIDENDEN, kassaldo=KASSALDO):
        with ExitStack() as stack:
            for naam in ("db_get_portfolio_naam_en_transacties", "db_get_rekening_regels_eur", "db_get_rekening_koop_verkoop", "db_get_dividenden",
                         "db_get_isin_ticker_product", "db_get_kassaldo", "db_get_order_id_rijen",
                         "db_get_order_id_periodes"):
                stack.enter_context(patch.object(po, naam, side_effect=_faalt(naam)))
            stack.enter_context(patch.object(db, "db_connect", side_effect=_faalt("db_connect")))
            regels = [{"datum": d, "tijd": None, "omschrijving": PROMOTIE, "mutatie_valuta": "EUR", "mutatie": b}
                      for d, b in promoties or []] if promoties is not None else None
            return po.analyze_transacties(_transacties_df(), code=None, naam="Test", kassaldo=kassaldo,
                                          box3_dividenden=dividenden, promoties=promoties, rekening_regels=regels)


class TestNietOpslaanGelijkAanOpgeslagen(_Basis):
    def test_promotie_kosten_en_rendement_gelijk(self):
        opgeslagen, niet = self.opgeslagen(), self.niet_opslaan()
        totalen = niet["statistieken"]["totalen"]
        self.assertEqual(totalen["totale_transactiekosten"], 4.0)
        self.assertEqual(totalen["totale_transactiekosten_betaald"], 7.0)
        self.assertEqual(totalen["transactiekosten_korting"], 3.0)
        self.assertEqual(niet["chart_data"], opgeslagen["chart_data"])
        self.assertEqual(niet["per_ticker"], opgeslagen["per_ticker"])
        self.assertEqual(niet["statistieken"], opgeslagen["statistieken"])

    def test_kostenkorting_per_transactie_en_promotie_info(self):
        niet = self.niet_opslaan()
        kortingen = sorted(r["kostenkorting_eur"] for r in niet["transacties_lijst"])
        self.assertEqual(kortingen, [0.0, 1.0, 2.0])
        self.assertEqual(niet["transacties_promotie"],
                         {"bedrag_eur": 3.0, "datum": "2024-03-01", "regels": [{"datum": "2024-03-01", "bedrag_eur": 3.0}]})

    def test_dividend_in_statistieken_gelijk(self):
        opgeslagen, niet = self.opgeslagen(), self.niet_opslaan()
        self.assertEqual(niet["statistieken"]["totalen"]["dividend_netto"], 5.95)
        self.assertEqual(niet["statistieken"]["totalen"]["dividend_netto"],
                         opgeslagen["statistieken"]["totalen"]["dividend_netto"])
        self.assertEqual(niet["statistieken"]["posities"], opgeslagen["statistieken"]["posities"])
        per_ticker = {p["ticker"]: p["dividend_ontvangen"] for p in niet["statistieken"]["posities"]}
        self.assertEqual(per_ticker, {"AAA": 5.95, "BBB": 0.0})

    def test_netto_gestort_in_beide_routes(self):
        for antwoord in (self.opgeslagen(), self.niet_opslaan()):
            self.assertEqual(antwoord["statistieken"]["totalen"]["netto_gestort_eur"], 1000.0)


class TestZonderRekeningoverzicht(_Basis):
    def test_geen_korting_geen_dividend(self):
        niet = self.niet_opslaan(promoties=None, dividenden=None, kassaldo=None)
        totalen = niet["statistieken"]["totalen"]
        self.assertEqual(totalen["totale_transactiekosten"], 7.0)
        self.assertEqual(totalen["transactiekosten_korting"], 0.0)
        self.assertIsNone(totalen["dividend_netto"])
        self.assertIsNone(totalen["netto_gestort_eur"])
        self.assertIsNone(niet["transacties_promotie"])
        self.assertTrue(all(r["kostenkorting_eur"] == 0.0 for r in niet["transacties_lijst"]))

    def test_gelijk_aan_opgeslagen_zonder_rekeningoverzicht(self):
        niet = self.niet_opslaan(promoties=None, dividenden=None, kassaldo=None)
        opgeslagen = self.opgeslagen(promoties=[], dividenden=[], kassaldo=None)
        self.assertEqual(niet["statistieken"], opgeslagen["statistieken"])


def _rekening_rij(datum, tijd, omschrijving, valuta, mutatie):
    return {"Datum": pd.Timestamp(datum), "Tijd": tijd, "Valutadatum": pd.Timestamp(datum), "Product": None,
            "ISIN": None, "Omschrijving": omschrijving, "FX": None, "valuta_mutatie": valuta, "mutatie": mutatie,
            "valuta_saldo": "EUR", "saldo": 0.0, ORDER_ID_KOLOM_REKENING: None}


class TestPromotieBedragen(unittest.TestCase):
    def test_zelfde_selectie_als_database(self):
        df = pd.DataFrame([
            _rekening_rij("2024-05-01", "09:00", PROMOTIE, "EUR", 2.5),
            _rekening_rij("2024-03-01", "12:00", PROMOTIE, "EUR", 3.0),
            _rekening_rij("2024-03-01", "08:00", PROMOTIE, "EUR", 1.0),
            _rekening_rij("2024-03-02", "08:00", PROMOTIE, "USD", 9.0),
            _rekening_rij("2024-03-03", "08:00", PROMOTIE, "EUR", None),
            _rekening_rij("2024-03-04", "08:00", "iDEAL storting", "EUR", 100.0),
        ])
        self.assertEqual(promotie_bedragen(bouw_rekening_regels(df)),
                         [(date(2024, 3, 1), 1.0), (date(2024, 3, 1), 3.0), (date(2024, 5, 1), 2.5)])


if __name__ == "__main__":
    unittest.main()
