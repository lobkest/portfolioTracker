"""Land van een ETF met alleen een Yahoo-top-10 benaderen via een iShares-ETF met (bijna) dezelfde holdings.
De keuze rust op de vergelijking van de top-10, nooit op de naam van het fonds."""
import math
import re
from concurrent.futures import ThreadPoolExecutor

from db import (
    db_get_ishares_fondsen, db_save_ishares_fondsen, db_get_etf_proxies, db_save_etf_proxy, db_get_ticker_details,
)
from etf_holdings_provider import fetch_ishares_fondsenlijst, fetch_ishares_holdings_via_productpagina
from portfolio_verdeling import bereken_land_dekking, DREMPEL_ONBEKEND_LAND_PCT, EUROPESE_LANDEN
from ticker_classificatie import get_etf_holdings

MAX_PROXY_KANDIDATEN = 5
MAX_AFWIJKING_PROXY_PP = 1.0

# Codes uit de iShares-screener (aladdin*Code).
ASSET_CLASS_AANDELEN = "43511"
REGIO_WERELDWIJD = "43520"
REGIO_EUROPA = "43522"
REGIO_NOORD_AMERIKA = "43512"
REGIO_AZIE_PACIFIC = "43526"
MARKT_ONTWIKKELD = "43513"
MARKT_OPKOMEND = "43524"
SUB_ASSET_SMALL_CAP = "43615"
SUB_ASSET_MID_CAP = "43584"
STRATEGIE_DUURZAAM = "50586"
STRATEGIE_VALUTA_AFGEDEKT = "50569"
# Sectoren, Smart beta, Factoren, Dividend, Thematic, Alternatieve beleggingen, Uitkomst-gericht.
UITGESLOTEN_STRATEGIEEN = frozenset({"50581", "50584", "44329", "50572", "51034", "50566", "44337"})

# Begin van een (Morningstar-)category zoals Yahoo die geeft, bv. "Global Large-Cap Blend Equity".
_REGIO_PER_CATEGORIE_BEGIN = (
    ("Global", REGIO_WERELDWIJD),
    ("Europe", REGIO_EUROPA),
    ("Eurozone", REGIO_EUROPA),
    ("UK", REGIO_EUROPA),
    ("US", REGIO_NOORD_AMERIKA),
    ("North America", REGIO_NOORD_AMERIKA),
    ("Asia", REGIO_AZIE_PACIFIC),
    ("Japan", REGIO_AZIE_PACIFIC),
    ("Pacific", REGIO_AZIE_PACIFIC),
)

# Alleen ontwikkelde markten: iShares zet opkomende-marktenfondsen onder "Wereldwijd", ook als ze Aziatisch zijn.
_LANDEN_PER_REGIO = {
    REGIO_NOORD_AMERIKA: frozenset({"United States", "Canada"}),
    REGIO_EUROPA: EUROPESE_LANDEN,
    REGIO_AZIE_PACIFIC: frozenset({"Japan", "Australia", "Hong Kong", "Singapore", "New Zealand"}),
}

_NAAM_RUIS = frozenset({
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "nv", "sa", "ag", "se", "the",
    "adr", "reg", "shs",
})


def regio_bronfonds(categorie, top10_landen):
    """Screener-regiocode: uit de Yahoo-category, anders uit de landen van de top-10
    (alles in één regio -> die regio, gemengd -> Wereldwijd). None als geen van beide iets zegt."""
    if categorie:
        for begin, regio in _REGIO_PER_CATEGORIE_BEGIN:
            if categorie.startswith(begin + " ") or categorie == begin:
                return regio
        return None
    landen = {land for land in top10_landen if land and land != "Unknown"}
    if not landen:
        return None
    for regio, regio_landen in _LANDEN_PER_REGIO.items():
        if landen <= regio_landen:
            return regio
    return REGIO_WERELDWIJD


def markttype_bronfonds(categorie, top10_landen):
    """Opkomend volgens de category; ontwikkeld als de top-10 een Amerikaans bedrijf bevat
    (opkomende-marktenfondsen hebben die nooit); anders geen filter (None)."""
    if categorie and "Emerging" in categorie:
        return MARKT_OPKOMEND
    if "United States" in top10_landen:
        return MARKT_ONTWIKKELD
    return None


