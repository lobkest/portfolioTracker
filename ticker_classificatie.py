"""ETF of aandeel, plus land/sector/holdings per ticker (met database-cache)."""
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from db import (
    get_cached_classifications, save_classification, get_cached_land_sector, save_land_sector,
    get_cached_etf_sector_verdeling, save_etf_sector_verdeling, get_cached_etf_holdings, save_etf_holdings,
    get_ticker_details,
)
from debug_utils import dprint
from yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from etf_holdings_provider import ETF_HOLDINGS_BRON, fetch_provider_holdings


def _fetch_yf_info(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """yf.Ticker(ticker).info met retry; None bij een definitieve fout."""
    def _actie():
        _tel_yahoo_call("yf.Ticker.info")
        return yf.Ticker(ticker).info

    info, fout = _met_rate_limit_retry(_actie, pogingen, wachttijd)
    if fout is not None:
        return None
    return info


def _classify_ticker_uncached(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """{is_etf, land, sector, quote_type, valuta, yahoo_beurs, fund_family, category}, of None bij een fout."""
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
        dprint(f"[classify] '{ticker}': quoteType='{quote_type}' -> ETF={is_etf}")
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
    save_land_sector(ticker, country, sector)

    # info["category"] ontbreekt bij UCITS-ETF's en mutual funds; fund_overview is de fallback.
    if not category and (is_etf or quote_type == "MUTUALFUND"):
        try:
            _tel_yahoo_call("yf.Ticker.funds_data.fund_overview")
            category = yf.Ticker(ticker).funds_data.fund_overview.get("categoryName")
            dprint(f"[classify] '{ticker}': category via funds_data.fund_overview -> {category}")
        except Exception as e:
            dprint(f"[classify] kon funds_data.fund_overview niet ophalen voor '{ticker}' "
                   f"(category-fallback): {e}")

    return {
        "is_etf": is_etf,
        "land": country,
        "sector": sector,
        "quote_type": quote_type or None,
        "valuta": info.get("currency"),
        "yahoo_beurs": info.get("exchange"),
        "fund_family": fund_family,
        "category": category,
    }


def get_land_sector(ticker):
    """(land, sector), met "Unknown" i.p.v. None."""
    cached = get_cached_land_sector([ticker])
    if ticker in cached:
        land, sector = cached[ticker]
        dprint(f"[land-sector] '{ticker}': uit cache -> land={land}, sector={sector}")
        return (land or "Unknown", sector or "Unknown")

    info = _fetch_yf_info(ticker)
    if info is None:
        return ("Unknown", "Unknown")  # niet cachen, volgende keer opnieuw proberen

    land = info.get("country")
    sector = info.get("sector")
    save_land_sector(ticker, land, sector)  # None mag hier gecached worden, is niet kritiek
    return (land or "Unknown", sector or "Unknown")


def _sector_naam(sector_key):
    """'consumer_cyclical' -> 'Consumer Cyclical'."""
    return sector_key.replace("_", " ").title()


def get_etf_sector_verdeling(ticker):
    """{sector: gewicht als fractie 0-1}; leeg bij een fout (dan niet gecachet)."""
    cached = get_cached_etf_sector_verdeling(ticker)
    if cached is not None:
        dprint(f"[etf-sector] '{ticker}': uit cache -> {len(cached)} sectoren")
        return cached

    try:
        _tel_yahoo_call("yf.Ticker.funds_data.sector_weightings")
        weightings = yf.Ticker(ticker).funds_data.sector_weightings
    except Exception:
        return {}

    if not weightings:
        return {}

    sector_dict = {_sector_naam(sector_key): float(gewicht) for sector_key, gewicht in weightings.items()}
    save_etf_sector_verdeling(ticker, sector_dict)
    return sector_dict


def get_etf_holdings(ticker):
    """[{holding_naam, holding_ticker, gewicht (0-1), land, bron}]: eerst de provider, anders yfinance's top 10.
    Een yfinance_top10-cache wordt alsnog vervangen zodra er een provider-URL bekend is."""
    heeft_provider_url = ticker in ETF_HOLDINGS_BRON

    cached = get_cached_etf_holdings(ticker)
    if cached is not None:
        cached_bron = cached[0]["bron"] if cached else "yfinance_top10"
        if cached_bron == "provider_csv" or not heeft_provider_url:
            dprint(f"[etf-holdings] '{ticker}': uit cache ({cached_bron}) -> {len(cached)} holdings")
            return cached
        dprint(f"[etf-holdings] '{ticker}': yfinance-top10-cache is nog vers, maar er is inmiddels "
               f"een provider-URL bekend -> alsnog proberen te upgraden naar de volledige lijst")

    if heeft_provider_url:
        provider_holdings = fetch_provider_holdings(ticker)
        if provider_holdings:
            holdings = [
                {
                    "holding_naam": h["naam"],
                    "holding_ticker": None,
                    "gewicht": h["gewicht"] / 100.0,
                    "land": h["land"],
                    "bron": "provider_csv",
                }
                for h in provider_holdings
            ]
            save_etf_holdings(ticker, holdings)
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

    save_etf_holdings(ticker, holdings)
    return holdings


def classify_ticker(ticker):
    """True als Yahoo de ticker als ETF ziet. Bij een fout False, zonder te cachen."""
    cached = get_cached_classifications([ticker])
    if ticker in cached:
        dprint(f"[classify] '{ticker}': uit cache -> ETF={cached[ticker]}")
        return cached[ticker]

    details = _classify_ticker_uncached(ticker)
    if details is None:
        return False

    save_classification(ticker, details["is_etf"], details)
    return details["is_etf"]


def classify_tickers(tickers):
    """{ticker: is_etf}, met een pauze tussen Yahoo-calls tegen rate limiting."""
    tickers = list(dict.fromkeys(t for t in tickers if t))  # uniek, volgorde behouden
    cached = get_cached_classifications(tickers)
    result = dict(cached)

    te_doen = [t for t in tickers if t not in cached]
    for i, t in enumerate(te_doen):
        if i > 0:
            time.sleep(1.5)  # kleine pauze tussen calls om rate limiting te voorkomen
        details = _classify_ticker_uncached(t)
        if details is None:
            result[t] = False  # niet cachen, volgende keer opnieuw proberen
        else:
            save_classification(t, details["is_etf"], details)
            result[t] = details["is_etf"]

    return result


def _verwarm_land_sector_cache_parallel(tickers, is_etf_map, max_workers=8):
    """Vult alleen de caches, zodat de verdelingsfuncties daarna sequentieel alleen cache-hits krijgen."""
    def _warm(ticker):
        if is_etf_map.get(ticker, False):
            get_etf_sector_verdeling(ticker)
            get_etf_holdings(ticker)
        else:
            get_land_sector(ticker)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_warm, tickers))


def _ticker_details_met_cache(ticker):
    """Details uit de ticker_info-cache; zijn valuta én quote_type leeg, dan is de rij stale en opnieuw ophalen."""
    bestaand = get_ticker_details([ticker])
    details = bestaand.get(ticker)
    if details and (details.get("valuta") or details.get("quote_type")):
        dprint(f"[prijscheck] '{ticker}': ticker_info-cache bruikbaar -> {details}")
        return details

    nieuw = _classify_ticker_uncached(ticker)
    if nieuw is None:
        return details or {}
    save_classification(ticker, nieuw["is_etf"], nieuw)
    return nieuw
