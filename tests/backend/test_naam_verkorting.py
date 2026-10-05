"""Unit tests voor naam_verkorting.py: korte_naam() en kies_korte_namen() (pure functies)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from naam_verkorting import korte_naam, kies_korte_namen

BLACKROCK = "BlackRock Asset Management Ireland - ETF"
VANGUARD = "Vanguard Group (Ireland) Limited"
VANECK = "VanEck Asset Management B.V."

VOORBEELDEN = [
    ("iShares Core S&P 500 UCITS ETF USD (Acc)", BLACKROCK, "S&P 500"),
    ("Vanguard S&P 500 UCITS ETF", VANGUARD, "S&P 500"),
    ("Vanguard FTSE All-World UCITS ETF", VANGUARD, "FTSE All-World"),
    ("iShares Core EURO STOXX 50 UCITS ETF EUR (Dist)", BLACKROCK, "EURO STOXX 50"),
    ("VanEck AEX UCITS ETF", VANECK, "AEX"),
    ("VanEck Sustainable Future of Food UCITS ETF A USD Acc", VANECK, "Sustainable Future of Food"),
    ("ASML Holding N.V.", None, "ASML Holding"),
    ("Take-Two Interactive Software, Inc.", None, "Take-Two Interactive Software"),
    ("Akzo Nobel N.V.", None, "Akzo Nobel"),
    ("Mastercard Incorporated", None, "Mastercard"),
    ("BYD Co Ltd", None, "BYD"),
]


class TestKorteNaam(unittest.TestCase):
    def test_voorbeelden(self):
        for long_name, family, verwacht in VOORBEELDEN:
            with self.subTest(long_name=long_name):
                self.assertEqual(korte_naam(long_name, family), verwacht)

    def test_geen_long_name_geeft_none(self):
        self.assertIsNone(korte_naam(None, BLACKROCK))
        self.assertIsNone(korte_naam("", BLACKROCK))
        self.assertIsNone(korte_naam("   ", None))

    def test_alles_valt_weg_geeft_long_name(self):
        self.assertEqual(korte_naam("iShares Core UCITS ETF", BLACKROCK), "iShares Core UCITS ETF")

    def test_family_none_gebruikt_alleen_constante_merken(self):
        self.assertEqual(korte_naam("iShares MSCI World UCITS ETF", None), "MSCI World")
        self.assertEqual(korte_naam("Vanguard S&P 500 UCITS ETF", None), "Vanguard S&P 500")

    def test_family_woord_midden_in_de_naam_blijft(self):
        self.assertEqual(korte_naam("iShares MSCI Ireland ETF", BLACKROCK), "MSCI Ireland")

    def test_class_a_midden_in_de_naam_blijft(self):
        self.assertEqual(korte_naam("Foo Class A Holdings Corp", None), "Foo Class A Holdings")

    def test_class_a_aan_het_einde_is_juridische_staart(self):
        self.assertEqual(korte_naam("Alphabet Inc. Class A", None), "Alphabet")

    def test_losse_a_alleen_weg_met_valuta_of_klasse_erbij(self):
        self.assertEqual(korte_naam("VanEck Gold Miners UCITS ETF A USD", VANECK), "Gold Miners")
        self.assertEqual(korte_naam("Foo Fund A", None), "Foo Fund A")

    def test_euro_is_geen_eur(self):
        self.assertEqual(korte_naam("Foo EURO STOXX EUR Acc", None), "Foo EURO STOXX")
        self.assertEqual(korte_naam("EURO STOXX 50", None), "EURO STOXX 50")

    def test_achtervoegsels_herhaald_maar_een_woord_blijft(self):
        self.assertEqual(korte_naam("BYD Co Ltd", None), "BYD")
        self.assertEqual(korte_naam("Ltd", None), "Ltd")


class TestKiesKorteNamen(unittest.TestCase):
    def _positie(self, long_name, family=None):
        return {"long_name": long_name, "fund_family": family}

    def test_botsing_cspx_vusa_onderscheidt_op_klasse(self):
        uit = kies_korte_namen({
            "CSPX.AS": self._positie("iShares Core S&P 500 UCITS ETF USD (Acc)", BLACKROCK),
            "VUSA.AS": self._positie("Vanguard S&P 500 UCITS ETF", VANGUARD),
        })
        self.assertEqual(uit, {"CSPX.AS": "S&P 500 Acc", "VUSA.AS": "S&P 500"})

    def test_botsing_zonder_klasse_onderscheidt_op_merk(self):
        uit = kies_korte_namen({
            "AAA.AS": self._positie("iShares MSCI World UCITS ETF", BLACKROCK),
            "BBB.AS": self._positie("Vanguard MSCI World UCITS ETF", VANGUARD),
        })
        self.assertEqual(uit, {"AAA.AS": "MSCI World iShares", "BBB.AS": "MSCI World Vanguard"})

    def test_botsing_onderscheidt_op_valuta(self):
        uit = kies_korte_namen({
            "AAA.AS": self._positie("iShares MSCI World UCITS ETF USD", BLACKROCK),
            "AAA.MI": self._positie("iShares MSCI World UCITS ETF EUR", BLACKROCK),
        })
        self.assertEqual(uit, {"AAA.AS": "MSCI World USD", "AAA.MI": "MSCI World EUR"})

    def test_botsing_pas_de_ticker_onderscheidt(self):
        uit = kies_korte_namen({
            "XYZ.AS": self._positie("Foo ETF"),
            "QRS.DE": self._positie("Foo ETF"),
        })
        self.assertEqual(uit, {"XYZ.AS": "Foo XYZ", "QRS.DE": "Foo QRS"})

    def test_geen_botsing_laat_namen_ongemoeid(self):
        uit = kies_korte_namen({
            "ASML.AS": self._positie("ASML Holding N.V."),
            "AKZA.AS": self._positie("Akzo Nobel N.V."),
        })
        self.assertEqual(uit, {"ASML.AS": "ASML Holding", "AKZA.AS": "Akzo Nobel"})

    def test_ticker_zonder_long_name_ontbreekt(self):
        uit = kies_korte_namen({
            "ASML.AS": self._positie("ASML Holding N.V."),
            "ONBEKEND": self._positie(None),
        })
        self.assertEqual(uit, {"ASML.AS": "ASML Holding"})


if __name__ == "__main__":
    unittest.main()
