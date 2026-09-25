"""
Ticker-classificatie: is dit een ETF of een aandeel (yfinance quoteType +
heuristiek), en de bijbehorende land-/sector-/holdings-opzoekingen (met
30-dagen-cache via db.py). Gebruikt door zowel de portfoliobrede
Verdeling/Land/Sector-aggregatie (portfolio_verdeling.py) als de Ticker-
zekerheid-verificatie.

Losgetrokken uit analysis.py; ongewijzigd overgenomen.
"""
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
    """
    Haalt yf.Ticker(ticker).info op met retry/backoff bij rate limiting
    (via _met_rate_limit_retry). Gedeeld door classify_ticker() en
    get_land_sector() zodat beide niet onafhankelijk van elkaar dezelfde
    Yahoo-call voor dezelfde ticker doen (rate limiting is een bekend
    pijnpunt in dit project). Geeft None terug bij een definitieve fout
    (rate limit na alle retries).
    """
    def _actie():
        _tel_yahoo_call("yf.Ticker.info")
        return yf.Ticker(ticker).info

    info, fout = _met_rate_limit_retry(_actie, "yf-info", f"'{ticker}'", pogingen, wachttijd)
    if fout is not None:
        # print(f"[yf-info] ❌ kon info niet ophalen voor '{ticker}': {fout}")
        return None
    return info


