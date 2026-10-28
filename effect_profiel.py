"""Profiel van één effect (type, koers, land, sector, holdings) voor Zoeken & wat als; heeft geen portfolio nodig."""
import math
from datetime import date, timedelta

import pandas as pd

from db import db_get_isins_voor_ticker
from debug_utils import dprint
from effect_zoeken import noteringen_voor_isin_zoekopdracht, zoek_gecachet
from etf_holdings_provider import ETF_HOLDINGS_BRON
from etf_proxy import land_proxies_voor_etfs
from portfolio_verdeling import holdings_lijst_uit, land_pct_uit_holdings, sector_pct_met_restant
from prijzen import get_prices
from ticker_classificatie import (
    get_etf_holdings, get_etf_holdings_voor_isin, get_etf_sector_verdeling, get_land_sector, get_ticker_details,
    ishares_fondsen,
)

KOERS_TERUGKIJK_DAGEN = 10
MAX_TOP_HOLDINGS = 25
TYPE_ETF = "ETF"
TYPE_AANDEEL = "EQUITY"

LAND_BRON_AANDEEL = "aandeel"
LAND_BRON_PROXY = "proxy"
BRON_TOP10 = "yfinance_top10"
BRON_PROVIDER = "provider_csv"

ROUTE_PROVIDER_TICKER = "provider_ticker"
ROUTE_ISIN = "isin"
ROUTE_TOP10 = "top10"

WAARSCHUWING_GEEN_KOERS = "geen_koers"
WAARSCHUWING_GEEN_SECTOR = "geen_sector"
WAARSCHUWING_GEEN_HOLDINGS = "geen_holdings"
WAARSCHUWING_ALLEEN_TOP10 = "alleen_top10"
WAARSCHUWING_ISIN_GEKOZEN = "isin_gekozen"
WAARSCHUWING_LAND_VIA_PROXY = "land_via_proxy"


class OnbekendEffect(Exception):
    pass


def verifieer_isin_bij_ticker(ticker, isin):
    """Op de server vastgesteld, nooit uit een vlag van de client: de ISIN staat bij die ticker in de eigen
    transacties, of de ticker is een exacte notering van die ISIN."""
    try:
        if isin in db_get_isins_voor_ticker(ticker):
            return True
    except Exception as e:
        dprint(f"[profiel] WARN eigen ISIN's niet opgehaald ({e!a})")
    try:
        quotes = noteringen_voor_isin_zoekopdracht(zoek_gecachet(isin), zoek_gecachet)
    except Exception as e:
        dprint(f"[profiel] WARN noteringen van {isin} niet opgehaald ({e!a})")
        return False
    return ticker.upper() in {q["symbol"].upper() for q in quotes if q.get("symbol")}


def _haal_koers(ticker):
    try:
        koersen = get_prices([ticker], date.today() - timedelta(days=KOERS_TERUGKIJK_DAGEN))
        reeks = koersen[ticker].dropna() if ticker in koersen.columns else []
        if len(reeks) == 0:
            return None, None
        return float(reeks.iloc[-1]), pd.Timestamp(reeks.index[-1]).date().isoformat()
    except Exception as e:
        dprint(f"[profiel] WARN koers niet opgehaald ({e!a})")
        return None, None


def _haal_sector(ticker):
    try:
        verdeling = get_etf_sector_verdeling(ticker)
    except Exception as e:
        dprint(f"[profiel] WARN sectorverdeling niet opgehaald ({e!a})")
        return None
    return sector_pct_met_restant(verdeling) if verdeling else None


def _haal_holdings(ticker, isin, geverifieerd, waarschuwingen):
    """(holdings, route). Alleen ticker-exacte bronnen onder de ticker; een gekozen ISIN gaat via de ISIN-sleutel."""
    try:
        if ticker in ETF_HOLDINGS_BRON:
            return get_etf_holdings(ticker) or [], ROUTE_PROVIDER_TICKER
        if isin:
            holdings = get_etf_holdings_voor_isin(isin)
            if holdings:
                if not geverifieerd:
                    waarschuwingen.append(WAARSCHUWING_ISIN_GEKOZEN)
                return holdings, ROUTE_ISIN
        holdings = get_etf_holdings(ticker) or []
        # Een eerdere portfolio kan de ticker-cache al met provider-holdings gevuld hebben.
        provider = bool(holdings) and holdings[0]["bron"] == BRON_PROVIDER
        return holdings, ROUTE_PROVIDER_TICKER if provider else ROUTE_TOP10
    except Exception as e:
        dprint(f"[profiel] WARN holdings niet opgehaald ({e!a})")
        return [], None


