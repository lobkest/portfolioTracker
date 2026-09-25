"""Diagnostiek-meldingen per request (Instellingen > Diagnostiek), verzameld op Flask's `g`.

Importeert geen projectmodules. Buiten een app-context, ook in worker-threads,
is meld() bewust een stille no-op.
"""
from flask import g, has_app_context

# Niveaus, van ernstig naar gunstig.
FOUT = "FOUT"
LET_OP = "LET_OP"
INFO = "INFO"
GOED = "GOED"
NIVEAUS = (FOUT, LET_OP, INFO, GOED)

# Categorieën. Nieuwe categorie: hier een constante toevoegen en meld() aanroepen.
CATEGORIE_WISSELKOERSEN = "Wisselkoersen"
CATEGORIE_ORDER_IDS = "Order ID's"
CATEGORIE_OPSLAAN = "Opslaan"
CATEGORIE_DIVIDEND = "Dividend"
CATEGORIE_KOERSEN = "Koersen"
CATEGORIE_SPLITS = "Splits"
CATEGORIE_ETF_HOLDINGS = "ETF-holdings"
CATEGORIE_LAADTIJDEN = "Laadtijden"

DIAGNOSTIEK_SLEUTEL = "diagnostiek"

# Alleen deze meet_tijd()-labels (tot aan " (") worden als laadtijd gemeld.
LAADTIJD_FASEN = {
    "ticker_resolutie": "Tickers koppelen",
    "ticker_resolutie_niet_opslaan": "Tickers koppelen",
    "db_backfill_verouderde_tickers": "Tickers bijwerken",
    "db_backfill_verouderde_tickers_ophalen": "Tickers opnieuw bepalen",
    "dividend_bestand_verwerken": "Rekeningoverzicht verwerken",
    "basis_ophalen_db": "Transacties ophalen uit de database",
    "basis_koersen_ophalen": "Koersen ophalen",
    "koersen_ophalen_kern": "Koersen ophalen",
    "verrijking_totaal": "Verdeling, land/sector, bedrijven en ETF-overlap",
}
# Aanname: een derde van gunicorns standaard-timeout (30 s); die op Render staat niet in de repo.
DREMPEL_LAADTIJD_LET_OP_SECONDEN = 10

_G_ATTR = "_diagnostiek_meldingen"


def _verzameling():
    if not has_app_context():
        return None
    lijst = getattr(g, _G_ATTR, None)
    if lijst is None:
        lijst = []
        setattr(g, _G_ATTR, lijst)
    return lijst


def meld(categorie, niveau, tekst, sleutel=None):
    """Zelfde (categorie, sleutel) vervangt de eerdere melding op dezelfde plek. Gooit nooit een exception."""
    try:
        verzameling = _verzameling()
        if verzameling is None:
            return
        if niveau not in NIVEAUS:
            niveau = INFO
        if sleutel is None:
            sleutel = tekst
        nieuw = {"categorie": categorie, "niveau": niveau, "tekst": tekst, "sleutel": sleutel}
        for i, bestaand in enumerate(verzameling):
            if bestaand["categorie"] == categorie and bestaand["sleutel"] == sleutel:
                verzameling[i] = nieuw
                return
        verzameling.append(nieuw)
    except Exception as e:
        print(f"[diagnostiek] WARN melding niet opgeslagen ({e!a})")


def haal_meldingen():
    try:
        verzameling = _verzameling()
        return [dict(m) for m in verzameling] if verzameling else []
    except Exception:
        return []


def meldingen_sinds(eerder):
    """Nieuwe of gewijzigde meldingen t.o.v. een eerdere haal_meldingen()."""
    return [m for m in haal_meldingen() if m not in eerder]


def meld_opnieuw(meldingen):
    for m in meldingen or []:
        meld(m.get("categorie"), m.get("niveau"), m.get("tekst"), m.get("sleutel"))


def meld_laadtijd(label, duur_seconden):
    """Gooit nooit een exception."""
    try:
        fase = str(label).split(" (")[0]
        naam = LAADTIJD_FASEN.get(fase)
        if naam is None:
            return
        niveau = LET_OP if duur_seconden > DREMPEL_LAADTIJD_LET_OP_SECONDEN else INFO
        duur_tekst = f"{duur_seconden:.1f}".replace(".", ",")
        meld(CATEGORIE_LAADTIJDEN, niveau, f"{naam}: {duur_tekst} s.", sleutel=fase)
    except Exception as e:
        print(f"[diagnostiek] WARN laadtijd niet gemeld ({e!a})")


def voeg_diagnostiek_toe(resultaat):
    """Iets anders dan een dict (bv. None) gaat ongewijzigd terug."""
    if isinstance(resultaat, dict):
        resultaat[DIAGNOSTIEK_SLEUTEL] = haal_meldingen()
    return resultaat
