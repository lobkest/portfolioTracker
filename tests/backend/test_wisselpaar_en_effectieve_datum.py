"""ISIN-wissel bij een split (XELA, 1:3 op 26-01-2021) en de koppeling van DeGiro-boekingen aan Yahoo-splits.

De rijen zijn de echte XELA-transacties uit de Transacties-tab; ISIN's en beurzen staan daar niet bij en zijn
PLAATSHOUDERS (OUD_ISIN/NIEUW_ISIN, beurs "DEG" of "NSQ"). Beide varianten zijn getest: de wisselrijen op DEG zonder
ticker, en op een gewone beurs mét ticker.
"""
import datetime
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

import pandas as pd
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from diagnostiek import haal_meldingen, LET_OP, INFO
from portfolio_calc import (
    bepaal_split_boekingen, compute_per_ticker, compute_per_ticker_koers_en_aankopen, compute_split_adjusted_shares,
    compute_value_over_time, meld_split_koppeling,
)
from statistieken import _bouw_xirr_cashflows
from split_correctie import Wisselpaar, bepaal_effectieve_datums, isin_ketens, vind_wisselparen

OUD_ISIN = "PLAATSHOUDER-OUD-ISIN"
NIEUW_ISIN = "PLAATSHOUDER-NIEUW-ISIN"
XELA_SPLITS = {"2021-01-26": 1 / 3, "2022-07-26": 0.05, "2023-05-15": 0.005}
MIDDERNACHT = datetime.time(0, 0)
NAN = float("nan")


def _xela_rijen(wisselbeurs, wisselticker, wisseldatum="2021-01-26", laatste_datum="2021-01-27"):
    kolommen = ["datum", "tijd", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "transactiekosten"]
    rijen = [
        ("2021-01-08", datetime.time(17, 57), "EXELA TECHNOLOGIES IN", OUD_ISIN, "NSQ", "XELA", 13, 0.41, -5.89, -0.54),
        ("2021-01-25", datetime.time(21, 36), "EXELA TECHNOLOGIES IN", OUD_ISIN, "NSQ", "XELA", 1, 0.71, -1.21, -0.50),
        (wisseldatum, MIDDERNACHT, "EXELA TECHNOLOGIES IN", OUD_ISIN, wisselbeurs, wisselticker, -14, 0.72, 10.06, NAN),
        (wisseldatum, MIDDERNACHT, "EXELA TECHNOLOGIES INC. - COMMON STOCK", NIEUW_ISIN, wisselbeurs, wisselticker, 4, 2.16, -8.62, NAN),
        (laatste_datum, datetime.time(16, 17), "EXELA TECHNOLOGIES INC. - COMMON STOCK", NIEUW_ISIN, "NSQ", "XELA", -4, 1.91, 7.14, -0.51),
    ]
    df = pd.DataFrame(rijen, columns=kolommen)
    df["datum"] = pd.to_datetime(df["datum"])
    return df


def _cumulatief(df, kolom="datum"):
    """Ruw aantal per ticker XELA na elke dag, gesorteerd op `kolom`."""
    xela = df[df["ticker"] == "XELA"].sort_values(kolom, kind="mergesort")
    per_dag = xela.groupby(kolom)["aantal"].sum().cumsum()
    return {d.strftime("%Y-%m-%d"): float(a) for d, a in per_dag.items()}


class _MetRequest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def _in_request(self, functie, *args):
        with self.app.test_request_context(), redirect_stdout(io.StringIO()):
            resultaat = functie(*args)
            return resultaat, haal_meldingen()


