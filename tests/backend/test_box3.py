"""Box 3: de basis per jaar (bouw_box3_basis) en de drie stelsels (bereken_box3). Puur, geen database."""
import json
import os
import sys
import unittest
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from box3 import bouw_box3_basis, bereken_box3, valideer_box3_invoer
from portfolio_calc import compute_value_over_time
from statistieken import bereken_holdings_en_gesloten

GEEN_INVOER = valideer_box3_invoer({})[0]


def _rij(datum, aantal, totaal, kosten=-2.0, ticker="AAA", tijd="10:00", waarde_eur=None, autofx=None):
    return {
        "datum": pd.Timestamp(datum), "tijd": tijd, "product": f"Bijnaam {ticker}", "isin": "NL0000000001",
        "beurs": "EAM", "ticker": ticker, "aantal": aantal, "koers": 0.0, "totaal_eur": totaal,
        "transactiekosten": kosten, "autofx_kosten": autofx,
        "waarde_eur": waarde_eur if waarde_eur is not None else totaal,
    }


def _resultaat(punten, geinvesteerd=None):
    """punten: {datum: waarde}; elke dag tussen eerste en laatste krijgt de vorige waarde."""
    reeks = pd.Series({pd.Timestamp(d): w for d, w in punten.items()}).sort_index()
    dagen = pd.date_range(reeks.index.min(), reeks.index.max(), freq="D")
    df = pd.DataFrame({"waarde": reeks.reindex(dagen).ffill()})
    df["geinvesteerd"] = geinvesteerd if geinvesteerd is not None else 0.0
    return df


def _jaar(jaar, begin=0.0, eind=0.0, inleg=0.0, kosten=0.0, dividend=None, gerealiseerd=0.0, lopend=False):
    return {
        "jaar": jaar, "lopend": lopend, "waarde_begin": begin, "waarde_eind": eind, "netto_inleg": inleg,
        "kosten": kosten, "kosten_onvolledig": False, "dividend_bruto": dividend, "dividendbelasting": None,
        "gerealiseerd": gerealiseerd,
    }


def _basis(*jaren, latente_winst=0.0):
    return {"jaren": list(jaren), "verkopen": [], "latente_winst": latente_winst, "dividend_beschikbaar": True}


def _invoer(**kw):
    invoer, fout = valideer_box3_invoer(kw)
    assert fout is None, fout
    return invoer


