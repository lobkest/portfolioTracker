"""
Unit tests voor de Diagnostiek-meldingen van Deel B (laden): categorieën
Koersen, Splits, ETF-holdings en Laadtijden -- plus regressie dat de
berekeningen (koersen, split-correctie, retry-gedrag) identiek blijven en
dat een cache-hit de nieuwe meldingen opnieuw meegeeft (zonder laadtijden).

Draait geheel offline: database, Yahoo en time.sleep worden gemockt,
app.py wordt niet geïmporteerd (een losse Flask-app levert de context).
"""
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest.mock import patch, MagicMock

import pandas as pd
from flask import Flask

PROJECT_MAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_MAP)

import diagnostiek
import debug_utils
import yahoo_client
import prijzen
import portfolio_calc
import portfolio_orchestratie as po
from diagnostiek import (
    haal_meldingen, CATEGORIE_KOERSEN, CATEGORIE_SPLITS, CATEGORIE_ETF_HOLDINGS, CATEGORIE_LAADTIJDEN,
    GOED, INFO, LET_OP, FOUT,
)


def _per_sleutel(meldingen):
    return {m["sleutel"]: m for m in meldingen}


class _MetRequest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def _in_request(self, functie, *args, **kwargs):
        with self.app.test_request_context(), redirect_stdout(io.StringIO()) as uitvoer:
            resultaat = functie(*args, **kwargs)
            meldingen = haal_meldingen()
        return resultaat, meldingen, uitvoer.getvalue()


class TestLaadtijden(_MetRequest):
    def test_bekende_fase_info_met_leesbare_naam(self):
        _, meldingen, _ = self._in_request(diagnostiek.meld_laadtijd, "basis_ophalen_db (code=ABC)", 1.26)
        self.assertEqual(meldingen, [{
            "categorie": CATEGORIE_LAADTIJDEN, "niveau": INFO,
            "tekst": "Transacties ophalen uit de database: 1,3 s.", "sleutel": "basis_ophalen_db",
        }])

    def test_boven_drempel_let_op(self):
        duur = diagnostiek.DREMPEL_LAADTIJD_LET_OP_SECONDEN + 0.5
        _, meldingen, _ = self._in_request(diagnostiek.meld_laadtijd, "verrijking_totaal", duur)
        self.assertEqual(meldingen[0]["niveau"], LET_OP)

    def test_precies_op_drempel_nog_info(self):
        duur = diagnostiek.DREMPEL_LAADTIJD_LET_OP_SECONDEN
        _, meldingen, _ = self._in_request(diagnostiek.meld_laadtijd, "verrijking_totaal", duur)
        self.assertEqual(meldingen[0]["niveau"], INFO)

    def test_subfase_wordt_niet_gemeld(self):
        _, meldingen, _ = self._in_request(diagnostiek.meld_laadtijd, "verrijking_land_sector", 3.0)
        self.assertEqual(meldingen, [])

    def test_meet_tijd_print_blijft_en_meldt(self):
        def blok():
            with debug_utils.meet_tijd("koersen_ophalen_kern (3 ticker(s))"):
                pass
        _, meldingen, uitvoer = self._in_request(blok)
        self.assertRegex(uitvoer, r"^\[timing\] koersen_ophalen_kern \(3 ticker\(s\)\): \d+\.\d\ds\n$")
        self.assertEqual(meldingen[0]["sleutel"], "koersen_ophalen_kern")

    def test_meet_tijd_zonder_context_geen_crash(self):
        with redirect_stdout(io.StringIO()):
            with debug_utils.meet_tijd("verrijking_totaal"):
                pass
        self.assertEqual(haal_meldingen(), [])

    def test_geen_circulaire_import(self):
        # Verse interpreter: debug_utils als eerste importeren moet werken, en
        # diagnostiek mag geen andere projectmodules binnenhalen.
        code = ("import sys, debug_utils; "
                "eigen = {'prijzen', 'db', 'yahoo_client', 'portfolio_calc', 'upload_verwerking'}; "
                "print(sorted(eigen & set(sys.modules)))")
        uit = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_MAP, capture_output=True, text=True)
        self.assertEqual(uit.returncode, 0, uit.stderr)
        self.assertEqual(uit.stdout.strip(), "[]")


