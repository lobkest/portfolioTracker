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
CATEGORIE_DATA = "Data"
CATEGORIE_PLAUSIBILITEIT = "Plausibiliteit"
CATEGORIE_TICKERS = "Tickers"
CATEGORIE_REKENINGOVERZICHT = "Rekeningoverzicht"

DIAGNOSTIEK_SLEUTEL = "diagnostiek"
DIAGNOSTIEK_CONTROLES_SLEUTEL = "diagnostiek_controles"

CONTROLE_GEDRAAID = "gedraaid"
CONTROLE_OVERGESLAGEN = "overgeslagen"
CONTROLE_FOUT = "fout"

# Na een upload bewaard per portfolio: deze meldingen zijn er na verversen anders niet meer (de Excel is weg).
BEWAARDE_UPLOAD_CATEGORIEEN = (
    CATEGORIE_OPSLAAN, CATEGORIE_ORDER_IDS, CATEGORIE_WISSELKOERSEN, CATEGORIE_TICKERS, CATEGORIE_DIVIDEND,
    CATEGORIE_REKENINGOVERZICHT,
)
MAX_BEWAARDE_UPLOADS = 5
KLASSE_ALLEEN_BIJ_UPLOAD = "alleen_bij_upload"
KLASSE_OOK_LIVE = "ook_live"
# Alleen bij upload: ontstaat alleen tijdens een upload (Excel, zoeken of de vergelijking van beide bestanden nodig).
# Ook live: gewoon laden berekent het opnieuw; dat telt niet mee, anders blijft een opgeloste waarschuwing staan tot de
# volgende upload. Per categorie wint het langste voorvoegsel; "" is de rest van de categorie. Een sleutel die in
# geen enkel voorvoegsel past telt als ook live (en logt een waarschuwing).
SLEUTEL_KLASSEN = {
    CATEGORIE_OPSLAAN: {"": KLASSE_ALLEEN_BIJ_UPLOAD},
    CATEGORIE_ORDER_IDS: {"": KLASSE_ALLEEN_BIJ_UPLOAD},
    CATEGORIE_REKENINGOVERZICHT: {"": KLASSE_ALLEEN_BIJ_UPLOAD},
    CATEGORIE_WISSELKOERSEN: {
        "": KLASSE_OOK_LIVE,  # wisselkoerspaar of ticker uit prijzen.py, en prijscheck_geen_fx:<ticker>
        "excel_wisselkoers": KLASSE_ALLEEN_BIJ_UPLOAD,
    },
    CATEGORIE_TICKERS: {
        "zoekstappen:": KLASSE_ALLEEN_BIJ_UPLOAD,
        "tickers:valuta:": KLASSE_ALLEEN_BIJ_UPLOAD,
        "check_mislukt:Valuta-consistentie": KLASSE_ALLEEN_BIJ_UPLOAD,
        "tickers:otc:": KLASSE_OOK_LIVE,
        "tickers:andere_beurs:": KLASSE_OOK_LIVE,
        "tickers:override:": KLASSE_OOK_LIVE,
        "tickers:dis_acc:": KLASSE_OOK_LIVE,
        "tickers:openfigi_root:": KLASSE_OOK_LIVE,
        "tickers:openfigi_leeg:": KLASSE_OOK_LIVE,
        "tickers:ticker_info:": KLASSE_OOK_LIVE,
        "tickers:samenvatting": KLASSE_OOK_LIVE,
        "check_mislukt:Tickers": KLASSE_OOK_LIVE,
    },
    CATEGORIE_DIVIDEND: {
        "dividend_samenvatting": KLASSE_ALLEEN_BIJ_UPLOAD,
        "dividend_zonder_conversie_overig": KLASSE_ALLEEN_BIJ_UPLOAD,
        "dividend:": KLASSE_ALLEEN_BIJ_UPLOAD,
        "dividend:zonder_positie:": KLASSE_OOK_LIVE,
        "div_verwachting:": KLASSE_OOK_LIVE,
        "check_mislukt:Dividend zonder positie": KLASSE_OOK_LIVE,
        "check_mislukt:Verwacht dividend": KLASSE_OOK_LIVE,
    },
}

# Link onder een melding naar het tabblad waar je het oplost; `tab` is een view-naam uit menu.js.
ACTIE_TICKER_ZEKERHEID = {"label": "Ticker-zekerheid", "tab": "instellingen-ticker"}
ACTIE_BESTANDEN_BIJWERKEN = {"label": "Bestanden bijwerken", "tab": "instellingen-bestanden"}
ACTIE_KOERSEN = {"label": "Koersen", "tab": "koersoverzicht"}

