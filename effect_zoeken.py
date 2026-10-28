"""Zoeken naar aandelen en ETF's (Yahoo) en de ISIN-kandidaten van een ETF, voor het tabblad Zoeken & wat als."""
import re
import threading
import time

from db import db_get_isins_voor_ticker
from debug_utils import dprint
from ticker_classificatie import ishares_fondsen
from ticker_matching import _yahoo_search

TOEGESTANE_TYPES = frozenset({"ETF", "EQUITY"})
BEURS_VOORKEUR = ("AMS", "GER")
ISIN_PATROON = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
NAAM_STOPWOORDEN = frozenset({"ucits", "etf", "acc", "dist", "usd", "eur", "gbp"})
ZOEK_CACHE_SECONDEN = 3600
ZOEK_CACHE_MAX_ITEMS = 500
MAX_KANDIDATEN_VOORSELECTIE = 6

ZEKERHEID_ZEKER = "zeker"
ZEKERHEID_KIEZEN = "kiezen"
ZEKERHEID_GEEN = "geen"
BRON_ZOEKOPDRACHT = "zoekopdracht"
BRON_EIGEN_DATA = "eigen_data"
BRON_ISHARES_NAAM = "ishares_naam"

_zoek_cache = {}
_zoek_cache_lock = threading.Lock()


def herken_isin(tekst):
    kandidaat = (tekst or "").strip().upper()
    return kandidaat if ISIN_PATROON.match(kandidaat) else None


def normaliseer_fondsnaam(naam):
    woorden = re.sub(r"[^a-z0-9]+", " ", (naam or "").lower()).split()
    return " ".join(w for w in woorden if w not in NAAM_STOPWOORDEN)


def kies_voorkeur_ticker(noteringen):
    for beurs in BEURS_VOORKEUR:
        for notering in noteringen:
            if notering["beurs"] == beurs:
                return notering["ticker"]
    return noteringen[0]["ticker"] if noteringen else None


def groepeer_noteringen(quotes):
    groepen = {}
    for quote in quotes:
        quote_type = quote.get("quoteType")
        naam = quote.get("longname") or quote.get("shortname")
        ticker = quote.get("symbol")
        if quote_type not in TOEGESTANE_TYPES or not naam or not ticker:
            continue
        groep = groepen.setdefault((naam, quote_type), {"naam": naam, "type": quote_type, "noteringen": []})
        if any(n["ticker"] == ticker for n in groep["noteringen"]):
            continue
        groep["noteringen"].append({"ticker": ticker, "beurs": quote.get("exchange"), "beurs_naam": quote.get("exchDisp")})
    resultaten = list(groepen.values())
    for resultaat in resultaten:
        resultaat["voorkeur_ticker"] = kies_voorkeur_ticker(resultaat["noteringen"])
    return resultaten


def noteringen_voor_isin_zoekopdracht(quotes, zoek=None):
    """Zoeken op ISIN geeft één notering; een tweede zoekopdracht op de longname vult de andere beurzen aan."""
    zoek = zoek or _yahoo_search
    eerste = next((q for q in quotes if q.get("quoteType") in TOEGESTANE_TYPES and q.get("longname")), None)
    if eerste is None:
        return quotes
    bekend = {q.get("symbol") for q in quotes}
    extra = [
        q for q in zoek(eerste["longname"])
        if q.get("longname") == eerste["longname"] and q.get("quoteType") == eerste["quoteType"]
        and q.get("symbol") not in bekend
    ]
    return quotes + extra


def zoek_effecten(query, zoek=None):
    zoek = zoek or _yahoo_search
    quotes = zoek(query)
    if herken_isin(query):
        quotes = noteringen_voor_isin_zoekopdracht(quotes, zoek)
    return groepeer_noteringen(quotes)


def zoek_gecachet(query, klok=time.monotonic):
    sleutel = query.strip().lower()
    nu = klok()
    with _zoek_cache_lock:
        treffer = _zoek_cache.get(sleutel)
        if treffer and nu - treffer[0] < ZOEK_CACHE_SECONDEN:
            return treffer[1]
    quotes = _yahoo_search(query)
    # Een Yahoo-fout is niet te onderscheiden van "niets gevonden": een lege uitslag niet onthouden.
    if quotes:
        with _zoek_cache_lock:
            if len(_zoek_cache) >= ZOEK_CACHE_MAX_ITEMS:
                del _zoek_cache[min(_zoek_cache, key=lambda s: _zoek_cache[s][0])]
            _zoek_cache[sleutel] = (nu, quotes)
    return quotes


