"""Fase B van "Alternatieven op een thuisbeurs": andere iShares/VanEck-ETF's op een thuisbeurs die qua inhoud sterk lijken
op een ETF-positie. Pure kern (kandidaten kiezen, vergelijken, selecteren) plus een dunne netwerklaag
(`zoek_vergelijkbare_etfs()`). Geen advies: alleen overlap in holdings, land en sector."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout

import pandas as pd

from backend.aansluitingskosten import THUISBEURZEN
from backend.beurs_alternatieven import onderzoek_isin
from backend.db import db_get_isin_ticker_product, db_get_ticker_details, db_portfolio_bestaat
from backend.debug_utils import dprint, meet_tijd
from backend.effect_profiel import BRON_TOP10, holdings_voor_effect
from backend.effect_zoeken import herken_isin, normaliseer_fondsnaam
from backend.etf_holdings_provider import ETF_HOLDINGS_BRON
from backend.naam_verkorting import uitkeringsvorm
from backend.portfolio_verdeling import (
    _groepeer_holdings_per_bedrijf, etf_overlap_uit, holdings_lijst_uit, land_pct_uit_holdings, sector_pct_met_restant)
from backend.ticker_classificatie import get_etf_holdings, get_etf_holdings_voor_isin, get_etf_sector_verdeling, ishares_fondsen
from backend.wat_als import MAX_NAAM_TEKENS, MAX_TICKER_TEKENS

MAX_KANDIDATEN = 10
MIN_OVERLAP_PCT = 70.0
MAX_ALTERNATIEVEN = 5
MIN_ONDER_DREMPEL_OVERLAP_PCT = 30.0
MAX_ONDER_DREMPEL = 3
MAX_PARALLEL = 4
TOP_N_BEDRIJVEN = 10
TIJDBUDGET_SECONDEN = 40
AANBIEDERNAMEN = frozenset({"ishares", "vaneck", "vanguard", "xtrackers", "spdr", "amundi"})
NAAM_STOPWOORDEN = frozenset({"the", "and", "of", "fund", "index"})
ASSET_CLASS_SLEUTELS = ("asset_class", "regio", "markt_type")
VANECK = "vaneck"
ISHARES = "ishares"

STATUS_OK = "ok"
STATUS_GEEN_KANDIDATEN = "geen_kandidaten"
STATUS_GEEN_ALTERNATIEVEN = "geen_alternatieven"
STATUS_FOUT = "fout"
WAARSCHUWING_BRON_ALLEEN_TOP10 = "bron_alleen_top10"
KANDIDATEN_VIA_CODES = "codes"
KANDIDATEN_VIA_NAAM = "naam"
FOUT_GEEN_HOLDINGS = "Van dit fonds zijn geen holdings bekend, dus er is niets om mee te vergelijken."
FOUT_NIET_ETF = "Alleen voor een ETF kunnen vergelijkbare fondsen gezocht worden."
FOUT_ALGEMEEN = "Vergelijkbare fondsen opzoeken is mislukt of duurde te lang. Probeer het opnieuw."

_THUIS_SUFFIX = {"AMS": ".AS", "BRU": ".BR"}


class OnbekendeTicker(Exception):
    pass


def betekenisvolle_woorden(naam):
    return {w for w in normaliseer_fondsnaam(naam).split()
            if w not in AANBIEDERNAMEN and w not in NAAM_STOPWOORDEN and len(w) > 1}


def _grootte(fonds):
    return fonds.get("fondsgrootte") or 0.0


def _eerste_per_naam(fondsen):
    """Share classes (Acc/Dist) van één fonds hebben dezelfde genormaliseerde naam en dezelfde holdings: één plek is genoeg."""
    gezien, uniek = set(), []
    for f in fondsen:
        sleutel = normaliseer_fondsnaam(f["naam"])
        if sleutel not in gezien:
            gezien.add(sleutel)
            uniek.append(f)
    return uniek


def vaneck_kandidaten(bron=ETF_HOLDINGS_BRON):
    """Eén kandidaat per VanEck-holdingsbestand (dezelfde URL = hetzelfde fonds op meerdere beurzen)."""
    per_url = {}
    for ticker, instelling in bron.items():
        if instelling.get("provider") == VANECK:
            per_url.setdefault(instelling["url"], []).append(ticker)
    return [{"bron": VANECK, "isin": None, "naam": None, "tickers": tickers, "fondsgrootte": None}
            for tickers in per_url.values()]


def _kandidaten_via_codes(bronfonds, fondsen, max_n):
    gelijk = [f for f in fondsen if all(f.get(s) == bronfonds.get(s) for s in ASSET_CLASS_SLEUTELS)]
    rang = sorted(_eerste_per_naam(sorted(gelijk, key=lambda f: -_grootte(f))),
                  key=lambda f: f.get("sub_asset_class") != bronfonds.get("sub_asset_class"))
    return [dict(f, bron=ISHARES) for f in sorted(rang[:max_n], key=lambda f: -_grootte(f))]


def _kandidaten_via_naam(bron_naam, bron_ticker, fondsen, vaneck, max_n):
    woorden = betekenisvolle_woorden(bron_naam)
    gedeeld = sorted(((len(woorden & betekenisvolle_woorden(f["naam"])), f) for f in fondsen),
                     key=lambda x: (-x[0], -_grootte(x[1])))
    ishares = _eerste_per_naam([f for aantal, f in gedeeld if aantal >= 1])
    eigen = [k for k in vaneck if bron_ticker not in k["tickers"]]
    alle = [dict(f, bron=ISHARES) for f in ishares[:max(0, max_n - len(eigen))]] + eigen[:max_n]
    return sorted(alle, key=lambda k: -_grootte(k))


def kies_kandidaten_voor_bron(bron_isin, bron_naam, bron_ticker, ishares_lijst, vaneck=None, max_n=MAX_KANDIDATEN):
    """(kandidaten, via): hoogstens max_n, op fondsgrootte aflopend (onbekend achteraan). Staat het bronfonds in de
    iShares-lijst (via = codes), dan zijn kandidaten de iShares-ETF's met dezelfde asset_class, regio en markt_type
    (zelfde sub_asset_class krijgt voorrang); anders (via = naam) iShares-fondsen met minstens één betekenisvol gedeeld
    naamwoord plus de VanEck-fondsen. Het bronfonds zelf (zelfde ISIN of ticker) valt altijd af."""
    vaneck = vaneck_kandidaten() if vaneck is None else vaneck
    fondsen = [f for f in ishares_lijst or [] if f.get("isin") and f.get("naam") and f["isin"] != bron_isin]
    bronfonds = next((f for f in ishares_lijst or [] if bron_isin and f.get("isin") == bron_isin), None)
    if bronfonds is not None:
        return _kandidaten_via_codes(bronfonds, fondsen, max_n), KANDIDATEN_VIA_CODES
    return _kandidaten_via_naam(bron_naam or "", bron_ticker, fondsen, vaneck, max_n), KANDIDATEN_VIA_NAAM


def overlap_pct(gewichten_a, gewichten_b):
    """Gewogen overlap in %, dezelfde definitie als de ETF-overlapmatrix (etf_overlap_uit)."""
    matrix = etf_overlap_uit({"a": gewichten_a, "b": gewichten_b})
    return round(matrix["a"]["b"] * 100, 1)


def gelijkenis_pct(verdeling_a, verdeling_b):
    """100 - de helft van de som van de absolute verschillen (in %) per categorie; verdelingen zijn {naam: fractie}."""
    sleutels = set(verdeling_a) | set(verdeling_b)
    verschil = sum(abs(verdeling_a.get(k, 0.0) - verdeling_b.get(k, 0.0)) for k in sleutels) * 100
    return round(max(0.0, 100 - verschil / 2), 1)


def _top_bedrijven(lijst):
    return [{"naam": r["naam"], "gewicht_pct": round(r["gewicht"] * 100, 2)} for r in lijst["holdings"][:TOP_N_BEDRIJVEN]]


def fonds_profiel(holdings, sector_verdeling=None):
    """Alles wat de vergelijking van één fonds nodig heeft, uit holdings (cache-vorm: holding_naam, gewicht 0-1, land)
    en een optionele sectorverdeling {sector: fractie}."""
    geldig = [h for h in holdings if pd.notna(h["gewicht"])]
    lijst = holdings_lijst_uit(geldig)
    return {
        "gewichten": _groepeer_holdings_per_bedrijf(geldig)[0],
        "land": land_pct_uit_holdings(geldig),
        "sector": sector_pct_met_restant(sector_verdeling) if sector_verdeling else None,
        "aantal_holdings": lijst["aantal"],
        "top10": _top_bedrijven(lijst),
    }


def vergelijk_fondsen(bron, kandidaat):
    """Eén alternatief; de sectorgelijkenis is None als van een van beide geen sectorverdeling bekend is."""
    sector = gelijkenis_pct(bron["sector"], kandidaat["sector"]) if bron["sector"] and kandidaat["sector"] else None
    return {
        "overlap_pct": overlap_pct(bron["gewichten"], kandidaat["gewichten"]),
        "land_gelijkenis_pct": gelijkenis_pct(bron["land"], kandidaat["land"]),
        "sector_gelijkenis_pct": sector,
        "aantal_holdings": kandidaat["aantal_holdings"],
        "top10": kandidaat["top10"],
    }


def selecteer_alternatieven(vergelijkingen, min_overlap=MIN_OVERLAP_PCT, max_n=MAX_ALTERNATIEVEN):
    """Alleen vanaf de drempel, aflopend op overlap, hoogstens max_n."""
    houden = [v for v in vergelijkingen if v["overlap_pct"] >= min_overlap]
    return sorted(houden, key=lambda v: -v["overlap_pct"])[:max_n]


def selecteer_onder_drempel(vergelijkingen, drempel=MIN_OVERLAP_PCT, ondergrens=MIN_ONDER_DREMPEL_OVERLAP_PCT,
                            max_n=MAX_ONDER_DREMPEL):
    """De beste kandidaten onder de drempel: minstens `ondergrens`, aflopend op overlap, hoogstens max_n."""
    eronder = [v for v in vergelijkingen if ondergrens <= v["overlap_pct"] < drempel]
    return sorted(eronder, key=lambda v: -v["overlap_pct"])[:max_n]


def vul_sector_aan(vergelijkingen, bron, sector_ophaler):
    """De sectorgelijkenis wordt alleen bij >= de drempel in onderzoek_kandidaat() opgehaald; wie eronder toch getoond wordt, krijgt hem hier."""
    aangevuld = []
    for v in vergelijkingen:
        if v["sector_gelijkenis_pct"] is None and bron["sector"]:
            verdeling = _sector_van_veilig(sector_ophaler, v["ticker"])
            if verdeling:
                v = {**v, "sector_gelijkenis_pct": gelijkenis_pct(bron["sector"], sector_pct_met_restant(verdeling))}
        aangevuld.append(v)
    return aangevuld


def _sector_van_veilig(sector_ophaler, ticker):
    try:
        return sector_ophaler(ticker)
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN sectorverdeling niet opgehaald ({e!a})")
        return None


def thuisnotering_van_vaneck(kandidaat):
    for ticker in kandidaat["tickers"]:
        for thuis in THUISBEURZEN:
            if ticker.upper().endswith(_THUIS_SUFFIX[thuis["yahoo"]].upper()):
                return {"ticker": ticker, "beurs": thuis["yahoo"], "beurs_code": thuis["code"], "beurs_naam": thuis["naam"]}
    return None


def _thuisnotering_van_ishares(kandidaat, onderzoeker):
    onderzoek = onderzoeker(kandidaat["isin"], kandidaat["naam"])
    # Een notering die alleen via de naam is gevonden is niet op ISIN bevestigd: dan liever geen kandidaat.
    bevestigd = [n for n in onderzoek.get("noteringen", []) if not n.get("via_naam")]
    return bevestigd[0] if bevestigd else None


def _kandidaat_holdings(kandidaat, notering):
    """Volledige provider-holdings, of None: een top-10 is voor een kandidaat niet goed genoeg."""
    if kandidaat["bron"] == ISHARES:
        holdings = get_etf_holdings_voor_isin(kandidaat["isin"])
    else:
        holdings = get_etf_holdings(notering["ticker"])
    if not holdings or holdings[0].get("bron") == BRON_TOP10:
        return None
    return holdings


def _sector_van(ticker):
    try:
        return get_etf_sector_verdeling(ticker) or None
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN sectorverdeling niet opgehaald ({e!a})")
        return None


def _naam_van_vaneck(notering):
    try:
        return (db_get_ticker_details([notering["ticker"]]).get(notering["ticker"]) or {}).get("long_name") or notering["ticker"]
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN naam niet opgehaald ({e!a})")
        return notering["ticker"]


def onderzoek_kandidaat(kandidaat, bron, onderzoeker=onderzoek_isin, holdings_ophaler=_kandidaat_holdings,
                        sector_ophaler=_sector_van):
    """Goedkoopste filter eerst: thuisnotering, dan holdings (overlap), dan pas de sector van wie de drempel haalt.
    None als de kandidaat vervalt (geen notering, geen volledige holdings of een fout)."""
    try:
        notering = (thuisnotering_van_vaneck(kandidaat) if kandidaat["bron"] == VANECK
                    else _thuisnotering_van_ishares(kandidaat, onderzoeker))
        if notering is None:
            return None
        holdings = holdings_ophaler(kandidaat, notering)
        if not holdings:
            return None
        profiel = fonds_profiel(holdings)
        vergelijking = vergelijk_fondsen(bron, profiel)
        naam = kandidaat["naam"] or _naam_van_vaneck(notering)
        if vergelijking["overlap_pct"] >= MIN_OVERLAP_PCT:
            profiel["sector"] = fonds_profiel(holdings, sector_ophaler(notering["ticker"]))["sector"]
            vergelijking = vergelijk_fondsen(bron, profiel)
        return {
            **vergelijking, "ticker": notering["ticker"], "naam": naam, "isin": kandidaat["isin"],
            "beurs": notering["beurs"], "beurs_code": notering["beurs_code"], "beurs_naam": notering["beurs_naam"],
            "uitkeringsvorm": uitkeringsvorm(naam),
        }
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN kandidaat niet onderzocht ({e!a})")
        return None


def _onderzoek_parallel(kandidaten, bron, onderzoeker, holdings_ophaler, sector_ophaler, klok=time.monotonic):
    """(resultaten, onvolledig): na TIJDBUDGET_SECONDEN stopt het wachten; wat dan nog loopt telt niet mee."""
    resultaten = []
    pool = ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(kandidaten)))
    try:
        futures = [pool.submit(onderzoek_kandidaat, k, bron, onderzoeker, holdings_ophaler, sector_ophaler) for k in kandidaten]
        try:
            for toekomst in as_completed(futures, timeout=TIJDBUDGET_SECONDEN):
                resultaten.append(toekomst.result())
        except FutureTimeout:
            return [r for r in resultaten if r], True
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [r for r in resultaten if r], False


def bouw_bron(ticker, isin, sector_ophaler=_sector_van):
    """(profiel, bron_alleen_top10) van het bronfonds, of (None, False) zonder bruikbare holdings."""
    hv = holdings_voor_effect(ticker, isin)
    if not hv["holdings"]:
        return None, False
    return fonds_profiel(hv["holdings"], sector_ophaler(ticker)), hv["bron"] == BRON_TOP10


def zoek_vergelijkbare_etfs(ticker, isin, naam, onderzoeker=onderzoek_isin, holdings_ophaler=_kandidaat_holdings,
                            sector_ophaler=_sector_van, fondsen_ophaler=ishares_fondsen):
    """Het antwoord voor één positie. status: ok, geen_kandidaten, geen_alternatieven of fout."""
    antwoord = {"status": STATUS_OK, "ticker": ticker, "naam": naam, "waarschuwing": None, "fout": None,
                "alternatieven": [], "onder_drempel": [], "aantal_kandidaten": 0, "aantal_onderzocht": 0, "onvolledig": False,
                "min_overlap_pct": MIN_OVERLAP_PCT, "kandidaten_via": None, "bron": None}
    with meet_tijd("vergelijkbare_etfs"):
        bron, alleen_top10 = bouw_bron(ticker, isin, sector_ophaler)
        if bron is None:
            return {**antwoord, "status": STATUS_FOUT, "fout": FOUT_GEEN_HOLDINGS}
        antwoord["bron"] = {"aantal_holdings": bron["aantal_holdings"], "top10": bron["top10"],
                            "uitkeringsvorm": uitkeringsvorm(naam)}
        if alleen_top10:
            antwoord["waarschuwing"] = WAARSCHUWING_BRON_ALLEEN_TOP10
        try:
            lijst = fondsen_ophaler()
        except Exception as e:
            dprint(f"[vergelijkbaar] WARN iShares-lijst niet opgehaald ({e!a})")
            lijst = None
        kandidaten, via = kies_kandidaten_voor_bron(isin, naam, ticker, lijst)
        antwoord["kandidaten_via"] = via
        antwoord["aantal_kandidaten"] = len(kandidaten)
        if not kandidaten:
            return {**antwoord, "status": STATUS_GEEN_KANDIDATEN}
        resultaten, onvolledig = _onderzoek_parallel(kandidaten, bron, onderzoeker, holdings_ophaler, sector_ophaler)
        antwoord["onvolledig"] = onvolledig
        antwoord["aantal_onderzocht"] = len(resultaten)
        antwoord["alternatieven"] = selecteer_alternatieven(resultaten)
        dprint(f"[vergelijkbaar] {len(kandidaten)} kandidaat/kandidaten via {via}, {len(resultaten)} met thuisnotering en "
               f"holdings, {len(antwoord['alternatieven'])} boven de drempel")
        if not antwoord["alternatieven"]:
            onder = selecteer_onder_drempel(resultaten)
            return {**antwoord, "status": STATUS_GEEN_ALTERNATIEVEN,
                    "onder_drempel": vul_sector_aan(onder, bron, sector_ophaler)}
        return antwoord


def bron_uit_portfolio(ticker, code):
    """(isin, naam) van een ticker in een opgeslagen portfolio; None bij een onbekende code, OnbekendeTicker zonder positie."""
    rijen = db_get_isin_ticker_product(code)
    if not rijen and not db_portfolio_bestaat(code):
        return None
    eigen = [(isin, product) for isin, t, product in rijen if t == ticker]
    if not eigen:
        raise OnbekendeTicker(ticker)
    ishares = {f["isin"] for f in ishares_fondsen() or []}
    isin, product = next((r for r in eigen if r[0] in ishares), eigen[0])
    try:
        long_name = (db_get_ticker_details([ticker]).get(ticker) or {}).get("long_name")
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN naam niet gelezen ({e!a})")
        long_name = None
    return isin, long_name or product


def is_etf_volgens_classificatie(ticker):
    """True/False uit ticker_info (alleen lezen), None als die onbekend is of de database faalt."""
    try:
        is_etf = (db_get_ticker_details([ticker]).get(ticker) or {}).get("is_etf")
    except Exception as e:
        dprint(f"[vergelijkbaar] WARN classificatie niet gelezen ({e!a})")
        return None
    return None if is_etf is None else bool(is_etf)


def valideer_vergelijk_invoer(data):
    """(ticker, isin, naam, fout) uit een JSON-body {ticker, isin?, naam?} voor 'niet opslaan'."""
    if not isinstance(data, dict):
        return None, None, None, "Stuur een JSON-body met een ticker."
    ticker = data.get("ticker")
    if not isinstance(ticker, str) or not 1 <= len(ticker.strip()) <= MAX_TICKER_TEKENS:
        return None, None, None, f"Geef een ticker van 1 tot {MAX_TICKER_TEKENS} tekens."
    isin = data.get("isin")
    if isin not in (None, ""):
        isin = herken_isin(isin) if isinstance(isin, str) else None
        if not isin:
            return None, None, None, "Dit is geen geldige ISIN."
    else:
        isin = None
    naam = data.get("naam")
    if naam is not None and (not isinstance(naam, str) or len(naam) > MAX_NAAM_TEKENS):
        return None, None, None, f"Een naam is maximaal {MAX_NAAM_TEKENS} tekens."
    return ticker.strip(), isin, naam or ticker.strip(), None
