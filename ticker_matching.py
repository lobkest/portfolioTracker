"""
Ticker-resolutie/matching: welke Yahoo Finance-ticker hoort bij een
DeGiro-productnaam/ISIN/beurs (yahooquery-zoekopdracht, progressief
inkorten, handmatige overrides), plus het experimentele OpenFIGI-
extra-validatiesignaal.

Losgetrokken uit analysis.py; ongewijzigd overgenomen.
"""
import os

import requests
from yahooquery import search

from db import get_cached_openfigi, save_openfigi
from debug_utils import dprint
from transactie_utils import _is_corporate_action_row
from yahoo_client import _tel_yahoo_call

BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    # TDG (Tradegate) verhandelt ook internationale (vooral Amerikaanse)
    # aandelen in EUR, die Yahoo niet apart onder een Duitse notering
    # indexeert -- alleen onder hun thuismarkt-ticker (bv. NFLX op NMS).
    # NMS/NYQ staan BEWUST achteraan: een echte Duitse notering (als die
    # bestaat) moet nog steeds voorrang krijgen boven de Amerikaanse
    # thuismarkt-ticker.
    "TDG": ["GER", "MUN", "FRA", "NMS", "NYQ"], "LSE": ["LSE"], "XLON": ["LSE"],
    "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
}

# Handmatige overrides voor fondsen die yahooquery.search() niet (goed) vindt.
# Overgenomen uit class_degiro.py — vul aan als je nog meer van dit soort
# gevallen tegenkomt (print hieronder waarschuwt je als find_ticker() een
# "blinde" quotes[0]-fallback moet gebruiken, dat is meestal het signaal om
# hier iets aan toe te voegen).
MANUAL_TICKER_OVERRIDES = {
    "VANGUARD S&P 500 UCITS": "VUSA.AS",
    "VANGUARD FTSE ALL-WORLD UCITS": "VWRL.AS",
}

# Handmatige overrides op (ISIN, Beurs) — voor het geval de zoekopdracht al
# een "zeker" resultaat vindt (een kandidaat op een van de verwachte
# beurzen), maar dat toevallig de VERKEERDE notering is. Anders dan
# MANUAL_TICKER_OVERRIDES hierboven (naam-gebaseerd, alleen fallback ná een
# mislukte zoekopdracht — zie find_ticker_detailed) wordt dit VOORAF
# gecheckt en overschrijft het dus ook een "zeker" automatisch resultaat: een
# (ISIN, Beurs)-combinatie is uniek genoeg om dat gerust te doen.
#
# Voorbeeld (BYD, ISIN CNE100000296, beurs "TDG" -> targets GER/MUN/FRA):
# Yahoo's zoekindex vindt voor de productnaam-spelling "BYD COMPANY LIMITED"
# een geldige Frankfurt-notering (4BY1.F) waarvan de koers structureel niet
# aansluit bij de echte DEGIRO-transactieprijzen (~11x te laag) — de juiste
# Münchense notering (BY6.MU) staat wél in Yahoo's index, maar wordt alleen
# gevonden met de spelling "BYD CO LTD"/"BYD Co Ltd". Progressief inkorten
# van "BYD COMPANY LIMITED" (zie _zoek_product_progressief) kan die andere
# spelling niet bereiken — geen woord weglaten maakt er ooit "CO LTD" van —
# en de ISIN-zoekopdracht vindt alleen de Hongkong-notering (niet op een van
# de verwachte Duitse beurzen). Vandaar deze expliciete override.
MANUAL_TICKER_OVERRIDES_ISIN = {
    ("CNE100000296", "TDG"): "BY6.MU",
    # Vanguard FTSE All-World UCITS ETF USD Dis (ISIN IE00B3RBWM25) --
    # yahooquery's naam-gebaseerde zoekopdracht vond hier de VERKEERDE
    # aandelenklasse (VWCE, de accumulerende variant, ISIN LU1737085518)
    # ondanks "Dis" in de productnaam. OpenFIGI's ISIN-lookup bevestigt de
    # juiste ticker-root is VWRL, niet VWCE. Zie opdracht_vwce_correctie.md.
    ("IE00B3RBWM25", "EAM"): "VWRL.AS",
}


