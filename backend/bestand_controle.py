"""Controle van een geüpload Excel-bestand vóór pandas het opent."""
import os
import zipfile

from backend.transactie_utils import OngeldigExcelBestand

# Zie CLAUDE.md: Openbaar online. De inhoudscheck is leidend; ".xls" staat erin omdat DeGiro zijn export zo kan noemen.
TOEGESTANE_EXTENSIES = frozenset({".xlsx", ".xls"})
VEREISTE_ZIP_ONDERDELEN = ("[Content_Types].xml", "xl/workbook.xml")
MACRO_ONDERDEEL = "xl/vbaProject.bin"
MAX_ZIP_BESTANDEN = 200
MAX_UITGEPAKTE_BYTES = 50 * 1024 * 1024
MAX_RIJEN = 50_000

MELDING_EXTENSIE = "Upload een Excel-bestand (.xlsx), zoals DeGiro het exporteert."
MELDING_GEEN_XLSX = ("Dit is geen .xlsx-bestand (een ouder .xls-formaat of een ander bestandstype). "
                     "Exporteer het bij DeGiro als Excel.")
MELDING_MACRO = "Excel-bestanden met macro's worden niet geaccepteerd."
MELDING_TE_VEEL_ONDERDELEN = "Dit Excel-bestand is ongewoon opgebouwd en wordt niet geaccepteerd."
MELDING_TE_GROOT_UITGEPAKT = "Dit Excel-bestand is te groot om te verwerken."
MELDING_TE_VEEL_RIJEN = f"Dit bestand heeft te veel rijen (maximaal {MAX_RIJEN})."


def controleer_excel_bestand(bestand):
    """Raises OngeldigExcelBestand; zet de bestandspositie altijd terug op het begin."""
    naam = getattr(bestand, "filename", None)
    if naam and os.path.splitext(naam)[1].lower() not in TOEGESTANE_EXTENSIES:
        raise OngeldigExcelBestand(MELDING_EXTENSIE)
    bestand.seek(0)
    try:
        _controleer_zip_inhoud(bestand)
    finally:
        bestand.seek(0)


def _controleer_zip_inhoud(bestand):
    if not zipfile.is_zipfile(bestand):
        raise OngeldigExcelBestand(MELDING_GEEN_XLSX)
    bestand.seek(0)
    try:
        with zipfile.ZipFile(bestand) as zip_bestand:
            onderdelen = zip_bestand.infolist()
    except zipfile.BadZipFile:
        raise OngeldigExcelBestand(MELDING_GEEN_XLSX)
    namen = {onderdeel.filename for onderdeel in onderdelen}
    if not all(vereist in namen for vereist in VEREISTE_ZIP_ONDERDELEN):
        raise OngeldigExcelBestand(MELDING_GEEN_XLSX)
    if MACRO_ONDERDEEL in namen:
        raise OngeldigExcelBestand(MELDING_MACRO)
    if len(onderdelen) > MAX_ZIP_BESTANDEN:
        raise OngeldigExcelBestand(MELDING_TE_VEEL_ONDERDELEN)
    if sum(onderdeel.file_size for onderdeel in onderdelen) > MAX_UITGEPAKTE_BYTES:
        raise OngeldigExcelBestand(MELDING_TE_GROOT_UITGEPAKT)


def controleer_aantal_rijen(df):
    if len(df) > MAX_RIJEN:
        raise OngeldigExcelBestand(MELDING_TE_VEEL_RIJEN)
