"""bereken_totaal_degiro(): waarde posities + saldo - netto gestort. Zonder database."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from statistieken import bereken_totaal_degiro


def _kassaldo(saldo, netto_gestort):
    return {"saldo_eur": saldo, "netto_gestort_eur": netto_gestort}


class TestTotaalDegiro(unittest.TestCase):
    def test_formule(self):
        # 1000 + 50 - 900 = 150
        self.assertEqual(bereken_totaal_degiro(1000.0, _kassaldo(50.0, 900.0)), 150.0)

    def test_voorbeeld_rna(self):
        self.assertEqual(bereken_totaal_degiro(16127.88, _kassaldo(3.48, 13440.0)), 2691.36)

    def test_zonder_kassaldo_none(self):
        self.assertIsNone(bereken_totaal_degiro(1000.0, None))
        self.assertIsNone(bereken_totaal_degiro(1000.0, {}))

    def test_dividend_of_promo_telt_alleen_via_saldo(self):
        zonder = bereken_totaal_degiro(1000.0, _kassaldo(50.0, 900.0))
        # 12,50 dividend/promo komt op het saldo; netto gestort blijft gelijk: totaal stijgt precies 12,50.
        met = bereken_totaal_degiro(1000.0, _kassaldo(62.5, 900.0))
        self.assertAlmostEqual(met - zonder, 12.5, places=2)

    def test_storting_verandert_niets(self):
        voor = bereken_totaal_degiro(1000.0, _kassaldo(50.0, 900.0))
        na = bereken_totaal_degiro(1000.0, _kassaldo(250.0, 1100.0))
        self.assertEqual(voor, na)

    def test_aankoop_verschuift_saldo_naar_waarde(self):
        voor = bereken_totaal_degiro(1000.0, _kassaldo(250.0, 900.0))
        na = bereken_totaal_degiro(1200.0, _kassaldo(50.0, 900.0))
        self.assertEqual(voor, na)


if __name__ == "__main__":
    unittest.main()
