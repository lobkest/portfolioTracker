"""De melding bij een split met conversierij (BYD): werkelijk aantal naast het theoretische, met de verrekening."""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from diagnostiek import CATEGORIE_SPLITS, haal_meldingen
from portfolio_calc import bepaal_split_boekingen, compute_split_adjusted_shares, meld_split_koppeling
from split_correctie import bepaal_effectieve_datums

ISIN = "CNE100000296"
TICKER = "BY6.MU"
SPLITS = {TICKER: {"2025-06-10": 3.0}}
VERREKENINGEN = [
    {"datum": pd.Timestamp("2025-07-31"), "ticker": TICKER, "isin": ISIN, "product": "BYD CO LTD", "bedrag": 5.18},
    {"datum": pd.Timestamp("2025-07-31"), "ticker": TICKER, "isin": ISIN, "product": "BYD CO LTD", "bedrag": 7.78},
]


def _rij(datum, tijd, aantal, beurs="TDG", koers=0.0, ticker=TICKER, product="BYD CO LTD"):
    return {"datum": pd.Timestamp(datum), "tijd": tijd, "product": product, "echte_naam": product, "isin": ISIN,
            "beurs": beurs, "ticker": ticker, "aantal": aantal, "koers": koers, "totaal_eur": 0.0,
            "transactiekosten": None, "waarde_eur": 0.0}


def _byd(nieuwe_claims):
    """12 stuks gekocht, claims op de splitdag (DEG), later omgezet naar `sum(nieuwe_claims)` gewone stuks."""
    totaal = float(sum(nieuwe_claims))
    koop = _rij("2024-06-21", datetime.time(9, 46), 12.0, koers=28.32)
    claims = [_rij("2025-06-10", datetime.time(0, 0), float(n), beurs="DEG", ticker=None,
                   product="BYD CO LTD - NON TRADEABLE") for n in nieuwe_claims]
    uit = _rij("2025-07-31", datetime.time(7, 49), -totaal, beurs="DEG", ticker=None, product="BYD CO LTD - NON TRADEABLE")
    erin = _rij("2025-07-31", datetime.time(7, 49), totaal)
    return pd.DataFrame([koop, *claims, uit, erin])


def _melding(nieuwe_claims, verrekeningen):
    with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()):
        df = compute_split_adjusted_shares(_byd(nieuwe_claims))
        _df, resultaat = bepaal_effectieve_datums(df, bepaal_split_boekingen(df), SPLITS)
        meld_split_koppeling(resultaat, verrekeningen)
        return [m for m in haal_meldingen() if m["categorie"] == CATEGORIE_SPLITS]


class TestByd(unittest.TestCase):
    def test_met_verrekening(self):
        [m] = _melding([9, 14], VERREKENINGEN)
        self.assertEqual(m["sleutel"], f"split:{ISIN}:2025-07-31")
        self.assertEqual(
            m["tekst"],
            "Split 3:1 van BY6.MU op 10-06-2025: 12 → 35 stuks (theoretisch 36; DeGiro-conversie 31-07-2025). "
            "Het verschil is contant uitbetaald (Verrekening van Aandelen, € 12,96, telt mee als opbrengst).")

    def test_zonder_rekeningoverzicht_valt_de_laatste_zin_weg(self):
        for verrekeningen in (None, []):
            [m] = _melding([9, 14], verrekeningen)
            self.assertEqual(
                m["tekst"],
                "Split 3:1 van BY6.MU op 10-06-2025: 12 → 35 stuks (theoretisch 36; DeGiro-conversie 31-07-2025).")

    def test_verrekening_van_een_andere_isin_of_een_ander_moment_telt_niet(self):
        andere = [{**VERREKENINGEN[0], "isin": "XX0000000000"}, {**VERREKENINGEN[1], "datum": pd.Timestamp("2026-01-05")}]
        [m] = _melding([9, 14], andere)
        self.assertNotIn("Verrekening", m["tekst"])

    def test_zonder_afwijking_de_gewone_tekst(self):
        [m] = _melding([10, 14], VERREKENINGEN)
        self.assertEqual(m["tekst"], "Split 3:1 van BY6.MU op 10-06-2025: 12 → 36 stuks (DeGiro-conversie 31-07-2025).")
        self.assertNotIn("theoretisch", m["tekst"])

    def test_vervangt_de_melding_met_alleen_de_factor(self):
        with Flask(__name__).test_request_context(), redirect_stdout(io.StringIO()):
            df = compute_split_adjusted_shares(_byd([9, 14]))
            aantal_voor = len([m for m in haal_meldingen() if m["sleutel"].startswith("split:")])
            _df, resultaat = bepaal_effectieve_datums(df, bepaal_split_boekingen(df), SPLITS)
            meld_split_koppeling(resultaat)
            na = [m for m in haal_meldingen() if m["sleutel"].startswith("split:")]
        self.assertEqual((aantal_voor, len(na)), (1, 1))
        self.assertNotIn("factor", na[0]["tekst"])


if __name__ == "__main__":
    unittest.main()