def _yahoo_search(query):
    """Wrapper rond yahooquery.search() — geeft altijd een lijst van quotes
    terug (leeg bij een fout), zodat aanroepers geen try/except nodig
    hebben."""
    try:
        _tel_yahoo_call("yahooquery.search")
        return search(query).get("quotes", [])
    except Exception as e:
        dprint(f"[ticker]   query='{query}' faalde: {e}")
        return []


def _kies_beurs_match(quotes, targets):
    """Geeft (symbol, exchange) van de eerste kandidaat op een van de
    'targets'-beurzen, of None als die er niet tussen zit."""
    for exch in targets:
        for q in quotes:
            if q.get("exchange") == exch:
                return q.get("symbol"), exch
    return None


def _onzeker_fallback(quotes):
    """Kiest het eerste resultaat als 'onzeker'-fallback (wel iets
    gevonden, maar niets op de verwachte beurs) — geeft (symbol,
    alternatieven) terug."""
    symbol = quotes[0].get("symbol")
    alternatieven = [{"symbol": q.get("symbol"), "exchange": q.get("exchange")} for q in quotes[1:]]
    return symbol, alternatieven


def _woorden_varianten(product, min_woorden=2):
    """Productnaam-varianten van vol naar ingekort: de volledige naam,
    dan met het laatste woord weggehaald, net zo lang tot 'min_woorden'
    woorden over zijn. Bijv. 'VANECK GOLD MINERS UCITS ETF USD A' (7
    woorden, min_woorden=2) geeft 6 varianten: 7, 6, 5, 4, 3, 2 woorden.
    Heeft de naam al minder dan/gelijk aan 'min_woorden' woorden, dan is er
    niets in te korten en komt er maar 1 variant terug (de naam zelf)."""
    woorden = product.split()
    if len(woorden) <= min_woorden:
        return [product]
    return [" ".join(woorden[:n]) for n in range(len(woorden), min_woorden - 1, -1)]


def _zoek_product_progressief(product, beurs, targets, min_woorden=2):
    """
    Zoekt op de productnaam; levert de volledige naam geen kandidaat op de
    verwachte beurs op, dan wordt de naam PROGRESSIEF ingekort (laatste
    woord eraf, opnieuw zoeken) tot een kandidaat op de juiste beurs
    gevonden wordt, of tot 'min_woorden' bereikt is. Voorkomt dat een fonds
    waarvan Yahoo's zoekindex de volledige naam niet herkent (en dus maar 1,
    verkeerde kandidaat teruggeeft) blind op die ene verkeerde kandidaat
    terechtkomt — bv. 'VANECK GOLD MINERS UCITS ETF USD A' vindt niets op
    de Duitse beurs, maar het ingekorte 'VANECK GOLD MINERS' vindt wel
    VEF5.MU (MUN).

    Stopt zodra een beurs-match gevonden is (geen reden om nog verder in te
    korten). Vindt geen enkele poging een beurs-match, dan valt dit terug op
    het eerste resultaat van de EERSTE poging die iets opleverde (niet per
    se de allereerste/langste poging — die kan zelf 0 resultaten hebben
    gehad, zoals in het voorbeeld hierboven).

    Geeft (symbol, zekerheid, alternatieven) terug, of (None, None, []) als
    geen enkele poging ook maar iets vond.
    """
    varianten = _woorden_varianten(product, min_woorden)
    eerste_quotes, eerste_query = None, None

    for i, variant in enumerate(varianten):
        quotes = _yahoo_search(variant)
        dprint(f"[ticker]   poging {i + 1}/{len(varianten)} ({len(variant.split())} woorden): "
               f"query='{variant}' -> {[(q.get('symbol'), q.get('exchange')) for q in quotes]}")

        if eerste_quotes is None and quotes:
            eerste_quotes, eerste_query = quotes, variant

        match = _kies_beurs_match(quotes, targets)
        if match:
            symbol, exch = match
            dprint(f"[ticker]   ✅ beurs-match ({len(variant.split())} woorden): "
                   f"'{variant}' -> {symbol} ({exch})")
            alternatieven = [
                {"symbol": q.get("symbol"), "exchange": q.get("exchange")}
                for q in quotes if q.get("symbol") != symbol
            ]
            return symbol, "zeker", alternatieven

    if eerste_quotes:
        symbol, alternatieven = _onzeker_fallback(eerste_quotes)
        dprint(f"[ticker]   ⚠️ '{product}': GEEN match voor beurs '{beurs}' (verwacht {targets}) na "
               f"{len(varianten)} poging(en) (progressief ingekort tot {min_woorden} woorden) — "
               f"val terug op eerste resultaat van query '{eerste_query}': "
               f"{symbol} ({eerste_quotes[0].get('exchange')}) — mogelijk fout! "
               f"Alle kandidaten van die zoekopdracht: "
               f"{[(q.get('symbol'), q.get('exchange')) for q in eerste_quotes]}")
        return symbol, "onzeker", alternatieven

    return None, None, []


