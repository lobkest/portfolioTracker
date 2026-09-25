"""Diagnostiek-meldingen per laadbeurt (request) -- voor het subtabblad
Instellingen > Diagnostiek. Een melding vertelt wat er tijdens het laden
goed ging, minder verwacht was of misging, en gaat mee in het JSON-antwoord
(sleutel "diagnostiek"). Niets wordt in de database bewaard.

Zonder afhankelijkheden op andere projectmodules (net als debug_utils.py).

Meldingen worden verzameld op Flask's `g` en bestaan dus alleen binnen één
request. Buiten een app-context -- unittests, losse scripts, maar ook de
worker-threads van een ThreadPoolExecutor (ticker-resolutie/prijscheck) --
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

# Sleutel in het JSON-antwoord.
DIAGNOSTIEK_SLEUTEL = "diagnostiek"

_G_ATTR = "_diagnostiek_meldingen"


def _verzameling():
    """De meldingenlijst van de huidige request, of None buiten een context."""
    if not has_app_context():
        return None
    lijst = getattr(g, _G_ATTR, None)
    if lijst is None:
        lijst = []
        setattr(g, _G_ATTR, lijst)
    return lijst


def meld(categorie, niveau, tekst, sleutel=None):
    """Voegt een melding toe aan de huidige request. `sleutel` dient om te
    ontdubbelen (default: de tekst zelf): een tweede melding met dezelfde
    (categorie, sleutel) vervangt de eerste, op dezelfde plek. Een ongeldig
    niveau valt terug op INFO. Gooit nooit een exception."""
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
    """Kopie van de meldingen van de huidige request, in volgorde van
    binnenkomst. Lege lijst buiten een context."""
    try:
        verzameling = _verzameling()
        return [dict(m) for m in verzameling] if verzameling else []
    except Exception:
        return []


def meldingen_sinds(eerder):
    """Meldingen die nieuw of gewijzigd zijn t.o.v. een eerdere
    haal_meldingen()-snapshot -- t.b.v. het bewaren van meldingen in een
    cache-entry (zie _haal_portfolio_basis())."""
    return [m for m in haal_meldingen() if m not in eerder]


def meld_opnieuw(meldingen):
    """Geeft eerder bewaarde meldingen opnieuw door aan meld() (bv. bij een
    cache-hit). Ontdubbelen gebeurt vanzelf op (categorie, sleutel)."""
    for m in meldingen or []:
        meld(m.get("categorie"), m.get("niveau"), m.get("tekst"), m.get("sleutel"))


def voeg_diagnostiek_toe(resultaat):
    """Zet de meldingen van deze request onder DIAGNOSTIEK_SLEUTEL in een
    response-dict en geeft die dict terug (one-liner in de routes). Iets
    anders dan een dict (bv. None) gaat ongewijzigd terug."""
    if isinstance(resultaat, dict):
        resultaat[DIAGNOSTIEK_SLEUTEL] = haal_meldingen()
    return resultaat