class TestBox3Basis(unittest.TestCase):
    def test_alleen_aankopen_in_een_jaar(self):
        df = pd.DataFrame([_rij("2024-03-01", 10, -1002.0)])
        resultaat = _resultaat({"2024-03-01": 1000.0, "2024-12-31": 1100.0})
        basis = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")
        self.assertEqual(len(basis["jaren"]), 1)
        j = basis["jaren"][0]
        self.assertEqual((j["jaar"], j["lopend"]), (2024, True))
        self.assertEqual((j["waarde_begin"], j["waarde_eind"]), (0.0, 1100.0))
        self.assertEqual(j["netto_inleg"], 1002.0)
        self.assertEqual(j["kosten"], 2.0)
        self.assertFalse(j["kosten_onvolledig"])
        self.assertEqual(j["gerealiseerd"], 0.0)
        self.assertEqual(basis["verkopen"], [])
        self.assertEqual(basis["latente_winst"], 98.0)  # 1100 - 1002

    def test_aankoop_jaar_1_verkoop_jaar_2(self):
        df = pd.DataFrame([_rij("2023-06-01", 10, -1002.0), _rij("2024-06-01", -5, 598.0)])
        resultaat = _resultaat({"2023-06-01": 1000.0, "2023-12-31": 1200.0, "2024-06-01": 600.0, "2024-12-31": 650.0})
        basis = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")
        j2023, j2024 = basis["jaren"]
        self.assertEqual((j2023["waarde_begin"], j2023["waarde_eind"], j2023["netto_inleg"]), (0.0, 1200.0, 1002.0))
        self.assertEqual((j2024["waarde_begin"], j2024["waarde_eind"], j2024["netto_inleg"]), (1200.0, 650.0, -598.0))
        self.assertFalse(j2023["lopend"])
        # Kostenbasis 1002 / 10 * 5 = 501; winst 598 - 501 = 97.
        self.assertEqual(j2023["gerealiseerd"], 0.0)
        self.assertEqual(j2024["gerealiseerd"], 97.0)
        self.assertEqual(basis["verkopen"], [{
            "datum": "2024-06-01", "ticker": "AAA", "bijnaam": "Bijnaam AAA", "aantal": 5.0,
            "opbrengst": 598.0, "kostenbasis": 501.0, "winst": 97.0,
        }])
        self.assertEqual(basis["latente_winst"], 149.0)  # 650 - 501

    def test_lopend_jaar_zonder_transacties_loopt_door_tot_vandaag(self):
        df = pd.DataFrame([_rij("2024-06-01", 10, -1000.0)])
        resultaat = _resultaat({"2024-06-01": 1000.0, "2025-03-01": 1100.0})
        basis = bouw_box3_basis(df, resultaat, None, vandaag="2025-03-01")
        self.assertEqual([(j["jaar"], j["lopend"]) for j in basis["jaren"]], [(2024, False), (2025, True)])
        self.assertEqual(basis["jaren"][1]["waarde_eind"], 1100.0)

    def test_split_midden_in_een_jaar_verandert_netto_inleg_niet(self):
        rijen = [_rij("2024-01-10", 10, -1000.0),
                 _rij("2024-06-01", -10, 0.0, kosten=None, tijd="00:00"),
                 _rij("2024-06-01", 20, 0.0, kosten=None, tijd="00:00")]
        resultaat = _resultaat({"2024-01-10": 1000.0, "2024-12-31": 1000.0})
        j = bouw_box3_basis(pd.DataFrame(rijen), resultaat, None, vandaag="2024-12-31")["jaren"][0]
        self.assertEqual(j["netto_inleg"], 1000.0)
        self.assertFalse(j["kosten_onvolledig"])  # splitrijen hebben normaal geen kosten

    def test_decimal_invoer_uit_postgres(self):
        rij = _rij("2024-03-01", Decimal("10"), Decimal("-1002.00"), kosten=Decimal("-2.00"))
        resultaat = _resultaat({"2024-03-01": 1000.0, "2024-12-31": 1100.0})
        j = bouw_box3_basis(pd.DataFrame([rij]), resultaat, None, vandaag="2024-12-31")["jaren"][0]
        self.assertEqual((j["netto_inleg"], j["kosten"]), (1002.0, 2.0))
        self.assertIsInstance(j["netto_inleg"], float)

    def test_nan_wordt_overgeslagen_en_komt_niet_in_json(self):
        rijen = [_rij("2024-03-01", 10, -1000.0), _rij("2024-04-01", 1, float("nan"), kosten=float("nan"))]
        rijen[1]["koers"] = float("nan")
        resultaat = _resultaat({"2024-03-01": 1000.0, "2024-12-31": 1100.0})
        basis = bouw_box3_basis(pd.DataFrame(rijen), resultaat, None, vandaag="2024-12-31")
        self.assertEqual(basis["jaren"][0]["netto_inleg"], 1000.0)
        json.dumps(basis, allow_nan=False)

    def test_ontbrekende_kosten_bij_een_aankoop_markeert_onvolledig(self):
        df = pd.DataFrame([_rij("2024-03-01", 10, -1000.0, kosten=None)])
        resultaat = _resultaat({"2024-03-01": 1000.0, "2024-12-31": 1000.0})
        j = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")["jaren"][0]
        self.assertTrue(j["kosten_onvolledig"])

    def test_autofx_kosten_tellen_mee(self):
        df = pd.DataFrame([_rij("2024-03-01", 10, -1003.0, kosten=-2.0, autofx=-1.0)])
        resultaat = _resultaat({"2024-03-01": 1000.0, "2024-12-31": 1000.0})
        j = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")["jaren"][0]
        self.assertEqual(j["kosten"], 3.0)

    def test_dividend_none_tegenover_lijst(self):
        df = pd.DataFrame([_rij("2023-03-01", 10, -1000.0)])
        resultaat = _resultaat({"2023-03-01": 1000.0, "2024-12-31": 1000.0})
        zonder = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")
        self.assertFalse(zonder["dividend_beschikbaar"])
        self.assertEqual({j["dividend_bruto"] for j in zonder["jaren"]}, {None})

        dividenden = [
            {"datum": pd.Timestamp("2024-03-15").date(), "bruto_eur": 10.0, "belasting_eur": -1.5, "herinvesteerd": False},
            {"datum": pd.Timestamp("2024-09-15").date(), "bruto_eur": 5.0, "belasting_eur": None, "herinvesteerd": True},
            {"datum": pd.Timestamp("2024-10-15").date(), "bruto_eur": None, "belasting_eur": None, "herinvesteerd": False},
        ]
        met = bouw_box3_basis(df, resultaat, dividenden, vandaag="2024-12-31")
        self.assertTrue(met["dividend_beschikbaar"])
        j2023, j2024 = met["jaren"]
        self.assertEqual((j2023["dividend_bruto"], j2023["dividendbelasting"]), (0.0, 0.0))
        self.assertEqual((j2024["dividend_bruto"], j2024["dividendbelasting"]), (15.0, 1.5))

    def test_som_netto_inleg_is_eindstand_geinvesteerd(self):
        rijen = [
            _rij("2022-05-02", 10, -1002.0),
            _rij("2023-02-01", 5, -260.0, ticker="BBB"),
            _rij("2023-06-01", -10, 0.0, kosten=None, tijd="00:00"),
            _rij("2023-06-01", 20, 0.0, kosten=None, tijd="00:00"),
            _rij("2024-03-01", -8, 430.0),
            _rij("2024-07-01", -5, 300.0, ticker="BBB"),
        ]
        df = pd.DataFrame(rijen)
        dagen = pd.date_range("2022-05-02", "2024-12-31", freq="D")
        prijzen = pd.DataFrame({"AAA": 100.0, "BBB": 52.0}, index=dagen)
        prijzen.loc[prijzen.index >= "2023-06-01", "AAA"] = 50.0
        resultaat = compute_value_over_time(df, prijzen)
        basis = bouw_box3_basis(df, resultaat, None, vandaag="2024-12-31")
        som = sum(j["netto_inleg"] for j in basis["jaren"])
        self.assertAlmostEqual(som, float(resultaat["geinvesteerd"].iloc[-1]), places=2)

    def test_zonder_resultaat_lege_basis(self):
        basis = bouw_box3_basis(pd.DataFrame([_rij("2024-03-01", 10, -1000.0)]), None, None)
        self.assertEqual(basis["jaren"], [])