def kies_kandidaten(fondsen, regio, markt_type=None, categorie=None, max_n=MAX_PROXY_KANDIDATEN):
    """Aandelen-ETF's uit dezelfde regio, één per fonds; gewone vóór duurzame, niet-afgedekte vóór alleen-afgedekte
    (zelfde holdings als het origineel, kost dus alleen een plek), dan op fondsgrootte.
    Share classes van één fonds hebben dezelfde fondsgrootte; is één ervan sector/factor/dividend, dan valt het fonds af."""
    if regio is None:
        return []
    toegestane_kap = set()
    if categorie and "Small" in categorie:
        toegestane_kap.add(SUB_ASSET_SMALL_CAP)
    if categorie and "Mid" in categorie:
        toegestane_kap.add(SUB_ASSET_MID_CAP)

    groepen = {}
    for f in sorted(fondsen, key=lambda f: f["portfolio_id"]):
        if f.get("asset_class") != ASSET_CLASS_AANDELEN or f.get("regio") != regio:
            continue
        if markt_type and f.get("markt_type") != markt_type:
            continue
        kap = f.get("sub_asset_class")
        if kap in (SUB_ASSET_SMALL_CAP, SUB_ASSET_MID_CAP) and kap not in toegestane_kap:
            continue
        sleutel = f["fondsgrootte"] if f.get("fondsgrootte") is not None else f["portfolio_id"]
        groepen.setdefault(sleutel, []).append(f)

    kandidaten = []
    for leden in groepen.values():
        strategieen = {code for f in leden for code in f.get("strategie_codes") or []}
        if strategieen & UITGESLOTEN_STRATEGIEEN:
            continue
        niet_afgedekt = [f for f in leden if STRATEGIE_VALUTA_AFGEDEKT not in (f.get("strategie_codes") or [])]
        kandidaten.append(((STRATEGIE_DUURZAAM in strategieen, not niet_afgedekt), (niet_afgedekt or leden)[0]))

    kandidaten.sort(key=lambda k: (k[0], -(k[1].get("fondsgrootte") or 0.0)))
    return [f for _rang, f in kandidaten[:max_n]]


def ticker_root(ticker):
    """'2330.TW' -> '2330', 'BRK-B' -> 'BRKB'; Yahoo's beurssuffix eraf, leestekens weg."""
    if not ticker:
        return None
    t = re.sub(r"\.[A-Z]{1,3}$", "", str(ticker).strip().upper())
    return re.sub(r"[^A-Z0-9]", "", t) or None


def normaliseer_holdingnaam(naam):
    """'Meta Platforms Inc Class A' -> 'meta platforms'; rechtsvorm en aandelenklasse weg."""
    if not naam:
        return None
    tekst = re.sub(r"[^a-z0-9]+", " ", str(naam).lower())
    tekst = re.sub(r"\b(class|cl)\s+[a-z]\b", " ", tekst)
    woorden = [w for w in tekst.split() if w not in _NAAM_RUIS]
    return " ".join(woorden) or None


def vergelijk_top10(bron_top10, kandidaat_holdings):
    """Beide als [{naam, ticker, gewicht_pct}]. Koppelt eerst op ticker-root, dan op genormaliseerde naam.
    {rijen: [{bedrijf, bron_pct, proxy_pct, verschil_pp}], ontbrekend: [naam], max_afwijking_pp (None zonder koppeling)}"""
    per_root = {}
    per_naam = {}
    for h in sorted(kandidaat_holdings, key=lambda h: h["gewicht_pct"], reverse=True):
        per_root.setdefault(ticker_root(h.get("ticker")), h)
        per_naam.setdefault(normaliseer_holdingnaam(h.get("naam")), h)
    per_root.pop(None, None)
    per_naam.pop(None, None)

    rijen = []
    ontbrekend = []
    for h in sorted(bron_top10, key=lambda h: h["gewicht_pct"], reverse=True):
        match = per_root.get(ticker_root(h.get("ticker"))) or per_naam.get(normaliseer_holdingnaam(h.get("naam")))
        proxy_pct = match["gewicht_pct"] if match else None
        if match is None:
            ontbrekend.append(h["naam"])
        rijen.append({
            "bedrijf": h["naam"],
            "bron_pct": round(h["gewicht_pct"], 2),
            "proxy_pct": round(proxy_pct, 2) if proxy_pct is not None else None,
            "verschil_pp": round(abs(proxy_pct - h["gewicht_pct"]), 2) if proxy_pct is not None else None,
        })
    verschillen = [r["verschil_pp"] for r in rijen if r["verschil_pp"] is not None]
    return {"rijen": rijen, "ontbrekend": ontbrekend, "max_afwijking_pp": max(verschillen) if verschillen else None}


