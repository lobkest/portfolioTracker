"""Welke Yahoo-ticker hoort bij een DeGiro-positie (zoeken, overrides), plus OpenFIGI als extra signaal."""
import os

import requests
from yahooquery import search

from db import get_cached_openfigi, save_openfigi
from debug_utils import dprint
from transactie_utils import _is_corporate_action_row
from yahoo_client import _tel_yahoo_call

BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    # Tradegate verhandelt ook Amerikaanse aandelen die Yahoo alleen op de thuismarkt kent;
    # NMS/NYQ achteraan zodat een Duitse notering voorgaat.
    "TDG": ["GER", "MUN", "FRA", "NMS", "NYQ"], "LSE": ["LSE"], "XLON": ["LSE"],
    "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
}

# Op naam, alleen als fallback ná het zoeken (zie CLAUDE.md: Yahoo en tickers).
MANUAL_TICKER_OVERRIDES = {
    "VANGUARD S&P 500 UCITS": "VUSA.AS",
    "VANGUARD FTSE ALL-WORLD UCITS": "VWRL.AS",
}

# Vóór het zoeken gecheckt; mag een "zeker" resultaat overschrijven (zie CLAUDE.md: Yahoo en tickers).
MANUAL_TICKER_OVERRIDES_ISIN = {
    # Zoeken vindt alleen 4BY1.F (koers ~11x te laag).
    ("CNE100000296", "TDG"): "BY6.MU",
    # Zoeken vond VWCE (Acc) ondanks "Dis" in de naam; OpenFIGI bevestigt VWRL.
    ("IE00B3RBWM25", "EAM"): "VWRL.AS",
}


def _yahoo_search(query):
    """Altijd een lijst; leeg bij een fout (niet te onderscheiden van 'niets gevonden')."""
    try:
        _tel_yahoo_call("yahooquery.search")
        return search(query).get("quotes", [])
    except Exception as e:
        dprint(f"[ticker]   query='{query}' faalde: {e}")
        return []


def _kies_beurs_match(quotes, targets):
    for exch in targets:
        for q in quotes:
            if q.get("exchange") == exch:
                return q.get("symbol"), exch
    return None


def _onzeker_fallback(quotes):
    symbol = quotes[0].get("symbol")
    alternatieven = [{"symbol": q.get("symbol"), "exchange": q.get("exchange")} for q in quotes[1:]]
    return symbol, alternatieven


def _woorden_varianten(product, min_woorden=2):
    """Van de volledige naam naar steeds één woord korter, tot min_woorden."""
    woorden = product.split()
    if len(woorden) <= min_woorden:
        return [product]
    return [" ".join(woorden[:n]) for n in range(len(woorden), min_woorden - 1, -1)]


def _zoek_product_progressief(product, beurs, targets, min_woorden=2):
    """Kort de naam in tot er een match op de verwachte beurs is; anders het eerste resultaat
    van de eerste poging die iets vond. Geeft (symbol, zekerheid, alternatieven)."""
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
            dprint(f"[ticker]   OK beurs-match ({len(variant.split())} woorden): "
                   f"'{variant}' -> {symbol} ({exch})")
            alternatieven = [
                {"symbol": q.get("symbol"), "exchange": q.get("exchange")}
                for q in quotes if q.get("symbol") != symbol
            ]
            return symbol, "zeker", alternatieven

    if eerste_quotes:
        symbol, alternatieven = _onzeker_fallback(eerste_quotes)
        dprint(f"[ticker]   WARN '{product}': GEEN match voor beurs '{beurs}' (verwacht {targets}) na "
               f"{len(varianten)} poging(en) (progressief ingekort tot {min_woorden} woorden) - "
               f"val terug op eerste resultaat van query '{eerste_query}': "
               f"{symbol} ({eerste_quotes[0].get('exchange')}) - mogelijk fout! "
               f"Alle kandidaten van die zoekopdracht: "
               f"{[(q.get('symbol'), q.get('exchange')) for q in eerste_quotes]}")
        return symbol, "onzeker", alternatieven

    return None, None, []


def find_ticker_detailed(product, isin, beurs):
    """{ticker, zekerheid, alternatieven}. zekerheid: "zeker" (override of beurs-match),
    "onzeker" (eerste zoekresultaat) of "geen_match"."""
    if _is_corporate_action_row({"beurs": beurs, "product": product}):
        return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}

    isin_override = MANUAL_TICKER_OVERRIDES_ISIN.get((isin, beurs))
    if isin_override:
        dprint(f"[ticker] ({isin}, {beurs}) -> ISIN-override '{isin_override}' (voor het zoeken toegepast)")
        return {"ticker": isin_override, "zekerheid": "zeker", "alternatieven": []}

    targets = BEURS_MAP.get(beurs, [])

    kandidaten = [_zoek_product_progressief(product, beurs, targets)]

    # ISIN alleen als de naam nog niet "zeker" gaf (scheelt een call); "zeker" wint altijd.
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
            dprint(f"[ticker]   WARN query='{isin}': GEEN match voor beurs '{beurs}' (verwacht {targets}), "
                   f"val terug op eerste resultaat {symbol} ({isin_quotes[0].get('exchange')}) - "
                   f"mogelijk fout! Alle kandidaten: "
                   f"{[(q.get('symbol'), q.get('exchange')) for q in isin_quotes]}")
            kandidaten.append((symbol, "onzeker", alternatieven))

    beste = None
    for symbol, zekerheid, alternatieven in kandidaten:
        if symbol is None:
            continue
        if beste is None or (zekerheid == "zeker" and beste[1] != "zeker"):
            beste = (symbol, zekerheid, alternatieven)

    if beste is not None and beste[1] == "zeker":
        symbol, zekerheid, alternatieven = beste
        return {"ticker": symbol, "zekerheid": zekerheid, "alternatieven": alternatieven}

    # Pas ná het zoeken: een naam-override is niet beurs-specifiek.
    for key, override_ticker in MANUAL_TICKER_OVERRIDES.items():
        if product.upper().startswith(key):
            dprint(f"[ticker] '{product}' -> override '{override_ticker}' (geen exacte beurs-match via search)")
            return {"ticker": override_ticker, "zekerheid": "zeker", "alternatieven": []}

    if beste is not None:
        symbol, zekerheid, alternatieven = beste
        return {"ticker": symbol, "zekerheid": zekerheid, "alternatieven": alternatieven}

    return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}


# OpenFIGI verandert nooit welke ticker wordt opgeslagen (zie CLAUDE.md: Yahoo en tickers).
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY")  # optioneel, mag None zijn


def haal_openfigi_resultaten(isin):
    """{resultaten: [{ticker, exchCode, naam, securityType, marketSector, compositeFIGI}], fout}.
    Permanent gecachet, ook 'geen match'; fouten niet."""
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
    """Aantal OpenFIGI-resultaten met dezelfde ticker-root ('BY6' uit 'BY6.MU'); None = geen oordeel mogelijk.
    Alleen op root: Bloomberg-beurscodes mappen niet 1-op-1 op Yahoo-suffixen."""
    if not ticker or not openfigi_resultaten:
        return None
    root = ticker.split(".")[0].upper()
    figi_tickers = [r["ticker"].upper() for r in openfigi_resultaten if r.get("ticker")]
    # Prefix-match: sommige OpenFIGI-tickers hebben een suffix ('1211HKD', 'VUSACHF').
    return sum(1 for t in figi_tickers if root == t or t.startswith(root))
