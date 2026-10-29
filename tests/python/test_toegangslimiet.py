import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.toegangslimiet import (
    Toegangslimiet, Regel, wachttijd_tekst, CATEGORIE_FOUTE_CODE, CATEGORIE_GEVOELIGE_ACTIE, REGELS_FOUTE_CODE,
    MAX_BIJGEHOUDEN_SLEUTELS,
)

TIJDELIJK = "tijdelijk"


class Klok:
    def __init__(self):
        self.nu = 1000.0

    def __call__(self):
        return self.nu


def maak(regels=None):
    klok = Klok()
    return Toegangslimiet(klok, regels_per_categorie={TIJDELIJK: regels or (Regel(3, 60),)}), klok


class TestToegangslimiet(unittest.TestCase):
    def test_onder_het_maximum_is_vrij(self):
        limiet, _ = maak()
        limiet.registreer(TIJDELIJK, "ip")
        limiet.registreer(TIJDELIJK, "ip")
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "ip"), 0)

    def test_op_het_maximum_geblokkeerd_tot_oudste_uit_venster_valt(self):
        limiet, klok = maak()
        for _ in range(3):
            limiet.registreer(TIJDELIJK, "ip")
            klok.nu += 10
        # Oudste poging was 30 s geleden, venster 60 s.
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "ip"), 30)
        klok.nu += 30
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "ip"), 0)

    def test_ips_zijn_onafhankelijk(self):
        limiet, _ = maak()
        for _ in range(3):
            limiet.registreer(TIJDELIJK, "a")
        self.assertGreater(limiet.wachttijd(TIJDELIJK, "a"), 0)
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "b"), 0)

    def test_langste_wachttijd_van_meerdere_regels_geldt(self):
        limiet, klok = maak((Regel(2, 60), Regel(3, 600)))
        for _ in range(3):
            limiet.registreer(TIJDELIJK, "ip")
            klok.nu += 61
        # De korte regel is alweer vrij; de lange (3 per 600 s) niet: oudste 183 s geleden.
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "ip"), 600 - 183)

    def test_verbruik_telt_alleen_toegestane_pogingen(self):
        limiet, klok = maak()
        self.assertEqual([limiet.verbruik(TIJDELIJK, "ip") for _ in range(3)], [0, 0, 0])
        self.assertEqual(limiet.verbruik(TIJDELIJK, "ip"), 60)
        klok.nu += 30
        # De geweigerde poging verlengde het venster niet.
        self.assertEqual(limiet.verbruik(TIJDELIJK, "ip"), 30)
        klok.nu += 30
        self.assertEqual(limiet.verbruik(TIJDELIJK, "ip"), 0)

    def test_wis_leegt_alles(self):
        limiet, _ = maak()
        for _ in range(3):
            limiet.registreer(TIJDELIJK, "ip")
        limiet.wis()
        self.assertEqual(limiet.wachttijd(TIJDELIJK, "ip"), 0)

    def test_standaardregels_foute_code_per_ip(self):
        klok = Klok()
        limiet = Toegangslimiet(klok)
        for _ in range(REGELS_FOUTE_CODE[0].maximum):
            limiet.registreer(CATEGORIE_FOUTE_CODE, "ip")
        self.assertEqual(limiet.wachttijd(CATEGORIE_FOUTE_CODE, "ip"), REGELS_FOUTE_CODE[0].venster_seconden)
        klok.nu += REGELS_FOUTE_CODE[0].venster_seconden
        self.assertEqual(limiet.wachttijd(CATEGORIE_FOUTE_CODE, "ip"), 0)

    def test_daglimiet_blijft_gelden_na_het_korte_venster(self):
        klok = Klok()
        limiet = Toegangslimiet(klok)
        korte, dag = REGELS_FOUTE_CODE
        for _ in range(dag.maximum // korte.maximum):
            for _ in range(korte.maximum):
                limiet.registreer(CATEGORIE_FOUTE_CODE, "ip")
            klok.nu += korte.venster_seconden
        self.assertGreater(limiet.wachttijd(CATEGORIE_FOUTE_CODE, "ip"), 0)

    def test_gevoelige_actie_standaard_vijf_per_uur(self):
        limiet = Toegangslimiet(Klok())
        resultaten = [limiet.verbruik(CATEGORIE_GEVOELIGE_ACTIE, "ip") for _ in range(6)]
        self.assertEqual(resultaten[:5], [0] * 5)
        self.assertGreater(resultaten[5], 0)

    def test_oude_sleutels_worden_opgeruimd_bij_volle_opslag(self):
        limiet, klok = maak()
        for i in range(MAX_BIJGEHOUDEN_SLEUTELS):
            limiet.registreer(TIJDELIJK, f"ip{i}")
        klok.nu += 1000
        limiet.registreer(TIJDELIJK, "nieuw")
        self.assertEqual(len(limiet._gebeurtenissen), 1)


class TestWachttijdTekst(unittest.TestCase):
    def test_minuten_naar_boven_afgerond(self):
        self.assertEqual(wachttijd_tekst(1), "over 1 minuut")
        self.assertEqual(wachttijd_tekst(61), "over 2 minuten")
        self.assertEqual(wachttijd_tekst(600), "over 10 minuten")

    def test_lange_wachttijd_in_uren(self):
        self.assertEqual(wachttijd_tekst(3 * 3600), "over 3 uur")


if __name__ == "__main__":
    unittest.main()
