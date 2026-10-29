import io
import os
import sys
import unittest
import zipfile
from unittest.mock import patch

import pandas as pd
from werkzeug.datastructures import FileStorage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend import bestand_controle
from backend.bestand_controle import controleer_excel_bestand, controleer_aantal_rijen
from backend.dividend import lees_rekeningoverzicht
from backend.transactie_utils import OngeldigExcelBestand
from backend.upload_verwerking import lees_transacties_excel

TEST_FILES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_files")


def maak_zip(onderdelen):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for naam, inhoud in onderdelen.items():
            z.writestr(naam, inhoud)
    buffer.seek(0)
    return buffer


GELDIG = {"[Content_Types].xml": "<x/>", "xl/workbook.xml": "<x/>"}


def bestand(buffer, naam="export.xlsx"):
    return FileStorage(stream=buffer, filename=naam)


def testbestand(naam):
    with open(os.path.join(TEST_FILES, naam), "rb") as f:
        return io.BytesIO(f.read())


class TestControleerExcelBestand(unittest.TestCase):
    def test_echt_xlsx_is_geldig_en_positie_staat_weer_op_begin(self):
        b = bestand(testbestand("Transactions_test.xlsx"))
        b.stream.seek(10)
        controleer_excel_bestand(b)
        self.assertEqual(b.stream.tell(), 0)

    def test_positie_staat_ook_na_een_afkeuring_op_begin(self):
        buffer = maak_zip({"iets.txt": "x"})
        with self.assertRaises(OngeldigExcelBestand):
            controleer_excel_bestand(bestand(buffer))
        self.assertEqual(buffer.tell(), 0)

    def test_zip_met_andere_inhoud_wordt_geweigerd(self):
        with self.assertRaisesRegex(OngeldigExcelBestand, "Excel-bestand"):
            controleer_excel_bestand(bestand(maak_zip({"foto.png": "x"}), "export.zip"))

    def test_gewone_zip_met_xlsx_naam_wordt_op_inhoud_geweigerd(self):
        with self.assertRaisesRegex(OngeldigExcelBestand, "geen .xlsx"):
            controleer_excel_bestand(bestand(maak_zip({"a.txt": "x"}), "export.xlsx"))

    def test_verkeerde_extensie_wordt_geweigerd(self):
        for naam in ("export.zip", "export.csv", "export.xlsm", "export"):
            with self.subTest(naam=naam), self.assertRaisesRegex(OngeldigExcelBestand, "Excel-bestand"):
                controleer_excel_bestand(bestand(maak_zip(GELDIG), naam))

    def test_extensie_hoofdletters_zijn_toegestaan(self):
        controleer_excel_bestand(bestand(maak_zip(GELDIG), "EXPORT.XLSX"))

    def test_oud_xls_formaat_wordt_geweigerd_ook_met_xls_naam(self):
        ole = io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
        with self.assertRaisesRegex(OngeldigExcelBestand, "geen .xlsx"):
            controleer_excel_bestand(bestand(ole, "Transactions.xls"))

    def test_kapotte_zip_wordt_geweigerd(self):
        kapot = io.BytesIO(maak_zip(GELDIG).getvalue()[:-30] + b"x" * 30)
        with self.assertRaises(OngeldigExcelBestand):
            controleer_excel_bestand(bestand(kapot))

    def test_macro_wordt_geweigerd(self):
        buffer = maak_zip({**GELDIG, "xl/vbaProject.bin": "macro"})
        with self.assertRaisesRegex(OngeldigExcelBestand, "macro"):
            controleer_excel_bestand(bestand(buffer))

    def test_te_veel_bestanden_in_de_zip(self):
        onderdelen = {**GELDIG, **{f"xl/extra{i}.xml": "x" for i in range(5)}}
        with patch.object(bestand_controle, "MAX_ZIP_BESTANDEN", 4), \
                self.assertRaisesRegex(OngeldigExcelBestand, "ongewoon"):
            controleer_excel_bestand(bestand(maak_zip(onderdelen)))

    def test_precies_het_maximum_aantal_bestanden_mag(self):
        onderdelen = {**GELDIG, "xl/a.xml": "x", "xl/b.xml": "x"}
        with patch.object(bestand_controle, "MAX_ZIP_BESTANDEN", 4):
            controleer_excel_bestand(bestand(maak_zip(onderdelen)))

    def test_te_grote_uitgepakte_grootte(self):
        buffer = maak_zip({**GELDIG, "xl/groot.xml": "a" * 2000})
        with patch.object(bestand_controle, "MAX_UITGEPAKTE_BYTES", 1000), \
                self.assertRaisesRegex(OngeldigExcelBestand, "te groot"):
            controleer_excel_bestand(bestand(buffer))

    def test_bestand_zonder_naam_wordt_alleen_op_inhoud_gecontroleerd(self):
        controleer_excel_bestand(maak_zip(GELDIG))


class TestAantalRijen(unittest.TestCase):
    def test_boven_het_maximum_wordt_geweigerd(self):
        with patch.object(bestand_controle, "MAX_RIJEN", 2), \
                self.assertRaisesRegex(OngeldigExcelBestand, "te veel rijen"):
            controleer_aantal_rijen(pd.DataFrame({"a": [1, 2, 3]}))

    def test_op_het_maximum_mag(self):
        with patch.object(bestand_controle, "MAX_RIJEN", 2):
            controleer_aantal_rijen(pd.DataFrame({"a": [1, 2]}))


class TestLezersGebruikenDeControle(unittest.TestCase):
    def test_transactiebestand_met_zipinhoud_zonder_excel_wordt_geweigerd(self):
        with self.assertRaises(OngeldigExcelBestand):
            lees_transacties_excel(bestand(maak_zip({"a.txt": "x"})))

    def test_rekeningoverzicht_met_macro_wordt_geweigerd(self):
        with self.assertRaisesRegex(OngeldigExcelBestand, "macro"):
            lees_rekeningoverzicht(bestand(maak_zip({**GELDIG, "xl/vbaProject.bin": "x"})))

    def test_echte_testbestanden_worden_nog_gelezen(self):
        self.assertGreater(len(lees_transacties_excel(bestand(testbestand("Transactions_test.xlsx")))), 0)
        self.assertGreater(len(lees_rekeningoverzicht(bestand(testbestand("Account_test.xlsx")))), 0)

    def test_te_veel_rijen_in_transactiebestand(self):
        with patch.object(bestand_controle, "MAX_RIJEN", 3), self.assertRaisesRegex(OngeldigExcelBestand, "te veel"):
            lees_transacties_excel(bestand(testbestand("Transactions_test.xlsx")))


if __name__ == "__main__":
    unittest.main()
