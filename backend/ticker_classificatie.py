"""ETF of aandeel, plus land/sector/holdings per ticker (met database-cache)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf
from yahooquery import Ticker as YahooqueryTicker

from backend.db import (
    db_save_classification, db_get_cached_land_sector, db_save_land_sector,
    db_get_cached_etf_sector_verdeling, db_save_etf_sector_verdeling, db_get_cached_etf_holdings, db_save_etf_holdings,
    db_get_ticker_details, db_save_long_names, db_get_ishares_fondsen, db_save_ishares_fondsen,
)
from backend.debug_utils import dprint
from backend.yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from backend.etf_holdings_provider import (
    ETF_HOLDINGS_BRON, fetch_provider_holdings, fetch_ishares_fondsenlijst, fetch_ishares_holdings_via_productpagina,
)


def _fetch_yf_info(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """yf.Ticker(ticker).info met retry; None bij een definitieve fout."""
    def _actie():
        _tel_yahoo_call("yf.Ticker.info")
        return yf.Ticker(ticker).info

    info, fout = _met_rate_limit_retry(_actie, pogingen, wachttijd)
    if fout is not None:
        return None
    return info


def haal_long_names(tickers):
    """{ticker: Yahoo longName of None}, in één batch-call; bij een mislukte call None voor alle tickers."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}

    def _actie():
        _tel_yahoo_call("yahooquery.Ticker.price")
        return YahooqueryTicker(tickers).price

    price, fout = _met_rate_limit_retry(_actie)
    if fout is not None or not isinstance(price, dict):
        return {t: None for t in tickers}

    long_names = {}
    for t in tickers:
        regel = _price_regel(price, t)
        naam = regel.get("longName") if regel else None
        long_names[t] = naam.strip() if isinstance(naam, str) and naam.strip() else None
    return long_names


def _price_regel(price, ticker):
    """De dict van één ticker uit yahooquery's .price; None bij een foutstring of onbekende ticker."""
    regel = price.get(ticker) if isinstance(price, dict) else None
    if not isinstance(regel, dict) and isinstance(price, dict):
        regel = price.get(ticker.upper())
    return regel if isinstance(regel, dict) else None


def bewaar_long_names(long_names):
    """Naar ticker_info.long_name (alleen bestaande rijen); een fout breekt de aanroeper nooit."""
    try:
        db_save_long_names(long_names)
    except Exception as e:
        print(f"[classify] WARN long_name niet opgeslagen ({e!a})")


def _rij_is_vers(details):
    """Wanneer een ticker_info-rij als cache-hit telt; long_name heeft een eigen call (vul_ontbrekende_long_names)."""
    return bool(details) and bool(details.get("valuta") or details.get("quote_type"))


def vul_ontbrekende_long_names(tickers):
    """Eén batch-call voor tickers met een ticker_info-rij zonder long_name; mag de aanroeper nooit breken."""
    try:
        details = db_get_ticker_details(tickers)
        ontbrekend = [t for t in tickers if t in details and not details[t].get("long_name")]
        if not ontbrekend:
            return
        long_names = haal_long_names(ontbrekend)
        bewaar_long_names(long_names)
        gevonden = sum(1 for naam in long_names.values() if naam)
        print(f"[classify] long_name aangevuld: {gevonden} van {len(ontbrekend)} ontbrekend")
    except Exception as e:
        print(f"[classify] WARN long_names aanvullen mislukt ({e!a})")


def _yq_fonds_data(ticker, eigenschap, teller):
    """Eigenschap van yahooquery.Ticker met retry; None bij een fout, ook als yahooquery een string of dict
    teruggeeft i.p.v. een exceptie te gooien."""
    def _actie():
        _tel_yahoo_call(teller)
        return getattr(YahooqueryTicker(ticker), eigenschap)

    data, fout = _met_rate_limit_retry(_actie, pogingen=1)
    if fout is not None:
        dprint(f"[yahooquery] '{ticker}': {eigenschap} mislukt: {fout}")
        return None
    return data


