"""
Unit tests voor de Diagnostiek-meldingen van Deel A (upload): categorieën
Order ID's, Opslaan en Dividend in upload_verwerking.py -- plus
regressietests dat _insert_nieuwe_transacties() dezelfde SQL, dezelfde
parameters en hetzelfde return-gedrag houdt.

Draait geheel offline: de cursor is een mock, de database en Yahoo worden
niet aangeraakt, app.py wordt niet geïmporteerd (een losse Flask-app levert
de request-context).
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, MagicMock

import openpyxl
import pandas as pd
import psycopg2
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upload_verwerking as uv
from diagnostiek import (
    haal_meldingen, CATEGORIE_ORDER_IDS, CATEGORIE_OPSLAAN, CATEGORIE_DIVIDEND, GOED, INFO, LET_OP, FOUT,
)

UUID_1 = "11111111-2222-3333-4444-555555555555"
UUID_2 = "66666666-7777-8888-9999-000000000000"

# Exact de SQL uit _insert_nieuwe_transacties() vóór de Diagnostiek-wijziging.
VERWACHTE_INSERT_SQL = """INSERT INTO transacties
                   (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam, transactiekosten, waarde_eur, tijd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, order_id) DO NOTHING"""


def _per_sleutel(meldingen):
    return {m["sleutel"]: m for m in meldingen}


def _rijen(n=2, **extra):
    """Minimale 'rows_to_insert'-DataFrame zoals na _normaliseer_transactie_kolommen/_bepaal_order_ids."""
    data = {
        "Datum": pd.to_datetime(["2024-01-02"] * n),
        "Tijd": ["10:00"] * n,
        "Product": [f"FONDS {i}" for i in range(n)],
        "ISIN": [f"NL000000000{i}" for i in range(n)],
        "Beurs": ["EAM"] * n,
        "Aantal": [10.0] * n,
        "Koers": [5.0] * n,
        "Totaal EUR": [-51.0] * n,
        "Order ID": [f"ID-{i}" for i in range(n)],
        uv.KOSTEN_KOLOM: [-1.0] * n,
        uv.WAARDE_KOLOM: [-50.0] * n,
        "_kosten_eur": [-1.0] * n,
        "_waarde_eur": [-50.0] * n,
        "_koers_eur": [5.0] * n,
    }
    data.update(extra)
    return pd.DataFrame(data)


class _MetRequest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def _in_request(self, functie, *args, **kwargs):
        with self.app.test_request_context(), redirect_stdout(io.StringIO()) as uitvoer:
            resultaat = functie(*args, **kwargs)
            meldingen = haal_meldingen()
        return resultaat, meldingen, uitvoer.getvalue()


class TestOrderIdMeldingen(_MetRequest):
    def test_alle_echte_ids_goed(self):
        _, meldingen, _ = self._in_request(uv._meld_order_ids, [UUID_1, UUID_2], 2)
        self.assertEqual([(m["categorie"], m["niveau"]) for m in meldingen], [(CATEGORIE_ORDER_IDS, GOED)])
        self.assertEqual(meldingen[0]["tekst"], "Alle 2 transacties hebben een echte Order ID.")

    def test_deels_synthetisch_info(self):
        _, meldingen, _ = self._in_request(uv._meld_order_ids, [UUID_1, None, None], 3)
        self.assertEqual(meldingen[0]["niveau"], INFO)
        self.assertTrue(meldingen[0]["tekst"].startswith("2 van 3 transacties zonder Order ID"))

    def test_mismatch_let_op(self):
        _, meldingen, _ = self._in_request(uv._meld_order_ids, [UUID_1, None, None], 2)
        self.assertEqual(meldingen[0]["niveau"], LET_OP)
        self.assertIn("(3)", meldingen[0]["tekst"])
        self.assertIn("(2)", meldingen[0]["tekst"])
        self.assertIn("kan", meldingen[0]["tekst"])
        # Geen Order ID-waarden in de tekst.
        self.assertNotIn(UUID_1, meldingen[0]["tekst"])

    def _excel(self, order_ids):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Datum", "Order ID"])
        for i, order_id in enumerate(order_ids):
            ws.append([f"0{i + 1}-01-2024", order_id])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_bepaal_order_ids_ongewijzigd_en_meldt(self):
        df = pd.DataFrame({
            "Datum": ["01-01-2024", "02-01-2024"], "Tijd": ["10:00", "11:00"], "Product": ["A", "B"],
            "ISIN": ["X1", "X2"], "Aantal": [1.0, 2.0], "Totaal EUR": [-10.0, -20.0],
        })
        uit, meldingen, _ = self._in_request(uv._bepaal_order_ids, self._excel([UUID_1, None]), df)
        self.assertEqual(uit["Order ID"].iloc[0], UUID_1)
        self.assertTrue(uit["Order ID"].iloc[1].startswith("SYN-"))
        self.assertTrue(uit["Order ID"].iloc[1].endswith("-0"))
        self.assertEqual([m["niveau"] for m in meldingen], [INFO])


class TestPortfolioCodeMelding(_MetRequest):
    def _roep_aan(self, match, missing_ids):
        df = _rijen(3)
        with patch("upload_verwerking.find_matching_code", return_value=(match, missing_ids)), \
             patch("upload_verwerking.generate_code", return_value="NEW"):
            return self._in_request(uv._vind_of_maak_portfolio_code, MagicMock(), df, "")

    def test_nieuwe_portfolio(self):
        (code, match, rijen), meldingen, _ = self._roep_aan(None, None)
        self.assertEqual(code, "NEW")
        self.assertEqual(len(rijen), 3)
        self.assertEqual(meldingen[0]["categorie"], CATEGORIE_OPSLAAN)
        self.assertEqual(meldingen[0]["tekst"], "Nieuwe portfolio aangemaakt met 3 transacties.")

    def test_bestaande_aangevuld(self):
        (code, _, rijen), meldingen, _ = self._roep_aan("ABC", {"ID-1"})
        self.assertEqual((code, len(rijen)), ("ABC", 1))
        self.assertEqual(meldingen[0]["tekst"], "Bestaande portfolio aangevuld: 1 nieuwe transacties.")

    def test_bestaande_zonder_nieuwe(self):
        (_, _, rijen), meldingen, _ = self._roep_aan("ABC", set())
        self.assertTrue(rijen.empty)
        self.assertEqual(meldingen[0]["tekst"], "Bestaande portfolio herkend: geen nieuwe transacties.")


class TestNieuweRijenKwaliteit(_MetRequest):
    def test_alles_compleet_geen_meldingen(self):
        _, meldingen, _ = self._in_request(uv._meld_nieuwe_rijen_kwaliteit, _rijen(2))
        self.assertEqual(meldingen, [])

    def test_lege_invoer_geen_meldingen(self):
        _, meldingen, _ = self._in_request(uv._meld_nieuwe_rijen_kwaliteit, _rijen(2).iloc[0:0])
        self.assertEqual(meldingen, [])

    def test_ontbrekende_kolommen(self):
        rijen = _rijen(2).drop(columns=[uv.KOSTEN_KOLOM, uv.WAARDE_KOLOM])
        _, meldingen, _ = self._in_request(uv._meld_nieuwe_rijen_kwaliteit, rijen)
        per = _per_sleutel(meldingen)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_EXCEL_KOSTEN]["niveau"], LET_OP)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE]["niveau"], LET_OP)
        self.assertIn("ontbreekt", per[uv.DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE]["tekst"])

    def test_nan_waarde_telt_alleen_gewone_aankopen(self):
        rijen = _rijen(
            4,
            Aantal=[10.0, 10.0, -5.0, 10.0],          # rij 2 is een verkoop
            Beurs=["EAM", "EAM", "EAM", "DEG"],        # rij 3 is een corporate action
            _waarde_eur=[None, -50.0, None, None],
        )
        _, meldingen, _ = self._in_request(uv._meld_nieuwe_rijen_kwaliteit, rijen)
        per = _per_sleutel(meldingen)
        # Gewone aankopen: rij 0 en 1; daarvan mist alleen rij 0 een waarde.
        self.assertTrue(per[uv.DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE]["tekst"].startswith("1 van de 2 nieuwe aankopen"))
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS]["niveau"], INFO)
        self.assertTrue(per[uv.DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS]["tekst"].startswith("1 corporate-action-rijen"))
        # Losse NaN-kosten worden bewust niet gemeld.
        self.assertNotIn(uv.DIAGNOSTIEK_SLEUTEL_EXCEL_KOSTEN, per)

    def test_non_tradeable_telt_als_corporate_action(self):
        rijen = _rijen(2, Product=["BYD CO LTD - NON TRADEABLE", "GEWOON"], Beurs=["TDG", "EAM"])
        _, meldingen, _ = self._in_request(uv._meld_nieuwe_rijen_kwaliteit, rijen)
        self.assertIn(uv.DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS, _per_sleutel(meldingen))


class _FakeCursor:
    """Registreert execute-aanroepen; rowcount/exception per aanroep instelbaar."""

    def __init__(self, uitkomsten):
        self.uitkomsten = list(uitkomsten)  # per execute: 1, 0 of een exception
        self.aanroepen = []
        self.rowcount = -1

    def execute(self, sql, params):
        self.aanroepen.append((sql, params))
        uitkomst = self.uitkomsten.pop(0)
        if isinstance(uitkomst, BaseException):
            raise uitkomst
        self.rowcount = uitkomst


class TestInsertRegressie(_MetRequest):
    def _tickers(self, rijen):
        return {(isin, beurs): "T.AS" for isin, beurs in zip(rijen["ISIN"], rijen["Beurs"])}

    def test_zelfde_sql_en_parameters(self):
        rijen = _rijen(1, _kosten_eur=[None], _waarde_eur=[-50.0])
        cur = _FakeCursor([1])
        self._in_request(uv._insert_nieuwe_transacties, cur, "ABC", rijen, self._tickers(rijen))
        sql, params = cur.aanroepen[0]
        self.assertEqual(sql, VERWACHTE_INSERT_SQL)
        self.assertEqual(params, (
            "ABC", pd.Timestamp("2024-01-02").date(), "FONDS 0", "NL0000000000", "EAM", "T.AS",
            10.0, 5.0, -51.0, "ID-0", "FONDS 0", None, -50.0, "10:00",
        ))

    def test_return_telt_pogingen_zonder_exception_incl_conflict(self):
        rijen = _rijen(4)
        cur = _FakeCursor([1, 0, ValueError("x"), 1])
        aantal, _, _ = self._in_request(uv._insert_nieuwe_transacties, cur, "ABC", rijen, self._tickers(rijen))
        self.assertEqual(aantal, 3)  # 2 ingevoegd + 1 conflict; de exception telt niet
        self.assertEqual(len(cur.aanroepen), 4)  # na een fout gaat de lus gewoon door

    def test_exception_wordt_nog_steeds_ingeslikt(self):
        rijen = _rijen(1)
        cur = _FakeCursor([psycopg2.DatabaseError("kapot")])
        aantal, _, _ = self._in_request(uv._insert_nieuwe_transacties, cur, "ABC", rijen, self._tickers(rijen))
        self.assertEqual(aantal, 0)

    def test_werkt_buiten_request_context(self):
        rijen = _rijen(1)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(uv._insert_nieuwe_transacties(_FakeCursor([1]), "ABC", rijen, self._tickers(rijen)), 1)


class TestInsertMeldingen(_MetRequest):
    def _insert(self, uitkomsten):
        rijen = _rijen(len(uitkomsten))
        tickers = {(isin, beurs): "T.AS" for isin, beurs in zip(rijen["ISIN"], rijen["Beurs"])}
        return self._in_request(uv._insert_nieuwe_transacties, _FakeCursor(uitkomsten), "ABC", rijen, tickers)

    def test_opgeslagen_en_genegeerd(self):
        _, meldingen, uitvoer = self._insert([1, 1, 0])
        per = _per_sleutel(meldingen)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN]["niveau"], GOED)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN]["tekst"], "2 transacties opgeslagen.")
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_GENEGEERD]["niveau"], INFO)
        self.assertTrue(per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_GENEGEERD]["tekst"].startswith("1 transacties"))
        self.assertNotIn(uv.DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT, per)
        self.assertNotIn("WARN", uitvoer)

    def test_python_fout_goed_plus_fout_zonder_rollback_zin(self):
        _, meldingen, uitvoer = self._insert([1, KeyError("geheim detail")])
        per = _per_sleutel(meldingen)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN]["niveau"], GOED)
        fout = per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT]
        self.assertEqual(fout["niveau"], FOUT)
        self.assertEqual(fout["tekst"], "1 transacties niet opgeslagen (eerste fout: KeyError).")
        self.assertNotIn("geheim detail", fout["tekst"])
        self.assertIn("[upload] WARN 1 transactie(s) niet opgeslagen (eerste fout: KeyError)", uitvoer)

    def test_psycopg2_fout_alleen_fout_geen_goed(self):
        _, meldingen, uitvoer = self._insert([1, 0, psycopg2.DatabaseError("x"), psycopg2.DatabaseError("y")])
        per = _per_sleutel(meldingen)
        self.assertEqual(list(per), [uv.DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT])
        fout = per[uv.DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT]
        self.assertEqual(fout["niveau"], FOUT)
        self.assertIn("2 transacties niet opgeslagen (eerste fout: DatabaseError)", fout["tekst"])
        self.assertIn("kan de database de hele upload hebben teruggedraaid", fout["tekst"])
        self.assertIn("[upload] WARN 2 transactie(s)", uitvoer)
        self.assertTrue(uitvoer.isascii())


class TestDividendMeldingen(_MetRequest):
    def _record(self, valuta="USD", netto=1.0, herinvesteerd=False, isin="US1", datum="2024-03-01"):
        return {"datum": pd.Timestamp(datum).date(), "product": f"P {isin}", "isin": isin, "valuta": valuta,
                "bruto_eur": netto, "belasting_eur": 0.0, "netto_eur": netto, "dividend_id": "D",
                "herinvesteerd": herinvesteerd}

    def test_samenvatting_goed(self):
        records = [self._record("EUR"), self._record("USD"), self._record("USD", herinvesteerd=True)]
        _, meldingen, _ = self._in_request(uv._meld_dividend_records, records)
        self.assertEqual(len(meldingen), 1)
        self.assertEqual(meldingen[0]["categorie"], CATEGORIE_DIVIDEND)
        self.assertEqual(meldingen[0]["niveau"], GOED)
        self.assertEqual(meldingen[0]["tekst"], "3 uitkeringen verwerkt: 1 in EUR, 2 in vreemde valuta gekoppeld "
                                                "aan een valutaconversie, 1 herinvesteerd.")

    def test_geen_records_info(self):
        _, meldingen, _ = self._in_request(uv._meld_dividend_records, [])
        self.assertEqual(meldingen[0]["niveau"], INFO)

    def test_zonder_conversie_let_op_per_uitkering(self):
        records = [self._record("EUR"), self._record("USD", netto=None, isin="US9")]
        _, meldingen, _ = self._in_request(uv._meld_dividend_records, records)
        per = _per_sleutel(meldingen)
        self.assertEqual(per[uv.DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING]["niveau"], INFO)
        self.assertIn("1 zonder valutaconversie", per[uv.DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING]["tekst"])
        los = per["dividend:US9:2024-03-01"]
        self.assertEqual(los["niveau"], LET_OP)
        self.assertEqual(los["tekst"], "Dividend P US9 op 2024-03-01 (USD): geen valutaconversie gevonden, "
                                       "bedrag onbekend.")

    def test_boven_maximum_een_samenvattende_let_op(self):
        n = uv.MAX_LOSSE_DIVIDEND_MELDINGEN + 3
        records = [self._record("USD", netto=None, isin=f"US{i}") for i in range(n)]
        _, meldingen, _ = self._in_request(uv._meld_dividend_records, records)
        losse = [m for m in meldingen if m["sleutel"].startswith("dividend:")]
        self.assertEqual(len(losse), uv.MAX_LOSSE_DIVIDEND_MELDINGEN)
        overig = _per_sleutel(meldingen)[uv.DIAGNOSTIEK_SLEUTEL_DIVIDEND_OVERIG]
        self.assertEqual((overig["niveau"], overig["tekst"]), (LET_OP, "Nog 3 uitkeringen zonder valutaconversie."))

    def _met_bestand2(self, functie, *args):
        data = {"bestand2": (io.BytesIO(b"x"), "rekening.xlsx")}
        with self.app.test_request_context(method="POST", data=data, content_type="multipart/form-data"), \
             redirect_stdout(io.StringIO()):
            functie(*args)
            return haal_meldingen()

    @patch("upload_verwerking.save_dividenden")
    @patch("upload_verwerking.verwerk_rekeningoverzicht")
    def test_verwerk_bestand_meldt_en_slaat_ongewijzigd_op(self, mock_verwerk, mock_save):
        records = [self._record("EUR")]
        mock_verwerk.return_value = records
        meldingen = self._met_bestand2(uv._verwerk_dividend_bestand_indien_aanwezig, "ABC")
        mock_save.assert_called_once_with("ABC", records)
        self.assertEqual([m["sleutel"] for m in meldingen], [uv.DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING])

    def test_niet_opslaan_met_bestand2_info(self):
        meldingen = self._met_bestand2(uv._meld_dividend_bestand_genegeerd)
        self.assertEqual([(m["niveau"], m["sleutel"]) for m in meldingen],
                         [(INFO, uv.DIAGNOSTIEK_SLEUTEL_DIVIDEND_NIET_OPSLAAN)])

    def test_niet_opslaan_zonder_bestand2_niets(self):
        _, meldingen, _ = self._in_request(uv._meld_dividend_bestand_genegeerd)
        self.assertEqual(meldingen, [])


if __name__ == "__main__":
    unittest.main()