def find_ticker_detailed(product, isin, beurs):
    """
    Zoekt de Yahoo Finance ticker op basis van productnaam of ISIN, en geeft
    er een zekerheidsindicatie + alternatieven bij terug (voor het
    Instellingen-ticker-overzicht). Bevat verder dezelfde matching-logica als
    de oude find_ticker().

    Geeft een dict terug:
        ticker:        gevonden symbool, of None
        zekerheid:     "zeker"      — handmatige override, of exacte beurs-match
                       "onzeker"    — geen enkele kandidaat op de verwachte beurs,
                                      teruggevallen op het eerste zoekresultaat
                       "geen_match" — helemaal geen kandidaat gevonden
        alternatieven: lijst van {"symbol", "exchange"} van kandidaten die niet
                       gekozen zijn (alleen relevant/gevuld bij "onzeker")
    """
    if _is_corporate_action_row({"beurs": beurs, "product": product}):  # corporate-action rij, geen echt aandeel/ETF
        return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}

    isin_override = MANUAL_TICKER_OVERRIDES_ISIN.get((isin, beurs))
    if isin_override:
        dprint(f"[ticker] ({isin}, {beurs}) -> ISIN-override '{isin_override}' (vóór het zoeken toegepast)")
        return {"ticker": isin_override, "zekerheid": "zeker", "alternatieven": []}

    targets = BEURS_MAP.get(beurs, [])

    # Productnaam: progressief inkorten bij een mislukte beurs-match (zie
    # _zoek_product_progressief hierboven). ISIN: één enkele zoekopdracht —
    # een ISIN heeft geen 'woorden' om weg te laten.
    kandidaten = [_zoek_product_progressief(product, beurs, targets)]

    # Alleen de ISIN erbij proberen als de productnaam nog geen "zeker"
    # resultaat opleverde — kan toch niet beter worden, en scheelt een
    # yahooquery-call (rate limiting is een bekend pijnpunt in dit project).
    # Zelfde volgorde-onafhankelijke voorrangsregel als voorheen: "zeker"
    # wint altijd van "onzeker", ongeacht welke van de twee het vond — zo
    # kan bv. een ISIN-zoekopdracht alsnog de juiste Europese notering
    # vinden als de productnaam alleen een Amerikaanse ADR oplevert.
    if kandidaten[0][1] != "zeker":
        isin_quotes = _yahoo_search(isin)
        isin_match = _kies_beurs_match(isin_quotes, targets)
        if isin_match:
            symbol, exch = isin_match
            dprint(f"[ticker]   query='{isin}': exact beurs-match {symbol} ({exch})")
            alternatieven = [
                {"symbol": q.get("symbol"), "exchange": q.get("exchange")}
                for q in isin_quotes if q.get("symbol") != symbol
            ]
            kandidaten.append((symbol, "zeker", alternatieven))
        elif isin_quotes:
            symbol, alternatieven = _onzeker_fallback(isin_quotes)
            dprint(f"[ticker]   ⚠️ query='{isin}': GEEN match voor beurs '{beurs}' (verwacht {targets}), "
                   f"val terug op eerste resultaat {symbol} ({isin_quotes[0].get('exchange')}) — "
                   f"mogelijk fout! Alle kandidaten: "
                   f"{[(q.get('symbol'), q.get('exchange')) for q in isin_quotes]}")
            kandidaten.append((symbol, "onzeker", alternatieven))

    beste = None  # (symbol, zekerheid, alternatieven)
    for symbol, zekerheid, alternatieven in kandidaten:
        if symbol is None:
            continue
        if beste is None or (zekerheid == "zeker" and beste[1] != "zeker"):
            beste = (symbol, zekerheid, alternatieven)

    if beste is not None and beste[1] == "zeker":
        symbol, zekerheid, alternatieven = beste
        return {"ticker": symbol, "zekerheid": zekerheid, "alternatieven": alternatieven}

    # Zoeken gaf geen exacte beurs-match — nu pas de handmatige overrides
    # checken (fondsen die yahooquery.search() structureel niet goed vindt,
    # zoals VUSA.AS op Amsterdam). Bewust NA het zoeken i.p.v. ervoor: een
    # override is fonds-specifiek maar niet beurs-specifiek, dus mag een
    # écht gevonden exacte match op de juiste beurs (bv. VUSD.L op LSE voor
    # dezelfde ISIN, een andere notering van hetzelfde fonds) niet
    # overschrijven met een override die voor een ándere beurs bedoeld was.
    for key, override_ticker in MANUAL_TICKER_OVERRIDES.items():
        if product.upper().startswith(key):
            dprint(f"[ticker] '{product}' -> override '{override_ticker}' (geen exacte beurs-match via search)")
            return {"ticker": override_ticker, "zekerheid": "zeker", "alternatieven": []}

    if beste is not None:
        symbol, zekerheid, alternatieven = beste
        return {"ticker": symbol, "zekerheid": zekerheid, "alternatieven": alternatieven}

    # print(f"[ticker] ❌ GEEN ticker gevonden voor '{product}' (ISIN={isin}, beurs={beurs})")
    return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}


