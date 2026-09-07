"""
Tests voor Opdracht 1: een verbeterde ticker-resolutielogica (bv. de
G2X.MU-fix: 'geen koersdata' telt nu als een prijsprobleem) corrigeert
alleen NIEUW ingevoegde rijen -- de hoofdpagina gebruikt de al opgeslagen
transacties.ticker-waarde, geen verse herberekening. analysis.
backfill_verouderde_tickers() herbeoordeelt daarom bij elke upload naar een
BESTAANDE portfolio-code ook de al opgeslagen tickers, en overschrijft
alleen als de oude ticker een prijsprobleem heeft EN de nieuwe kandidaat
dat niet heeft (nooit een werkende ticker vervangen door een onzekerdere).

_ticker_heeft_prijsprobleem() draait geheel offline (vergelijk_prijs_op_
datum gemockt). backfill_verouderde_tickers() raakt de echte database aan
(net als tests/test_wijzig_code_db.py) en wordt overgeslagen zonder
DATABASE_URL, met find_ticker_met_snelle_prijscheck/vergelijk_prijs_op_
datum gemockt (geen echte Yahoo-calls).
"""
import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

import analysis
from analysis import _ticker_heeft_prijsprobleem, backfill_verouderde_tickers


def _prijscheck(afwijking_pct):
    return {
        "yahoo_koers": 100.0, "bekende_koers": 100.0, "afwijking_pct": afwijking_pct,
        "niveau": None, "match": afwijking_pct is not None and afwijking_pct <= 6.0,
        "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
    }


class TestTickerHeeftPrijsprobleem(unittest.TestCase):
    def test_geen_ticker_is_altijd_een_probleem(self):
        self.assertTrue(_ticker_heeft_prijsprobleem(None, [{"datum": date(2023, 6, 10), "koers": 100.0}]))

    def test_geen_koersdata_is_een_probleem(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=_prijscheck(afwijking_pct=None)):
            self.assertTrue(_ticker_heeft_prijsprobleem("G2X.MU", [{"datum": date(2023, 6, 10), "koers": 100.0}]))

    def test_grote_afwijking_is_een_probleem(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=_prijscheck(afwijking_pct=15.0)):
            self.assertTrue(_ticker_heeft_prijsprobleem("FOUT.TICKER", [{"datum": date(2023, 6, 10), "koers": 100.0}]))

    def test_kloppende_prijs_is_geen_probleem(self):
        with patch.object(analysis, "vergelijk_prijs_op_datum", return_value=_prijscheck(afwijking_pct=1.0)):
            self.assertFalse(_ticker_heeft_prijsprobleem("GDX.L", [{"datum": date(2023, 6, 10), "koers": 100.0}]))

    def test_geen_transacties_is_geen_probleem_geen_crash(self):
        self.assertFalse(_ticker_heeft_prijsprobleem("AAPL", []))


SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test raakt een echte database aan en wordt overgeslagen "
    "(bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestBackfillVerouderdeTickers(unittest.TestCase):
    """Reproductie van het G2X.MU-geval: een al opgeslagen ticker zonder
    koersdata moet vervangen worden door een werkende kandidaat (GDX.L),
    maar NOOIT als de nieuwe kandidaat zelf ook een probleem heeft, en
    NOOIT als de oude ticker al prima was."""

    CODE = "TBF"

    def _leeg_op(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def _voeg_positie_toe(self, isin, beurs, ticker, product="VANECK GOLD MINERS"):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.CODE, date(2023, 1, 10), product, isin, beurs, ticker, 10, 100.0, -1000.0,
             f"ORDER-{isin}-{beurs}", product),
        )
        conn.commit()
        cur.close()
        conn.close()

    def _huidige_ticker(self, isin, beurs):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM transacties WHERE code = %s AND isin = %s AND beurs = %s", (self.CODE, isin, beurs))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None

    def setUp(self):
        self._leeg_op()
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.CODE, "unittest"))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._leeg_op()

    def test_oude_ticker_zonder_koersdata_wordt_vervangen_door_werkende_kandidaat(self):
        self._voeg_positie_toe("IE00BQQP9F84", "TDG", "G2X.MU")

        def fake_probleem(ticker, transacties):
            return ticker == "G2X.MU"  # G2X.MU heeft een probleem, GDX.L niet

        with patch.object(analysis, "_ticker_heeft_prijsprobleem", side_effect=fake_probleem), \
             patch.object(analysis, "find_ticker_met_snelle_prijscheck",
                           return_value={"ticker": "GDX.L", "zekerheid": "zeker"}) as mock_find:
            gecorrigeerd = backfill_verouderde_tickers(self.CODE)

        mock_find.assert_called_once()
        self.assertEqual(gecorrigeerd, 1)
        self.assertEqual(self._huidige_ticker("IE00BQQP9F84", "TDG"), "GDX.L")

    def test_nieuwe_kandidaat_met_eigen_probleem_wordt_niet_overgenomen(self):
        self._voeg_positie_toe("IE00BQQP9F84", "TDG", "G2X.MU")

        with patch.object(analysis, "_ticker_heeft_prijsprobleem", return_value=True), \
             patch.object(analysis, "find_ticker_met_snelle_prijscheck",
                           return_value={"ticker": "OOK.FOUT", "zekerheid": "onzeker"}) as mock_find:
            gecorrigeerd = backfill_verouderde_tickers(self.CODE)

        mock_find.assert_called_once()
        self.assertEqual(gecorrigeerd, 0)
        self.assertEqual(self._huidige_ticker("IE00BQQP9F84", "TDG"), "G2X.MU")

    def test_werkende_oude_ticker_wordt_niet_aangeraakt_geen_zoekopdracht(self):
        self._voeg_positie_toe("US0378331005", "NASDAQ", "AAPL", product="APPLE INC")

        with patch.object(analysis, "_ticker_heeft_prijsprobleem", return_value=False), \
             patch.object(analysis, "find_ticker_met_snelle_prijscheck") as mock_find:
            gecorrigeerd = backfill_verouderde_tickers(self.CODE)

        mock_find.assert_not_called()
        self.assertEqual(gecorrigeerd, 0)
        self.assertEqual(self._huidige_ticker("US0378331005", "NASDAQ"), "AAPL")

    def test_geen_transacties_voor_code_geeft_geen_crash(self):
        gecorrigeerd = backfill_verouderde_tickers("XYZ-NIET-BESTAAND")
        self.assertEqual(gecorrigeerd, 0)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestBackfillMetForceerVlag(unittest.TestCase):
    """Vinkje "ticker-informatie opnieuw bepalen" op het uploadscherm (zie
    app.py/_upload_impl, CLAUDE.md): forceer=True overroept de prijsprobleem-
    check hierboven en herzoekt ALTIJD, ook zonder gedetecteerd probleem.
    forceer=False (standaard) blijft exact het hierboven al geteste gedrag."""

    CODE = "TBF2"

    def _leeg_op(self):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transacties WHERE code = %s", (self.CODE,))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (self.CODE,))
        conn.commit()
        cur.close()
        conn.close()

    def _voeg_positie_toe(self, isin, beurs, ticker, product="APPLE INC"):
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transacties (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (self.CODE, date(2023, 1, 10), product, isin, beurs, ticker, 10, 100.0, -1000.0,
             f"ORDER-{isin}-{beurs}", product),
        )
        conn.commit()
        cur.close()
        conn.close()

    def setUp(self):
        self._leeg_op()
        from db import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (self.CODE, "unittest"))
        conn.commit()
        cur.close()
        conn.close()

    def tearDown(self):
        self._leeg_op()

    def test_forceer_true_herzoekt_ook_zonder_prijsprobleem(self):
        self._voeg_positie_toe("US0378331005", "NASDAQ", "AAPL")

        with patch.object(analysis, "_ticker_heeft_prijsprobleem", return_value=False), \
             patch.object(analysis, "find_ticker_met_snelle_prijscheck",
                           return_value={"ticker": "AAPL", "zekerheid": "zeker"}) as mock_find:
            backfill_verouderde_tickers(self.CODE, forceer=True)

        mock_find.assert_called_once()

    def test_forceer_false_expliciet_ongewijzigd_gedrag(self):
        # Zelfde assertie als test_werkende_oude_ticker_wordt_niet_
        # aangeraakt_geen_zoekopdracht hierboven, maar met forceer=False
        # EXPLICIET meegegeven (i.p.v. de default) -- bevestigt dat het
        # vinkje-uit-pad niets aan het bestaande gedrag verandert.
        self._voeg_positie_toe("US0378331005", "NASDAQ", "AAPL")

        with patch.object(analysis, "_ticker_heeft_prijsprobleem", return_value=False), \
             patch.object(analysis, "find_ticker_met_snelle_prijscheck") as mock_find:
            gecorrigeerd = backfill_verouderde_tickers(self.CODE, forceer=False)

        mock_find.assert_not_called()
        self.assertEqual(gecorrigeerd, 0)


if __name__ == "__main__":
    unittest.main()