def _yq_price(ticker):
    """De .price-dict van één ticker uit yahooquery; None bij een fout of foutstring."""
    regel = _price_regel(_yq_fonds_data(ticker, "price", "yahooquery.Ticker.price"), ticker)
    if regel is None:
        dprint(f"[yahooquery] '{ticker}': geen bruikbare price")
    return regel


def _yq_sector_verdeling(ticker):
    """{sector_key: fractie} uit yahooquery; {} bij een lege respons, None bij een fout of foutstring."""
    df = _yq_fonds_data(ticker, "fund_sector_weightings", "yahooquery.Ticker.fund_sector_weightings")
    if not isinstance(df, pd.DataFrame):
        dprint(f"[yahooquery] '{ticker}': geen sector_weightings ({df!r:.80})")
        return None
    if df.empty or ticker not in df.columns:
        dprint(f"[yahooquery] '{ticker}': geen sector_weightings ({df!r:.80})")
        return {}
    return {sector_key: float(gewicht) for sector_key, gewicht in df[ticker].items() if pd.notna(gewicht)}


def _yq_top_holdings(ticker):
    """[(holding_ticker, naam, fractie)] uit yahooquery; [] bij een lege respons, None bij een fout of foutstring."""
    df = _yq_fonds_data(ticker, "fund_top_holdings", "yahooquery.Ticker.fund_top_holdings")
    if not isinstance(df, pd.DataFrame):
        dprint(f"[yahooquery] '{ticker}': geen top_holdings ({df!r:.80})")
        return None
    if df.empty or not {"symbol", "holdingName", "holdingPercent"} <= set(df.columns):
        dprint(f"[yahooquery] '{ticker}': geen top_holdings ({df!r:.80})")
        return []
    return [(r["symbol"], r["holdingName"], float(r["holdingPercent"])) for _, r in df.iterrows()
            if pd.notna(r["holdingPercent"])]


def _yq_categorie(ticker):
    """categoryName uit fund_profile; "" als het profiel er geen heeft, None bij een fout of foutstring."""
    profiel = _yq_fonds_data(ticker, "fund_profile", "yahooquery.Ticker.fund_profile")
    regel = profiel.get(ticker) if isinstance(profiel, dict) else None
    if not isinstance(regel, dict):
        dprint(f"[yahooquery] '{ticker}': geen categoryName in fund_profile")
        return None
    naam = regel.get("categoryName")
    if not naam:
        dprint(f"[yahooquery] '{ticker}': geen categoryName in fund_profile")
    return naam or ""


ONDERDEEL_SECTOR = "sector"
ONDERDEEL_HOLDINGS = "holdings"
ONDERDEEL_CATEGORIE = "categorie"
GEEN_FONDSDATA_CACHE_SECONDEN = 3600
GEEN_FONDSDATA_CACHE_MAX_ITEMS = 500
_geen_fondsdata_cache = {}
_geen_fondsdata_lock = threading.Lock()


def _geen_fondsdata_bekend(ticker, onderdeel):
    """True als yahooquery en yfinance voor dit onderdeel net nog geen data gaven (alleen geheugen, geen Neon)."""
    with _geen_fondsdata_lock:
        sinds = _geen_fondsdata_cache.get((ticker, onderdeel))
    if sinds is None or time.monotonic() - sinds >= GEEN_FONDSDATA_CACHE_SECONDEN:
        return False
    dprint(f"[fondsdata] '{ticker}': {onderdeel} recent leeg bevonden, Yahoo overgeslagen")
    return True


def _onthoud_geen_fondsdata(ticker, onderdeel):
    sleutel = (ticker, onderdeel)
    with _geen_fondsdata_lock:
        if sleutel not in _geen_fondsdata_cache and len(_geen_fondsdata_cache) >= GEEN_FONDSDATA_CACHE_MAX_ITEMS:
            del _geen_fondsdata_cache[min(_geen_fondsdata_cache, key=_geen_fondsdata_cache.get)]
        _geen_fondsdata_cache[sleutel] = time.monotonic()


def wis_geen_fondsdata_cache():
    with _geen_fondsdata_lock:
        _geen_fondsdata_cache.clear()


def _yq_was_leeg(resultaat):
    """Een lege uitkomst van yahooquery, anders dan een fout (None): alleen die mag onthouden worden."""
    return resultaat is not None and not resultaat