def find_ticker(product, isin, beurs):
    """Backwards-compatible wrapper rond find_ticker_detailed() die alleen de ticker teruggeeft."""
    return find_ticker_detailed(product, isin, beurs)["ticker"]


# EXPERIMENTEEL/DIAGNOSTISCH — haal_openfigi_resultaten() hieronder is een
# los, extra paneel op de Ticker-zekerheid-pagina om te beoordelen of
# OpenFIGI bruikbare/betere matches geeft dan de bestaande yahooquery-
# aanpak. Verandert NIETS aan find_ticker_detailed() of aan welke ticker
# daadwerkelijk gebruikt/opgeslagen wordt.
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY")  # optioneel, mag None zijn


def haal_openfigi_resultaten(isin):
    """
    EXPERIMENTEEL — puur diagnostisch. Haalt bij OpenFIGI alle bekende
    beursnoteringen op voor een ISIN, voor weergave op de
    Ticker-zekerheid-pagina naast de bestaande (yahooquery-gebaseerde)
    ticker-resolutie. Verandert niets aan find_ticker_detailed() of aan
    welke ticker daadwerkelijk gebruikt/opgeslagen wordt.

    Geeft terug: {"resultaten": [...], "fout": None} bij succes, of
    {"resultaten": [], "fout": "<boodschap>"} bij een mislukte aanroep of
    "geen match". Elk element in 'resultaten' is een dict met de velden
    ticker, exchCode, naam, securityType, marketSector, compositeFIGI —
    rechtstreeks van OpenFIGI, ongefilterd (ook noteringen op beurzen die
    niet in BEURS_MAP voorkomen worden getoond, juist om te zien of
    OpenFIGI meer/andere beurzen kent dan verwacht).

    Gebruikt een permanente DB-cache (openfigi_cache) -- een ISIN->ticker-
    mapping verandert vrijwel nooit, dus bij een cache-hit geen externe call.
    Een "geen match" wordt ook gecached (als lege lijst) -- dat is net zo
    stabiel als een positieve match. Fouten/rate-limits worden NIET
    gecached, zodat een volgende poging opnieuw geprobeerd wordt.
    """
    if not isin:
        return {"resultaten": [], "fout": "Geen ISIN beschikbaar voor deze positie."}

    gecached = get_cached_openfigi(isin)
    if gecached is not None:
        return {"resultaten": gecached, "fout": None}

    headers = {"Content-Type": "application/json"}
    if OPENFIGI_API_KEY:
        headers["X-OPENFIGI-APIKEY"] = OPENFIGI_API_KEY

    try:
        response = requests.post(
            "https://api.openfigi.com/v3/mapping",
            json=[{"idType": "ID_ISIN", "idValue": isin}],
            headers=headers,
            timeout=10,
        )
    except requests.exceptions.RequestException as e:
        dprint(f"[openfigi] netwerkfout voor ISIN={isin}: {e}")
        return {"resultaten": [], "fout": f"OpenFIGI niet bereikbaar: {e}"}

    if response.status_code == 429:
        return {"resultaten": [], "fout": "OpenFIGI rate limit bereikt — probeer straks opnieuw."}
    if response.status_code != 200:
        dprint(f"[openfigi] status {response.status_code} voor ISIN={isin}: {response.text[:200]}")
        return {"resultaten": [], "fout": f"OpenFIGI gaf status {response.status_code} terug."}

    body = response.json()
    if not body or "data" not in body[0]:
        waarschuwing = (body[0].get("warning") if body else None) or "Geen match bij OpenFIGI."
        save_openfigi(isin, [])
        return {"resultaten": [], "fout": waarschuwing}

    resultaten = [
        {
            "ticker": item.get("ticker"),
            "exchCode": item.get("exchCode"),
            "naam": item.get("name"),
            "securityType": item.get("securityType"),
            "marketSector": item.get("marketSector"),
            "compositeFIGI": item.get("compositeFIGI"),
        }
        for item in body[0]["data"]
    ]
    save_openfigi(isin, resultaten)
    return {"resultaten": resultaten, "fout": None}