def _holdings_fonds(isin):
    try:
        fonds = next((f for f in ishares_fondsen() or [] if f["isin"] == isin), None)
    except Exception as e:
        dprint(f"[profiel] WARN iShares-fondsenlijst niet opgehaald ({e!a})")
        fonds = None
    return {"isin": isin, "naam": fonds["naam"] if fonds else None}


def _proxy_land(ticker, isin):
    try:
        proxy = land_proxies_voor_etfs({ticker: isin}).get(ticker)
    except Exception as e:
        dprint(f"[profiel] WARN land-proxy mislukt ({e!a})")
        return None
    if proxy and proxy.get("proxy_isin") and proxy.get("proxy_land"):
        return dict(proxy["proxy_land"]), {"naam": proxy["proxy_naam"], "max_afwijking_pp": proxy["max_afwijking_pp"]}
    return None


def _zonder_nan(waarde):
    if isinstance(waarde, float):
        return None if math.isnan(waarde) or math.isinf(waarde) else waarde
    if isinstance(waarde, dict):
        return {k: _zonder_nan(v) for k, v in waarde.items()}
    if isinstance(waarde, list):
        return [_zonder_nan(v) for v in waarde]
    return waarde


def _etf_onderdelen(ticker, isin, geverifieerd, waarschuwingen):
    """(land, land_bron, land_proxy, sector, holdings, holdings_route, holdings_fonds)."""
    sector = _haal_sector(ticker)
    if sector is None:
        waarschuwingen.append(WAARSCHUWING_GEEN_SECTOR)

    holdings, route = _haal_holdings(ticker, isin, geverifieerd, waarschuwingen)
    geldig = [h for h in holdings if pd.notna(h["gewicht"])]
    bron = geldig[0]["bron"] if geldig else None
    if not geldig:
        waarschuwingen.append(WAARSCHUWING_GEEN_HOLDINGS)
    elif bron == BRON_TOP10:
        waarschuwingen.append(WAARSCHUWING_ALLEEN_TOP10)

    land = land_pct_uit_holdings(geldig)
    land_bron = bron or BRON_TOP10
    land_proxy = None
    # Een proxy op een niet-geverifieerde ISIN zou de etf_proxy-cache (per ISIN) met een verkeerd fonds vullen.
    if geverifieerd and bron == BRON_TOP10:
        proxy = _proxy_land(ticker, isin)
        if proxy:
            (land, land_proxy), land_bron = proxy, LAND_BRON_PROXY
            waarschuwingen.append(WAARSCHUWING_LAND_VIA_PROXY)

    samenvatting = None
    if geldig:
        lijst = holdings_lijst_uit(geldig)
        samenvatting = {
            "bron": lijst["bron"], "aantal": lijst["aantal"], "gedekt_gewicht": lijst["gedekt_gewicht"],
            "top": [{"naam": r["naam"], "gewicht": r["gewicht"]} for r in lijst["holdings"][:MAX_TOP_HOLDINGS]],
        }
    fonds = _holdings_fonds(isin) if route == ROUTE_ISIN else None
    return land, land_bron, land_proxy, sector or {}, samenvatting, route, fonds


def bouw_effect_profiel(ticker, isin=None):
    """Raises OnbekendEffect bij een ticker zonder gegevens en zonder koers; elke andere bronfout wordt een waarschuwing."""
    waarschuwingen = []
    details = get_ticker_details(ticker)
    koers_eur, koers_datum = _haal_koers(ticker)
    if not details and koers_eur is None:
        raise OnbekendEffect(ticker)
    if koers_eur is None:
        waarschuwingen.append(WAARSCHUWING_GEEN_KOERS)

    # Alleen "hoort deze ISIN bij deze ticker", los van welke holdings-route gebruikt is.
    geverifieerd = verifieer_isin_bij_ticker(ticker, isin) if isin else None
    is_etf = bool(details.get("is_etf"))
    if is_etf:
        land, land_bron, land_proxy, sector, holdings, route, fonds = _etf_onderdelen(
            ticker, isin, geverifieerd, waarschuwingen)
    else:
        aandeel_land, aandeel_sector = get_land_sector(ticker)
        land, sector, land_bron, land_proxy, holdings, route, fonds = (
            {aandeel_land: 1.0}, {aandeel_sector: 1.0}, LAND_BRON_AANDEEL, None, None, None, None)

    return _zonder_nan({
        "ticker": ticker,
        "naam": details.get("long_name") or ticker,
        "type": TYPE_ETF if is_etf else TYPE_AANDEEL,
        "valuta": details.get("valuta"),
        "koers_eur": koers_eur,
        "koers_datum": koers_datum,
        "land": land,
        "sector": sector,
        "land_bron": land_bron,
        "land_proxy": land_proxy,
        "holdings": holdings,
        "holdings_route": route,
        "holdings_fonds": fonds,
        "isin_geverifieerd": geverifieerd,
        "waarschuwingen": waarschuwingen,
    })