def _classify_ticker_uncached(ticker, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Doet de daadwerkelijke yfinance-lookup, met retry/backoff bij rate limiting.
    Geeft None terug bij een definitieve fout (rate limit na alle retries).
    Anders een dict met de ETF/aandeel-classificatie plus alle Yahoo-info die
    daarvoor gebruikt is (land, sector, beurs, valuta, ...) — zodat dit in
    Instellingen > Ticker-zekerheid getoond en met het Excel-bestand
    vergeleken kan worden.
    """
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
        # print(f"[classify] ⚠️ quoteType onbekend voor '{ticker}', gok ETF={is_etf} "
              # f"(country={country}, sector={sector}, totalAssets={total_assets}, "
              # f"fundFamily={fund_family}, category={category})")

    # Land/sector kwam toch al mee met deze call — meteen ook in de aparte
    # ticker_land_sector-cache zetten, zodat get_land_sector() voor deze
    # ticker geen tweede identieke Yahoo-call meer hoeft te doen.
    save_land_sector(ticker, country, sector)

    # info["category"] bestaat alleen voor Amerikaanse fondsen (bv. SPY/VOO
    # -> "Large Blend"); voor mutual funds (bv. VTSAX) en voor de Ierse
    # UCITS-ETF's die dit project vooral tegenkomt (CSPX.AS, VUSA.AS, ...)
    # ontbreekt die key in info helemaal, maar staat 'm (als aanwezig) in
    # funds_data.fund_overview["categoryName"] — dus dat als fallback
    # proberen voor fonds-achtige tickers. Blijft None als Yahoo het zelf
    # ook niet heeft (bevestigd voor meerdere UCITS-ETF's: geen bug, gewoon
    # geen data).
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
    """
    Land + sector van een los aandeel of holding-ticker, met 30-dagen-cache
    (tabel ticker_land_sector — los van de ticker_info-classificatiecache).
    Geeft altijd een (land, sector)-tuple terug: "Unknown" i.p.v. None als
    het niet gevonden is, zodat aanroepers geen None-checks nodig hebben.

    In de praktijk is dit vaak al een cache-hit tegen de tijd dat dit wordt
    aangeroepen: _classify_ticker_uncached() vult ticker_land_sector als
    bijproduct van zijn eigen (identieke) yfinance-call.
    """
    cached = get_cached_land_sector([ticker])
    if ticker in cached:
        land, sector = cached[ticker]
        dprint(f"[land-sector] '{ticker}': uit cache -> land={land}, sector={sector}")
        return (land or "Unknown", sector or "Unknown")

    info = _fetch_yf_info(ticker)
    if info is None:
        # kon niet opgehaald worden (rate limit na alle retries) — niet
        # cachen, gewoon Unknown teruggeven voor déze keer maar volgende
        # keer opnieuw proberen
        # print(f"[land-sector] ⚠️ '{ticker}': kon niet opgehaald worden, Unknown voor nu")
        return ("Unknown", "Unknown")

    land = info.get("country")
    sector = info.get("sector")
    # print(f"[land-sector] '{ticker}': opgehaald -> land={land}, sector={sector}")
    save_land_sector(ticker, land, sector)  # None mag hier gecached worden, is niet kritiek
    return (land or "Unknown", sector or "Unknown")


def _sector_naam(sector_key):
    """Zet yfinance's snake_case sector-sleutel (bv. 'consumer_cyclical') om
    naar een leesbare naam ('Consumer Cyclical')."""
    return sector_key.replace("_", " ").title()


def get_etf_sector_verdeling(ticker):
    """
    Sectorverdeling van een ETF/fonds, met 30-dagen-cache (tabel
    etf_sector_verdeling). Geeft {sector: gewicht} terug.

    Format-keuze: gewicht als fractie 0-1 — zo geeft yfinance dit zelf al
    terug (bv. 0.374 voor 37.4%), dus geen extra *100 of /100 nodig bij
    gebruik: waarde_in_euro * gewicht is direct het bedrag in die sector.
    get_etf_holdings() hieronder gebruikt dezelfde 0-1 schaal.

    Faalt de call of is de verdeling leeg, dan een lege dict teruggeven en
    NIET cachen (zelfde patroon als _classify_ticker_uncached bij rate
    limiting: gewoon opnieuw proberen bij de volgende upload).
    """
    cached = get_cached_etf_sector_verdeling(ticker)
    if cached is not None:
        dprint(f"[etf-sector] '{ticker}': uit cache -> {len(cached)} sectoren")
        return cached

    try:
        _tel_yahoo_call("yf.Ticker.funds_data.sector_weightings")
        weightings = yf.Ticker(ticker).funds_data.sector_weightings
    except Exception as e:
        # print(f"[etf-sector] ❌ kon sectorverdeling niet ophalen voor '{ticker}': {e}")
        return {}

    if not weightings:
        # print(f"[etf-sector] ⚠️ lege sectorverdeling voor '{ticker}', niet gecached")
        return {}

    sector_dict = {_sector_naam(sector_key): float(gewicht) for sector_key, gewicht in weightings.items()}
    # print(f"[etf-sector] '{ticker}': opgehaald -> {sector_dict}")
    save_etf_sector_verdeling(ticker, sector_dict)
    return sector_dict


def get_etf_holdings(ticker):
    """
    Holdings van een ETF/fonds (naam, ticker, gewicht, land, bron), met
    30-dagen-cache (tabel etf_holdings). Gewicht als fractie 0-1, zelfde
    schaal als get_etf_sector_verdeling().

    Probeert eerst de VOLLEDIGE holdings-lijst bij de fondsprovider zelf op
    te halen (fetch_provider_holdings(), zie ETF_HOLDINGS_BRON) — dekking
    bijna 100%, tegenover de ~35-40% van yfinance's top 10 voor een breed
    gespreid fonds. Alleen als daarvoor geen URL bekend is, of het ophalen/
    parsen mislukt, wordt teruggevallen op yfinance's funds_data.
    top_holdings (max 10, land per holding via get_land_sector()).

    Elke rij krijgt een "bron"-veld ("provider_csv" of "yfinance_top10") —
    zo weet de aanroeper (en de UI) hoe betrouwbaar de landverdeling voor
    déze ETF is. Een verse yfinance_top10-cache telt NIET als "goed genoeg"
    als er ondertussen een provider-URL voor deze ticker bekend is geworden
    (ETF_HOLDINGS_BRON kan na de vorige cache-vulling zijn aangevuld) — dan
    wordt alsnog geprobeerd te upgraden naar de volledige lijst.

    Faalt alles, dan een lege lijst teruggeven en NIET cachen (zelfde
    patroon als de andere ETF-caches bij een mislukte poging).
    """
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
        # print(f"[etf-holdings] '{ticker}': provider-holdings ophalen mislukt, terugvallen op yfinance-top-10")

    try:
        _tel_yahoo_call("yf.Ticker.funds_data.top_holdings")
        top_holdings = yf.Ticker(ticker).funds_data.top_holdings
    except Exception as e:
        # print(f"[etf-holdings] ❌ kon top-holdings niet ophalen voor '{ticker}': {e}")
        return cached or []

    if top_holdings is None or top_holdings.empty:
        # print(f"[etf-holdings] ⚠️ geen top-holdings gevonden voor '{ticker}', niet gecached")
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

    # print(f"[etf-holdings] '{ticker}': opgehaald -> {len(holdings)} holdings (yfinance_top10)")
    save_etf_holdings(ticker, holdings)
    return holdings


# _normaliseer_bedrijfsnaam/_sorteer_tickers_voor_dropdown/_sorteer_verdeling_
# groot_naar_klein/bereken_bedrijven_verdeling/bereken_etf_overlap/
# compute_land_sector_verdeling staan sinds de module-splitsing in
# portfolio_verdeling.py -- GEEN terug-import hier: niets in de rest van dit
# bestand gebruikt ze nog (alleen app.py, dat rechtstreeks uit
# portfolio_verdeling importeert). Een terug-import zou hier bovendien een
# fragiele circulaire import opleveren (portfolio_verdeling.py importeert op
# zijn beurt get_etf_holdings/get_etf_sector_verdeling/get_land_sector UIT
# dit bestand).


def classify_ticker(ticker):
    """
    Is dit een ETF volgens Yahoo Finance? Wordt gecached in de database (tabel
    ticker_info) zodat dit niet bij elke upload opnieuw tegen Yahoo hoeft —
    dat was de oorzaak van de rate-limit fouten die alles op "100% aandelen"
    lieten uitkomen (elke .info-call faalde, classify_ticker gaf dan overal
    False terug).
    """
    cached = get_cached_classifications([ticker])
    if ticker in cached:
        dprint(f"[classify] '{ticker}': uit cache -> ETF={cached[ticker]}")
        return cached[ticker]

    details = _classify_ticker_uncached(ticker)
    if details is None:
        # kon niet bepaald worden (rate limit na alle retries) — niet cachen,
        # gewoon False teruggeven voor déze keer maar volgende upload opnieuw proberen
        return False

    save_classification(ticker, details["is_etf"], details)
    return details["is_etf"]


def classify_tickers(tickers):
    """
    Batch-variant: 1 cache-lookup voor alle tickers tegelijk, en een korte
    pauze tussen de individuele Yahoo-calls voor tickers die nog niet
    gecached zijn (voorkomt dat je meteen weer rate limited wordt na de
    prijzen-download die er meestal net aan vooraf ging). Geeft {ticker: bool} terug.
    """
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
    """Haalt voor alle meegegeven tickers parallel de land/sector/holdings-
    data op (of pakt 'm uit cache) zodat de latere SEQUENTIËLE verwerking in
    compute_land_sector_verdeling / bereken_bedrijven_verdeling / bereken_etf_overlap
    alleen nog cache-hits tegenkomt. Puur een side-effect-functie (vult de
    database-caches), geeft niets bruikbaars terug — de resultaten worden
    zoals voorheen per functie apart via de cache opgehaald."""
    def _warm(ticker):
        if is_etf_map.get(ticker, False):
            get_etf_sector_verdeling(ticker)
            get_etf_holdings(ticker)
        else:
            get_land_sector(ticker)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_warm, tickers))


def _ticker_details_met_cache(ticker):
    """
    Land/sector/valuta/fondsfamilie/category/quote_type voor een ticker, via
    de ticker_info-cache (gevuld door classify_ticker/classify_tickers) als
    eerste stop. Voorkomt een extra yfinance-.info-call voor een ticker die
    al eerder in dit request (of een vorige upload) geclassificeerd is —
    zoals bij een positie in de portfolio zelf, die al via classify_tickers()
    in analyze_transacties gecached is vóórdat de Ticker-zekerheid-pagina
    wordt opgebouwd.

    Let op: een rij met is_etf gezet maar alle overige velden NULL is niet
    hetzelfde als "succesvol gecontroleerd en er is gewoon geen data" (bv.
    land/sector zijn voor een ETF legitiem None). valuta en quote_type zijn vrijwel
    altijd aanwezig bij een geslaagde .info-call (elke ticker heeft een
    beurs en een valuta), dus als BEIDE None zijn behandelen we de rij als
    "nog nooit met de huidige velden gevuld" en halen we 'm opnieuw op.
    """
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
