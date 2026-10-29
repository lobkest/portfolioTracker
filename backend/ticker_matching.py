"""Welke Yahoo-ticker hoort bij een DeGiro-positie (zoeken, overrides), plus OpenFIGI als extra signaal."""
import os
import threading

import requests
# Interne functie: search() zelf neemt geen sessie aan (yahooquery staat vast op 2.4.1 in requirements.txt).
from yahooquery.misc import _make_request
from yahooquery.session_management import initialize_session

from backend.db import db_get_cached_openfigi, db_get_ticker_details, db_save_openfigi
from backend.debug_utils import dprint
from backend.transactie_utils import _is_corporate_action_row
from backend.yahoo_client import _tel_yahoo_call

BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    # Tradegate verhandelt ook Amerikaanse aandelen die Yahoo alleen op de thuismarkt kent;
    # NMS/NYQ achteraan zodat een Duitse notering voorgaat.
    "TDG": ["GER", "MUN", "FRA", "NMS", "NYQ"], "LSE": ["LSE"], "XLON": ["LSE"],
    # Yahoo splitst Nasdaq in Global Select (NMS), Global Market (NGM) en Capital Market (NCM).
    "NYSE": ["NYQ"], "NSY": ["NYQ"], "NASDAQ": ["NMS", "NGM", "NCM"], "NDQ": ["NMS", "NGM", "NCM"],
    "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"],
}


def degiro_codes_voor_yahoo_beurs(yahoo_beurs):
    """Alle DeGiro-beurscodes die op deze Yahoo-beurs uitkomen; leeg bij een onbekende beurs."""
    return {code for code, beurzen in BEURS_MAP.items() if yahoo_beurs in beurzen}

# Handelsvaluta per Yahoo-beurscode, om bij het zoeken kandidaten uit te sluiten die niet bij de valuta van de Excel
# passen. Een bekende ticker_info.valuta gaat voor; een onbekende beurs doet niet mee.
EXCHANGE_VALUTA = {
    "NMS": "USD", "NGM": "USD", "NCM": "USD", "NYQ": "USD", "PCX": "USD", "ASE": "USD",
    "GER": "EUR", "FRA": "EUR", "MUN": "EUR", "STU": "EUR", "DUS": "EUR", "HAM": "EUR", "BER": "EUR", "HAN": "EUR",
    "AMS": "EUR", "PAR": "EUR", "BRU": "EUR", "MIL": "EUR", "MCE": "EUR", "HEL": "EUR", "LIS": "EUR", "VIE": "EUR",
    "LSE": "GBP", "SWX": "CHF", "TOR": "CAD", "ASX": "AUD",
}
RECHTSVORMEN = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "NV", "SA", "AG", "SE", "LLC",
                "LP", "SPA", "OYJ", "AB", "ASA"}

# Bewust niet in BEURS_MAP: dan kiest het zoeken OTC-noteringen (BBRYF) voor aandelen die gewoon op de beurs staan.
AMERIKAANSE_BEURZEN = {"NDQ", "NSY", "NASDAQ", "NYSE"}
OTC_BEURZEN = {"PNK", "OQB", "OQX"}

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


YAHOO_ZOEK_URL = "https://query2.finance.yahoo.com/v1/finance/search"
# Per thread één sessie: het opzetten (cookies) kost ~0,4 s per keer en curl-sessies zijn niet thread-safe.
_zoek_sessies = threading.local()


def _zoek_sessie(vernieuw=False):
    if vernieuw or getattr(_zoek_sessies, "sessie", None) is None:
        _zoek_sessies.sessie = initialize_session()
    return _zoek_sessies.sessie


def _zoek_met_sessie(query):
    def _verzoek(sessie):
        params = {"q": query, "quotes_count": 10, "news_count": 10}
        return _make_request(YAHOO_ZOEK_URL, country="United States", params=params, session=sessie)

    try:
        data = _verzoek(_zoek_sessie())
    except Exception:
        # Een verlopen sessie: één keer opnieuw met een verse.
        data = _verzoek(_zoek_sessie(vernieuw=True))
    return data.get("quotes", [])


def _yahoo_search(query):
    """Altijd een lijst; leeg bij een fout (niet te onderscheiden van 'niets gevonden')."""
    try:
        _tel_yahoo_call("yahooquery.search")
        return _zoek_met_sessie(query)
    except Exception as e:
        dprint(f"[ticker]   query='{query}' faalde: {e}")
        return []


