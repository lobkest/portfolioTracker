"""beoordeel_transactiebestand(): lijkt het transactiebestand de hele periode te beslaan? Verzonnen data, offline."""
import datetime
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from transactiebestand_status import (
    REDEN_DIVIDEND_ZONDER_POSITIE, REDEN_NEGATIEF_AANTAL, REDEN_REKENING_ZONDER_TRANSACTIE,
    beoordeel_transactiebestand,
)

M = datetime.time(0, 0)
VOLLEDIG = {"volledig": True}
ONVOLLEDIG = {"volledig": False}


def _rij(datum, aantal, isin="NL0000000001", product="ACME", beurs="EAM", tijd=datetime.time(10, 0), order_id=None,
         koers=10.0, transactiekosten=-1.0):
    return {"datum": pd.Timestamp(datum), "tijd": tijd, "product": product, "echte_naam": product, "isin": isin,
            "beurs": beurs, "aantal": float(aantal), "koers": koers, "transactiekosten": transactiekosten,
            "order_id": order_id}


def _df(*rijen):
    return pd.DataFrame(list(rijen))


def _dividend(datum, isin="NL0000000001", product="ACME"):
    return {"isin": isin, "product": product, "datum": datetime.date.fromisoformat(datum)}


def _regel(datum, omschrijving, isin="NL0000000001", order_id=None, product="ACME"):
    return {"datum": datetime.date.fromisoformat(datum), "tijd": "10:00", "omschrijving": omschrijving, "isin": isin,
            "product": product, "order_id": order_id}


def _codes(status):
    return [r["code"] for r in status["redenen"]]


class TestNegatiefAantal(unittest.TestCase):
    def test_volledig_portfolio_geen_melding(self):
        status = beoordeel_transactiebestand(_df(_rij("2024-01-02", 10), _rij("2024-03-01", -4)), None, None, None)
        self.assertTrue(status["volledig"])
        self.assertEqual(status["redenen"], [])
        self.assertEqual(status["eerste_transactie_datum"], "2024-01-02")
        self.assertEqual(status["laatste_transactie_datum"], "2024-03-01")

    def test_verkoop_zonder_eerdere_aankoop(self):
        status = beoordeel_transactiebestand(_df(_rij("2025-03-12", -5)), None, None, None)
        self.assertFalse(status["volledig"])
        [reden] = status["redenen"]
        self.assertEqual(reden["code"], REDEN_NEGATIEF_AANTAL)
        self.assertEqual(reden["items"], [{"isin": "NL0000000001", "product": "ACME", "datum": "2025-03-12"}])
        self.assertIn("ACME", reden["tekst"])
        self.assertIn("12-03-2025", reden["tekst"])

    def test_verkoop_en_aankoop_zelfde_dag_zonder_tijd_geen_melding(self):
        df = _df(_rij("2024-05-01", -5, tijd=None), _rij("2024-05-01", 5, tijd=None))
        self.assertTrue(beoordeel_transactiebestand(df, None, None, None)["volledig"])

    def test_positie_precies_naar_nul(self):
        self.assertTrue(beoordeel_transactiebestand(_df(_rij("2024-01-02", 3), _rij("2024-02-01", -3)), None, None, None)["volledig"])

    def test_afrondingsmarge(self):
        df = _df(_rij("2024-01-02", 0.3), _rij("2024-02-01", -0.1), _rij("2024-02-02", -0.1), _rij("2024-02-03", -0.1 - 5e-7))
        self.assertTrue(beoordeel_transactiebestand(df, None, None, None)["volledig"])
        df = _df(_rij("2024-01-02", 1.0), _rij("2024-02-01", -1.001))
        self.assertFalse(beoordeel_transactiebestand(df, None, None, None)["volledig"])

    def test_split_via_deg_rijen_geen_valse_melding(self):
        df = _df(_rij("2021-01-01", 10),
                 _rij("2021-06-01", -10, beurs="DEG", product="ACME - NON TRADEABLE", tijd=M, koers=0.0),
                 _rij("2021-06-01", 20, beurs="DEG", product="ACME - NON TRADEABLE", tijd=M, koers=0.0),
                 _rij("2021-07-01", -20))
        self.assertTrue(beoordeel_transactiebestand(df, None, None, None)["volledig"])

    def test_positie_via_deg_rijen_binnengekomen(self):
        df = _df(_rij("2021-06-01", 20, beurs="DEG", product="NEW - NON TRADEABLE", tijd=M, koers=0.0),
                 _rij("2021-07-01", -20))
        self.assertTrue(beoordeel_transactiebestand(df, None, None, None)["volledig"])

    def test_meer_dan_vijf_producten_en_n_andere(self):
        rijen = [_rij(f"2025-03-{10 + i}", -1, isin=f"NL000000000{i}", product=f"P{i}") for i in range(7)]
        [reden] = beoordeel_transactiebestand(_df(*rijen), None, None, None)["redenen"]
        self.assertEqual(len(reden["items"]), 7)
        self.assertIn("P4", reden["tekst"])
        self.assertNotIn("P5", reden["tekst"])
        self.assertIn("en 2 andere", reden["tekst"])

    def test_zonder_transacties_geen_veld(self):
        self.assertIsNone(beoordeel_transactiebestand(pd.DataFrame(), None, None, None))
        self.assertIsNone(beoordeel_transactiebestand(None, None, None, None))