def is_geaccepteerd(vergelijking, max_afwijking_pp=MAX_AFWIJKING_PROXY_PP):
    return (bool(vergelijking["rijen"]) and not vergelijking["ontbrekend"]
            and vergelijking["max_afwijking_pp"] is not None and vergelijking["max_afwijking_pp"] <= max_afwijking_pp)


def kies_proxy(beoordelingen, max_afwijking_pp=MAX_AFWIJKING_PROXY_PP):
    """beoordelingen: [{isin, naam, **vergelijk_top10()}]. {gekozen, beste, reden}: gekozen = geaccepteerde kandidaat
    met de laagste afwijking (of None); beste = gekozen, anders de minst slechte, voor de diagnostiek."""
    def afwijking(b):
        return b["max_afwijking_pp"] if b["max_afwijking_pp"] is not None else math.inf

    geaccepteerd = [b for b in beoordelingen if is_geaccepteerd(b, max_afwijking_pp)]
    if geaccepteerd:
        gekozen = min(geaccepteerd, key=afwijking)
        return {"gekozen": gekozen, "beste": gekozen, "reden": None}
    if not beoordelingen:
        return {"gekozen": None, "beste": None, "reden": "geen kandidaten"}

    beste = min(beoordelingen, key=lambda b: (len(b["ontbrekend"]), afwijking(b)))
    if beste["ontbrekend"]:
        reden = f"niet gevonden bij de kandidaat: {', '.join(beste['ontbrekend'])}"
    else:
        reden = (f"top-10 wijkt max. {_pp_nl(beste['max_afwijking_pp'])} pp af "
                 f"(grens {_pp_nl(max_afwijking_pp)} pp)")
    return {"gekozen": None, "beste": beste, "reden": reden}


def land_uit_holdings(holdings):
    """[{gewicht (%), land}] -> {land: fractie 0-1}; het niet-gedekte restant als 'Unknown'."""
    land = {}
    for h in holdings:
        naam = h.get("land") or "Unknown"
        land[naam] = land.get(naam, 0.0) + h["gewicht"] / 100.0
    restant = 1.0 - sum(land.values())
    if restant > 1e-9:
        land["Unknown"] = land.get("Unknown", 0.0) + restant
    return land


def _pp_nl(waarde):
    return f"{waarde:.2f}".replace(".", ",")


def _heeft_proxy_nodig(holdings):
    if not holdings or holdings[0].get("bron") != "yfinance_top10":
        return False
    dekking = bereken_land_dekking([{"naam": h["holding_naam"], "gewicht": h["gewicht"], "land": h.get("land")}
                                    for h in holdings])
    return dekking["onbekend_pct"] > DREMPEL_ONBEKEND_LAND_PCT


def _ishares_fondsen():
    fondsen = db_get_ishares_fondsen()
    if fondsen is not None:
        return fondsen
    fondsen = fetch_ishares_fondsenlijst()
    if fondsen:
        db_save_ishares_fondsen(fondsen)
    return fondsen


