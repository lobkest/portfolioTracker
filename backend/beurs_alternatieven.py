"""Fase A van "Alternatieven op een thuisbeurs": per open positie op een beurs met aansluitingskosten kijken of exact
hetzelfde product (zelfde ISIN) ook op een thuisbeurs noteert. Pure kern (`bouw_beurs_alternatieven()`) plus een dunne
laag die Yahoo aanroept (`haal_alternatieven()`). Geen advies en geen DEGIRO-tarieven: alle bedragen komen uit de
eigen kostenregels en transacties (zie `alternatieven_invoer` in aansluitingskosten.py)."""
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.aansluitingskosten import THUISBEURZEN
from backend.db import db_get_ticker_details
from backend.debug_utils import dprint
from backend.effect_profiel import haal_koers
from backend.effect_zoeken import (
    TOEGESTANE_TYPES, herken_isin, noteringen_voor_isin_zoekopdracht, normaliseer_fondsnaam, zoek_gecachet)
from backend.wat_als import MAX_NAAM_TEKENS, MAX_POSITIES_WAT_ALS, MAX_TICKER_TEKENS
from backend.yahoo_client import yahoo_teller_stand

KOERS_AFWIJKING_DREMPEL = 0.05
MIN_GEDEELDE_NAAMWOORDEN = 2
FONDS_TYPES = frozenset({"ETF", "MUTUALFUND"})
MAX_PARALLEL = 4
ONDERZOEK_CACHE_SECONDEN = 3600
ONDERZOEK_CACHE_MAX_ITEMS = 300
BEURSCODE_PATROON = re.compile(r"^[A-Za-z0-9]{1,10}$")

STATUS_GEVONDEN = "gevonden"
STATUS_WAARSCHUWING = "waarschuwing"
STATUS_NIET_GEVONDEN = "niet_gevonden"
STATUS_FOUT = "fout"
WAARSCHUWING_KOERS_WIJKT_AF = "koers wijkt af"
WAARSCHUWING_GEEN_KOERS = "geen koers om mee te vergelijken"
WAARSCHUWING_NIET_OP_ISIN = "niet_op_isin_bevestigd"
AFKEURENDE_WAARSCHUWINGEN = frozenset({WAARSCHUWING_KOERS_WIJKT_AF, WAARSCHUWING_GEEN_KOERS})
FOUT_GEEN_ZOEKRESULTAAT = "Yahoo gaf niets terug voor deze ISIN (storing of niet bekend)."
FOUT_GEEN_ISIN = "Van deze positie is geen ISIN bekend."
FOUT_ALGEMEEN = "Opzoeken bij Yahoo is mislukt; probeer het later opnieuw."

_onderzoek_cache = {}
_onderzoek_cache_lock = threading.Lock()


def is_thuisbeurs_code(code):
    return any(str(code).strip().upper() in thuis["codes"] for thuis in THUISBEURZEN)


def thuisnoteringen(quotes, via_naam=frozenset()):
    """(is_etf, noteringen) uit Yahoo-quotes van één ISIN; alleen noteringen op een thuisbeurs, in volgorde van voorkeur.
    `via_naam`: symbolen die alleen via de zoekopdracht op productnaam zijn gevonden (niet op ISIN bevestigd)."""
    bruikbaar = [q for q in quotes if q.get("quoteType") in TOEGESTANE_TYPES and q.get("symbol")]
    is_etf = bool(bruikbaar) and bruikbaar[0].get("quoteType") == "ETF"
    gevonden, gezien = [], set()
    for thuis in THUISBEURZEN:
        for q in bruikbaar:
            if q.get("exchange") == thuis["yahoo"] and q["symbol"] not in gezien:
                gezien.add(q["symbol"])
                gevonden.append({"ticker": q["symbol"], "beurs": thuis["yahoo"], "beurs_code": thuis["code"],
                                 "beurs_naam": thuis["naam"], "via_naam": q["symbol"] in via_naam})
    return is_etf, gevonden


