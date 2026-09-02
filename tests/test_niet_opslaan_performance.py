"""
Bevestigt de hypothese achter het Statistieken-incident van 2026-08-31 (zie
CLAUDE.md): een 'niet opslaan'-analyse van een grotere portfolio bleef
hangen zonder foutmelding. verifieer_tickers_met_prijs_parallel()'s eigen
docstring noemt al ~84s sequentieel voor 12 posities met een warme cache --
ruim boven de standaard gunicorn-timeout van 30s. Deze tests maken dat
kwantitatief en reproduceerbaar (offline, met een kunstmatige vertraging
i.p.v. echte Yahoo-calls) voor een iets grotere, realistischere portfolio:

1. De OUDE aanpak (verifieer_tickers_met_prijs_parallel voor de volle
   portfolio) duurt bij 25 posities met meerdere kandidaten en een koude
   cache lang genoeg om een 30s-timeout te riskeren.
2. De NIEUWE aanpak (basis_ticker_zekerheid_parallel, wat het 'niet
   opslaan'-pad in app.py nu standaard gebruikt) blijft voor dezelfde
   portfoliogrootte ruim onder een redelijke tijdslimiet -- OOK met de
   sindsdien toegevoegde standaard lichte prijscontrole (find_ticker_met_
   snelle_prijscheck) en met een volledig KOUDE ticker_prijscheck-cache
   (elke positie kost dus 1 echte -- hier gesimuleerde -- Yahoo-call, niet
   0). Dit is de her-meting die het vervolgdocument op de eerste
   timeout-fix vroeg: de standaard prijscontrole voegt immers weer een
   Yahoo-call per positie toe aan het snelle pad, en moest daarom parallel
   (basis_ticker_zekerheid_parallel) blijven i.p.v. sequentieel.

Draait geheel offline: find_ticker_detailed en vergelijk_prijs_op_datum
worden gemockt (net als tests/test_ticker_verificatie.py), dus geen echte
yahooquery/yfinance-calls en geen databasetoegang nodig -- deze tests
draaien dus ook gewoon in de GitHub Actions-CI.
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from analysis import verifieer_tickers_met_prijs_parallel, basis_ticker_zekerheid_parallel

AANTAL_POSITIES = 25
# Representatief voor een koude cache: elke yahooquery-zoekopdracht en elke
# historische-koers-opvraag kost hier 0,1s resp. 0,5s i.p.v. een echte
# netwerkroundtrip -- ver genoeg van 0 om een reëel tijdsverschil te meten,
# maar klein genoeg om de testsuite niet onnodig te vertragen.
ZOEK_VERTRAGING = 0.1
PRIJSCHECK_VERTRAGING = 0.5


def _transacties():
    return [{"datum": "2023-01-10", "koers": 100.0}, {"datum": "2023-06-10", "koers": 100.0}]


class TestOudeAanpakRisicoOpTimeout(unittest.TestCase):
    def test_25_posities_met_koude_cache_nadert_of_overschrijdt_gunicorn_timeout(self):
        posities = [(f"FONDS {i}", f"ISIN{i}", "TDG", _transacties()) for i in range(AANTAL_POSITIES)]

        def trage_find_ticker_detailed(product, isin, beurs):
            time.sleep(ZOEK_VERTRAGING)
            return {
                "ticker": f"TICK-{isin}",
                "zekerheid": "onzeker",
                "alternatieven": [
                    {"symbol": f"ALT1-{isin}", "exchange": "MUN"},
                    {"symbol": f"ALT2-{isin}", "exchange": "MUN"},
                    {"symbol": f"ALT3-{isin}", "exchange": "MUN"},
                ],
            }

        def traag_vergelijk(ticker, datum, bekende_koers):
            time.sleep(PRIJSCHECK_VERTRAGING)
            # Eerste datum matcht, tweede niet -- GEEN enkele kandidaat
            # levert dus ooit een 'overtuigende' match (alle datums tegelijk
            # kloppend), dus de kortsluit-optimalisatie (stop bij een
            # overtuigende match) kan nooit vroeg stoppen en ALLE
            # alternatieven worden volledig doorgerekend. Dit is precies het
            # scenario dat verifieer_tickers_met_prijs_parallel()'s
            # docstring beschrijft (fondsen op meerdere beurzen genoteerd,
            # koersen te dicht bij elkaar voor een overtuigende match).
            match = str(datum) == "2023-01-10"
            return {
                "yahoo_koers": 101.0, "bekende_koers": bekende_koers,
                "afwijking_pct": 1.0 if match else 8.0,
                "niveau": "ok" if match else "waarschuwing", "match": match,
            }

        with patch.object(analysis, "find_ticker_detailed", side_effect=trage_find_ticker_detailed), \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=traag_vergelijk), \
             patch.object(analysis, "_ticker_details_met_cache", return_value={}), \
             patch.object(analysis, "_land_sector_voor_weergave", return_value=(None, None, None)), \
             patch.object(analysis, "classify_ticker", return_value=False):  # generieke test-tickers, geen echt fonds
            start = time.time()
            verifieer_tickers_met_prijs_parallel(posities, max_workers=6)
            duur = time.time() - start

        # Verwacht ~20s (25 posities / 6 workers * ~4s per positie: 2
        # eigen prijschecks + 3 kandidaten x 2 prijschecks, à 0,5s) --
        # ruim genoeg richting de standaard gunicorn-timeout van 30s om de
        # hypothese te bevestigen, met marge voor tragere testomgevingen.
        self.assertGreater(duur, 10, f"verwachtte >10s als bevestiging van het timeout-risico, kreeg {duur:.1f}s")


class TestNieuweAanpakBlijftSnel(unittest.TestCase):
    def test_25_posities_met_koude_prijscheck_cache_blijft_ruim_onder_tijdslimiet(self):
        # Realistisch worst-case scenario voor de standaard lichte
        # prijscontrole: 25 UNIEKE tickers, GEEN enkele eerder gecontroleerd
        # (volledig koude ticker_prijscheck-cache), dus elke positie kost
        # zowel de zoekopdracht (ZOEK_VERTRAGING) als 1 prijscheck
        # (PRIJSCHECK_VERTRAGING) -- geen enkele wijkt af, dus geen
        # escalatie naar stap 2/3.
        def trage_find_ticker_detailed(product, isin, beurs):
            time.sleep(ZOEK_VERTRAGING)
            return {"ticker": f"TICK-{isin}", "zekerheid": "zeker", "alternatieven": []}

        def trage_vergelijk(ticker, datum, bekende_koers):
            time.sleep(PRIJSCHECK_VERTRAGING)
            return {"yahoo_koers": bekende_koers, "bekende_koers": bekende_koers, "afwijking_pct": 0.5, "niveau": "ok", "match": True}

        posities = [(f"FONDS {i}", f"ISIN{i}", "TDG", _transacties()) for i in range(AANTAL_POSITIES)]

        with patch.object(analysis, "find_ticker_detailed", side_effect=trage_find_ticker_detailed), \
             patch.object(analysis, "vergelijk_prijs_op_datum", side_effect=trage_vergelijk):
            start = time.time()
            basis_ticker_zekerheid_parallel(posities, max_workers=8)
            duur = time.time() - start

        # Sequentieel zou dit 25 x (0,1s + 0,5s) = 15s zijn -- met 8
        # parallelle workers verwacht ~ceil(25/8) x 0,6s = 1,8s. Ruime marge
        # (10s) voor tragere testomgevingen, maar nog altijd een harde
        # garantie dat dit ver onder een 30s-gunicorn-timeout blijft.
        self.assertLess(duur, 10, f"verwachtte <10s, kreeg {duur:.1f}s")


if __name__ == "__main__":
    unittest.main()
