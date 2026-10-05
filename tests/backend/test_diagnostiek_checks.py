"""Unit tests voor diagnostiek_checks.py (stap 1: datakwaliteit). Offline, geen database."""
import datetime
import os
import sys
import unittest

import pandas as pd

PROJECT_MAP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_MAP)

from diagnostiek import INFO, LET_OP
from diagnostiek_checks import (
    MAX_BEVINDINGEN_PER_CHECK, check_ontbrekende_kolommen, check_posities_zonder_ticker,
    check_synthetische_order_ids, check_corporate_action_rijen, check_isin_wissels,
)


def _rij(datum=datetime.date(2021, 3, 1), product="ACME", isin="NL0000000001", beurs="EAM", ticker="ACM.AS",
         aantal=10.0, transactiekosten=1.0, waarde_eur=-100.0, tijd=datetime.time(10, 0), echte_naam=None):
    return {"datum": datum, "product": product, "isin": isin, "beurs": beurs, "ticker": ticker, "aantal": aantal,
            "transactiekosten": transactiekosten, "waarde_eur": waarde_eur, "tijd": tijd,
            "echte_naam": echte_naam or product}


def _df(*rijen):
    return pd.DataFrame(list(rijen))


class TestOntbrekendeKolommen(unittest.TestCase):
    def test_volledige_data_geeft_niets(self):
        self.assertEqual(check_ontbrekende_kolommen(_df(_rij(), _rij())), [])

    def test_telt_per_kolom(self):
        df = _df(_rij(tijd=None), _rij(tijd=None, transactiekosten=float("nan")), _rij())
        uit = {b["sleutel"]: b for b in check_ontbrekende_kolommen(df)}
        self.assertEqual(set(uit), {"data:ontbrekend:tijd", "data:ontbrekend:transactiekosten"})
        self.assertIn("2 transacties", uit["data:ontbrekend:tijd"]["tekst"])
        self.assertIn("1 transacties", uit["data:ontbrekend:transactiekosten"]["tekst"])
        self.assertIn("verwijderen en opnieuw te uploaden", uit["data:ontbrekend:tijd"]["tekst"])
        self.assertEqual(uit["data:ontbrekend:tijd"]["niveau"], LET_OP)

    def test_corporate_action_en_verkopen_tellen_niet_mee(self):
        ca = _rij(beurs="DEG", ticker=None, transactiekosten=None, waarde_eur=None, tijd=None)
        verkoop = _rij(aantal=-5.0, waarde_eur=float("nan"))
        self.assertEqual(check_ontbrekende_kolommen(_df(ca, verkoop)), [])

    def test_lege_df(self):
        self.assertEqual(check_ontbrekende_kolommen(_df()), [])


class TestPositiesZonderTicker(unittest.TestCase):
    def test_noemt_naam_isin_en_datum(self):
        df = _df(_rij(ticker=None, product="LOSSE", isin="XX1", datum=datetime.date(2022, 5, 3)), _rij())
        [b] = check_posities_zonder_ticker(df)
        self.assertEqual(b["niveau"], LET_OP)
        for stuk in ("LOSSE", "XX1", "03-05-2022", "1 transacties"):
            self.assertIn(stuk, b["tekst"])

    def test_corporate_action_zonder_ticker_telt_niet(self):
        self.assertEqual(check_posities_zonder_ticker(_df(_rij(beurs="DEG", ticker=None))), [])

    def test_beperkt_tot_max_en_en_x_meer(self):
        aantal = MAX_BEVINDINGEN_PER_CHECK + 3
        df = _df(*[_rij(ticker=None, isin=f"XX{i}") for i in range(aantal)])
        uit = check_posities_zonder_ticker(df)
        self.assertEqual(len(uit), MAX_BEVINDINGEN_PER_CHECK + 1)
        self.assertIn("en 3 meer", uit[-1]["tekst"])

    def test_ergste_eerst(self):
        df = _df(_rij(ticker=None, isin="KLEIN"), _rij(ticker=None, isin="GROOT"), _rij(ticker=None, isin="GROOT"))
        self.assertEqual(check_posities_zonder_ticker(df)[0]["sleutel"], "data:geen_ticker:GROOT")