def zoek_effecten_gecachet(query, klok=time.monotonic):
    return zoek_effecten(query, zoek=lambda q: zoek_gecachet(q, klok))


def wis_zoek_cache():
    with _zoek_cache_lock:
        _zoek_cache.clear()


def _root(ticker):
    return (ticker or "").split(".")[0].upper()


def _noteringen_van_isin(isin, zoek):
    try:
        quotes = noteringen_voor_isin_zoekopdracht(zoek(isin), zoek)
    except Exception as e:
        dprint(f"[zoeken] WARN noteringen van {isin} niet opgehaald ({e!a})")
        return []
    return [q["symbol"].upper() for q in quotes if q.get("symbol")]


def _voorgeselecteerde_isin(ticker, isins, zoek):
    """Exacte ticker in de noteringen gaat voor op dezelfde root (die verschilt per beurs); alleen bij precies één."""
    gekozen = (ticker or "").upper()
    root = _root(ticker)
    if not root:
        return None
    noteringen = {isin: _noteringen_van_isin(isin, zoek) for isin in isins[:MAX_KANDIDATEN_VOORSELECTIE]}
    exact = [isin for isin, tickers in noteringen.items() if gekozen in tickers]
    if exact:
        return exact[0] if len(exact) == 1 else None
    per_root = [isin for isin, tickers in noteringen.items() if any(_root(t) == root for t in tickers)]
    return per_root[0] if len(per_root) == 1 else None


def _kandidaten(isins, naam, voorgeselecteerd=None):
    return [{"isin": isin, "naam": naam, "voorgeselecteerd": isin == voorgeselecteerd} for isin in isins]


def isin_kandidaten(ticker, naam, isin_uit_zoekopdracht, eigen_isins, ishares_fondsen_lijst, zoek=None):
    """{zekerheid, bron, kandidaten: [{isin, naam, voorgeselecteerd}]}."""
    zoek = zoek or zoek_gecachet
    if isin_uit_zoekopdracht:
        return {"zekerheid": ZEKERHEID_ZEKER, "bron": BRON_ZOEKOPDRACHT,
                "kandidaten": _kandidaten([isin_uit_zoekopdracht], naam, isin_uit_zoekopdracht)}

    eigen = sorted(set(eigen_isins or []))
    if eigen:
        zeker = len(eigen) == 1
        return {"zekerheid": ZEKERHEID_ZEKER if zeker else ZEKERHEID_KIEZEN, "bron": BRON_EIGEN_DATA,
                "kandidaten": _kandidaten(eigen, naam, eigen[0] if zeker else None)}

    genormaliseerd = normaliseer_fondsnaam(naam)
    fondsen = [f for f in ishares_fondsen_lijst or []
               if genormaliseerd and normaliseer_fondsnaam(f.get("naam")) == genormaliseerd]
    if not fondsen:
        return {"zekerheid": ZEKERHEID_GEEN, "bron": None, "kandidaten": []}
    if len(fondsen) == 1:
        return {"zekerheid": ZEKERHEID_ZEKER, "bron": BRON_ISHARES_NAAM,
                "kandidaten": [{"isin": fondsen[0]["isin"], "naam": fondsen[0]["naam"], "voorgeselecteerd": True}]}
    voorgeselecteerd = _voorgeselecteerde_isin(ticker, [f["isin"] for f in fondsen], zoek)
    return {"zekerheid": ZEKERHEID_KIEZEN, "bron": BRON_ISHARES_NAAM,
            "kandidaten": [{"isin": f["isin"], "naam": f["naam"], "voorgeselecteerd": f["isin"] == voorgeselecteerd}
                           for f in fondsen]}


def bepaal_isin_kandidaten(ticker, naam, isin_param):
    """De echte bronnen (eigen transacties, iShares-cache) aan isin_kandidaten koppelen; een bronfout telt als 'niets'."""
    try:
        eigen_isins = db_get_isins_voor_ticker(ticker)
    except Exception as e:
        dprint(f"[zoeken] WARN eigen ISIN's niet opgehaald ({e!a})")
        eigen_isins = []
    try:
        fondsen = ishares_fondsen()
    except Exception as e:
        dprint(f"[zoeken] WARN iShares-fondsenlijst niet opgehaald ({e!a})")
        fondsen = None
    return isin_kandidaten(ticker, naam, herken_isin(isin_param), eigen_isins, fondsen)
