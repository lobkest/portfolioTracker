"""ETF of aandeel, plus land/sector/holdings per ticker (met database-cache)."""
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf
from yahooquery import Ticker as YahooqueryTicker

from db import (
    db_save_classification, db_get_cached_land_sector, db_save_land_sector,
    db_get_cached_etf_sector_verdeling, db_save_etf_sector_verdeling, db_get_cached_etf_holdings, db_save_etf_holdings,
    db_get_ticker_details, db_save_long_names, db_get_ishares_fondsen, db_save_ishares_fondsen,
)
from debug_utils import dprint
from yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from etf_holdings_provider import (
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
        # Een fout of onbekende ticker komt als string terug, geen dict.
        regel = price.get(t)
        if not isinstance(regel, dict):
            regel = price.get(t.upper())
        naam = regel.get("longName") if isinstance(regel, dict) else None
        long_names[t] = naam.strip() if isinstance(naam, str) and naam.strip() else None
    return long_names


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


def _classify_ticker_uncached(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """{is_etf, quote_type, valuta, yahoo_beurs, fund_family, category, long_name}, of None bij een fout.
    Land en sector gaan alleen naar de ticker_land_sector-cache."""
    info = _fetch_yf_info(ticker, pogingen, wachttijd)
    if info is None:
        return None  # onbekend, NIET als aandeel cachen — gewoon opnieuw proberen volgende keer

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

    # info["category"] ontbreekt bij UCITS-ETF's en mutual funds; fund_overview is de fallback.
    if not category and (is_etf or quote_type == "MUTUALFUND"):
        try:
            _tel_yahoo_call("yf.Ticker.funds_data.fund_overview")
            category = yf.Ticker(ticker).funds_data.fund_overview.get("categoryName")
        except Exception as e:
            dprint(f"[classify] kon funds_data.fund_overview niet ophalen voor '{ticker}' "
                   f"(category-fallback): {e}")

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
    """{sector: gewicht als fractie 0-1}; leeg bij een fout (dan niet gecachet)."""
    cached = db_get_cached_etf_sector_verdeling(ticker)
    if cached is not None:
        return {_sector_naam(sector): gewicht for sector, gewicht in cached.items()}

    try:
        _tel_yahoo_call("yf.Ticker.funds_data.sector_weightings")
        weightings = yf.Ticker(ticker).funds_data.sector_weightings
    except Exception:
        return {}

    if not weightings:
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
    Een yfinance_top10-cache wordt alsnog vervangen zodra er een provider bekend is."""
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

    try:
        _tel_yahoo_call("yf.Ticker.funds_data.top_holdings")
        top_holdings = yf.Ticker(ticker).funds_data.top_holdings
    except Exception:
        return cached or []

    if top_holdings is None or top_holdings.empty:
        return cached or []

    holdings = []
    for holding_ticker, row in top_holdings.iterrows():
        land, _ = get_land_sector(holding_ticker)
        holdings.append({
            "holding_naam": row["Name"],
            "holding_ticker": holding_ticker,
            "gewicht": float(row["Holding Percent"]),
            "land": land,
            "bron": "yfinance_top10",
        })

    db_save_etf_holdings(ticker, holdings)
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