# Standaardactie als de aanroeper er geen meegeeft; alleen bij FOUT en LET_OP.
ACTIE_PER_CATEGORIE = {
    CATEGORIE_ORDER_IDS: ACTIE_BESTANDEN_BIJWERKEN,
    CATEGORIE_REKENINGOVERZICHT: ACTIE_BESTANDEN_BIJWERKEN,
    CATEGORIE_KOERSEN: ACTIE_KOERSEN,
}
NIVEAUS_MET_ACTIE = (FOUT, LET_OP)
# De Yahoo-calls (technisch) horen bij Koersen maar zijn niet op te lossen op het tabblad Koersen.
SLEUTEL_ZONDER_STANDAARDACTIE = "yahoo_"

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
    "dividend_verwachting": "Verwacht dividend",
    "historisch_rendement": "Historisch rendement",
}
# Bewust ruim onder de frontend-timeouts (55/60 s, fetchMetTimeout/UPLOAD_TIMEOUT_MS); gunicorn zelf stopt pas na 120 s.
DREMPEL_LAADTIJD_LET_OP_SECONDEN = 10

NAAM_CONTROLE_LAADTIJDEN = "Laadtijden"

_G_ATTR = "_diagnostiek_meldingen"
_G_CONTROLES_ATTR = "_diagnostiek_controles"
_ONBEKENDE_SLEUTELS_GEMELD = set()


def _verzameling():
    if not has_app_context():
        return None
    lijst = getattr(g, _G_ATTR, None)
    if lijst is None:
        lijst = []
        setattr(g, _G_ATTR, lijst)
    return lijst


def meld(categorie, niveau, tekst, sleutel=None, tabel=None, actie=None, rijen=None):
    """Zelfde (categorie, sleutel) vervangt de eerdere melding op dezelfde plek. Gooit nooit een exception.
    tabel: optioneel {"kolommen": [...], "rijen": [[...], ...]}, getoond onder de tekst.
    rijen: optioneel zo'n tabel plus "meer" (aantal niet getoonde rijen): de betrokken transacties, inklapbaar.
    actie: optioneel {"label", "tab"}, een link naar dat tabblad (bv. ACTIE_TICKER_ZEKERHEID)."""
    try:
        verzameling = _verzameling()
        if verzameling is None:
            return
        if niveau not in NIVEAUS:
            niveau = INFO
        if sleutel is None:
            sleutel = tekst
        if actie is None and niveau in NIVEAUS_MET_ACTIE and not str(sleutel).startswith(SLEUTEL_ZONDER_STANDAARDACTIE):
            actie = ACTIE_PER_CATEGORIE.get(categorie)
        nieuw = {"categorie": categorie, "niveau": niveau, "tekst": tekst, "sleutel": sleutel}
        if tabel is not None:
            nieuw["tabel"] = tabel
        if rijen is not None:
            nieuw["rijen"] = rijen
        if actie is not None:
            nieuw["actie"] = dict(actie)
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


def sleutel_klasse(categorie, sleutel):
    """KLASSE_... van het langste passende voorvoegsel, of None als de sleutel nergens in SLEUTEL_KLASSEN past."""
    passend = [v for v in SLEUTEL_KLASSEN.get(categorie, {}) if str(sleutel).startswith(v)]
    return SLEUTEL_KLASSEN[categorie][max(passend, key=len)] if passend else None


def alleen_bij_upload(melding):
    categorie, sleutel = melding.get("categorie"), str(melding.get("sleutel") or "")
    klasse = sleutel_klasse(categorie, sleutel)
    if klasse is None:
        _waarschuw_onbekende_sleutel(categorie, sleutel)
    return klasse == KLASSE_ALLEEN_BIJ_UPLOAD


def _waarschuw_onbekende_sleutel(categorie, sleutel):
    """Eén keer per categorie + sleutelsoort; alleen het deel voor de eerste ':' (de rest kan een ISIN of ticker zijn)."""
    if categorie not in BEWAARDE_UPLOAD_CATEGORIEEN:
        return
    soort = sleutel.split(":")[0]
    if (categorie, soort) in _ONBEKENDE_SLEUTELS_GEMELD:
        return
    _ONBEKENDE_SLEUTELS_GEMELD.add((categorie, soort))
    print(f"[diagnostiek] WARN sleutel '{soort}' ({categorie}) staat niet in SLEUTEL_KLASSEN; telt als ook_live")


