"""Een split NA de laatste transactie mag de historische waarde van een gesloten positie niet opblazen.

Yahoo-koersen zijn achteraf gecorrigeerd voor alle latere splits (auto_adjust); DeGiro boekt zo'n split
alleen zolang je het aandeel bezit. Zonder database en zonder netwerk: transacties, koersen en splits zijn
gemockt.
"""
import datetime
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import portfolio_orchestratie
from portfolio_calc import compute_value_over_time

TICKER = "TST"
ISIN = "XX0000000001"
AANTAL = 10.0
ECHTE_KOERS_EUR = 1.0
SPLIT_RATIO = 0.05  # reverse split 1:20 in juni 2021, na de laatste transactie
SPLIT_DATUM = "2021-06-01"
# Yahoo-reeks van januari na de split: alles x20.
ADJUSTED_KOERS_EUR = ECHTE_KOERS_EUR / SPLIT_RATIO

# Volgorde van TRANSACTIE_KOLOMMEN in db.py.
KOOP = (datetime.date(2021, 1, 4), "Test", ISIN, "NDQ", TICKER, AANTAL, ECHTE_KOERS_EUR, -10.0, "Test", 0.0, -10.0, None)
VERKOOP = (datetime.date(2021, 1, 8), "Test", ISIN, "NDQ", TICKER, -AANTAL, ECHTE_KOERS_EUR, 10.0, "Test", 0.0, 10.0, None)


class TestWaardeNaLatereSplit(unittest.TestCase):
    def test_gesloten_positie_waardeert_tegen_echte_koers(self):
        dagen = pd.bdate_range("2021-01-04", "2021-01-12")
        price_data = pd.DataFrame({TICKER: ADJUSTED_KOERS_EUR}, index=dagen)

        with patch.object(portfolio_orchestratie, "db_get_portfolio_naam_en_transacties",
                          return_value=("Test", [KOOP, VERKOOP])), \
             patch.object(portfolio_orchestratie, "get_prices", return_value=price_data), \
             patch("ticker_prijscheck._haal_splits_op", return_value={SPLIT_DATUM: SPLIT_RATIO}):
            _naam, transacties_df, prijzen = portfolio_orchestratie._haal_portfolio_basis("ZZTESTSPLIT", forceer_vers=True)
        self.addCleanup(portfolio_orchestratie._basis_cache.pop, "ZZTESTSPLIT", None)

        resultaat = compute_value_over_time(transacties_df, prijzen)

        # 6 januari: 10 stuks vastgehouden, echte koers 1 euro.
        self.assertAlmostEqual(resultaat.loc["2021-01-06", "waarde"], AANTAL * ECHTE_KOERS_EUR, places=2)
        # Na de verkoop op 8 januari is de waarde 0.
        self.assertAlmostEqual(resultaat.loc["2021-01-11", "waarde"], 0.0, places=2)


if __name__ == "__main__":
    unittest.main()