class TestDividendZonderPositie(unittest.TestCase):
    def test_dividend_binnen_60_dagen_na_verkoop_geen_melding(self):
        df = _df(_rij("2024-01-02", 10), _rij("2024-03-01", -10))
        self.assertTrue(beoordeel_transactiebestand(df, None, [_dividend("2024-04-20")], None)["volledig"])

    def test_dividend_ruim_na_verkoop_wel_melding(self):
        df = _df(_rij("2024-01-02", 10), _rij("2024-03-01", -10))
        status = beoordeel_transactiebestand(df, None, [_dividend("2024-06-01")], None)
        self.assertEqual(_codes(status), [REDEN_DIVIDEND_ZONDER_POSITIE])

    def test_dividend_zonder_enige_positie(self):
        df = _df(_rij("2024-01-02", 10))
        status = beoordeel_transactiebestand(df, None, [_dividend("2024-02-01", isin="NL0000000009", product="ANDERS")], None)
        [reden] = status["redenen"]
        self.assertEqual(reden["items"][0]["isin"], "NL0000000009")
        self.assertIn("ANDERS", reden["tekst"])

    def test_dividend_voor_eerste_transactie_telt_mee(self):
        df = _df(_rij("2024-06-01", 10))
        status = beoordeel_transactiebestand(df, None, [_dividend("2024-02-01")], None)
        self.assertEqual(_codes(status), [REDEN_DIVIDEND_ZONDER_POSITIE])
        self.assertEqual(status["redenen"][0]["items"][0]["datum"], "2024-02-01")

    def test_dividend_tijdens_positie_geen_melding(self):
        df = _df(_rij("2024-01-02", 10))
        self.assertTrue(beoordeel_transactiebestand(df, None, [_dividend("2024-08-01")], None)["volledig"])

    def test_zonder_dividendgegevens_overgeslagen(self):
        df = _df(_rij("2024-01-02", 10))
        for dividenden in (None, []):
            self.assertTrue(beoordeel_transactiebestand(df, None, dividenden, None)["volledig"])

    def test_isin_wissel_tel_als_een_positie(self):
        df = _df(_rij("2024-01-02", 10, isin="OLD"),
                 _rij("2024-06-01", -10, isin="OLD", tijd=M, transactiekosten=None),
                 _rij("2024-06-01", 10, isin="NEW", tijd=M, transactiekosten=None))
        self.assertTrue(beoordeel_transactiebestand(df, None, [_dividend("2024-08-01", isin="OLD")], None)["volledig"])