class TestVindWisselparen(unittest.TestCase):
    def test_xela_paar_wordt_herkend_en_gewone_trades_niet(self):
        paren, onduidelijk = vind_wisselparen(_xela_rijen("DEG", None))
        self.assertEqual(onduidelijk, [])
        self.assertEqual(len(paren), 1)
        paar = paren[0]
        self.assertEqual((paar.oud_isin, paar.nieuw_isin), (OUD_ISIN, NIEUW_ISIN))
        self.assertEqual((paar.oud_aantal, paar.nieuw_aantal), (14.0, 4.0))
        self.assertEqual(paar.datum, pd.Timestamp("2021-01-26"))
        self.assertEqual((paar.oud_rijen, paar.nieuw_rijen), ((2,), (3,)))

    def test_rij_met_kosten_of_andere_tijd_is_geen_wissel(self):
        df = _xela_rijen("DEG", None)
        df.loc[2, "transactiekosten"] = -0.5
        self.assertEqual(vind_wisselparen(df)[0], [])
        df = _xela_rijen("DEG", None)
        df.loc[3, "tijd"] = datetime.time(10, 0)
        self.assertEqual(vind_wisselparen(df)[0], [])

    def test_zelfde_isin_aan_beide_kanten_is_geen_wissel(self):
        df = _xela_rijen("DEG", None)
        df.loc[3, "isin"] = OUD_ISIN
        self.assertEqual(vind_wisselparen(df), ([], []))

    def test_meerdere_isins_per_kant_is_onduidelijk(self):
        df = _xela_rijen("DEG", None)
        df.loc[len(df)] = df.loc[3]
        df.loc[len(df) - 1, "isin"] = "PLAATSHOUDER-DERDE-ISIN"
        paren, onduidelijk = vind_wisselparen(df)
        self.assertEqual(paren, [])
        self.assertEqual(onduidelijk, [pd.Timestamp("2021-01-26")])

    def test_zonder_tijdkolom_niets_herkennen(self):
        self.assertEqual(vind_wisselparen(_xela_rijen("DEG", None).drop(columns="tijd")), ([], []))


class TestXelaRuweAantallen(_MetRequest):
    def _controleer(self, wisselbeurs, wisselticker):
        df, meldingen = self._in_request(compute_split_adjusted_shares, _xela_rijen(wisselbeurs, wisselticker))
        # Oude en nieuwe ISIN vormen samen één aantal per ticker; de gesloten positie eindigt op 0.
        self.assertEqual(_cumulatief(df), {"2021-01-08": 13.0, "2021-01-25": 14.0, "2021-01-26": 4.0, "2021-01-27": 0.0})
        self.assertEqual(df["is_wisselrij"].tolist(), [False, False, True, True, False])
        self.assertEqual(df["ticker"].tolist(), ["XELA"] * 5)
        # De wissel wordt pas gemeld bij de koppeling aan Yahoo (meld_split_koppeling), in één regel; ook geen
        # "geen splitfactor"-melding voor de wisselrijen op DEG.
        self.assertEqual(meldingen, [])

    def test_wisselrijen_op_deg_zonder_ticker_krijgen_de_ticker_via_isin(self):
        self._controleer("DEG", None)

    def test_wisselrijen_op_gewone_beurs_met_ticker(self):
        self._controleer("NSQ", "XELA")

    def test_adj_aantal_ongewijzigd_want_geen_conversierij(self):
        df, _ = self._in_request(compute_split_adjusted_shares, _xela_rijen("DEG", None))
        self.assertEqual(df["adj_aantal"].tolist(), [13.0, 1.0, -14.0, 4.0, -4.0])

    def test_effectieve_datum_standaard_gelijk_aan_datum(self):
        df, _ = self._in_request(compute_split_adjusted_shares, _xela_rijen("DEG", None))
        self.assertEqual(df["effectieve_datum"].tolist(), df["datum"].tolist())