def _openfigi_root_matches(ticker, openfigi_resultaten):
    """
    Telt hoeveel van OpenFIGI's resultaten voor deze ISIN de ROOT van
    'ticker' matchen (zonder Yahoo-beurssuffix, bv. 'BY6' uit 'BY6.MU') --
    ongeacht beurs (Bloomberg's exchCode-namen mappen niet 1-op-1 naar
    Yahoo-suffixen, dus alleen op root-niveau vergelijken, niet per beurs).

    Geeft None terug als er niets te vergelijken valt (geen ticker, of
    OpenFIGI had geen resultaten) -- dat betekent NIET "onbekend/fout", puur
    "geen oordeel mogelijk". Anders een int (0 = root niet gevonden, gebruikt
    door _openfigi_root_bekend() en de samenvattingsregel op de
    Ticker-zekerheid-pagina).
    """
    if not ticker or not openfigi_resultaten:
        return None
    root = ticker.split(".")[0].upper()
    figi_tickers = [r["ticker"].upper() for r in openfigi_resultaten if r.get("ticker")]
    # Sommige OpenFIGI-tickers hebben een valuta-/varianten-suffix
    # (bv. '1211HKD', 'VUSACHF') -- een prefix-match voorkomt dat zulke
    # varianten ten onrechte als "root niet gevonden" gelden.
    return sum(1 for t in figi_tickers if root == t or t.startswith(root))


def _openfigi_root_bekend(ticker, openfigi_resultaten):
    """
    Of de ROOT van 'ticker' voorkomt tussen OpenFIGI's resultaten voor deze
    ISIN (zie _openfigi_root_matches() voor de matchregel). None als er
    niets te vergelijken valt -- moet door de aanroeper als "geen oordeel
    mogelijk" behandeld worden, niet als een negatief signaal.
    """
    matches = _openfigi_root_matches(ticker, openfigi_resultaten)
    return None if matches is None else matches > 0
