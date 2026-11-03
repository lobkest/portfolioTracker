"""Na een automatische tickerwissel rekent hetzelfde bezoek opnieuw met de nieuwe ticker (zonder netwerk of database)."""
import unittest
from unittest.mock import patch

from flask import Flask

from backend import portfolio_orchestratie as po
from backend.diagnostiek import (
    CATEGORIE_TICKERS, CONTROLE_GEDRAAID, INFO, haal_controles, haal_meldingen, meld, registreer_controle,
)


class TestBuildPortfolioResponseNaWissel(unittest.TestCase):
    def _bouw(self, kern_antwoorden):
        with patch.object(po, "haal_portfolio_basis", return_value=("Naam", "df", "prijzen")) as basis, \
             patch.object(po, "analyze_transacties_kern", side_effect=kern_antwoorden) as kern:
            antwoord = po.build_portfolio_response("ZZTEST")
        return antwoord, basis, kern

    def test_zonder_wissel_een_berekening_en_geen_vlag(self):
        antwoord, basis, kern = self._bouw([{"per_ticker": {}}])
        self.assertNotIn("tickers_gewijzigd", antwoord)
        self.assertEqual((basis.call_count, kern.call_count), (1, 1))

    def test_na_wissel_opnieuw_rekenen_met_vlag_en_zonder_nieuwe_wissel(self):
        antwoord, basis, kern = self._bouw([{"tickers_gewijzigd": True}, {"per_ticker": {"NIEUW.AS": {}}}])
        self.assertTrue(antwoord["tickers_gewijzigd"])
        self.assertIn("NIEUW.AS", antwoord["per_ticker"])
        self.assertEqual((basis.call_count, kern.call_count), (2, 2))
        self.assertFalse(kern.call_args_list[0].kwargs.get("automatisch_wisselen", True) is False)
        self.assertFalse(kern.call_args_list[1].kwargs["automatisch_wisselen"])


class TestDiagnostiekNaTweedeRonde(unittest.TestCase):
    def test_alleen_de_meldingen_van_de_tweede_ronde_tellen(self):
        ronde = {"n": 0}

        def kern(*args, **kwargs):
            ronde["n"] += 1
            meld(CATEGORIE_TICKERS, INFO, "gedeeld", sleutel="gedeeld")
            registreer_controle(CATEGORIE_TICKERS, "Check", CONTROLE_GEDRAAID, bevindingen=1, ophogen=True)
            if ronde["n"] == 1:
                meld(CATEGORIE_TICKERS, INFO, "oude ticker", sleutel="alleen_oud")
                return {"tickers_gewijzigd": True}
            meld(CATEGORIE_TICKERS, INFO, "nieuwe ticker", sleutel="alleen_nieuw")
            return {}

        with Flask(__name__).test_request_context():
            meld(CATEGORIE_TICKERS, INFO, "van voor het laden", sleutel="voor")
            with patch.object(po, "haal_portfolio_basis", return_value=("Naam", "df", "prijzen")),                  patch.object(po, "analyze_transacties_kern", side_effect=kern):
                po.build_portfolio_response("ZZTEST")
            sleutels = [m["sleutel"] for m in haal_meldingen()]
            controles = haal_controles()

        self.assertEqual(sorted(sleutels), ["alleen_nieuw", "gedeeld", "voor"])
        self.assertEqual([c["bevindingen"] for c in controles], [1])


if __name__ == "__main__":
    unittest.main()
