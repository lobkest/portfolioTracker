"""Logregels bevatten geen portfolio-codes of IP-adressen (zie templates/privacy.html: serverlogs)."""
import ast
import glob
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from debug_utils import maskeer_codes, print_traceback

LOG_FUNCTIES = {"print", "dprint", "meet_tijd"}
VERBODEN_NAMEN = {"code", "nieuwe_code", "oude_code", "ip"}
VERBODEN_ATTRIBUTEN = {"remote_addr", "headers", "access_route"}


def _log_aanroepen(pad):
    with open(pad, encoding="utf-8") as f:
        boom = ast.parse(f.read())
    for node in ast.walk(boom):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in LOG_FUNCTIES:
            yield node


class TestMaskeerCodes(unittest.TestCase):
    def test_code_in_sleutel_van_dubbele_waarde(self):
        tekst = "DETAIL:  Key (code, order_id)=(ABC, 123) already exists."
        self.assertEqual(maskeer_codes(tekst), "DETAIL:  Key (code, order_id)=(***, 123) already exists.")

    def test_code_in_enkele_sleutel(self):
        self.assertEqual(maskeer_codes("Key (code)=(XYZ) is not present"), "Key (code)=(***) is not present")

    def test_overige_tekst_blijft_staan(self):
        self.assertEqual(maskeer_codes("ValueError: ABC is geen getal"), "ValueError: ABC is geen getal")

    def test_traceback_wordt_gemaskeerd(self):
        uitvoer = io.StringIO()
        try:
            raise RuntimeError("Key (code)=(QXZ) bestaat")
        except RuntimeError:
            with redirect_stdout(uitvoer):
                print_traceback()
        self.assertIn("Key (code)=(***) bestaat", uitvoer.getvalue())
        self.assertNotIn("QXZ", uitvoer.getvalue())


class TestGeenCodesOfIpInLogregels(unittest.TestCase):
    def test_geen_code_of_ip_in_print_dprint_of_meet_tijd(self):
        gevonden = []
        for pad in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
            for aanroep in _log_aanroepen(pad):
                for deel in ast.walk(aanroep):
                    if isinstance(deel, ast.Name) and deel.id in VERBODEN_NAMEN:
                        gevonden.append(f"{os.path.basename(pad)}:{aanroep.lineno} gebruikt '{deel.id}'")
                    if isinstance(deel, ast.Attribute) and deel.attr in VERBODEN_ATTRIBUTEN:
                        gevonden.append(f"{os.path.basename(pad)}:{aanroep.lineno} gebruikt '.{deel.attr}'")
                    if isinstance(deel, ast.Constant) and isinstance(deel.value, str) and "[ip]" in deel.value:
                        gevonden.append(f"{os.path.basename(pad)}:{aanroep.lineno} logt [ip]")
        self.assertEqual(gevonden, [])

    def test_waakhond_ziet_een_overtreding(self):
        boom = ast.parse('print(f"portfolio {code}")')
        namen = {n.id for n in ast.walk(boom) if isinstance(n, ast.Name)}
        self.assertTrue(namen & VERBODEN_NAMEN)

    def test_de_tijdelijke_ip_controle_is_weg(self):
        import app
        self.assertFalse(hasattr(app, "_log_ip_ter_controle"))


if __name__ == "__main__":
    unittest.main()