def info_is_bruikbaar(info):
    """False voor None en voor de uitgeklede dict die .info bij een 401 (Invalid Crumb) teruggeeft."""
    return isinstance(info, dict) and bool(info.get("quoteType") or info.get("currency"))


def _zoek_categorie(ticker, categorie_uit_info):
    """info["category"] ontbreekt bij UCITS-ETF's en mutual funds; yahooquery is de fallback, daarna fund_overview
    (yfinance's funds_data faalt op Render met 401)."""
    if categorie_uit_info or _geen_fondsdata_bekend(ticker, ONDERDEEL_CATEGORIE):
        return categorie_uit_info or None
    yq_categorie = _yq_categorie(ticker)
    category = yq_categorie
    if not category:
        try:
            _tel_yahoo_call("yf.Ticker.funds_data.fund_overview")
            category = yf.Ticker(ticker).funds_data.fund_overview.get("categoryName")
        except Exception as e:
            dprint(f"[classify] kon funds_data.fund_overview niet ophalen voor '{ticker}' "
                   f"(category-fallback): {e}")
    if not category and _yq_was_leeg(yq_categorie):
        _onthoud_geen_fondsdata(ticker, ONDERDEEL_CATEGORIE)
    return category or None


def _classify_via_price(ticker):
    """Terugval als .info mislukt of uitgekleed is: alleen wat yahooquery's .price zeker weet, geen heuristiek."""
    regel = _yq_price(ticker)
    if not info_is_bruikbaar(regel):
        return None
    quote_type = regel.get("quoteType") or ""
    is_etf = quote_type == "ETF"
    category = _zoek_categorie(ticker, None) if is_etf or quote_type == "MUTUALFUND" else None
    return {
        "is_etf": is_etf,
        "quote_type": quote_type or None,
        "valuta": regel.get("currency"),
        "yahoo_beurs": regel.get("exchange"),
        "fund_family": regel.get("fundFamily"),
        "category": category,
        "long_name": regel.get("longName") or regel.get("shortName"),
    }