def _valuta_botst(valuta, in_eur):
    """in_eur: DeGiro rekende in EUR (geen Wisselkoers) of juist niet; None = onbekend. Onbekende valuta botst niet."""
    if in_eur is None or not valuta:
        return False
    return valuta != "EUR" if in_eur else valuta == "EUR"


def botsende_symbolen(quotes, in_eur):
    """Symbolen waarvan de valuta niet past bij de Excel. ticker_info.valuta gaat voor de beurscode; een databasefout
    laat alleen de beurscode over."""
    if in_eur is None or not quotes:
        return frozenset()
    symbolen = [q.get("symbol") for q in quotes if q.get("symbol")]
    try:
        bekend = db_get_ticker_details(symbolen)
    except Exception:
        bekend = {}
    return frozenset(
        q.get("symbol") for q in quotes
        if _valuta_botst((bekend.get(q.get("symbol")) or {}).get("valuta") or EXCHANGE_VALUTA.get(q.get("exchange")),
                         in_eur)
    )


def _kies_beurs_match(quotes, targets, botsend=frozenset()):
    for exch in targets:
        for q in quotes:
            if q.get("exchange") == exch and q.get("symbol") not in botsend:
                return q.get("symbol"), exch
    return None


def _onzeker_fallback(quotes, botsend=frozenset()):
    eerste = next((q for q in quotes if q.get("symbol") not in botsend), quotes[0])
    alternatieven = [{"symbol": q.get("symbol"), "exchange": q.get("exchange")} for q in quotes if q is not eerste]
    return eerste.get("symbol"), alternatieven


def zoeknaam_zonder_rechtsvorm(product):
    """'NETFLIX, INC.' -> 'NETFLIX': Yahoo's zoekresultaten hangen af van de exacte spelling. Leeg als er niets verandert."""
    woorden = [w.replace(".", "") for w in product.replace(",", " ").split()]
    while len(woorden) > 1 and woorden[-1].upper() in RECHTSVORMEN:
        woorden.pop()
    naam = " ".join(woorden)
    return naam if naam != product else ""


def _woorden_varianten(product, min_woorden=2):
    """Van de volledige naam naar steeds één woord korter, tot min_woorden."""
    woorden = product.split()
    if len(woorden) <= min_woorden:
        return [product]
    return [" ".join(woorden[:n]) for n in range(len(woorden), min_woorden - 1, -1)]


def _noteer_stap(stappen, query, quotes, match):
    if stappen is not None:
        stappen.append({
            "query": query,
            "resultaten": [[q.get("symbol"), q.get("exchange")] for q in quotes],
            "beurs_match": match[0] if match else None,
        })


def _zoek_product_progressief(product, beurs, targets, min_woorden=2, stappen=None, in_eur=None):
    """Kort de naam in tot er een match op de verwachte beurs is; anders het eerste resultaat
    van de eerste poging die iets vond. Geeft (symbol, zekerheid, alternatieven).
    stappen: optionele lijst waar elke zoekopdracht aan wordt toegevoegd (voor de Diagnostiek)."""
    varianten = _woorden_varianten(product, min_woorden)
    kaal = zoeknaam_zonder_rechtsvorm(product)
    if kaal and kaal not in varianten:
        varianten.append(kaal)
    eerste_quotes = None
    botsend_totaal = set()

    for variant in varianten:
        quotes = _yahoo_search(variant)
        botsend = botsende_symbolen(quotes, in_eur)
        botsend_totaal |= botsend
        match = _kies_beurs_match(quotes, targets, botsend)
        _noteer_stap(stappen, variant, quotes, match)

        if eerste_quotes is None and quotes:
            eerste_quotes = quotes

        if match:
            symbol, _exch = match
            return symbol, "zeker", _alternatieven_naast(quotes, symbol)

    if eerste_quotes:
        symbol, alternatieven = _onzeker_fallback(eerste_quotes, botsend_totaal)
        return symbol, "onzeker", alternatieven

    return None, None, []


def _alternatieven_naast(quotes, symbol):
    return [
        {"symbol": q.get("symbol"), "exchange": q.get("exchange")}
        for q in quotes if q.get("symbol") != symbol
    ]