def upload_meldingen_om_te_bewaren(meldingen):
    """Kopieën met 'ook_live' erbij (zie ALLEEN_BIJ_UPLOAD_CATEGORIEEN)."""
    return [{**m, "ook_live": not alleen_bij_upload(m)}
            for m in meldingen or [] if m.get("categorie") in BEWAARDE_UPLOAD_CATEGORIEEN]


def meld_opnieuw(meldingen):
    for m in meldingen or []:
        meld(m.get("categorie"), m.get("niveau"), m.get("tekst"), m.get("sleutel"), tabel=m.get("tabel"),
             actie=m.get("actie"), rijen=m.get("rijen"))


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
        registreer_controle(CATEGORIE_LAADTIJDEN, NAAM_CONTROLE_LAADTIJDEN, CONTROLE_GEDRAAID,
                            bevindingen=1 if niveau == LET_OP else 0, technisch=True, ophogen=True)
    except Exception as e:
        print(f"[diagnostiek] WARN laadtijd niet gemeld ({e!a})")


def voeg_diagnostiek_toe(resultaat):
    """Iets anders dan een dict (bv. None) gaat ongewijzigd terug."""
    if isinstance(resultaat, dict):
        resultaat[DIAGNOSTIEK_SLEUTEL] = haal_meldingen()
        resultaat[DIAGNOSTIEK_CONTROLES_SLEUTEL] = haal_controles()
    return resultaat


def _controle_verzameling():
    if not has_app_context():
        return None
    verzameling = getattr(g, _G_CONTROLES_ATTR, None)
    if verzameling is None:
        verzameling = {}
        setattr(g, _G_CONTROLES_ATTR, verzameling)
    return verzameling


def registreer_controle(categorie, naam, status, bevindingen=0, reden=None, technisch=False, ophogen=False):
    """Legt vast dat een controle gedraaid heeft, ook zonder bevinding. Dezelfde (categorie, naam) vervangt de eerdere,
    of telt er bij `ophogen` bij op. Gooit nooit een exception."""
    try:
        verzameling = _controle_verzameling()
        if verzameling is None:
            return
        sleutel = (categorie, naam)
        if ophogen and sleutel in verzameling:
            bevindingen += verzameling[sleutel]["bevindingen"]
        verzameling[sleutel] = {"categorie": categorie, "naam": naam, "status": status,
                                "bevindingen": bevindingen, "reden": reden, "technisch": technisch}
    except Exception as e:
        print(f"[diagnostiek] WARN controle niet geregistreerd ({e!a})")


def haal_controles():
    try:
        verzameling = _controle_verzameling()
        return [dict(c) for c in verzameling.values()] if verzameling else []
    except Exception:
        return []


def controles_sinds(eerder):
    """Nieuwe of gewijzigde, niet-technische controles t.o.v. een eerdere haal_controles()."""
    return [c for c in haal_controles() if c not in eerder and not c["technisch"]]


def registreer_controles_opnieuw(controles):
    for c in controles or []:
        registreer_controle(c.get("categorie"), c.get("naam"), c.get("status"), c.get("bevindingen", 0),
                            c.get("reden"), c.get("technisch", False))


def sla_controle_over(categorie, naam, reden):
    registreer_controle(categorie, naam, CONTROLE_OVERGESLAGEN, reden=reden)


def meld_check_mislukt(categorie, wat, e):
    meld(categorie, LET_OP, f"{wat} niet gecontroleerd door een fout ({type(e).__name__}: {e!a}).",
         sleutel=f"check_mislukt:{wat}")


def draai_controle(categorie, naam, uitvoeren):
    """Voert `uitvoeren()` uit (dat zelf meldt) en registreert de controle met het aantal nieuwe FOUT/LET_OP-meldingen.
    Een exception wordt een melding en status 'fout': Diagnostiek breekt het laden nooit en andere controles draaien door."""
    meldingen_voor = haal_meldingen()
    try:
        uitvoeren()
    except Exception as e:
        meld_check_mislukt(categorie, naam, e)
        registreer_controle(categorie, naam, CONTROLE_FOUT, reden=type(e).__name__)
        return
    bevindingen = sum(1 for m in meldingen_sinds(meldingen_voor) if m["niveau"] in (FOUT, LET_OP))
    registreer_controle(categorie, naam, CONTROLE_GEDRAAID, bevindingen=bevindingen)