class TestEffectieveDatum(_MetRequest):
    def _boekingen_en_df(self, **kwargs):
        df, _ = self._in_request(compute_split_adjusted_shares, _xela_rijen("DEG", None, **kwargs))
        return df, bepaal_split_boekingen(df)

    def test_boeking_op_dezelfde_dag_als_yahoo(self):
        df, boekingen = self._boekingen_en_df()
        self.assertEqual([(b.ticker, b.patroon, b.gebeurtenis.oud_aantal, b.gebeurtenis.nieuw_aantal) for b in boekingen],
                         [("XELA", "wisselpaar", 14.0, 4.0)])
        uit, resultaat = bepaal_effectieve_datums(df, boekingen, {"XELA": XELA_SPLITS})
        self.assertEqual(len(resultaat.gekoppeld), 1)
        self.assertEqual(uit["effectieve_datum"].tolist(), df["datum"].tolist())
        # De splits van 2022 en 2023 vallen na de verkoop: geen stukken gehouden, dus geen melding.
        self.assertEqual((resultaat.zonder_yahoo, resultaat.zonder_boeking), ([], []))

    def test_een_dag_te_laat_geboekt_schuift_naar_yahoo_datum(self):
        df, boekingen = self._boekingen_en_df(wisseldatum="2021-01-27", laatste_datum="2021-01-28")
        uit, resultaat = bepaal_effectieve_datums(df, boekingen, {"XELA": XELA_SPLITS})
        self.assertEqual(len(resultaat.gekoppeld), 1)
        self.assertEqual(uit["effectieve_datum"].dt.strftime("%Y-%m-%d").tolist(),
                         ["2021-01-08", "2021-01-25", "2021-01-26", "2021-01-26", "2021-01-28"])
        # Zonder verschuiving zou het ruwe aantal op 26-01 nog 14 zijn (oude basis x nieuwe koers).
        self.assertEqual(_cumulatief(df)["2021-01-26" if "2021-01-26" in _cumulatief(df) else "2021-01-25"], 14.0)
        self.assertEqual(_cumulatief(uit, "effectieve_datum")["2021-01-26"], 4.0)
        # Het origineel is niet aangepast, en de datum (voor cashflows) blijft de boekdatum.
        self.assertEqual(df["effectieve_datum"].dt.strftime("%Y-%m-%d").tolist()[2], "2021-01-27")
        self.assertEqual(uit["datum"].dt.strftime("%Y-%m-%d").tolist()[2], "2021-01-27")

    def test_zonder_yahoo_split_blijft_boekdatum(self):
        df, boekingen = self._boekingen_en_df()
        uit, resultaat = bepaal_effectieve_datums(df, boekingen, {"XELA": {}})
        self.assertEqual(resultaat.gekoppeld, [])
        self.assertEqual(resultaat.zonder_yahoo, boekingen)
        self.assertEqual(uit["effectieve_datum"].tolist(), df["datum"].tolist())

    def test_yahoo_split_zonder_boeking_terwijl_stukken_gehouden(self):
        df, _ = self._in_request(compute_split_adjusted_shares, _xela_rijen("DEG", None))
        alleen_aankopen = df.iloc[:2].copy()
        uit, resultaat = bepaal_effectieve_datums(alleen_aankopen, [], {"XELA": {"2021-01-26": 1 / 3}})
        self.assertEqual(resultaat.zonder_boeking, [("XELA", pd.Timestamp("2021-01-26"), 1 / 3)])
        self.assertEqual(uit["effectieve_datum"].tolist(), alleen_aankopen["datum"].tolist())

    def test_meldingen_voor_alle_drie_de_uitkomsten(self):
        df, boekingen = self._boekingen_en_df(wisseldatum="2021-01-27", laatste_datum="2021-01-28")
        _, gekoppeld = bepaal_effectieve_datums(df, boekingen, {"XELA": XELA_SPLITS})
        _, meldingen = self._in_request(meld_split_koppeling, gekoppeld)
        self.assertEqual([(m["niveau"], m["sleutel"]) for m in meldingen], [(INFO, "split_koppeling:XELA:2021-01-26")])
        self.assertEqual(meldingen[0]["tekst"],
                         f"Reverse split 1:3 van XELA op 26-01-2021 met ISIN-wissel ({OUD_ISIN} -> {NIEUW_ISIN}): "
                         f"14 stuks uit, 4 stuks in, fractie 0,67 stuk contant uitbetaald. DeGiro boekte op 27-01-2021; "
                         f"het aantal telt mee vanaf Yahoo's datum.")

        _, geen_yahoo = bepaal_effectieve_datums(df, boekingen, {"XELA": {}})
        _, meldingen = self._in_request(meld_split_koppeling, geen_yahoo)
        self.assertEqual([(m["niveau"], m["sleutel"]) for m in meldingen], [(LET_OP, "split_zonder_yahoo:XELA:2021-01-27")])

        _, geen_boeking = bepaal_effectieve_datums(df.iloc[:2], [], {"XELA": {"2021-01-26": 1 / 3}})
        _, meldingen = self._in_request(meld_split_koppeling, geen_boeking)
        self.assertEqual([(m["niveau"], m["sleutel"]) for m in meldingen], [(LET_OP, "split_zonder_boeking:XELA:2021-01-26")])
        self.assertIn("(reverse split 1:3)", meldingen[0]["tekst"])

    def test_wissel_op_yahoo_datum_geeft_ook_een_regel(self):
        df, boekingen = self._boekingen_en_df()
        _, gekoppeld = bepaal_effectieve_datums(df, boekingen, {"XELA": XELA_SPLITS})
        _, meldingen = self._in_request(meld_split_koppeling, gekoppeld)
        [m] = meldingen
        self.assertIn("Reverse split 1:3 van XELA op 26-01-2021", m["tekst"])
        self.assertNotIn("DeGiro boekte", m["tekst"])