class TestBox3Kostenbasis(unittest.TestCase):
    def _verkopen(self, rijen, resultaat_tot="2024-12-31"):
        resultaat = _resultaat({"2023-01-01": 0.0, resultaat_tot: 0.0})
        return bouw_box3_basis(pd.DataFrame(rijen), resultaat, None, vandaag=resultaat_tot)["verkopen"]

    def test_aankoopkosten_zitten_in_de_kostenbasis(self):
        # Waarde 1000 + kosten 10 = 1010; verkoop van alles voor 1100 netto: winst 90, niet 100.
        v = self._verkopen([_rij("2023-02-01", 10, -1010.0, waarde_eur=-1000.0), _rij("2024-02-01", -10, 1100.0)])
        self.assertEqual((v[0]["kostenbasis"], v[0]["winst"]), (1010.0, 90.0))

    def test_gedeeltelijke_verkoop(self):
        v = self._verkopen([
            _rij("2023-02-01", 10, -1000.0), _rij("2023-05-01", 10, -1400.0), _rij("2024-02-01", -5, 700.0),
        ])
        # Gemiddeld (1000 + 1400) / 20 = 120 per stuk; 5 stuks = 600.
        self.assertEqual((v[0]["aantal"], v[0]["kostenbasis"], v[0]["winst"]), (5.0, 600.0, 100.0))

    def test_split_tussen_aankoop_en_verkoop(self):
        v = self._verkopen([
            _rij("2023-02-01", 10, -1000.0),
            _rij("2023-06-01", -10, 0.0, kosten=None, tijd="00:00"),
            _rij("2023-06-01", 20, 0.0, kosten=None, tijd="00:00"),
            _rij("2024-02-01", -10, 700.0),
        ])
        # Na de split 20 stuks voor 1000: 10 stuks = 500.
        self.assertEqual((v[0]["kostenbasis"], v[0]["winst"]), (500.0, 200.0))

    def test_verlies_op_een_verkoop(self):
        v = self._verkopen([_rij("2023-02-01", 10, -1000.0), _rij("2024-02-01", -10, 800.0)])
        self.assertEqual(v[0]["winst"], -200.0)

    def test_gak_blijft_op_waarde_eur(self):
        """Watchdog: de bestaande GAK rekent nog steeds zonder kosten, box 3 met."""
        rijen = [_rij("2023-02-01", 10, -1010.0, waarde_eur=-1000.0)]
        open_posities, _ = bereken_holdings_en_gesloten(pd.DataFrame(rijen))
        self.assertEqual(open_posities["AAA"]["gak"], 100.0)