class TestYahooTellers(_MetRequest):
    def setUp(self):
        super().setUp()
        yahoo_client.reset_yahoo_call_teller()

    @patch("yahoo_client.time.sleep")
    def test_rate_limit_retry_telt_retry_en_gedrag_gelijk(self, _sleep):
        pogingen = []

        def actie():
            pogingen.append(1)
            if len(pogingen) == 1:
                raise RuntimeError("Too Many Requests")
            return "ok"

        self.assertEqual(yahoo_client._met_rate_limit_retry(actie), ("ok", None))
        self.assertEqual(yahoo_client.yahoo_teller_stand()[1:], (1, 0))

    @patch("yahoo_client.time.sleep")
    def test_andere_fout_direct_mislukt_zonder_retry(self, mock_sleep):
        fout = ValueError("iets anders")

        def actie():
            raise fout

        self.assertEqual(yahoo_client._met_rate_limit_retry(actie), (None, fout))
        mock_sleep.assert_not_called()
        self.assertEqual(yahoo_client.yahoo_teller_stand()[1:], (0, 1))

    @patch("yahoo_client.time.sleep")
    @patch("yahoo_client.yf.download")
    def test_download_met_retry_telt_en_geeft_lege_series(self, mock_download, _sleep):
        mock_download.side_effect = RuntimeError("netwerk")
        resultaat = yahoo_client.download_met_retry("AAPL", "2024-01-01")
        self.assertIsInstance(resultaat, pd.Series)
        self.assertTrue(resultaat.empty)
        self.assertEqual(yahoo_client.yahoo_teller_stand(), (3, 2, 1))

    def test_reset_zet_tellers_op_nul(self):
        yahoo_client._tel_yahoo_retry("retries")
        yahoo_client._tel_yahoo_call("x")
        yahoo_client.reset_yahoo_call_teller()
        self.assertEqual(yahoo_client.yahoo_teller_stand(), (0, 0, 0))

    def test_melding_niveaus(self):
        _, meldingen, _ = self._in_request(yahoo_client.meld_yahoo_samenvatting, "k", "upload")
        self.assertEqual(meldingen[0]["niveau"], INFO)
        self.assertEqual(meldingen[0]["tekst"],
                         "Yahoo-calls (upload): 0, retries: 0, mislukt (na eventuele retries): 0.")
        yahoo_client._tel_yahoo_retry("retries")
        _, meldingen, _ = self._in_request(yahoo_client.meld_yahoo_samenvatting, "k", "upload")
        self.assertEqual(meldingen[0]["niveau"], LET_OP)
        yahoo_client._tel_yahoo_retry("mislukt")
        _, meldingen, _ = self._in_request(yahoo_client.meld_yahoo_samenvatting, "k", "upload")
        self.assertEqual(meldingen[0]["niveau"], FOUT)

    def test_verschilmeting_en_nooit_negatief(self):
        for _ in range(3):
            yahoo_client._tel_yahoo_call("x")
        voor = yahoo_client.yahoo_teller_stand()
        yahoo_client._tel_yahoo_call("x")
        _, meldingen, _ = self._in_request(yahoo_client.meld_yahoo_samenvatting, "k", "verrijking", vanaf=voor)
        self.assertIn("(verrijking): 1,", meldingen[0]["tekst"])
        yahoo_client.reset_yahoo_call_teller()
        _, meldingen, _ = self._in_request(yahoo_client.meld_yahoo_samenvatting, "k", "verrijking", vanaf=voor)
        self.assertIn("(verrijking): 0,", meldingen[0]["tekst"])