class TestRekeningTegenTransacties(unittest.TestCase):
    def _df(self):
        return _df(_rij("2024-01-02", 10, order_id="aaaaaaaa-0000-0000-0000-000000000001"),
                   _rij("2024-02-01", 5, order_id="aaaaaaaa-0000-0000-0000-000000000002"))

    def test_koop_zonder_transactie_bij_volledig_overzicht(self):
        regels = [_regel("2024-01-02", "Koop 10 @ 10,00 EUR", order_id="aaaaaaaa-0000-0000-0000-000000000001"),
                  _regel("2023-11-05", "Koop 3 @ 9,00 EUR", order_id="bbbbbbbb-0000-0000-0000-000000000009"),
                  _regel("2023-12-05", "Verkoop 1 @ 9,00 EUR", order_id="bbbbbbbb-0000-0000-0000-000000000010")]
        status = beoordeel_transactiebestand(self._df(), regels, None, VOLLEDIG)
        [reden] = status["redenen"]
        self.assertEqual(reden["code"], REDEN_REKENING_ZONDER_TRANSACTIE)
        self.assertIn("2 aan-/verkopen", reden["tekst"])
        self.assertIn("05-11-2023", reden["tekst"])

    def test_overgeslagen_bij_onvolledig_of_ontbrekend_overzicht(self):
        regels = [_regel("2023-11-05", "Koop 3 @ 9,00 EUR", order_id="bbbbbbbb-0000-0000-0000-000000000009")]
        for status in (ONVOLLEDIG, None):
            self.assertTrue(beoordeel_transactiebestand(self._df(), regels, None, status)["volledig"])
        self.assertTrue(beoordeel_transactiebestand(self._df(), None, None, VOLLEDIG)["volledig"])

    def test_deelorders_delen_een_uuid(self):
        df = _df(_rij("2024-01-02", 4, order_id="aaaaaaaa-0000-0000-0000-000000000001"),
                 _rij("2024-01-02", 6, order_id="aaaaaaaa-0000-0000-0000-000000000001-1"))
        regels = [_regel("2024-01-02", "Koop 4 @ 10,00 EUR", order_id="aaaaaaaa-0000-0000-0000-000000000001"),
                  _regel("2024-01-02", "Koop 6 @ 10,00 EUR", order_id="aaaaaaaa-0000-0000-0000-000000000001")]
        self.assertTrue(beoordeel_transactiebestand(df, regels, None, VOLLEDIG)["volledig"])

    def test_leeg_order_id_valt_terug_op_datum_isin_aantal(self):
        regels = [_regel("2024-01-02", "Koop 10 @ 10,00 EUR", order_id=None)]
        self.assertTrue(beoordeel_transactiebestand(self._df(), regels, None, VOLLEDIG)["volledig"])
        regels = [_regel("2024-01-03", "Koop 10 @ 10,00 EUR", order_id=None)]
        self.assertFalse(beoordeel_transactiebestand(self._df(), regels, None, VOLLEDIG)["volledig"])

    def test_transactie_met_syn_order_id_matcht_op_datum_isin_aantal(self):
        df = _df(_rij("2024-01-02", 10, order_id="SYN-abc123"))
        regels = [_regel("2024-01-02", "Koop 10 @ 10,00 EUR", order_id="cccccccc-0000-0000-0000-000000000001")]
        self.assertTrue(beoordeel_transactiebestand(df, regels, None, VOLLEDIG)["volledig"])

    def test_nl_getallen_en_verkoop_negatief(self):
        df = _df(_rij("2024-01-02", 1000, order_id=None), _rij("2024-02-01", -0.5, order_id=None))
        regels = [_regel("2024-01-02", "Koop 1.000 @ 1,00 EUR"), _regel("2024-02-01", "Verkoop 0,5 @ 1,00 EUR")]
        self.assertTrue(beoordeel_transactiebestand(df, regels, None, VOLLEDIG)["volledig"])

    def test_corporate_action_regels_tellen_niet_mee(self):
        regels = [_regel("2024-01-02", "CLAIMEMISSIE: Koop 3 @ 9,00 EUR"), _regel("2024-01-02", "Dividend")]
        self.assertTrue(beoordeel_transactiebestand(self._df(), regels, None, VOLLEDIG)["volledig"])


class TestOpslaanGelijkAanNietOpslaan(unittest.TestCase):
    def test_zelfde_uitkomst_voor_datum_en_tijd_als_timestamp_of_date(self):
        df = _df(_rij("2025-03-12", -5))
        regels_date = [_regel("2025-03-01", "Koop 1 @ 1,00 EUR", order_id="x")]
        regels_ts = [{**r, "datum": pd.Timestamp(r["datum"])} for r in regels_date]
        dividenden_ts = [{**_dividend("2025-04-01"), "datum": pd.Timestamp("2025-04-01")}]
        a = beoordeel_transactiebestand(df, regels_date, [_dividend("2025-04-01")], VOLLEDIG)
        b = beoordeel_transactiebestand(df, regels_ts, dividenden_ts, VOLLEDIG)
        self.assertEqual(a, b)
        self.assertEqual(_codes(a), [REDEN_NEGATIEF_AANTAL, REDEN_DIVIDEND_ZONDER_POSITIE, REDEN_REKENING_ZONDER_TRANSACTIE])


if __name__ == "__main__":
    unittest.main()