class TestScenarioAanwas(unittest.TestCase):
    def test_verlies_boven_drempel_verrekend_in_volgend_jaar(self):
        # Jaar 1: -1200, verrekenbaar 1200 - 500 = 700. Jaar 2: 3000 - 1800 = 1200, min 700 = 500 belastbaar.
        basis = _basis(_jaar(2024, begin=10000, eind=8800), _jaar(2025, begin=8800, eind=11800))
        j1, j2 = bereken_box3(basis, GEEN_INVOER)["jaren"]
        self.assertEqual((j1["aanwas"]["rendement"], j1["aanwas"]["verlies_erbij"], j1["aanwas"]["belasting"]),
                         (-1200.0, 700.0, 0.0))
        self.assertEqual(j1["aanwas"]["verliesvoorraad"], 700.0)
        self.assertEqual((j2["aanwas"]["verrekend_verlies"], j2["aanwas"]["belastbaar"]), (700.0, 500.0))
        self.assertEqual(j2["aanwas"]["belasting"], 180.0)  # 500 x 36%
        self.assertEqual(j2["aanwas"]["verliesvoorraad"], 0.0)

    def test_verlies_onder_drempel_vervalt(self):
        basis = _basis(_jaar(2024, begin=10000, eind=9600), _jaar(2025, begin=9600, eind=12600))
        j1, j2 = bereken_box3(basis, GEEN_INVOER)["jaren"]
        self.assertEqual(j1["aanwas"]["verlies_erbij"], 0.0)
        self.assertEqual(j2["aanwas"]["belastbaar"], 1200.0)

    def test_heffingsvrij_enkel_en_partner(self):
        basis = _basis(_jaar(2025, begin=10000, eind=15000))
        enkel = bereken_box3(basis, GEEN_INVOER)["jaren"][0]["aanwas"]
        partner = bereken_box3(basis, _invoer(fiscale_partner=True))["jaren"][0]["aanwas"]
        self.assertEqual((enkel["heffingsvrij"], enkel["belastbaar"], enkel["belasting"]), (1800, 3200.0, 1152.0))
        self.assertEqual((partner["heffingsvrij"], partner["belastbaar"], partner["belasting"]), (3600, 1400.0, 504.0))

    def test_dividend_en_ander_vermogen_tellen_mee(self):
        basis = _basis(_jaar(2025, begin=10000, eind=11000, dividend=1500.0))
        a = bereken_box3(basis, _invoer(rendement_ander_vermogen=300))["jaren"][0]["aanwas"]
        # 1000 + 1500 + 300 = 2800; min 1800 = 1000; x 36% = 360.
        self.assertEqual((a["koersresultaat"], a["rendement"], a["belasting"]), (1000.0, 2800.0, 360.0))

    def test_netto_inleg_is_geen_rendement(self):
        basis = _basis(_jaar(2025, begin=10000, eind=20000, inleg=10000))
        self.assertEqual(bereken_box3(basis, GEEN_INVOER)["jaren"][0]["aanwas"]["rendement"], 0.0)