def _mock_conn(min_max_rows, laatst_ververst_rows, cached_rows):
    """Zelfde patroon als tests/test_koersen_cache.py."""
    cur = MagicMock()
    cur.fetchall.side_effect = [min_max_rows, laatst_ververst_rows, cached_rows]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestKoersenMeldingen(_MetRequest):
    @patch("prijzen.yf.Ticker")
    @patch("prijzen.save_prices")
    @patch("prijzen.download_met_retry")
    @patch("prijzen.get_db_connection")
    def test_cache_en_download_zonder_data(self, mock_conn, mock_download, _save, mock_ticker):
        mock_ticker.return_value.info = {"currency": "EUR"}
        vandaag = pd.Timestamp.now().normalize()
        eerste = vandaag - pd.Timedelta(days=30)
        # AAPL: < 2 min geleden ververst -> uit cache. MSFT: niet in cache ->
        # download, maar Yahoo levert niets.
        mock_conn.return_value = _mock_conn(
            min_max_rows=[("AAPL", eerste.date(), vandaag.date())],
            laatst_ververst_rows=[("AAPL", datetime.now())],
            cached_rows=[("AAPL", eerste.date(), 100.0), ("AAPL", vandaag.date(), 110.0)],
        )
        mock_download.return_value = pd.Series(dtype=float)

        resultaat, meldingen, _ = self._in_request(prijzen.get_prices, ["AAPL", "MSFT"], eerste)

        # Berekening ongewijzigd: alleen AAPL, met de gecachete koersen.
        self.assertEqual(list(resultaat.columns), ["AAPL"])
        self.assertEqual(resultaat["AAPL"].iloc[-1], 110.0)
        per = _per_sleutel(meldingen)
        self.assertEqual(per["geen_koers:MSFT"]["niveau"], LET_OP)
        self.assertNotIn("geen_koers:AAPL", per)
        samenvatting = per[prijzen.DIAGNOSTIEK_SLEUTEL_KOERSEN]
        self.assertEqual(samenvatting["categorie"], CATEGORIE_KOERSEN)
        self.assertEqual(samenvatting["niveau"], GOED)
        self.assertEqual(samenvatting["tekst"], "2 tickers: 1 uit cache, 1 nieuw gedownload, 0 ververst.")

    def test_sterkste_bron_wint_en_fx_telt_niet(self):
        def noteer():
            prijzen._noteer_koers_bron("AAPL", prijzen.FX_BRON_GEDOWNLOAD)
            prijzen._noteer_koers_bron("AAPL", prijzen.FX_BRON_CACHE)   # latere aanroep
            prijzen._noteer_koers_bron("MSFT", prijzen.FX_BRON_CACHE)
            prijzen._noteer_koers_bron("MSFT", prijzen.FX_BRON_VERVERST)
            prijzen._noteer_koers_bron("USDEUR=X", prijzen.FX_BRON_GEDOWNLOAD)
            prijzen._meld_koersen([], set())
        _, meldingen, _ = self._in_request(noteer)
        self.assertEqual(meldingen[0]["tekst"], "2 tickers: 0 uit cache, 1 nieuw gedownload, 1 ververst.")

    def test_fx_paar_zonder_data_geen_koersen_melding(self):
        _, meldingen, _ = self._in_request(prijzen._meld_koersen, ["USDEUR=X"], set())
        self.assertEqual(meldingen, [])


