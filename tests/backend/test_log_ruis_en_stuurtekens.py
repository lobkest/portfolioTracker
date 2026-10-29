"""Detail-timing staat standaard uit; totalen blijven; geen stuurtekens in de eigen CSS en JS."""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend import debug_utils
from backend.debug_utils import meet_tijd, tprint, TIMING_DETAIL_VARIABELE, TIMING_TOTAAL_LABELS

TOEGESTANE_STUURTEKENS = {9, 10, 13}


def uitvoer_van(label):
    uit = io.StringIO()
    with redirect_stdout(uit):
        with meet_tijd(label):
            pass
    return uit.getvalue()


class TestTimingDetail(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(TIMING_DETAIL_VARIABELE, None)

    def test_detailstap_wordt_zonder_schakelaar_niet_gelogd(self):
        self.assertEqual(uitvoer_van("basis_split_correctie"), "")
        self.assertEqual(uitvoer_van("koersen_download_nieuw (3 ticker(s))"), "")

    def test_totalen_worden_altijd_gelogd(self):
        for label in TIMING_TOTAAL_LABELS:
            self.assertIn(f"[timing] {label}:", uitvoer_van(label), label)

    def test_totaal_met_toelichting_telt_ook_als_totaal(self):
        self.assertIn("[timing] ophalen_totaal (iets):", uitvoer_van("ophalen_totaal (iets)"))

    def test_detail_aan_logt_alles(self):
        os.environ[TIMING_DETAIL_VARIABELE] = "1"
        self.assertIn("[timing] basis_split_correctie:", uitvoer_van("basis_split_correctie"))

    def test_andere_waarde_dan_1_zet_detail_niet_aan(self):
        os.environ[TIMING_DETAIL_VARIABELE] = "0"
        self.assertEqual(uitvoer_van("basis_split_correctie"), "")

    def test_tprint_volgt_dezelfde_schakelaar(self):
        uit = io.StringIO()
        with redirect_stdout(uit):
            tprint("[timing] detail")
            os.environ[TIMING_DETAIL_VARIABELE] = "1"
            tprint("[timing] wel")
        self.assertEqual(uit.getvalue(), "[timing] wel\n")

    def test_diagnostiek_krijgt_de_laadtijd_ook_zonder_detail(self):
        with patch.object(debug_utils, "meld_laadtijd") as meld:
            uitvoer_van("basis_split_correctie")
        meld.assert_called_once()


class TestGeenStuurtekens(unittest.TestCase):
    def test_geen_stuurtekens_in_eigen_css_en_js(self):
        gevonden = []
        for submap in ("css", "js"):
            for map_pad, _mappen, namen in os.walk(os.path.join(ROOT, "static", submap)):
                for naam in namen:
                    pad = os.path.join(map_pad, naam)
                    with open(pad, "rb") as f:
                        inhoud = f.read()
                    for plek, byte in enumerate(inhoud):
                        if byte < 32 and byte not in TOEGESTANE_STUURTEKENS:
                            regel = inhoud[:plek].count(b"\n") + 1
                            gevonden.append(f"{os.path.relpath(pad, ROOT)}:{regel} byte {byte:#04x}")
        self.assertEqual(gevonden, [])

    def test_pijltje_bij_diagnostiek_is_het_juiste_teken(self):
        with open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8") as f:
            tekst = f.read()
        blok = tekst[tekst.index(".diagnostiekAantal::after"):]
        self.assertIn('content: "\\25B8";', blok.split("}")[0])


if __name__ == "__main__":
    unittest.main()