def _beoordeel_notering(notering, koers_positie):
    koers = notering.get("koers_eur")
    afwijking, waarschuwing = None, None
    if koers is None or not koers_positie or koers_positie <= 0:
        waarschuwing = WAARSCHUWING_GEEN_KOERS
    else:
        afwijking = (koers - koers_positie) / koers_positie
        if abs(afwijking) > KOERS_AFWIJKING_DREMPEL:
            waarschuwing = WAARSCHUWING_KOERS_WIJKT_AF
    if waarschuwing is None and notering.get("via_naam"):
        waarschuwing = WAARSCHUWING_NIET_OP_ISIN
    return {**notering, "afwijking_pct": round(afwijking * 100, 2) if afwijking is not None else None,
            "waarschuwing": waarschuwing}


def beoordeel_positie(positie, onderzoek, isins_op_thuisbeurs, is_etf=None):
    """Eén resultaatregel. `onderzoek`: {"is_etf", "noteringen": [{ticker, beurs, beurs_code, beurs_naam, koers_eur}]}
    of {"fout": tekst}; `is_etf` uit de classificatie gaat voor op die uit de zoekuitslag. De voorkeur is de eerste notering (AMS voor BRU) zonder waarschuwing."""
    resultaat = {
        "ticker": positie["ticker"], "naam": positie["naam"], "echte_naam": positie.get("echte_naam"), "isin": positie.get("isin"),
        "status": STATUS_NIET_GEVONDEN, "voorkeur": None, "noteringen": [], "waarschuwing": None, "fout": None,
        "al_in_bezit_op_thuisbeurs": bool(positie.get("isin")) and positie["isin"] in isins_op_thuisbeurs,
        "fase_b_mogelijk": False,
    }
    if "fout" in onderzoek:
        return {**resultaat, "status": STATUS_FOUT, "fout": onderzoek["fout"]}
    koers_positie = None
    if positie.get("waarde_eur") and positie.get("aantal"):
        koers_positie = positie["waarde_eur"] / positie["aantal"]
    noteringen = [_beoordeel_notering(n, koers_positie) for n in onderzoek["noteringen"]]
    resultaat["noteringen"] = noteringen
    resultaat["fase_b_mogelijk"] = bool(onderzoek.get("is_etf") if is_etf is None else is_etf)
    if not noteringen:
        return resultaat
    # Een notering die op ISIN is gevonden gaat voor op een die alleen via de naam is gevonden.
    acceptabel = sorted((n for n in noteringen if n["waarschuwing"] not in AFKEURENDE_WAARSCHUWINGEN),
                        key=lambda n: bool(n.get("via_naam")))
    voorkeur = acceptabel[0] if acceptabel else None
    if voorkeur:
        return {**resultaat, "status": STATUS_GEVONDEN, "voorkeur": voorkeur}
    return {**resultaat, "status": STATUS_WAARSCHUWING, "waarschuwing": noteringen[0]["waarschuwing"]}


def bouw_beurs_alternatieven(invoer, onderzoek_per_isin, is_etf_per_ticker=None):
    """invoer: `alternatieven_invoer` van één beurs. Per positie een resultaat; per beurs `blijft_betalen`,
    `besparing_per_jaar_eur`, `overstapkosten_schatting_eur` (None = onbekend), `terugverdientijd_jaren`."""
    isins_thuis = set(invoer["isins_op_thuisbeurs"])
    is_etf_per_ticker = is_etf_per_ticker or {}

    def onderzoek_van(positie):
        if not positie.get("isin"):
            return {"fout": FOUT_GEEN_ISIN}
        return onderzoek_per_isin.get(positie["isin"]) or {"fout": FOUT_GEEN_ZOEKRESULTAAT}

    posities = [beoordeel_positie(p, onderzoek_van(p), isins_thuis, is_etf_per_ticker.get(p["ticker"]))
                for p in invoer["posities"]]
    kan_alles = bool(posities) and all(p["status"] == STATUS_GEVONDEN for p in posities)
    jaarkosten = invoer.get("jaarkosten_eur")
    besparing = jaarkosten if kan_alles else 0
    overstapkosten = _overstapkosten(invoer, posities)
    return {
        "beurs": invoer["beurs"],
        "beurs_naam": invoer.get("beurs_naam"),
        "jaarkosten_eur": jaarkosten,
        "betaald_dit_jaar": invoer.get("betaald_dit_jaar", False),
        "thuisbeurzen": [{"code": t["code"], "naam": t["naam"]} for t in THUISBEURZEN],
        "posities": posities,
        "blijft_betalen": not kan_alles,
        "besparing_per_jaar_eur": besparing,
        "overstapkosten_schatting_eur": overstapkosten,
        "terugverdientijd_jaren": _terugverdientijd(besparing, overstapkosten),
    }