class TestConversierijPatroon(_MetRequest):
    """Het bestaande patroon (conversierij met koers 0 + DEG-rijen) blijft werken en levert dezelfde soort boeking."""

    def _df(self):
        return pd.DataFrame([
            (pd.Timestamp("2024-01-01"), "US1", "ACME", "NSY", "ACME", 10.0, 100.0, -1000.0),
            (pd.Timestamp("2024-02-01"), "US1", "ACME", "DEG", None, 30.0, 0.0, 0.0),
            (pd.Timestamp("2024-02-02"), "US1", "ACME", "NSY", "ACME", 40.0, 0.0, 0.0),
        ], columns=["datum", "isin", "product", "beurs", "ticker", "aantal", "koers", "totaal_eur"])

    def test_boeking_en_koppeling_met_een_dag_verschil(self):
        df, _ = self._in_request(compute_split_adjusted_shares, self._df())
        self.assertEqual(df["adj_aantal"].tolist(), [40.0, 30.0, 40.0])
        boekingen = bepaal_split_boekingen(df)
        self.assertEqual(len(boekingen), 1)
        self.assertEqual((boekingen[0].ticker, boekingen[0].patroon), ("ACME", "conversierij"))
        self.assertEqual((boekingen[0].gebeurtenis.oud_aantal, boekingen[0].gebeurtenis.nieuw_aantal), (10.0, 40.0))
        self.assertEqual(sorted(boekingen[0].rijen), [1, 2])

        uit, resultaat = bepaal_effectieve_datums(df, boekingen, {"ACME": {"2024-02-01": 4.0}})
        self.assertEqual(len(resultaat.gekoppeld), 1)
        self.assertEqual(uit["effectieve_datum"].dt.strftime("%Y-%m-%d").tolist(), ["2024-01-01", "2024-02-01", "2024-02-01"])


def _paar(datum, oud, nieuw):
    return Wisselpaar(pd.Timestamp(datum), oud, nieuw, (), (), 1.0, 1.0)


class TestIsinKetens(unittest.TestCase):
    def test_geen_wissel_geeft_lege_mapping(self):
        self.assertEqual(isin_ketens([]), {})

    def test_een_wissel(self):
        self.assertEqual(isin_ketens([_paar("2021-01-26", "A", "B")]), {"A": "B", "B": "B"})

    def test_keten_volgt_de_datum(self):
        paren = [_paar("2023-05-15", "B", "C"), _paar("2021-01-26", "A", "B")]
        self.assertEqual(isin_ketens(paren), {"A": "C", "B": "C", "C": "C"})