def _classify_ticker_uncached(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """{is_etf, quote_type, valuta, yahoo_beurs, fund_family, category, long_name}, of None bij een fout.
    Land en sector gaan alleen naar de ticker_land_sector-cache."""
    info = _fetch_yf_info(ticker, pogingen, wachttijd)
    if not info_is_bruikbaar(info):
        sleutels = list(info)[:10] if isinstance(info, dict) else None
        dprint(f"[classify] '{ticker}': .info onbruikbaar (sleutels: {sleutels}), terugval op yahooquery .price")
        # Land en sector ontbreken hier bewust en worden niet gecachet: None zou get_land_sector blokkeren.
        return _classify_via_price(ticker)  # None = onbekend, NIET cachen: volgende keer opnieuw proberen

    quote_type = info.get("quoteType", "")
    country = info.get("country")
    sector = info.get("sector")
    total_assets = info.get("totalAssets")
    fund_family = info.get("fundFamily")
    category = info.get("category")

    if quote_type:
        is_etf = quote_type == "ETF"
    else:
        # quoteType onbekend/leeg -> heuristiek
        signals = [
            country is None,
            sector is None,
            total_assets is not None,
            fund_family is not None,
            category is not None,
        ]
        is_etf = sum(signals) >= 2

    # Scheelt get_land_sector() later een identieke Yahoo-call.
    db_save_land_sector(ticker, country, sector)

    if is_etf or quote_type == "MUTUALFUND":
        category = _zoek_categorie(ticker, category)

    return {
        "is_etf": is_etf,
        "quote_type": quote_type or None,
        "valuta": info.get("currency"),
        "yahoo_beurs": info.get("exchange"),
        "fund_family": fund_family,
        "category": category,
        "long_name": info.get("longName"),
    }


def get_land_sector(ticker):
    """(land, sector), met "Unknown" i.p.v. None."""
    cached = db_get_cached_land_sector([ticker])
    if ticker in cached:
        land, sector = cached[ticker]
        return (land or "Unknown", sector or "Unknown")

    info = _fetch_yf_info(ticker)
    if info is None:
        return ("Unknown", "Unknown")  # niet cachen, volgende keer opnieuw proberen

    land = info.get("country")
    sector = info.get("sector")
    db_save_land_sector(ticker, land, sector)  # None mag hier gecached worden, is niet kritiek
    return (land or "Unknown", sector or "Unknown")


def normaliseer_valuta(valuta):
    """"Unknown" i.p.v. leeg; Yahoo's "GBp" (pence) telt als GBP."""
    if not valuta:
        return "Unknown"
    return "GBP" if valuta == "GBp" else valuta


def get_valuta(ticker):
    return normaliseer_valuta(_ticker_details_met_cache(ticker).get("valuta"))


def get_valutas(tickers):
    """{ticker: valuta} met één cache-query; alleen tickers zonder verse cacherij gaan via get_valuta() (Yahoo)."""
    tickers = list(tickers)
    details = db_get_ticker_details(tickers)
    valutas = {}
    via_yahoo = 0
    for ticker in tickers:
        rij = details.get(ticker)
        if _rij_is_vers(rij):
            valutas[ticker] = normaliseer_valuta(rij.get("valuta"))
        else:
            valutas[ticker] = get_valuta(ticker)
            via_yahoo += 1
    dprint(f"[valuta] {via_yahoo} van {len(tickers)} ticker(s) niet in de cache, via Yahoo")
    return valutas


# Yahoo-sleutels die aan elkaar geschreven zijn; de rest volgt uit "_" -> spatie.
SECTOR_WEERGAVE = {"realestate": "Real Estate"}


def _sector_naam(sector_key):
    """'consumer_cyclical' -> 'Consumer Cyclical'; ook op al opgemaakte namen uit de cache ('Realestate')."""
    sleutel = sector_key.lower().replace("_", "").replace(" ", "")
    return SECTOR_WEERGAVE.get(sleutel) or sector_key.replace("_", " ").title()


def get_etf_sector_verdeling(ticker):
    """{sector: gewicht als fractie 0-1}; leeg bij een fout (dan niet gecachet).
    Eerst yahooquery, daarna yfinance: yfinance's funds_data faalt op Render met 401 Invalid Crumb.
    Een lege uitkomst van beide wordt kort in het geheugen onthouden."""
    cached = db_get_cached_etf_sector_verdeling(ticker)
    if cached is not None:
        return {_sector_naam(sector): gewicht for sector, gewicht in cached.items()}
    if _geen_fondsdata_bekend(ticker, ONDERDEEL_SECTOR):
        return {}

    weightings = _yq_sector_verdeling(ticker)
    yq_leeg = _yq_was_leeg(weightings)
    if not weightings:
        try:
            _tel_yahoo_call("yf.Ticker.funds_data.sector_weightings")
            weightings = yf.Ticker(ticker).funds_data.sector_weightings
        except Exception as e:
            dprint(f"[etf-sector] '{ticker}': yfinance sector_weightings mislukt: {e}")
            weightings = None
    if not weightings:
        dprint(f"[etf-sector] '{ticker}': geen sectorverdeling gevonden")
        if yq_leeg:
            _onthoud_geen_fondsdata(ticker, ONDERDEEL_SECTOR)
        return {}

    sector_dict = {_sector_naam(sector_key): float(gewicht) for sector_key, gewicht in weightings.items()}
    db_save_etf_sector_verdeling(ticker, sector_dict)
    return sector_dict


def ishares_fondsen():
    """iShares-fondsenlijst uit de cache (ISHARES_FONDSEN_GELDIGHEID), anders uit de screener; None bij een fout."""
    fondsen = db_get_ishares_fondsen()
    if fondsen is not None:
        return fondsen
    fondsen = fetch_ishares_fondsenlijst()
    if fondsen:
        db_save_ishares_fondsen(fondsen)
    return fondsen


def _ishares_fonds_voor_isin(isin):
    if not isin:
        return None
    return next((f for f in ishares_fondsen() or [] if f["isin"] == isin), None)


def _provider_holdings_naar_cache(provider_holdings):
    return [
        {
            "holding_naam": h["naam"],
            "holding_ticker": None,
            "gewicht": h["gewicht"] / 100.0,
            "land": h["land"],
            "bron": "provider_csv",
        }
        for h in provider_holdings
    ]


def _haal_provider_holdings(ticker, ishares_fonds):
    if ticker in ETF_HOLDINGS_BRON:
        return fetch_provider_holdings(ticker)
    holdings = fetch_ishares_holdings_via_productpagina(ishares_fonds["product_url"])
    if not holdings:
        print(f"[etf-holdings] WARN '{ticker}' ({ishares_fonds['isin']}): iShares-holdings via productpagina "
              f"niet opgehaald, terugval op yfinance top-10")
    return holdings


def get_etf_holdings(ticker, isin=None):
    """[{holding_naam, holding_ticker, gewicht (0-1), land, bron}]: eerst de provider, anders yfinance's top 10.
    Provider = ETF_HOLDINGS_BRON, of met isin elk iShares-fonds uit de screener (exacte ISIN).
    Een yfinance_top10-cache wordt alsnog vervangen zodra er een provider bekend is.
    De top 10 komt eerst van yahooquery, met yfinance als terugval (die geeft op Render 401 Invalid Crumb).
    Een lege uitkomst van beide wordt kort in het geheugen onthouden."""
    cached = db_get_cached_etf_holdings(ticker)
    if cached and cached[0]["bron"] == "provider_csv":
        return cached

    ishares_fonds = None if ticker in ETF_HOLDINGS_BRON else _ishares_fonds_voor_isin(isin)
    heeft_provider = ticker in ETF_HOLDINGS_BRON or ishares_fonds is not None
    if cached is not None:
        if not heeft_provider:
            return cached
        dprint(f"[etf-holdings] '{ticker}': yfinance-top10-cache is nog vers, maar er is inmiddels "
               f"een provider bekend -> alsnog proberen te upgraden naar de volledige lijst")

    if heeft_provider:
        provider_holdings = _haal_provider_holdings(ticker, ishares_fonds)
        if provider_holdings:
            holdings = _provider_holdings_naar_cache(provider_holdings)
            db_save_etf_holdings(ticker, holdings)
            return holdings

    if _geen_fondsdata_bekend(ticker, ONDERDEEL_HOLDINGS):
        return cached or []

    top10 = _yq_top_holdings(ticker)
    yq_leeg = _yq_was_leeg(top10)
    if not top10:
        top10 = []
        try:
            _tel_yahoo_call("yf.Ticker.funds_data.top_holdings")
            top_holdings = yf.Ticker(ticker).funds_data.top_holdings
            if top_holdings is not None and not top_holdings.empty:
                top10 = [(t, row["Name"], float(row["Holding Percent"])) for t, row in top_holdings.iterrows()]
        except Exception as e:
            dprint(f"[etf-holdings] '{ticker}': yfinance top_holdings mislukt: {e}")
    if not top10:
        dprint(f"[etf-holdings] '{ticker}': geen top 10 gevonden")
        if yq_leeg:
            _onthoud_geen_fondsdata(ticker, ONDERDEEL_HOLDINGS)
        return cached or []

    # Bron blijft yfinance_top10 (ook bij yahooquery): proxy- en waarschuwingslogica kijken alleen naar die waarde.
    holdings = []
    for holding_ticker, naam, gewicht in top10:
        land, _ = get_land_sector(holding_ticker)
        holdings.append({
            "holding_naam": naam,
            "holding_ticker": holding_ticker,
            "gewicht": gewicht,
            "land": land,
            "bron": "yfinance_top10",
        })

    db_save_etf_holdings(ticker, holdings)
    return holdings


# Holdings van een door de gebruiker gekozen ISIN horen bij het fonds, niet bij een ticker: onder de ticker zouden ze
# de cache van elke portfolio met die ticker vergiftigen.
ISIN_HOLDINGS_SLEUTEL_PREFIX = "isin:"


def get_etf_holdings_voor_isin(isin):
    """Provider-holdings van een iShares-fonds op exacte ISIN, alleen gecachet onder "isin:<ISIN>"; None zonder
    iShares-fonds of holdings (geen yfinance-terugval: die gaat via de ticker)."""
    sleutel = f"{ISIN_HOLDINGS_SLEUTEL_PREFIX}{isin}"
    cached = db_get_cached_etf_holdings(sleutel)
    if cached and cached[0]["bron"] == "provider_csv":
        return cached
    ishares_fonds = _ishares_fonds_voor_isin(isin)
    if ishares_fonds is None:
        return None
    provider_holdings = fetch_ishares_holdings_via_productpagina(ishares_fonds["product_url"])
    if not provider_holdings:
        return None
    holdings = _provider_holdings_naar_cache(provider_holdings)
    db_save_etf_holdings(sleutel, holdings)
    return holdings


def get_etf_holdings_uit_cache(ticker):
    """[{naam, gewicht (0-1), land, sector}] alleen uit de cache, nooit Yahoo; sector via
    ticker_land_sector (etf_holdings heeft geen sectorkolom), anders None."""
    holdings = db_get_cached_etf_holdings(ticker) or []
    holding_tickers = [h["holding_ticker"] for h in holdings if h.get("holding_ticker")]
    land_sector = db_get_cached_land_sector(holding_tickers)
    return [
        {
            "naam": h["holding_naam"],
            "gewicht": h["gewicht"],
            "land": h.get("land"),
            "sector": land_sector.get(h.get("holding_ticker"), (None, None))[1],
        }
        for h in holdings
    ]


def classify_ticker(ticker):
    """True als Yahoo de ticker als ETF ziet. Bij een fout de oude cachewaarde, anders False (niet gecachet)."""
    return bool(_ticker_details_met_cache(ticker).get("is_etf"))


def classify_tickers(tickers):
    """{ticker: is_etf}, met een pauze tussen Yahoo-calls tegen rate limiting."""
    tickers = list(dict.fromkeys(t for t in tickers if t))  # uniek, volgorde behouden
    cached = db_get_ticker_details(tickers)
    result = {t: bool(d["is_etf"]) for t, d in cached.items()}

    te_doen = [t for t in tickers if not _rij_is_vers(cached.get(t))]
    for i, t in enumerate(te_doen):
        if i > 0:
            time.sleep(1.5)  # kleine pauze tussen calls om rate limiting te voorkomen
        details = _classify_ticker_uncached(t)
        if details is None:
            result.setdefault(t, False)  # niet cachen, volgende keer opnieuw proberen
        else:
            db_save_classification(t, details["is_etf"], details)
            result[t] = details["is_etf"]

    return result


def _verwarm_land_sector_cache_parallel(tickers, is_etf_map, isin_per_ticker=None, max_workers=8):
    """Vult alleen de caches, zodat de verdelingsfuncties daarna sequentieel alleen cache-hits krijgen.
    Alleen hier krijgt get_etf_holdings() de ISIN mee; de latere aanroepen lezen de cache."""
    isin_per_ticker = isin_per_ticker or {}

    def _warm(ticker):
        get_valuta(ticker)
        if is_etf_map.get(ticker, False):
            get_etf_sector_verdeling(ticker)
            get_etf_holdings(ticker, isin_per_ticker.get(ticker))
        else:
            get_land_sector(ticker)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_warm, tickers))


def valuta_voor_effect(ticker):
    """De valuta van Yahoo uit de details, of None; bewust geen aanname van EUR (Zoeken & wat als)."""
    return get_ticker_details(ticker).get("valuta") or None


def get_ticker_details(ticker):
    """{is_etf, quote_type, valuta, long_name, ...} uit de ticker_info-cache of Yahoo; {} als beide falen."""
    return _ticker_details_met_cache(ticker)


def _ticker_details_met_cache(ticker):
    """Details uit de ticker_info-cache; een niet-verse rij (zie _rij_is_vers) wordt opnieuw opgehaald."""
    details = db_get_ticker_details([ticker]).get(ticker)
    if _rij_is_vers(details):
        return details

    nieuw = _classify_ticker_uncached(ticker)
    if nieuw is None:
        return details or {}
    db_save_classification(ticker, nieuw["is_etf"], nieuw)
    return nieuw