def _overstapkosten(invoer, posities):
    """Per positie met een voorkeur: gemiddelde kosten van een transactie op de huidige beurs (verkopen) plus op de
    thuisbeurs (kopen), uit de eigen transacties. None zodra een van die gemiddelden ontbreekt."""
    met_voorkeur = [p for p in posities if p["voorkeur"]]
    verkoop = invoer.get("gemiddelde_kosten_per_transactie_eur")
    if not met_voorkeur or verkoop is None:
        return None
    totaal = 0.0
    for p in met_voorkeur:
        koop = (invoer.get("thuisbeurs_kosten_per_transactie_eur") or {}).get(p["voorkeur"]["beurs_code"])
        if koop is None:
            return None
        totaal += verkoop + koop
    return round(totaal, 2)


def _terugverdientijd(besparing, overstapkosten):
    if besparing is None or overstapkosten is None or besparing <= 0:
        return None
    return round(overstapkosten / besparing, 1)


def _quote_familie(quote):
    if quote.get("quoteType") in FONDS_TYPES:
        return "fonds"
    return "aandeel" if quote.get("quoteType") == "EQUITY" else None


def quotes_via_productnaam(isin_quotes, naam_quotes, naam):
    """Quotes van de zoekopdracht op productnaam die aannemelijk bij het product van de ISIN horen: zelfde quoteType-familie
    als de quotes van de ISIN-zoekopdracht (als die er zijn), ETF of aandeel, en een longname waarvan de betekenisvolle
    woorden die van de productnaam omvatten of erin passen (minstens MIN_GEDEELDE_NAAMWOORDEN gedeeld). Alleen de eerste
    passende longname (Yahoo's volgorde) telt, zodat één product overblijft."""
    gezocht = set(normaliseer_fondsnaam(naam).split())
    families = {_quote_familie(q) for q in isin_quotes} - {None}
    gekozen = None
    gevonden = []
    for q in naam_quotes:
        if q.get("quoteType") not in TOEGESTANE_TYPES or not q.get("longname") or not q.get("symbol"):
            continue
        if families and _quote_familie(q) not in families:
            continue
        woorden = set(normaliseer_fondsnaam(q["longname"]).split())
        if len(gezocht & woorden) < MIN_GEDEELDE_NAAMWOORDEN or not (gezocht <= woorden or woorden <= gezocht):
            continue
        if gekozen is None:
            gekozen = (q["longname"], q["quoteType"])
        if (q["longname"], q["quoteType"]) == gekozen:
            gevonden.append(q)
    return gevonden


def _heeft_longname_quote(quotes):
    return any(q.get("quoteType") in TOEGESTANE_TYPES and q.get("longname") for q in quotes)


def _haal_onderzoek_op(isin, naam=None):
    isin_quotes = zoek_gecachet(isin)
    via_naam_symbolen = set()
    if _heeft_longname_quote(isin_quotes):
        alle_quotes = noteringen_voor_isin_zoekopdracht(isin_quotes, zoek_gecachet)
    else:
        via_naam = quotes_via_productnaam(isin_quotes, zoek_gecachet(naam), naam) if naam else []
        bekend = {q.get("symbol") for q in isin_quotes}
        nieuw = [q for q in via_naam if q.get("symbol") not in bekend]
        via_naam_symbolen = {q["symbol"] for q in nieuw}
        alle_quotes = isin_quotes + nieuw
    if not alle_quotes:
        return {"fout": FOUT_GEEN_ZOEKRESULTAAT}
    is_etf, noteringen = thuisnoteringen(alle_quotes, via_naam_symbolen)
    dprint(f"[alternatieven] {isin}: {len(alle_quotes)} quote(s), is_etf={is_etf}, "
           f"thuisbeurzen na filter: {[(n['ticker'], n['beurs']) for n in noteringen]}")
    for notering in noteringen:
        notering["koers_eur"], _datum = haal_koers(notering["ticker"])
    return {"is_etf": is_etf, "noteringen": noteringen, "aantal_quotes": len(alle_quotes)}


