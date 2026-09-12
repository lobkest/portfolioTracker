"""
Unit tests voor de gedeelde, kort-levende _basis_cache in app.py
(_haal_portfolio_basis()/_wis_portfolio_basis_cache()) -- zie opdracht
"bottleneck zichtbaar maken + dubbele database-fetches wegwerken". Zonder
deze cache haalden build_portfolio_response(), portfolio_verrijking() en
_ticker_zekerheid_groepen() elk apart dezelfde transacties op en herhaalden
compute_split_adjusted_shares()/get_prices() vanaf nul binnen hetzelfde
portfolio-bezoek.

Raakt de echte database aan via 'import app' (init_db() draait bij import,
zie CLAUDE.md) -- daarom, net als tests/test_gefaseerd_laden.py, overgeslagen
zonder DATABASE_URL. get_db_connection wordt gemockt zodat er geen echte
queries lopen; ticker=None in de nep-transactierij zorgt dat get_prices()
nooit wordt aangeroepen (lege tickerlijst), dus ook geen yfinance-calls.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

SKIP_REDEN = (
    "DATABASE_URL niet ingesteld -- deze test importeert app.py (init_db() draait bij import) en wordt "
    "overgeslagen (bv. in CI zonder databasetoegang; draait lokaal wel via de .env)"
)

# Fictieve code die door geen enkele andere test gebruikt wordt, zodat deze
# test zijn eigen _basis_cache-entry heeft en niets van andere tests raakt.
TEST_CODE = "ZZTESTBASISCACHE"

# Kolomvolgorde exact zoals de SELECT in _haal_portfolio_basis(): ticker=None
# zodat get_prices() wordt overgeslagen (lege tickerlijst -> geen netwerk-call
# nodig om deze test te laten slagen).
TRANSACTIE_RIJ = ("2024-01-01", "AAPL", "US0378331005", "NASDAQ", None, 1.0, 100.0, -100.0, "AAPL", None, None, None)


def _fake_conn():
    """Nep-DB-connectie: fetchone() geeft een portfolionaam terug, fetchall()
    één transactierij -- genoeg voor _haal_portfolio_basis() om een geldige
    (naam, transacties_df, price_data) te bouwen zonder een echte database."""
    cur = MagicMock()
    cur.fetchone.return_value = ("Testportfolio",)
    cur.fetchall.return_value = [TRANSACTIE_RIJ]
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


@unittest.skipUnless(os.environ.get("DATABASE_URL"), SKIP_REDEN)
class TestBasisCache(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        # Eigen entry opruimen vóór en na de test, nooit de hele cache leegen
        # (die kan entries van andere, écht opgeslagen codes bevatten).
        app_module._basis_cache.pop(TEST_CODE, None)
        self.addCleanup(app_module._basis_cache.pop, TEST_CODE, None)

        self._get_conn_patcher = patch.object(
            app_module, "get_db_connection", side_effect=lambda: _fake_conn()
        )
        self.mock_get_conn = self._get_conn_patcher.start()
        self.addCleanup(self._get_conn_patcher.stop)

    def test_tweede_aanroep_binnen_ttl_doet_geen_nieuwe_db_call(self):
        with patch.object(self.app_module.time, "time", return_value=1000.0):
            naam1, df1, prices1 = self.app_module._haal_portfolio_basis(TEST_CODE)
        self.assertEqual(naam1, "Testportfolio")
        self.assertEqual(self.mock_get_conn.call_count, 1)

        # 5s later, ruim binnen de TTL van 20s -> cache-hit, geen nieuwe call.
        with patch.object(self.app_module.time, "time", return_value=1005.0):
            naam2, df2, prices2 = self.app_module._haal_portfolio_basis(TEST_CODE)
        self.assertEqual(naam2, "Testportfolio")
        self.assertEqual(self.mock_get_conn.call_count, 1, "cache-hit had geen nieuwe DB-call mogen doen")
        self.assertIs(df2, df1, "cache-hit moet hetzelfde (gecachete) DataFrame teruggeven")

    def test_forceer_vers_negeert_de_cache(self):
        with patch.object(self.app_module.time, "time", return_value=1000.0):
            self.app_module._haal_portfolio_basis(TEST_CODE)
        self.assertEqual(self.mock_get_conn.call_count, 1)

        with patch.object(self.app_module.time, "time", return_value=1001.0):
            self.app_module._haal_portfolio_basis(TEST_CODE, forceer_vers=True)
        self.assertEqual(self.mock_get_conn.call_count, 2, "forceer_vers=True had een nieuwe DB-call moeten forceren")

    def test_wis_cache_verwijdert_entry_en_volgende_call_is_weer_vers(self):
        with patch.object(self.app_module.time, "time", return_value=1000.0):
            self.app_module._haal_portfolio_basis(TEST_CODE)
        self.assertEqual(self.mock_get_conn.call_count, 1)
        self.assertIn(TEST_CODE, self.app_module._basis_cache)

        self.app_module._wis_portfolio_basis_cache(TEST_CODE)
        self.assertNotIn(TEST_CODE, self.app_module._basis_cache)

        # Nog steeds ruim binnen wat de TTL zou zijn geweest -- zonder de
        # invalidatie zou dit een cache-hit zijn geweest (geen nieuwe call).
        with patch.object(self.app_module.time, "time", return_value=1001.0):
            self.app_module._haal_portfolio_basis(TEST_CODE)
        self.assertEqual(self.mock_get_conn.call_count, 2, "na _wis_portfolio_basis_cache() moet de volgende call weer vers ophalen")


if __name__ == "__main__":
    unittest.main()