class TestScenarioVermogenswinst(unittest.TestCase):
    def test_alleen_gerealiseerd(self):
        basis = _basis(_jaar(2025, begin=10000, eind=50000, gerealiseerd=2800.0))
        b = bereken_box3(basis, GEEN_INVOER)["jaren"][0]["vermogenswinst"]
        self.assertEqual((b["rendement"], b["belastbaar"], b["belasting"]), (2800.0, 1000.0, 360.0))


class TestBAllesVerkopen(unittest.TestCase):
    def _variant(self, *jaren, latente_winst, invoer=GEEN_INVOER):
        return bereken_box3(_basis(*jaren, latente_winst=latente_winst), invoer)["b_alles_verkopen"]

    def test_latente_winst_boven_heffingsvrij(self):
        # Gerealiseerd 1.000 + latent 5.000 = 6.000, min 1.800 = 4.200 x 36% = 1.512. Zonder verkopen: 0.
        v = self._variant(_jaar(2026, gerealiseerd=1000.0, lopend=True), latente_winst=5000.0)
        self.assertEqual((v["jaar"], v["rendement"], v["belastbaar"], v["belasting"], v["extra_belasting"]),
                         (2026, 6000.0, 4200.0, 1512.0, 1512.0))

    def test_extra_belasting_ten_opzichte_van_b(self):
        # B: 3.000 - 1.800 = 1.200 x 36% = 432. Variant: 5.000 - 1.800 = 3.200 x 36% = 1.152; extra 720.
        v = self._variant(_jaar(2026, gerealiseerd=3000.0, lopend=True), latente_winst=2000.0)
        self.assertEqual((v["belasting"], v["extra_belasting"]), (1152.0, 720.0))

    def test_latente_winst_onder_heffingsvrij(self):
        v = self._variant(_jaar(2026, gerealiseerd=500.0, lopend=True), latente_winst=1000.0)
        self.assertEqual((v["belastbaar"], v["belasting"], v["extra_belasting"]), (0.0, 0.0, 0.0))

    def test_openstaand_verlies_uit_eerdere_jaren(self):
        # 2025: verlies 2.000 -> 1.500 verrekenbaar. 2026: 6.000 - 1.800 = 4.200, min 1.500 = 2.700 x 36% = 972.
        v = self._variant(_jaar(2025, gerealiseerd=-2000.0), _jaar(2026, lopend=True), latente_winst=6000.0)
        self.assertEqual((v["belastbaar"], v["belasting"], v["extra_belasting"]), (2700.0, 972.0, 972.0))

    def test_partner(self):
        # 6.000 - 3.600 = 2.400 x 36% = 864.
        v = self._variant(_jaar(2026, lopend=True), latente_winst=6000.0, invoer=_invoer(fiscale_partner=True))
        self.assertEqual((v["heffingsvrij"], v["belasting"]), (3600, 864.0))

    def test_negatieve_latente_winst_verlaagt_het_resultaat(self):
        # Gerealiseerd 3.000 - latent verlies 1.000 = 2.000: 200 x 36% = 72; B zonder verkopen 432, dus extra -360.
        v = self._variant(_jaar(2026, gerealiseerd=3000.0, lopend=True), latente_winst=-1000.0)
        self.assertEqual((v["rendement"], v["belasting"], v["extra_belasting"]), (2000.0, 72.0, -360.0))

    def test_zonder_lopend_jaar_geen_variant(self):
        self.assertIsNone(self._variant(_jaar(2025, gerealiseerd=3000.0), latente_winst=5000.0))