class TestWisselrijenUitDePrijscheck(_MetRequest):
    def _excel(self, rijen):
        import upload_verwerking
        return pd.DataFrame({
            "Datum": rijen["datum"], "Tijd": rijen["tijd"], "Product": rijen["product"], "ISIN": rijen["isin"],
            "Beurs": rijen["beurs"], "Aantal": rijen["aantal"], "_koers_eur": rijen["koers"],
            upload_verwerking.KOSTEN_KOLOM: rijen["transactiekosten"],
        })

    def _posities(self, excel):
        import upload_verwerking
        return {(isin, beurs): transacties for _, isin, beurs, transacties in upload_verwerking._bouw_posities(excel)}

    def test_upload_wissel_is_een_positie_met_alleen_marktransacties(self):
        posities = self._posities(self._excel(_xela_rijen("NSQ", "XELA")))
        # De -14 @ 0,72 en +4 @ 2,16 tellen niet mee; de aankopen van de oude en de verkoop van de nieuwe ISIN wel.
        self.assertEqual(posities, {(NIEUW_ISIN, "NSQ"): [
            {"datum": "2021-01-08", "koers": 0.41}, {"datum": "2021-01-25", "koers": 0.71},
            {"datum": "2021-01-27", "koers": 1.91}]})

    def test_upload_zonder_tijd_blijven_twee_posities(self):
        rijen = _xela_rijen("NSQ", "XELA")
        rijen["tijd"] = None
        self.assertEqual(set(self._posities(self._excel(rijen))), {(OUD_ISIN, "NSQ"), (NIEUW_ISIN, "NSQ")})

    def test_upload_beide_isins_van_de_keten_krijgen_dezelfde_ticker(self):
        import upload_verwerking
        excel = self._excel(_xela_rijen("NSQ", "XELA"))
        self.assertEqual(upload_verwerking._ticker_per_isin_beurs(excel, {(NIEUW_ISIN, "NSQ"): "XELA"}),
                         {(OUD_ISIN, "NSQ"): "XELA", (NIEUW_ISIN, "NSQ"): "XELA"})

    def test_upload_nieuwe_isin_erft_bekende_ticker_van_de_oude(self):
        from unittest.mock import patch
        import upload_verwerking
        excel = self._excel(_xela_rijen("NSQ", "XELA"))

        def fake_check(posities, bekende_tickers):
            return [{"ticker": bekende_tickers.get((isin, beurs))} for _, isin, beurs, _t in posities]

        with patch.object(upload_verwerking, "db_get_bekende_tickers", return_value={(OUD_ISIN, "NSQ"): "XELA"}), \
             patch.object(upload_verwerking, "vind_tickers_met_snelle_prijscheck_parallel", side_effect=fake_check) as mock:
            tickers, _meldingen = self._in_request(upload_verwerking._ticker_resolutie_opslaan, None, "ZZTEST", excel, False)

        self.assertEqual(mock.call_args.kwargs["bekende_tickers"], {(NIEUW_ISIN, "NSQ"): "XELA"})
        self.assertEqual(tickers, {(OUD_ISIN, "NSQ"): "XELA", (NIEUW_ISIN, "NSQ"): "XELA"})

    def test_bekende_ticker_van_de_eind_isin_gaat_voor(self):
        import upload_verwerking
        bekend = {(OUD_ISIN, "NSQ"): "OUD.TICKER", (NIEUW_ISIN, "NSQ"): "XELA"}
        eind_van = {OUD_ISIN: NIEUW_ISIN, NIEUW_ISIN: NIEUW_ISIN}
        self.assertEqual(upload_verwerking._bekende_ticker_per_positie(bekend, eind_van), {(NIEUW_ISIN, "NSQ"): "XELA"})

    def test_niet_opslaan_ziet_een_samengevoegde_positie(self):
        from unittest.mock import patch
        import upload_verwerking
        excel = self._excel(_xela_rijen("NSQ", "XELA"))
        with patch.object(upload_verwerking, "basis_ticker_zekerheid_parallel",
                          side_effect=lambda posities: [{"ticker": "XELA"} for _ in posities]):
            (tickers, zekerheid, ruw), _meldingen = self._in_request(upload_verwerking.ticker_resolutie_niet_opslaan, excel)

        self.assertEqual([(p["isin"], p["beurs"], len(p["transacties"])) for p in ruw], [(NIEUW_ISIN, "NSQ", 3)])
        self.assertEqual([z["isin"] for z in zekerheid], [NIEUW_ISIN])
        self.assertEqual(tickers, {(OUD_ISIN, "NSQ"): "XELA", (NIEUW_ISIN, "NSQ"): "XELA"})

    def _groepen(self, df):
        from unittest.mock import patch
        import portfolio_orchestratie
        df = df.copy()
        df["echte_naam"] = df["product"]
        df["waarde_eur"] = None
        df["wisselkoers"] = None
        df["autofx_kosten"] = None
        df["datum"] = df["datum"].dt.date
        rijen = [tuple(r) for r in df[portfolio_orchestratie.TRANSACTIE_KOLOMMEN].itertuples(index=False)]
        with patch.object(portfolio_orchestratie, "db_get_portfolio_naam_en_transacties", return_value=("Test", rijen)),              patch.object(portfolio_orchestratie, "get_prices", side_effect=AssertionError("koersen opgehaald")),              patch.object(portfolio_orchestratie, "haal_portfolio_basis", side_effect=AssertionError("basis geladen")):
            return dict(portfolio_orchestratie.ticker_zekerheid_groepen("ZZTEST"))

    def test_ticker_zekerheid_groepen_voegen_wissel_samen_zonder_wisselrijen(self):
        groepen = self._groepen(_xela_rijen("NSQ", "XELA"))
        self.assertEqual(set(groepen), {(NIEUW_ISIN, "NSQ")})
        groep = groepen[(NIEUW_ISIN, "NSQ")]
        self.assertEqual(groep["isins"], [OUD_ISIN, NIEUW_ISIN])
        self.assertEqual(groep["isin"], NIEUW_ISIN)
        self.assertEqual([str(t["datum"]) for t in groep["transacties"]], ["2021-01-08", "2021-01-25", "2021-01-27"])
        self.assertEqual(groep["transacties"][1]["koers"], 0.71)
        self.assertEqual(groep["echte_naam"], "EXELA TECHNOLOGIES INC. - COMMON STOCK")

    def test_ticker_zekerheid_groepen_zonder_tijd_blijven_twee_groepen(self):
        df = _xela_rijen("NSQ", "XELA")
        df["tijd"] = None
        groepen = self._groepen(df)
        self.assertEqual(set(groepen), {(OUD_ISIN, "NSQ"), (NIEUW_ISIN, "NSQ")})
        self.assertEqual(groepen[(OUD_ISIN, "NSQ")]["isins"], [OUD_ISIN])

    def test_ticker_zekerheid_groepen_nieuwe_isin_met_alleen_omboeking_zit_in_de_keten(self):
        groepen = self._groepen(_xela_rijen("NSQ", "XELA").iloc[:4])
        self.assertEqual(set(groepen), {(NIEUW_ISIN, "NSQ")})
        groep = groepen[(NIEUW_ISIN, "NSQ")]
        self.assertEqual(groep["isins"], [OUD_ISIN, NIEUW_ISIN])
        self.assertEqual([str(t["datum"]) for t in groep["transacties"]], ["2021-01-08", "2021-01-25"])

    def test_ticker_zekerheid_groepen_onbekende_code_geeft_none(self):
        from unittest.mock import patch
        import portfolio_orchestratie
        with patch.object(portfolio_orchestratie, "db_get_portfolio_naam_en_transacties", return_value=(None, None)):
            self.assertIsNone(portfolio_orchestratie.ticker_zekerheid_groepen("ZZTEST"))