class TestSynthetischeOrderIds(unittest.TestCase):
    def test_telt_syn_rijen(self):
        [b] = check_synthetische_order_ids(["abc", "SYN-1-0", "SYN-2-0", "def"])
        self.assertEqual(b["niveau"], INFO)
        self.assertIn("2 van 4", b["tekst"])

    def test_geen_syn_geeft_niets(self):
        self.assertEqual(check_synthetische_order_ids(["abc", "def"]), [])
        self.assertEqual(check_synthetische_order_ids([]), [])


class TestCorporateActionRijen(unittest.TestCase):
    def test_per_isin_met_aantal_en_datums(self):
        df = _df(
            _rij(beurs="DEG", ticker=None, isin="AA1", datum=datetime.date(2021, 6, 1)),
            _rij(beurs="DEG", ticker=None, isin="AA1", datum=datetime.date(2021, 6, 2)),
            _rij(beurs="TDG", ticker=None, isin="BB2", product="X - NON TRADEABLE"),
            _rij(),
        )
        uit = check_corporate_action_rijen(df)
        self.assertEqual([b["sleutel"] for b in uit], ["data:corporate_action:AA1", "data:corporate_action:BB2"])
        self.assertIn("2 corporate-action-rijen", uit[0]["tekst"])
        self.assertIn("01-06-2021 t/m 02-06-2021", uit[0]["tekst"])
        self.assertEqual(uit[0]["niveau"], INFO)

    def test_geen_corporate_actions(self):
        self.assertEqual(check_corporate_action_rijen(_df(_rij())), [])


class TestIsinWissels(unittest.TestCase):
    def _wissel(self):
        d = datetime.date(2022, 2, 1)
        t = datetime.time(0, 0)
        return _df(
            _rij(ticker="XELA", isin="OLD", product="XELA OUD", aantal=-100.0, datum=d, tijd=t, transactiekosten=0.0),
            _rij(ticker="XELA", isin="NEW", product="XELA NIEUW", aantal=10.0, datum=d, tijd=t, transactiekosten=None),
        )

    def test_herkent_wissel_met_ratio(self):
        [b] = check_isin_wissels(self._wissel())
        self.assertEqual(b["niveau"], INFO)
        for stuk in ("XELA", "OLD", "NEW", "01-02-2022", "10.0000"):
            self.assertIn(stuk, b["tekst"])

    def test_meerdere_isins_zonder_patroon(self):
        df = _df(_rij(ticker="T", isin="A"), _rij(ticker="T", isin="B"))
        [b] = check_isin_wissels(df)
        self.assertIn("Geen wisselpatroon", b["tekst"])

    def test_een_isin_of_geen_ticker_geeft_niets(self):
        self.assertEqual(check_isin_wissels(_df(_rij(), _rij())), [])
        self.assertEqual(check_isin_wissels(_df(_rij(ticker=None, isin="A"), _rij(ticker=None, isin="B"))), [])

    def test_kosten_of_tijd_buiten_patroon(self):
        df = self._wissel()
        df["tijd"] = datetime.time(10, 0)
        self.assertIn("Geen wisselpatroon", check_isin_wissels(df)[0]["tekst"])


class TestMeldDatakwaliteit(unittest.TestCase):
    def test_meldt_in_categorie_data_en_breekt_niet_bij_dbfout(self):
        from unittest.mock import patch
        from flask import Flask
        import portfolio_orchestratie as po
        from diagnostiek import haal_meldingen, CATEGORIE_DATA

        df = _df(_rij(tijd=None))
        app = Flask(__name__)
        with app.test_request_context(), patch.object(po, "db_get_order_ids", return_value=["SYN-1-0"]):
            po._meld_datakwaliteit("ZZTEST", df)
            sleutels = {m["sleutel"] for m in haal_meldingen() if m["categorie"] == CATEGORIE_DATA}
        self.assertEqual(sleutels, {"data:ontbrekend:tijd", "data:synthetisch"})

        with app.test_request_context(), patch.object(po, "db_get_order_ids", side_effect=RuntimeError("db weg")):
            po._meld_datakwaliteit("ZZTEST", df)


if __name__ == "__main__":
    unittest.main()