def onderzoek_isin(isin, naam=None, klok=time.monotonic):
    """Per ISIN één uur onthouden, alleen na een zoekopdracht met minstens één quote; een fout of lege uitslag niet."""
    nu = klok()
    with _onderzoek_cache_lock:
        treffer = _onderzoek_cache.get(isin)
        if treffer and nu - treffer[0] < ONDERZOEK_CACHE_SECONDEN:
            return treffer[1]
    try:
        onderzoek = _haal_onderzoek_op(isin, naam)
    except Exception as e:
        dprint(f"[alternatieven] WARN {isin} niet opgezocht ({e!a})")
        return {"fout": FOUT_ALGEMEEN}
    if onderzoek.get("aantal_quotes"):
        with _onderzoek_cache_lock:
            if len(_onderzoek_cache) >= ONDERZOEK_CACHE_MAX_ITEMS:
                del _onderzoek_cache[min(_onderzoek_cache, key=lambda i: _onderzoek_cache[i][0])]
            _onderzoek_cache[isin] = (nu, onderzoek)
    return onderzoek


def wis_onderzoek_cache():
    with _onderzoek_cache_lock:
        _onderzoek_cache.clear()


def _is_etf_per_ticker(tickers):
    """Uit de classificatie in ticker_info (alleen lezen); leeg bij een databasefout of onbekende ticker."""
    try:
        details = db_get_ticker_details(sorted(tickers))
    except Exception as e:
        dprint(f"[alternatieven] WARN classificatie niet gelezen ({e!a})")
        return {}
    return {t: bool(d["is_etf"]) for t, d in details.items() if d.get("is_etf") is not None}


def haal_alternatieven(invoer, onderzoeker=onderzoek_isin):
    """Zoekt alle ISIN's parallel op (maximaal MAX_PARALLEL tegelijk) en bouwt het antwoord."""
    naam_per_isin = {}
    for p in invoer["posities"]:
        if p.get("isin"):
            naam_per_isin.setdefault(p["isin"], p.get("echte_naam"))
    isins = sorted(naam_per_isin)
    calls_voor = yahoo_teller_stand()[0]
    onderzoek = {}
    if isins:
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(isins))) as pool:
            for isin, resultaat in zip(isins, pool.map(lambda i: onderzoeker(i, naam_per_isin[i]), isins)):
                onderzoek[isin] = resultaat
    # De teller is globaal per proces: gelijktijdige requests tellen mee.
    dprint(f"[alternatieven] {len(isins)} ISIN(s), Yahoo-calls sinds start van deze klik: {yahoo_teller_stand()[0] - calls_voor}")
    return bouw_beurs_alternatieven(invoer, onderzoek, _is_etf_per_ticker({p["ticker"] for p in invoer["posities"]}))


def valideer_beurscode(beurs):
    """(code, fout): de beurscode uit de query of body; een thuisbeurs heeft geen aansluitingskosten."""
    if not isinstance(beurs, str) or not BEURSCODE_PATROON.match(beurs.strip()):
        return None, "Geef een geldige beurscode."
    code = beurs.strip().upper()
    if is_thuisbeurs_code(code):
        return None, "Op een thuisbeurs betaal je geen aansluitingskosten."
    return code, None