def _zoek_op_isin(isin, targets, beurs, stappen=None, in_eur=None):
    """Zelfde uitkomst als _zoek_product_progressief(): (symbol, zekerheid, alternatieven)."""
    quotes = _yahoo_search(isin)
    botsend = botsende_symbolen(quotes, in_eur)
    match = _kies_beurs_match(quotes, targets, botsend)
    _noteer_stap(stappen, isin, quotes, match)
    if match:
        symbol, _exch = match
        return symbol, "zeker", _alternatieven_naast(quotes, symbol)
    if quotes:
        symbol, alternatieven = _onzeker_fallback(quotes, botsend)
        return symbol, "onzeker", alternatieven
    return None, None, []


def _kies_beste(eerste, tweede):
    """Een "zekere" kandidaat wint altijd van een onzekere; verder gaat de eerste voor."""
    if eerste[0] is None:
        return tweede
    if tweede[0] is not None and tweede[1] == "zeker" and eerste[1] != "zeker":
        return tweede
    return eerste


def _naam_override(product):
    for key, override_ticker in MANUAL_TICKER_OVERRIDES.items():
        if product.upper().startswith(key):
            return override_ticker
    return None


def _als_resultaat(kandidaat):
    symbol, zekerheid, alternatieven = kandidaat
    if symbol is None:
        return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}
    return {"ticker": symbol, "zekerheid": zekerheid, "alternatieven": alternatieven}


def find_ticker_detailed(product, isin, beurs, in_eur=None):
    """{ticker, zekerheid, alternatieven, zoekstappen}. zekerheid: "zeker" (override of beurs-match),
    "onzeker" (eerste zoekresultaat) of "geen_match". zoekstappen ([{query, resultaten, beurs_match}])
    alleen als er bij Yahoo gezocht is. in_eur: DeGiro rekende in EUR (geen Wisselkoers), True/False; een kandidaat
    met een andere valuta is dan nooit "zeker"."""
    if _is_corporate_action_row({"beurs": beurs, "product": product}):
        return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}

    isin_override = MANUAL_TICKER_OVERRIDES_ISIN.get((isin, beurs))
    if isin_override:
        dprint(f"[ticker] ({isin}, {beurs}) -> ISIN-override '{isin_override}' (voor het zoeken toegepast)")
        return {"ticker": isin_override, "zekerheid": "zeker", "alternatieven": []}

    targets = BEURS_MAP.get(beurs, [])
    stappen = []
    kandidaat = _zoek_product_progressief(product, beurs, targets, stappen=stappen, in_eur=in_eur)

    # ISIN alleen als de naam nog niet "zeker" gaf (scheelt een call).
    if kandidaat[1] != "zeker":
        kandidaat = _kies_beste(kandidaat, _zoek_op_isin(isin, targets, beurs, stappen=stappen, in_eur=in_eur))

    # Pas ná het zoeken: een naam-override is niet beurs-specifiek.
    if kandidaat[1] != "zeker":
        override_ticker = _naam_override(product)
        if override_ticker:
            stappen.append({"query": "handmatige override op naam", "resultaten": [[override_ticker, None]],
                            "beurs_match": override_ticker})
            return {"ticker": override_ticker, "zekerheid": "zeker", "alternatieven": [], "zoekstappen": stappen}

    return {**_als_resultaat(kandidaat), "zoekstappen": stappen}


# OpenFIGI verandert nooit welke ticker wordt opgeslagen (zie CLAUDE.md: Yahoo en tickers).
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY")  # optioneel, mag None zijn


def haal_openfigi_resultaten(isin):
    """{resultaten: [{ticker, exchCode, naam, securityType, marketSector, compositeFIGI}], fout}.
    Permanent gecachet, ook 'geen match'; fouten niet."""
    if not isin:
        return {"resultaten": [], "fout": "Geen ISIN beschikbaar voor deze positie."}

    gecached = db_get_cached_openfigi(isin)
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
        return {"resultaten": [], "fout": f"OpenFIGI niet bereikbaar: {e}"}

    if response.status_code == 429:
        return {"resultaten": [], "fout": "OpenFIGI rate limit bereikt — probeer straks opnieuw."}
    if response.status_code != 200:
        dprint(f"[openfigi] status {response.status_code} voor ISIN={isin}: {response.text[:200]}")
        return {"resultaten": [], "fout": f"OpenFIGI gaf status {response.status_code} terug."}

    body = response.json()
    if not body or "data" not in body[0]:
        waarschuwing = (body[0].get("warning") if body else None) or "Geen match bij OpenFIGI."
        db_save_openfigi(isin, [])
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
    db_save_openfigi(isin, resultaten)
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