class TestHuidigStelsel(unittest.TestCase):
    def test_hand_uitgerekend_2024(self):
        # bank 20.000, overig 100.000 (portfolio op 1-1). Forfaitair 20.000 x 1,44% + 100.000 x 6,04% = 6.328.
        # Grondslag 120.000, na vrijstelling 63.000. Belasting 36% x 6.328 / 120.000 x 63.000 = 1.195,99.
        basis = _basis(_jaar(2024, begin=100000, eind=108000, kosten=20))
        h = bereken_box3(basis, _invoer(banktegoeden=20000, rendement_ander_vermogen=400))["jaren"][0]["huidig"]
        self.assertEqual(h["forfaitair_rendement"], 6328.0)
        self.assertEqual(h["grondslag"], 120000.0)
        self.assertEqual(h["grondslag_na_vrijstelling"], 63000.0)
        self.assertEqual(h["belasting_forfaitair"], 1195.99)
        # Tegenbewijs: 8.000 + 20 kosten + 400 = 8.420 x 36% = 3.031,20: hoger, dus forfaitair geldt.
        self.assertEqual((h["werkelijk_rendement"], h["belasting_tegenbewijs"]), (8420.0, 3031.2))
        self.assertEqual((h["geldt"], h["belasting"]), ("forfaitair", 1195.99))
        self.assertTrue(h["definitief"])
        self.assertFalse(h["geschat"])

    def test_tegenbewijs_lager_dan_geldt_tegenbewijs(self):
        basis = _basis(_jaar(2024, begin=100000, eind=101000, kosten=20))
        h = bereken_box3(basis, _invoer(banktegoeden=20000))["jaren"][0]["huidig"]
        self.assertEqual((h["belasting_tegenbewijs"], h["geldt"], h["belasting"]), (367.2, "tegenbewijs", 367.2))

    def test_grondslag_onder_heffingsvrij_vermogen(self):
        h = bereken_box3(_basis(_jaar(2024, begin=50000, eind=60000)), GEEN_INVOER)["jaren"][0]["huidig"]
        self.assertEqual((h["grondslag_na_vrijstelling"], h["belasting"]), (0.0, 0.0))

    def test_partner_verdubbelt_heffingsvrij_vermogen(self):
        h = bereken_box3(_basis(_jaar(2024, begin=100000, eind=110000)), _invoer(fiscale_partner=True))
        h = h["jaren"][0]["huidig"]
        self.assertEqual((h["heffingsvrij"], h["belasting"]), (114000, 0.0))

    def test_schulden_onder_en_boven_de_drempel(self):
        basis = _basis(_jaar(2024, begin=100000, eind=100000))
        zonder = bereken_box3(basis, GEEN_INVOER)["jaren"][0]["huidig"]
        onder = bereken_box3(basis, _invoer(schulden=3000))["jaren"][0]["huidig"]
        boven = bereken_box3(basis, _invoer(schulden=10000))["jaren"][0]["huidig"]
        self.assertEqual(onder["schulden_boven_drempel"], 0.0)
        self.assertEqual(onder["belasting_forfaitair"], zonder["belasting_forfaitair"])
        # 10.000 - 3.700 = 6.300; forfaitair 6.040 - 6.300 x 2,61% = 5.875,57; grondslag 93.700.
        self.assertEqual(boven["schulden_boven_drempel"], 6300.0)
        self.assertEqual(boven["forfaitair_rendement"], 5875.57)
        self.assertEqual(boven["grondslag"], 93700.0)

    def test_jaar_voor_2023_niet_berekend(self):
        uit = bereken_box3(_basis(_jaar(2022, begin=100000, eind=110000), _jaar(2023, begin=110000, eind=110000)),
                           GEEN_INVOER)
        self.assertEqual(uit["jaren"][0]["huidig"], {"berekend": False})
        self.assertTrue(uit["jaren"][1]["huidig"]["berekend"])
        self.assertEqual(uit["totaal"]["huidig"], uit["jaren"][1]["huidig"]["belasting"])

    def test_jaar_na_2026_geschat_met_parameters_2026(self):
        uit = bereken_box3(_basis(_jaar(2026, begin=100000), _jaar(2027, begin=100000)), GEEN_INVOER)
        h2026, h2027 = (j["huidig"] for j in uit["jaren"])
        self.assertFalse(h2026["geschat"])
        self.assertFalse(h2026["definitief"])
        self.assertTrue(h2027["geschat"])
        self.assertEqual(h2027["belasting_forfaitair"], h2026["belasting_forfaitair"])