class TestKoersdekking(_MetRequest):
    def _df(self, rijen):
        return pd.DataFrame(rijen, columns=["datum", "ticker", "beurs", "product"])

    def _prijzen(self, kolommen):
        index = pd.date_range("2024-01-01", "2024-03-01", freq="D")
        data = {}
        for ticker, eerste in kolommen.items():
            data[ticker] = [100.0 if d >= pd.Timestamp(eerste) else float("nan") for d in index]
        return pd.DataFrame(data, index=index)

    def test_later_dan_marge_let_op(self):
        df = self._df([(pd.Timestamp("2024-01-02").date(), "LAAT", "EAM", "LAAT NV")])
        _, meldingen, _ = self._in_request(po._meld_koersdekking, df, self._prijzen({"LAAT": "2024-01-10"}))
        self.assertEqual(len(meldingen), 1)
        m = meldingen[0]
        self.assertEqual((m["categorie"], m["niveau"], m["sleutel"]), (CATEGORIE_KOERSEN, LET_OP, "koers_later:LAAT"))
        self.assertIn("beginnen pas op 2024-01-10", m["tekst"])
        self.assertIn("eerste transactie was op 2024-01-02", m["tekst"])

    def test_binnen_marge_geen_melding(self):
        # Zaterdag gekocht, eerste koers maandag na een lang weekend: 5 dagen.
        df = self._df([(pd.Timestamp("2024-01-06").date(), "WEEK", "EAM", "WEEK NV")])
        _, meldingen, _ = self._in_request(po._meld_koersdekking, df, self._prijzen({"WEEK": "2024-01-11"}))
        self.assertEqual(meldingen, [])

    def test_vergelijkt_met_eerste_eigen_echte_transactie(self):
        # Corporate-action-rij van ver ervoor telt niet; de eigen eerste
        # echte transactie (2024-02-01) wel, niet de portfolio-start.
        df = self._df([
            (pd.Timestamp("2024-01-01").date(), "ANDER", "EAM", "ANDER NV"),
            (pd.Timestamp("2024-01-01").date(), "LAAT", "DEG", "LAAT NV"),
            (pd.Timestamp("2024-02-01").date(), "LAAT", "EAM", "LAAT NV"),
        ])
        prijzen_df = self._prijzen({"ANDER": "2024-01-01", "LAAT": "2024-02-03"})
        _, meldingen, _ = self._in_request(po._meld_koersdekking, df, prijzen_df)
        self.assertEqual(meldingen, [])

    def test_ticker_zonder_kolom_wordt_hier_niet_gemeld(self):
        df = self._df([(pd.Timestamp("2024-01-02").date(), "WEG", "EAM", "WEG NV")])
        _, meldingen, _ = self._in_request(po._meld_koersdekking, df, self._prijzen({"ANDER": "2024-01-01"}))
        self.assertEqual(meldingen, [])


class TestSplitMeldingen(_MetRequest):
    def _df(self, rijen):
        return pd.DataFrame(rijen, columns=["datum", "isin", "product", "beurs", "aantal", "koers", "totaal_eur"])

    def test_split_info_en_berekening_ongewijzigd(self):
        df = self._df([
            (pd.Timestamp("2024-01-01"), "US1", "ACME", "NSY", 10.0, 100.0, -1000.0),
            (pd.Timestamp("2024-02-01"), "US1", "ACME", "DEG", 30.0, 0.0, 0.0),
            (pd.Timestamp("2024-02-02"), "US1", "ACME", "NSY", 40.0, 0.0, 0.0),
        ])
        uit, meldingen, _ = self._in_request(portfolio_calc.compute_split_adjusted_shares, df)
        # (10 + 30) / 10 = factor 4: de aankoop vóór de conversie wordt 40.
        self.assertEqual(uit["adj_aantal"].tolist(), [40.0, 30.0, 40.0])
        self.assertEqual(meldingen, [{
            "categorie": CATEGORIE_SPLITS, "niveau": INFO,
            "tekst": "Split voor ACME (US1) op 2024-02-02: factor 4.0000.", "sleutel": "split:US1:2024-02-02",
        }])

    def test_geen_conversierij_let_op(self):
        df = self._df([
            (pd.Timestamp("2024-01-01"), "US2", "ISIN WISSEL", "NSY", 10.0, 100.0, -1000.0),
            (pd.Timestamp("2024-02-01"), "US2", "ISIN WISSEL", "DEG", -10.0, 0.0, 0.0),
        ])
        uit, meldingen, _ = self._in_request(portfolio_calc.compute_split_adjusted_shares, df)
        self.assertEqual(uit["adj_aantal"].tolist(), [10.0, -10.0])
        self.assertEqual(len(meldingen), 1)
        self.assertEqual((meldingen[0]["niveau"], meldingen[0]["sleutel"]), (LET_OP, "split_onbekend:US2"))
        self.assertIn("(geen conversierij gevonden)", meldingen[0]["tekst"])

    def test_zonder_corporate_actions_geen_melding(self):
        df = self._df([(pd.Timestamp("2024-01-01"), "US3", "GEWOON", "NSY", 10.0, 100.0, -1000.0)])
        _, meldingen, _ = self._in_request(portfolio_calc.compute_split_adjusted_shares, df)
        self.assertEqual(meldingen, [])