class TestWaardepadenOpRuwAantal(_MetRequest):
    """Waarde = ruw aantal x koers, met het aantal vanaf de effectieve datum en het geld vanaf de boekdatum."""

    def _prijzen(self):
        dagen = pd.bdate_range("2021-01-08", "2021-01-29")
        return pd.DataFrame({"XELA": 1.0}, index=dagen)

    def _df_te_laat_geboekt(self):
        df, _ = self._in_request(compute_split_adjusted_shares,
                                 _xela_rijen("DEG", None, wisseldatum="2021-01-27", laatste_datum="2021-01-28"))
        uit, _ = bepaal_effectieve_datums(df, bepaal_split_boekingen(df), {"XELA": XELA_SPLITS})
        return df, uit

    def test_waarde_gebruikt_effectieve_datum_en_geld_de_boekdatum(self):
        zonder, met = self._df_te_laat_geboekt()
        w_zonder = compute_value_over_time(zonder, self._prijzen())
        w_met = compute_value_over_time(met, self._prijzen())
        dag = pd.Timestamp("2021-01-26")
        self.assertEqual(w_zonder.loc[dag, "waarde"], 14.0)  # oude basis x nieuwe koers: de spike
        self.assertEqual(w_met.loc[dag, "waarde"], 4.0)
        # Het geld (10,06 binnen, 8,62 uit) staat nog op de boekdatum 27-01.
        self.assertEqual(w_met.loc[dag, "geinvesteerd"], w_zonder.loc[dag, "geinvesteerd"])
        self.assertNotEqual(w_met.loc[pd.Timestamp("2021-01-27"), "geinvesteerd"], w_met.loc[dag, "geinvesteerd"])
        self.assertEqual(w_met.loc[pd.Timestamp("2021-01-29"), "waarde"], 0.0)

    def test_per_ticker_holdings_en_markers(self):
        _, met = self._df_te_laat_geboekt()
        uit = compute_per_ticker_koers_en_aankopen(met, self._prijzen())["XELA"]
        holdings = dict(zip(uit["labels"], uit["holdings"]))
        self.assertEqual((holdings["2021-01-25"], holdings["2021-01-26"], holdings["2021-01-28"]), (14.0, 4.0, 0.0))
        # De omboeking (-14 en +4) is geen aan- of verkoop.
        self.assertEqual(uit["aankoop_datums"], ["2021-01-08", "2021-01-25"])
        self.assertEqual(uit["verkoop_datums"], ["2021-01-28"])
        waarde = compute_per_ticker(met, self._prijzen())["XELA"]
        self.assertEqual(dict(zip(waarde["labels"], waarde["waarde"]))["2021-01-26"], 4.0)

    def test_xirr_neemt_wisselrijen_mee_ook_op_deg(self):
        df, _ = self._in_request(compute_split_adjusted_shares, _xela_rijen("DEG", None))
        resultaat = compute_value_over_time(df, self._prijzen())
        cashflows = _bouw_xirr_cashflows(df, resultaat)
        op_wisseldag = sorted(bedrag for datum, bedrag in cashflows if datum == datetime.date(2021, 1, 26))
        self.assertEqual(op_wisseldag, [-8.62, 10.06])

    def test_xirr_slaat_gewone_corporate_actions_nog_steeds_over(self):
        df = pd.DataFrame([
            (pd.Timestamp("2024-01-01"), "US1", "ACME", "NSY", "ACME", 10.0, 100.0, -1000.0),
            (pd.Timestamp("2024-02-01"), "US1", "ACME", "DEG", "ACME", 30.0, 0.0, 5.0),
        ], columns=["datum", "isin", "product", "beurs", "ticker", "aantal", "koers", "totaal_eur"])
        df, _ = self._in_request(compute_split_adjusted_shares, df)
        resultaat = pd.DataFrame({"waarde": [1000.0], "geinvesteerd": [1000.0]}, index=pd.to_datetime(["2024-02-02"]))
        bedragen = [b for _, b in _bouw_xirr_cashflows(df, resultaat)]
        self.assertEqual(bedragen, [-1000.0, 1000.0])


if __name__ == "__main__":
    unittest.main()