def _is_getal(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def valideer_alternatieven_invoer(invoer):
    """(schone invoer, fout) voor 'niet opslaan': de client stuurt `alternatieven_invoer` uit het kernantwoord terug."""
    if not isinstance(invoer, dict):
        return None, "Stuur de gegevens van de beurs mee."
    code, fout = valideer_beurscode(invoer.get("beurs"))
    if fout:
        return None, fout
    posities = invoer.get("posities")
    if not isinstance(posities, list) or not 1 <= len(posities) <= MAX_POSITIES_WAT_ALS:
        return None, f"Stuur 1 tot {MAX_POSITIES_WAT_ALS} posities mee."
    schoon_posities = []
    for p in posities:
        if not isinstance(p, dict):
            return None, "Elke positie is een object."
        ticker, naam, isin = p.get("ticker"), p.get("naam"), p.get("isin")
        if not isinstance(ticker, str) or not 1 <= len(ticker.strip()) <= MAX_TICKER_TEKENS:
            return None, f"Elke positie heeft een ticker van 1 tot {MAX_TICKER_TEKENS} tekens."
        if not isinstance(naam, str) or len(naam) > MAX_NAAM_TEKENS:
            return None, f"Een naam is maximaal {MAX_NAAM_TEKENS} tekens."
        if isin is not None and (not isinstance(isin, str) or not herken_isin(isin)):
            return None, "Een ISIN is ongeldig."
        if not _is_getal(p.get("aantal")) or p["aantal"] <= 0:
            return None, "Elke positie heeft een aantal groter dan 0."
        waarde = p.get("waarde_eur")
        if waarde is not None and (not _is_getal(waarde) or waarde <= 0):
            return None, "De waarde van een positie is groter dan 0 of leeg."
        echte_naam = p.get("echte_naam")
        if echte_naam is not None and (not isinstance(echte_naam, str) or len(echte_naam) > MAX_NAAM_TEKENS):
            return None, f"Een productnaam is maximaal {MAX_NAAM_TEKENS} tekens."
        schoon_posities.append({"ticker": ticker.strip(), "naam": naam, "echte_naam": echte_naam,
                                "isin": herken_isin(isin) if isin else None,
                                "aantal": float(p["aantal"]), "waarde_eur": float(waarde) if waarde is not None else None})
    optioneel = {}
    for sleutel in ("jaarkosten_eur", "gemiddelde_kosten_per_transactie_eur"):
        waarde = invoer.get(sleutel)
        if waarde is not None and (not _is_getal(waarde) or waarde < 0):
            return None, f"'{sleutel}' is geen geldig bedrag."
        optioneel[sleutel] = float(waarde) if waarde is not None else None
    thuiskosten = invoer.get("thuisbeurs_kosten_per_transactie_eur")
    if not isinstance(thuiskosten, dict):
        return None, "Stuur de kosten per thuisbeurs mee."
    schone_thuiskosten = {}
    for thuis in THUISBEURZEN:
        waarde = thuiskosten.get(thuis["code"])
        if waarde is not None and (not _is_getal(waarde) or waarde < 0):
            return None, "Een kostenbedrag voor een thuisbeurs is ongeldig."
        schone_thuiskosten[thuis["code"]] = float(waarde) if waarde is not None else None
    isins_thuis = invoer.get("isins_op_thuisbeurs")
    if (not isinstance(isins_thuis, list) or len(isins_thuis) > MAX_POSITIES_WAT_ALS * 4
            or not all(isinstance(i, str) and herken_isin(i) for i in isins_thuis)):
        return None, "De lijst met ISIN's op een thuisbeurs is ongeldig."
    beurs_naam = invoer.get("beurs_naam")
    if beurs_naam is not None and (not isinstance(beurs_naam, str) or len(beurs_naam) > MAX_NAAM_TEKENS):
        return None, "De naam van de beurs is ongeldig."
    return {
        "beurs": code, "beurs_naam": beurs_naam, "posities": schoon_posities,
        "betaald_dit_jaar": bool(invoer.get("betaald_dit_jaar")),
        "thuisbeurs_kosten_per_transactie_eur": schone_thuiskosten,
        "isins_op_thuisbeurs": [herken_isin(i) for i in isins_thuis],
        **optioneel,
    }, None