class TestSpaarrente(unittest.TestCase):
    # 20.000 spaargeld x 2% = 400 rente per jaar.
    INVOER = {"banktegoeden": 20000, "spaarrente_pct": 2}

    def test_telt_mee_in_aanwas_en_vermogenswinst(self):
        basis = _basis(_jaar(2024, begin=100000, eind=108000, gerealiseerd=2000.0))
        j = bereken_box3(basis, _invoer(**self.INVOER))["jaren"][0]
        # A: 8.000 + 400 = 8.400, min 1.800 = 6.600 x 36% = 2.376. B: 2.000 + 400 = 2.400, min 1.800 = 600 x 36% = 216.
        self.assertEqual((j["aanwas"]["spaarrente"], j["aanwas"]["rendement"], j["aanwas"]["belasting"]),
                         (400.0, 8400.0, 2376.0))
        self.assertEqual((j["vermogenswinst"]["rendement"], j["vermogenswinst"]["belasting"]), (2400.0, 216.0))

    def test_telt_mee_in_tegenbewijs_niet_in_forfaitair(self):
        basis = _basis(_jaar(2024, begin=100000, eind=108000, kosten=20))
        met = bereken_box3(basis, _invoer(**self.INVOER))["jaren"][0]["huidig"]
        zonder = bereken_box3(basis, _invoer(banktegoeden=20000))["jaren"][0]["huidig"]
        self.assertEqual(met["forfaitair_rendement"], zonder["forfaitair_rendement"])
        self.assertEqual(met["belasting_forfaitair"], 1195.99)
        # 8.000 + 20 kosten + 400 rente = 8.420.
        self.assertEqual((met["spaarrente"], met["werkelijk_rendement"]), (400.0, 8420.0))
        self.assertEqual(zonder["werkelijk_rendement"], 8020.0)

    def test_opgeteld_bij_overig_rendement(self):
        basis = _basis(_jaar(2025, begin=10000, eind=10000))
        a = bereken_box3(basis, _invoer(rendement_ander_vermogen=100, **self.INVOER))["jaren"][0]["aanwas"]
        self.assertEqual((a["spaarrente"], a["rendement_ander_vermogen"], a["rendement"]), (400.0, 100.0, 500.0))

    def test_rente_zonder_spaargeld_is_nul(self):
        a = bereken_box3(_basis(_jaar(2025)), _invoer(spaarrente_pct=3))["jaren"][0]["aanwas"]
        self.assertEqual(a["spaarrente"], 0.0)


class TestTotalen(unittest.TestCase):
    def test_totaal_en_lopend_jaar(self):
        basis = _basis(_jaar(2025, begin=10000, eind=15000), _jaar(2026, begin=15000, eind=20000, lopend=True))
        uit = bereken_box3(basis, GEEN_INVOER)
        self.assertEqual(uit["totaal"]["aanwas"], 2304.0)  # 2 x 1152
        self.assertEqual(uit["lopend_jaar"]["aanwas"], 1152.0)
        self.assertEqual(uit["lopend_jaar"]["vermogenswinst"], 0.0)
        self.assertIn("parameters_stand", uit)
        json.dumps(uit, allow_nan=False)


class TestInvoerValidatie(unittest.TestCase):
    def test_leeg_geeft_nullen(self):
        self.assertEqual(valideer_box3_invoer(None), ({
            "banktegoeden": 0.0, "overige_bezittingen": 0.0, "schulden": 0.0, "rendement_ander_vermogen": 0.0,
            "spaarrente_pct": 0.0, "fiscale_partner": False,
        }, None))

    def test_ongeldige_invoer(self):
        for invoer in ({"banktegoeden": -1}, {"schulden": "veel"}, {"overige_bezittingen": True},
                       {"fiscale_partner": "ja"}, {"banktegoeden": float("inf")}, {"spaarrente_pct": -0.5},
                       {"spaarrente_pct": 20.5}, "geen dict"):
            schoon, fout = valideer_box3_invoer(invoer)
            self.assertIsNone(schoon, invoer)
            self.assertTrue(fout, invoer)


if __name__ == "__main__":
    unittest.main()
