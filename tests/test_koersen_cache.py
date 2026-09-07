"""
Regressietests voor de koersen-cache-staleness-bug: na een nieuwe upload met
een transactie ná de laatst gecachte koersdatum bleef price_data.index niet
doorlopen tot die nieuwe transactie, waardoor de portfoliowaarde en de
grafieken (Portfolio-home, Per aandeel) niet klopten -- terwijl Statistieken
(die rechtstreeks op transacties_df werkt, niet op price_data) wél het
juiste aantal aandelen toonde. Root cause: get_prices() controleerde alleen
MIN(datum) per ticker (gaat de cache ver genoeg terug), nooit MAX(datum)
(is de cache nog actueel). Zie analysis.get_prices().

Uitgebreid met de fix "koersen bij élke portfolio-opening verversen" (zie
CLAUDE.md): een cache-rij van "vandaag" werd voorheen pas ververst als de
cache >4 dagen achterliep -- een tijdens handelstijd opgehaalde, nog
niet-definitieve koers bleef daardoor de rest van de dag (en bij elke latere
opening diezelfde dag) ongewijzigd staan, ook na beurssluiting. get_prices()
behandelt de meest recente cache-rij nu bij ELKE aanroep als kandidaat voor
verversen, met een 2-minuten-hergebruikdrempel (o.b.v. de bijgewerkt_op-
kolom) om te voorkomen dat de meerdere endpoints van één portfolio-opening
(home, verrijking, ticker-zekerheid) Yahoo kort na elkaar dubbel bevragen.

Deze tests raken de database NIET aan -- get_db_connection, download_met_retry,
save_prices/upsert_prices en yf.Ticker worden gemockt.
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis


def _mock_conn(min_max_rows, laatst_ververst_rows, cached_rows):
    """Bouwt een gemockte get_db_connection()-return die na elkaar de drie
    execute()/fetchall()-aanroepen in get_prices() beantwoordt: eerst
    MIN/MAX per ticker, dan (ticker, bijgewerkt_op) voor de rij van
    'vandaag' (t.b.v. de 2-minuten-hergebruikdrempel), dan de gecachete
    (ticker, datum, koers_eur)-rijen."""
    cur = MagicMock()
    cur.fetchall.side_effect = [min_max_rows, laatst_ververst_rows, cached_rows]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class TestGetPricesCacheFreshness(unittest.TestCase):
    """Opdracht 1+2: get_prices() moet een verouderde cache (oude MAX(datum))
    incrementeel verversen, niet stilzwijgend laten staan."""

    @patch("analysis.yf.Ticker")
    @patch("analysis.upsert_prices")
    @patch("analysis.download_met_retry")
    @patch("analysis.get_db_connection")
    def test_verouderde_cache_wordt_incrementeel_ververst(
        self, mock_get_conn, mock_download, mock_upsert, mock_yf_ticker
    ):
        mock_yf_ticker.return_value.info = {"currency": "EUR"}

        vandaag = pd.Timestamp.now().normalize()
        oude_max = vandaag - pd.Timedelta(days=10)
        eerste = vandaag - pd.Timedelta(days=100)

        mock_get_conn.return_value = _mock_conn(
            min_max_rows=[("AAPL", eerste.date(), oude_max.date())],
            laatst_ververst_rows=[],  # geen rij voor 'vandaag' in de cache
            cached_rows=[("AAPL", eerste.date(), 100.0), ("AAPL", oude_max.date(), 150.0)],
        )
        # incrementele download levert precies 1 nieuwe (verse) koers op
        mock_download.return_value = pd.Series({vandaag: 160.0}, name="AAPL")

        result = analysis.get_prices(["AAPL"], eerste)

        # download_met_retry moet zijn aangeroepen VANAF de oude max-datum
        # zelf (niet pas de dag erna) -- die dag moet nu juist herververst
        # kunnen worden, dat is precies het punt van deze fix.
        ticker_arg, vanaf_arg = mock_download.call_args[0][:2]
        self.assertEqual(ticker_arg, "AAPL")
        self.assertEqual(pd.Timestamp(vanaf_arg), oude_max)

        mock_upsert.assert_called_once()
        self.assertIn(vandaag, result.index)
        self.assertEqual(result.loc[vandaag, "AAPL"], 160.0)

    @patch("analysis.yf.Ticker")
    @patch("analysis.upsert_prices")
    @patch("analysis.download_met_retry")
    @patch("analysis.get_db_connection")
    def test_ticker_met_rij_van_vandaag_wordt_toch_ververst(
        self, mock_get_conn, mock_download, mock_upsert, mock_yf_ticker
    ):
        """Kern van de fix: een ticker met een cache-rij van exact vandaag
        (dus NIET >4 dagen stale, de oude drempel) moet alsnog ververst
        worden, mits er geen recente (< 2 min) bijgewerkt_op-timestamp is."""
        # currency=EUR zodat _converteer_naar_eur() geen FX-conversie (en
        # dus geen geneste get_prices()-aanroep voor het FX-paar) triggert
        # -- die zou de gemockte cursor hieronder een 4e keer bevragen.
        mock_yf_ticker.return_value.info = {"currency": "EUR"}
        vandaag = pd.Timestamp.now().normalize()
        eerste = vandaag - pd.Timedelta(days=100)
        oud_moment = datetime.now() - timedelta(hours=3)

        mock_get_conn.return_value = _mock_conn(
            min_max_rows=[("AAPL", eerste.date(), vandaag.date())],
            laatst_ververst_rows=[("AAPL", oud_moment)],
            cached_rows=[("AAPL", eerste.date(), 100.0), ("AAPL", vandaag.date(), 200.0)],
        )
        # nieuwe download levert een afwijkende (bv. inmiddels definitieve) koers op
        mock_download.return_value = pd.Series({vandaag: 205.0}, name="AAPL")

        result = analysis.get_prices(["AAPL"], eerste)

        ticker_arg, vanaf_arg = mock_download.call_args[0][:2]
        self.assertEqual(ticker_arg, "AAPL")
        self.assertEqual(pd.Timestamp(vanaf_arg), vandaag)
        mock_upsert.assert_called_once()
        self.assertEqual(result.loc[vandaag, "AAPL"], 205.0)

    @patch("analysis.upsert_prices")
    @patch("analysis.download_met_retry")
    @patch("analysis.get_db_connection")
    def test_net_ververste_ticker_wordt_niet_dubbel_bevraagd(
        self, mock_get_conn, mock_download, mock_upsert
    ):
        """Als bijgewerkt_op < 2 minuten geleden is (bv. de home-endpoint
        heeft 'm net ververst), mag de verrijking-endpoint Yahoo niet
        nogmaals bevragen voor dezelfde ticker."""
        vandaag = pd.Timestamp.now().normalize()
        eerste = vandaag - pd.Timedelta(days=100)
        net_moment = datetime.now() - timedelta(seconds=30)

        mock_get_conn.return_value = _mock_conn(
            min_max_rows=[("AAPL", eerste.date(), vandaag.date())],
            laatst_ververst_rows=[("AAPL", net_moment)],
            cached_rows=[("AAPL", eerste.date(), 100.0), ("AAPL", vandaag.date(), 200.0)],
        )

        result = analysis.get_prices(["AAPL"], eerste)

        mock_download.assert_not_called()
        mock_upsert.assert_not_called()
        self.assertEqual(result.loc[vandaag, "AAPL"], 200.0)


class TestValueOverTimeStaleWaarschuwing(unittest.TestCase):
    """Opdracht 3: transacties ná price_data.index.max() moeten niet meer
    stil verdwijnen -- er komt nu een waarschuwing in de logs."""

    def test_transactie_na_laatste_koersdatum_wordt_gewaarschuwd_en_genegeerd(self):
        price_data = pd.DataFrame(
            {"AAPL": [100.0, 110.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        transacties_df = pd.DataFrame({
            "ticker": ["AAPL", "AAPL"],
            "datum": [pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-01-05").date()],
            "adj_aantal": [1.0, 1.0],
            "totaal_eur": [-100.0, -110.0],
        })

        buf = io.StringIO()
        with redirect_stdout(buf):
            result = analysis.compute_value_over_time(transacties_df, price_data)

        self.assertIn("ná de laatste beschikbare koersdatum", buf.getvalue())
        # bestaand (nog niet gewijzigd) gedrag: de transactie van 2024-01-05
        # valt buiten price_data.index en telt dus niet mee -- de
        # waarschuwing maakt dit nu zichtbaar i.p.v. stil te falen
        self.assertEqual(result.loc[pd.Timestamp("2024-01-02"), "geinvesteerd"], 100.0)

    def test_geen_waarschuwing_als_alle_transacties_binnen_bereik_vallen(self):
        price_data = pd.DataFrame(
            {"AAPL": [100.0, 110.0]},
            index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        )
        transacties_df = pd.DataFrame({
            "ticker": ["AAPL"],
            "datum": [pd.Timestamp("2024-01-01").date()],
            "adj_aantal": [1.0],
            "totaal_eur": [-100.0],
        })

        buf = io.StringIO()
        with redirect_stdout(buf):
            analysis.compute_value_over_time(transacties_df, price_data)

        self.assertNotIn("ná de laatste beschikbare koersdatum", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