def _resultaat(keuze, aantal_kandidaten, proxy_land=None, reden=None):
    """De vorm van een etf_proxy-rij."""
    gekozen, beste = keuze["gekozen"], keuze["beste"]
    return {
        "proxy_isin": gekozen["isin"] if gekozen else None,
        "proxy_naam": gekozen["naam"] if gekozen else None,
        "max_afwijking_pp": gekozen["max_afwijking_pp"] if gekozen else None,
        "proxy_land": proxy_land,
        "vergelijking": {
            "kandidaat_isin": beste["isin"] if beste else None,
            "kandidaat_naam": beste["naam"] if beste else None,
            "max_afwijking_pp": beste["max_afwijking_pp"] if beste else None,
            "reden": reden or keuze["reden"],
            "rijen": beste["rijen"] if beste else [],
            "aantal_kandidaten": aantal_kandidaten,
        },
    }


def _bepaal_proxy(ticker, holdings, categorie):
    """etf_proxy-rij, of None bij een netwerkfout (dan niet cachen en volgende keer opnieuw)."""
    landen = [h.get("land") for h in holdings]
    regio = regio_bronfonds(categorie, landen)
    geen = {"gekozen": None, "beste": None, "reden": None}
    if regio is None:
        return _resultaat(geen, 0, reden=f"geen regio te bepalen (category: {categorie or 'leeg'})")

    fondsen = _ishares_fondsen()
    if not fondsen:
        return None
    kandidaten = kies_kandidaten(fondsen, regio, markttype_bronfonds(categorie, landen), categorie)
    if not kandidaten:
        return _resultaat(geen, 0, reden="geen iShares-aandelen-ETF in dezelfde regio")

    with ThreadPoolExecutor(max_workers=MAX_PROXY_KANDIDATEN) as executor:
        kandidaat_holdings = list(executor.map(
            fetch_ishares_holdings_via_productpagina, [k["product_url"] for k in kandidaten]))
    if all(h is None for h in kandidaat_holdings):
        return None

    bron_top10 = [{"naam": h["holding_naam"], "ticker": h.get("holding_ticker"), "gewicht_pct": h["gewicht"] * 100}
                  for h in holdings]
    beoordelingen = []
    holdings_per_isin = {}
    for kandidaat, kh in zip(kandidaten, kandidaat_holdings):
        if kh is None:
            continue
        holdings_per_isin[kandidaat["isin"]] = kh
        vergelijking = vergelijk_top10(
            bron_top10, [{"naam": h["naam"], "ticker": h.get("ticker"), "gewicht_pct": h["gewicht"]} for h in kh])
        beoordelingen.append({"isin": kandidaat["isin"], "naam": kandidaat["naam"], **vergelijking})

    keuze = kies_proxy(beoordelingen)
    gekozen = keuze["gekozen"]
    proxy_land = land_uit_holdings(holdings_per_isin[gekozen["isin"]]) if gekozen else None
    return _resultaat(keuze, len(kandidaten), proxy_land)


def land_proxies_voor_etfs(isin_per_etf):
    """{ticker: etf_proxy-rij} voor ETF's met alleen een Yahoo-top-10 en meer dan DREMPEL_ONBEKEND_LAND_PCT
    onbekend land. Eerst de etf_proxy-cache (ook 'geen proxy'); alleen bij een cache-miss screener en CSV's.
    Leest de holdings uit de cache: aanroepen ná _verwarm_land_sector_cache_parallel()."""
    nodig = {}
    for ticker, isin in isin_per_etf.items():
        holdings = get_etf_holdings(ticker)
        if isin and _heeft_proxy_nodig(holdings):
            nodig[ticker] = (isin, holdings)
    if not nodig:
        return {}

    cached = db_get_etf_proxies([isin for isin, _h in nodig.values()])
    details = db_get_ticker_details(list(nodig))
    resultaten = {}
    for ticker, (isin, holdings) in nodig.items():
        if isin in cached:
            resultaten[ticker] = cached[isin]
            continue
        resultaat = _bepaal_proxy(ticker, holdings, (details.get(ticker) or {}).get("category"))
        if resultaat is None:
            # Niet cachen; de rij is alleen voor de melding in Diagnostiek.
            resultaten[ticker] = _resultaat({"gekozen": None, "beste": None, "reden": None}, 0,
                                            reden="iShares niet bereikbaar, volgende keer opnieuw")
            continue
        db_save_etf_proxy(isin, resultaat)
        resultaten[ticker] = resultaat
    return resultaten
