"""
Unit tests voor bereken_twr() (analysis.py) — Time-Weighted Return, de
rendementsmaat die (anders dan XIRR) niet vertekend wordt door de TIMING
van stortingen/onttrekkingen.

Draait geheel offline: geen database, geen yfinance-calls — pure functie op
transacties_df + resultaat (zelfde patroon als test_rendement_over_tijd.py).
"""
import sys
import os
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import bereken_twr


class TestTwr(unittest.TestCase):
    def _rij(self, datum, aantal, totaal_eur, ticker="X", beurs="EAM"):
        return {
            "ticker": ticker, "datum": pd.Timestamp(datum), "aantal": aantal,
            "koers": 0.0, "totaal_eur": totaal_eur, "beurs": beurs, "product": ticker,
        }

    def test_leeg_resultaat_geeft_none(self):
        self.assertIsNone(bereken_twr(pd.DataFrame(), pd.DataFrame()))

    def test_een_dag_resultaat_geeft_none(self):
        # Geen enkele sub-periode mogelijk met maar 1 datum in resultaat.
        resultaat = pd.DataFrame({"waarde": [1000.0]}, index=[pd.Timestamp("2023-01-01")])
        self.assertIsNone(bereken_twr(pd.DataFrame(), resultaat))

    def test_geen_tussentijdse_cashflow_matcht_totaalrendement(self):
        # €1000 storting op 1 jan, waarde loopt zonder verdere cashflows op
        # naar €1100 op 1 feb -- zonder tussentijdse stortingen is TWR gelijk
        # aan het simpele totaalrendement: 1100/1000 - 1 = 10%.
        index = [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-15"), pd.Timestamp("2023-02-01")]
        resultaat = pd.DataFrame({"waarde": [1000.0, 1050.0, 1100.0]}, index=index)
        transacties_df = pd.DataFrame([self._rij("2023-01-01", 10.0, -1000.0)])
        twr = bereken_twr(transacties_df, resultaat)
        self.assertAlmostEqual(twr, 0.10, places=6)

    def test_storting_vlak_voor_koersstijging_beinvloedt_twr_niet(self):
        # Sub-periode 1 (1 jan -> 15 jan): waarde 1000 -> 1000, geen
        # koersbeweging (r=0%). Op 15 jan wordt €1000 bijgestort (waarde
        # springt naar 2000 in dezelfde stap, dus de bijstorting zelf mag
        # geen "rendement" opleveren -- r = 2000/(1000+1000) - 1 = 0%).
        # Sub-periode 2 (15 jan -> 1 feb): waarde 2000 -> 2200, r = 10%.
        # TWR = (1+0%) * (1+10%) - 1 = 10%, ONGEACHT de timing van de
        # bijstorting -- dat is precies het punt van TWR t.o.v. XIRR.
        index = [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-15"), pd.Timestamp("2023-02-01")]
        resultaat = pd.DataFrame({"waarde": [1000.0, 2000.0, 2200.0]}, index=index)
        transacties_df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -1000.0),
            self._rij("2023-01-15", 10.0, -1000.0),
        ])
        twr = bereken_twr(transacties_df, resultaat)
        self.assertAlmostEqual(twr, 0.10, places=6)

    def test_corporate_action_rij_telt_niet_mee_als_cashflow(self):
        # Een DEG/NON TRADEABLE-boekingsrij (split-conversie) mag nooit als
        # externe cashflow meetellen, ook niet als hij (oneigenlijk) een
        # bedrag != 0 zou hebben -- er komt geen geld van de belegger bij,
        # alleen het aandelenaantal verandert. Zonder de
        # _is_corporate_action_row-filter zou deze rij de noemer van de
        # sub-periode vervuilen en een verkeerd rendement opleveren i.p.v.
        # de werkelijke 10%.
        index = [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-15")]
        resultaat = pd.DataFrame({"waarde": [1000.0, 1100.0]}, index=index)
        transacties_df = pd.DataFrame([
            self._rij("2023-01-01", 10.0, -1000.0),
            self._rij("2023-01-15", 5.0, -500.0, beurs="DEG"),
        ])
        twr = bereken_twr(transacties_df, resultaat)
        self.assertAlmostEqual(twr, 0.10, places=6)

    def test_periode_zonder_investering_wordt_overgeslagen(self):
        # Vóór de eerste aankoop staat waarde en cashflow allebei op 0 --
        # zo'n sub-periode heeft een noemer van 0 en moet overgeslagen
        # worden i.p.v. te crashen of een verzonnen rendement te geven.
        index = [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02"), pd.Timestamp("2023-01-03")]
        resultaat = pd.DataFrame({"waarde": [0.0, 0.0, 1100.0]}, index=index)
        transacties_df = pd.DataFrame([self._rij("2023-01-03", 10.0, -1000.0)])
        twr = bereken_twr(transacties_df, resultaat)
        self.assertAlmostEqual(twr, 0.10, places=6)


if __name__ == "__main__":
    unittest.main()