class TestEtfHoldingsMeldingen(_MetRequest):
    def test_drie_bronnen(self):
        land_sector = {"per_etf": {
            "VOL.AS": {"land": {"US": 0.6, "JP": 0.4}, "land_bron": "provider_csv"},
            "TOP.AS": {"land": {"US": 0.5, "Unknown": 0.5}, "land_bron": "yfinance_top10"},
            "LEEG.AS": {"land": {"Unknown": 1.0}, "land_bron": "yfinance_top10"},
        }}
        _, meldingen, _ = self._in_request(po._meld_etf_holdings, land_sector)
        per = _per_sleutel(meldingen)
        self.assertEqual({k: v["niveau"] for k, v in per.items()},
                         {"etf:VOL.AS": GOED, "etf:TOP.AS": INFO, "etf:LEEG.AS": LET_OP})
        self.assertTrue(all(m["categorie"] == CATEGORIE_ETF_HOLDINGS for m in meldingen))
        self.assertIn("geen holdings met landinformatie", per["etf:LEEG.AS"]["tekst"])
        self.assertIn("onvolledig", per["etf:TOP.AS"]["tekst"])

    def test_lege_invoer(self):
        _, meldingen, _ = self._in_request(po._meld_etf_holdings, {})
        self.assertEqual(meldingen, [])


# Kolomvolgorde zoals de SELECT in _haal_portfolio_basis().
TRANSACTIE_RIJ = (pd.Timestamp("2024-01-02").date(), "LAAT", "US0000000009", "NDQ", "LAAT", 1.0, 100.0, -100.0,
                  "LAAT", None, None, None)
TEST_CODE = "ZZTESTDIAGLADEN"


def _fake_conn():
    cur = MagicMock()
    cur.fetchone.return_value = ("Testportfolio",)
    cur.fetchall.return_value = [TRANSACTIE_RIJ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestCacheHitNieuweMeldingen(_MetRequest):
    def setUp(self):
        super().setUp()
        po._wis_portfolio_basis_cache(TEST_CODE)

    def tearDown(self):
        po._wis_portfolio_basis_cache(TEST_CODE)

    @patch("portfolio_orchestratie.get_prices")
    @patch("portfolio_orchestratie.get_db_connection")
    def test_hit_geeft_koersmeldingen_opnieuw_zonder_laadtijden(self, mock_conn, mock_get_prices):
        mock_conn.side_effect = lambda: _fake_conn()
        index = pd.date_range("2024-01-01", "2024-02-29", freq="D")
        mock_get_prices.return_value = pd.DataFrame(
            {"LAAT": [100.0 if d >= pd.Timestamp("2024-02-01") else float("nan") for d in index]}, index=index,
        )

        _, eerste, _ = self._in_request(po._haal_portfolio_basis, TEST_CODE)
        _, tweede, _ = self._in_request(po._haal_portfolio_basis, TEST_CODE)

        self.assertEqual(mock_get_prices.call_count, 1)
        self.assertIn("koers_later:LAAT", _per_sleutel(eerste))
        self.assertIn(CATEGORIE_LAADTIJDEN, [m["categorie"] for m in eerste])
        self.assertEqual([m["sleutel"] for m in tweede], ["koers_later:LAAT"])


if __name__ == "__main__":
    unittest.main()
