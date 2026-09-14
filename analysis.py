import random
import re
import string
import os
import threading
import pandas as pd
import requests
import yfinance as yf
from flask import g, has_app_context
from yahooquery import search
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import (get_db_connection, save_prices, upsert_prices, get_cached_classifications, save_classification,
                 get_cached_land_sector, save_land_sector, get_cached_etf_sector_verdeling,
                 save_etf_sector_verdeling, get_cached_etf_holdings, save_etf_holdings,
                 get_ticker_details, get_cached_prijscheck, save_prijscheck,
                 get_cached_splits, save_splits, get_cached_openfigi, save_openfigi)
import time

# dprint/meet_tijd/DEBUG staan sinds de module-splitsing (zie CLAUDE.md,
# herstructurering-opdracht) in debug_utils.py — hier opnieuw geïmporteerd
# zodat de rest van dit bestand ongewijzigd kan blijven verwijzen naar
# dprint/meet_tijd.
from debug_utils import DEBUG, dprint, meet_tijd

# _is_corporate_action_row/_sorteer_chronologisch staan in transactie_utils.py
# (afhankelijkheidsloze, kleine helpers, gedeeld door o.a. statistieken.py) —
# hier opnieuw geïmporteerd voor de nog niet verplaatste functies verderop
# in dit bestand.
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch

# Yahoo-call-telling en de rate-limit-/retry-infra staan sinds de module-
# splitsing in yahoo_client.py — hier opnieuw geïmporteerd zodat de nog niet
# verplaatste functies verderop in dit bestand (get_prices, _fetch_yf_info,
# _haal_slotkoers_op, ...) ongewijzigd kunnen blijven werken. Ook
# patch("analysis.download_met_retry")-achtige mocks in de testsuite blijven
# zo werken zolang de aanroepers van deze functies (nog) in analysis.py
# staan.
from yahoo_client import (
    reset_yahoo_call_teller, _tel_yahoo_call, log_yahoo_call_samenvatting,
    RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _is_rate_limit_fout, _met_rate_limit_retry,
    BULK_DOWNLOAD_POGINGEN, BULK_DOWNLOAD_WACHTTIJD, download_met_retry,
)


# Drempels voor de prijscontrole op de Ticker-zekerheid-pagina (zie
# vergelijk_prijs_op_datum): Yahoo's SLOTkoers wordt vergeleken met een
# intraday-transactieprijs uit het Excel-bestand, dus een kleine afwijking
# is normaal en geen teken van een foute ticker.
#   < PRIJSCHECK_DREMPEL_OK              -> "ok" (✓)
#   PRIJSCHECK_DREMPEL_OK..DREMPEL_WAARSCHUWING -> "mild" (🔍, wel even
#     bekijken, maar degradeert een beurs-bevestigde match niet naar Onzeker)
#   >= PRIJSCHECK_DREMPEL_WAARSCHUWING   -> "waarschuwing" (⚠️, telt mee
#     voor het Zeker/Onzeker-oordeel)
PRIJSCHECK_DREMPEL_OK = 0.02
PRIJSCHECK_DREMPEL_WAARSCHUWING = 0.06

# get_prices/_fx_prijzen_serie/FX_PAAR_PER_VALUTA/FX_ANKER_DATUM/
# DREMPEL_HERGEBRUIK_KOERS staan sinds de module-splitsing in prijzen.py --
# hier opnieuw geïmporteerd zodat get_prices() bruikbaar blijft voor app.py
# (via analysis) en _fx_koers_op_datum() hieronder ongewijzigd kan blijven
# werken.
from prijzen import (
    get_prices, _fx_prijzen_serie, FX_PAAR_PER_VALUTA, FX_ANKER_DATUM, DREMPEL_HERGEBRUIK_KOERS,
)

# Drempel voor de standaard, LICHTE prijscontrole (find_ticker_met_snelle_
# prijscheck, i.t.t. de volledige verifieer_ticker_met_prijs hierboven):
# pas boven deze afwijking (ná de stap-2-steekproef) worden ook alternatieve
# tickers doorgerekend -- zie find_ticker_met_snelle_prijscheck().
PRIJSCHECK_DREMPEL_ALTERNATIEVEN = 0.10

# Tier 1 (zie find_ticker_met_snelle_prijscheck): kandidaat staat op een
# VERWACHTE beurs (BEURS_MAP) EN de prijs klopt op minstens dit aantal
# gecontroleerde steekproefdatums -- sterkste, dubbel bevestigde match.
MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE = 2

# Tier 2: GEEN kandidaat op een verwachte beurs voldoet aan tier 1, maar
# een kandidaat op een ANDERE beurs matcht op ALLE gecontroleerde
# steekproefdatums (hogere lat, als compensatie voor het ontbrekende
# beursbewijs -- dit is het Vanguard/iShares-scenario: de juiste notering
# staat op een andere beurs dan verwacht). Pas toepassen als er minstens
# dit aantal steekproefdatums gecontroleerd is, anders is 1 toevalstreffer
# al genoeg voor een "volledige" match.
MIN_STEEKPROEF_VOOR_VOLLEDIGE_MATCH = 2

# Tolerantie op de High/Low-dagrange-check (vergelijk_prijs_op_datum): de
# exacte low <= koers <= high bleek te strak -- bekend-goede tickers
# (VUSA.AS, G2X.DE) hadden een Excel-koers die net (~1-2%) buiten Yahoo's
# High/Low viel, vermoedelijk door net iets andere sluitingsmomenten/
# afronding tussen DEGIRO en Yahoo, niet door een foute ticker.
DAGRANGE_TOLERANTIE = 0.05

# Landen met een aandeel onder deze drempel (fractie van de totale
# portfoliowaarde, dus 0.005 = 0.5%) worden op het Land-tabblad samengevoegd
# tot één "Overig"-taartpunt — anders eindig je met tientallen verwaarloosbare
# taartpunten in de legenda. Zie _voeg_kleine_landen_samen().
LAND_OVERIG_DREMPEL = 0.005

# Landen die meetellen als "Europa" voor de Europa-samenvoeg-toggle op het
# Land-tabblad (zie _groepeer_europa_samen). EU-landen plus de gebruikelijke
# niet-EU-Europese landen (UK, Zwitserland, Noorse/Balkan-landen, micro-
# staten). Namen zoals ze typisch terugkomen uit Yahoo's .info["country"]
# en pycountry se .name — bewust een paar synoniemen (bv. "Czechia" én
# "Czech Republic") omdat beide bronnen niet altijd dezelfde naam geven.
#
# Bewuste keuzes (geen omissie): Rusland en Turkije zijn NIET meegenomen —
# beide worden in investeringscontext (MSCI e.d.) als Emerging Markets
# geclassificeerd, niet als (Westers) Europa, en dat is hier de relevante
# maatstaf, niet pure aardrijkskunde.
EUROPESE_LANDEN = frozenset({
    # EU-landen
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic",
    "Czechia", "Denmark", "Estonia", "Finland", "France", "Germany",
    "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania",
    "Luxembourg", "Malta", "Netherlands", "Poland", "Portugal", "Romania",
    "Slovakia", "Slovenia", "Spain", "Sweden",
    # Niet-EU, wel gebruikelijk "Europa"
    "United Kingdom", "Switzerland", "Norway", "Iceland", "Liechtenstein",
    "Monaco", "Andorra", "San Marino", "Vatican City", "Jersey", "Guernsey",
    "Isle of Man", "Ukraine", "Belarus", "Serbia", "Bosnia and Herzegovina",
    "Montenegro", "North Macedonia", "Albania", "Kosovo", "Moldova",
})

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

# Benchmarks voor de rendement-vergelijking (zie bereken_benchmark_
# vergelijking hieronder) -- allemaal accumulerende (Acc.) UCITS-ETF's in
# EUR, zodat get_prices() ze zonder extra dividend-boekhouding kan gebruiken.
# S&P 500/Nasdaq 100 waren al bekend uit ETF_HOLDINGS_BRON; AEX is apart
# opgezocht en getest (yf.Ticker("IAEA.AS").info -> "iShares AEX UCITS ETF
# EUR (Acc)", koersdata vanaf 2020-07-29) -- er bestaat geen accumulerende
# AEX-ETF met een langere koershistorie op Yahoo.
BENCHMARK_TICKERS = {
    "S&P 500": "VUSA.AS",
    "Nasdaq 100": "CNDX.AS",
    "AEX": "IAEA.AS",
}

# Handmatige overrides voor bedrijfsnamen die _normaliseer_bedrijfsnaam()
# (lowercase + leestekens weg) niet tot dezelfde sleutel herleidt, omdat
# providers niet alleen qua casing/leestekens verschillen maar ook qua
# woordkeuze zelf (bv. de rechtspersoonsvorm "NV" wel/niet meegeschreven).
# Zelfde stijl/plek als MANUAL_TICKER_OVERRIDES hierboven -- aanvullen
# zodra een dubbele rij in Top 10 bedrijven / ETF-overlap in de praktijk
# opvalt. Sleutel en waarde zijn allebei al door de leesteken-normalisatie
# heen (dus lowercase, geen leestekens); de waarde is de canonieke sleutel
# waar de linkerkant naartoe gemapt wordt.
BEDRIJF_NAAM_OVERRIDES = {
    "asml holding": "asml holding nv",
    "asml": "asml holding nv",
}

# De volledige ETF-holdings-provider-parsing (iShares/Vanguard/VanEck CSV/
# XLSX, ETF_HOLDINGS_BRON) staat sinds de module-splitsing in
# etf_holdings_provider.py -- hier opnieuw geimporteerd zodat get_etf_holdings()
# verderop in dit bestand ongewijzigd kan blijven werken.
from etf_holdings_provider import (
    ETF_HOLDINGS_BRON, _PROVIDER_PARSERS, fetch_provider_holdings,
)

# compute_split_adjusted_shares/compute_value_over_time/compute_per_ticker/
# compute_per_ticker_koers_en_aankopen/debug_position staan sinds de module-
# splitsing in portfolio_calc.py -- hier opnieuw geïmporteerd (o.a. voor
# app.py's bestaande "from analysis import ...").
from portfolio_calc import (
    compute_split_adjusted_shares, compute_value_over_time, compute_per_ticker,
    compute_per_ticker_koers_en_aankopen, debug_position,
)


CODE_LENGTH = 3


def is_geldige_code(code):
    """Zelfde regels als een gegenereerde code (zie generate_code): exact
    CODE_LENGTH hoofdletters A-Z. Wordt hergebruikt bij het valideren van
    een door de gebruiker zelf gekozen nieuwe code (code-wijzigen)."""
    return bool(re.fullmatch(rf"[A-Z]{{{CODE_LENGTH}}}", code or ""))


def generate_code(cur, length=CODE_LENGTH):
    """Genereert een unieke portfolio-code die nog niet in gebruik is."""
    chars = string.ascii_uppercase
    while True:
        code = "".join(random.choices(chars, k=length))
        cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
        if cur.fetchone() is None:
            return code


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


def _naar_basis_vorm(beurs, resultaat):
    """
    Wikkelt een find_ticker_met_snelle_prijscheck()-resultaat in dezelfde
    placeholder-vorm als verifieer_ticker_met_prijs(), zodat de frontend-
    kaart (maakTickerZekerheidKaart) dit zonder aanpassing kan tonen. Velden
    die alleen de VOLLEDIGE prijsverificatie kan invullen (land, sector,
    valuta, ...) staan hier bewust op None i.p.v. weggelaten.
    'prijs_checks'/'waarschuwing'/'aanbevolen_alternatief' komen wél uit de
    lichte check, dus een positie met een echte afwijking laat dat hier
    alsnog zien. Gedeeld door basis_ticker_zekerheid() (1 positie) en
    basis_ticker_zekerheid_parallel() (meerdere tegelijk).
    """
    basis = {
        "ticker": resultaat["ticker"],
        "zekerheid": resultaat["zekerheid"],
        "waarschuwing": resultaat.get("prijswaarschuwing"),
        "is_etf": None, "land": None, "sector": None, "top_holding_land": None, "valuta": None,
        "fondsfamilie": None, "category": None, "quote_type": None,
        "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
        "prijs_checks": resultaat.get("prijs_checks", []), "alternatieven": [],
        "basis_alleen": True,
        "openfigi_root_bekend": resultaat.get("openfigi_root_bekend"),
        "openfigi_root_matches": resultaat.get("openfigi_root_matches"),
    }
    if resultaat.get("aanbevolen_alternatief"):
        basis["aanbevolen_alternatief"] = resultaat["aanbevolen_alternatief"]
    return basis


def basis_ticker_zekerheid(product, isin, beurs, transacties_van_dit_isin=None):
    """
    Lichtgewicht ticker-zekerheid-dict, in dezelfde vorm als
    verifieer_ticker_met_prijs() maar zonder de dure, volledige Yahoo-
    prijsverificatie (die alle 3 steekproefdatums én alle kandidaten
    doorrekent) -- gebruikt find_ticker_met_snelle_prijscheck(), dat in het
    gangbare geval (geen afwijking) maar 1 extra, gecachete Yahoo-call
    kost. Bedoeld voor het 'niet opslaan'-pad in app.py: de VOLLEDIGE check
    mag daar niet meer standaard/synchroon voor de hele portfolio draaien
    (kan bij een grotere portfolio met een koude cache ruim over de
    gunicorn-timeout heen lopen, zie verifieer_tickers_met_prijs_parallel()
    hieronder). De uitgebreide check blijft beschikbaar als losse, door de
    gebruiker aangevraagde actie.

    Voor MEERDERE posities tegelijk: gebruik basis_ticker_zekerheid_parallel()
    hieronder, niet deze functie in een for-loop -- zie die docstring voor
    waarom (koude-cache-timeoutrisico).
    """
    resultaat = find_ticker_met_snelle_prijscheck(product, isin, beurs, transacties_van_dit_isin or [])
    return _naar_basis_vorm(beurs, resultaat)


# Poolgrootte voor de LICHTE ticker-resolutie (basis_ticker_zekerheid_parallel/
# vind_tickers_met_snelle_prijscheck_parallel hieronder) -- empirisch bepaald
# op een cold-cache-test met 28 posities: 4->8 workers halveerde de totale
# tijd bijna, 8->12 gaf nog een reële extra winst (~13%), 12->16 nauwelijks
# meer, zonder aantoonbaar hoger rate-limit-risico bij 12. Losstaand van de
# poolgrootte van de dúre verificatiecheck (verifieer_tickers_met_prijs_
# parallel hieronder, max_workers=6) -- die bleef bewust lager, buiten deze
# meting.
TICKER_RESOLUTIE_POOL_GROOTTE = 12


def basis_ticker_zekerheid_parallel(posities, max_workers=TICKER_RESOLUTIE_POOL_GROOTTE):
    """
    basis_ticker_zekerheid() voor meerdere posities tegelijk
    (ThreadPoolExecutor) -- zie vind_tickers_met_snelle_prijscheck_parallel()
    hieronder voor de reden: de lichte prijscheck (find_ticker_met_snelle_
    prijscheck) draait nu bij ELKE upload, dus bij een portfolio met veel
    unieke, nog nooit gecontroleerde tickers (koude ticker_prijscheck-cache)
    zou zelfs 1 Yahoo-call per positie SEQUENTIEEL al genoeg kunnen optellen
    om de 'niet opslaan'-gunicorn-timeoutfix weer te ondermijnen (zie
    CLAUDE.md, vervolg op het Statistieken-incident van 2026-08-31).

    posities: lijst van (product, isin, beurs, transacties_van_dit_isin).
    Geeft een lijst van basis-vorm-dicts terug, in dezelfde volgorde.
    """
    ruwe_resultaten = vind_tickers_met_snelle_prijscheck_parallel(posities, max_workers=max_workers)
    return [
        _naar_basis_vorm(beurs, resultaat)
        for (_product, _isin, beurs, _transacties), resultaat in zip(posities, ruwe_resultaten)
    ]


def get_order_id_sets(cur):
    """Geeft per portfolio-code de set van al opgeslagen Order ID's terug."""
    cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def find_matching_code(cur, new_order_ids):
    """
    Zoekt een bestaande portfolio die dezelfde persoon vertegenwoordigt:
    - bestaande data zit volledig in de nieuwe upload (update met extra transacties), of
    - de nieuwe upload zit volledig in de bestaande data (niets nieuws)
    Geeft (code, ontbrekende_order_ids) terug, of (None, None) als er geen match is.
    """
    existing = get_order_id_sets(cur)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None


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


def _normaliseer_bedrijfsnaam(naam):
    """
    Normaliseert een bedrijfsnaam tot een matchbare sleutel: lowercase,
    leestekens weg (zonder spatie toe te voegen, dus "N.V." -> "nv", niet
    "n v"), whitespace samengevoegd. Vangt het gros van de casing-/
    leesteken-verschillen tussen ETF-providers ("Apple Inc" vs "APPLE
    INC"). Voor hardnekkige uitzonderingen die dit niet oplost (een
    providernaam mist een heel woord, bv. "ASML Holding NV" vs "ASML
    HOLDING") is er BEDRIJF_NAAM_OVERRIDES, zelfde patroon als
    MANUAL_TICKER_OVERRIDES.

    Geeft "" terug voor een lege/None naam -- aanroepers slaan zo'n
    holding dan over i.p.v.'m onder een valse gedeelde sleutel te tellen.
    """
    if not naam:
        return ""
    schoon = re.sub(r"[^a-z0-9\s]", "", naam.lower())
    schoon = re.sub(r"\s+", " ", schoon).strip()
    return BEDRIJF_NAAM_OVERRIDES.get(schoon, schoon)


def _sorteer_tickers_voor_dropdown(per_ticker):
    """
    Sorteert tickers voor de dropdown op 'Per aandeel' en 'Per aandeel
    aankoop': eerst posities die nog in bezit zijn (groot naar klein op
    huidige waarde), daarna verkochte posities (groot naar klein op de
    hoogste waarde die de positie ooit heeft gehad).
    """
    def sleutel(ticker):
        reeks = per_ticker[ticker]["waarde"]
        huidige_waarde = reeks[-1] if reeks else 0.0
        piekwaarde = max(reeks) if reeks else 0.0
        if per_ticker[ticker]["nog_in_bezit"]:
            return (0, -huidige_waarde)
        return (1, -piekwaarde)

    return sorted(per_ticker.keys(), key=sleutel)


def _sorteer_verdeling_groot_naar_klein(verdeling):
    """Sorteert een verdelingslijst (dicts met 'waarde') van grootste naar
    kleinste waarde, zodat het taartdiagram op Verdeling aflopend oogt."""
    return sorted(verdeling, key=lambda x: x["waarde"], reverse=True)


def bereken_bedrijven_verdeling(transacties_df, price_data, is_etf_map, top_n=20):
    """
    Top-N onderliggende bedrijven van de hele portfolio (via ETF's + losse
    aandelen), met per bedrijf een uitsplitsing van via welke posities
    (ETF-ticker of los aandeel) die blootstelling ontstaat -- zo blijft
    "dubbele blootstelling" zichtbaar als hetzelfde bedrijf zowel via een
    of meer ETF's als los wordt aangehouden. Zelfde basis als
    compute_land_sector_verdeling() hierboven: huidige holdings
    (aantal x laatste koers, de "aantal"-kolom, niet "adj_aantal") zodat
    de totalen op elkaar aansluiten. Voor de gestapelde-staafgrafiek-
    weergave op het "Top 20 bedrijven"-tabblad (zie static/js/app.js,
    renderGestapeldeStaafgrafiek): "totaal_pct" en "per_bron" zijn beide al
    percentages van totaal_waarde, dus per_bron-waarden per bedrijf tellen
    op tot totaal_pct van dat bedrijf -- direct bruikbaar als stack.

    Geeft terug:
        {
            "top": [
                {"bedrijf": "Apple Inc", "waarde": 1234.56, "totaal_pct": 24.69,
                 "per_bron": {"CSPX.AS": 16.0, "AAPL": 8.69}},
                ...
            ],  # aflopend gesorteerd op waarde, max top_n items
            "overig": 321.00,      # bedrijven buiten de top-N + het
                                    # niet-gedekte restant van ETF-holdings
                                    # (bv. bij een fonds met alleen
                                    # yfinance-top10-dekking) samen -- zelfde
                                    # eerlijkheidsprincipe als bij Land: dit
                                    # deel NIET verdoezelen als "compleet".
            "dekking_pct": 0.92,   # fractie van totaal_waarde die
                                    # daadwerkelijk aan een bekend bedrijf
                                    # is toegewezen (dus 1 - onbekend-restant)
            "totaal_waarde": 5000.0,
            "bronnen": [{"ticker": "CSPX.AS", "naam": "ISHARES CORE MSCI WORLD..."}, ...],
                # alle bronnen die ergens in de top-N voorkomen, aflopend op
                # totale bijdrage -- voor een consistente legenda-volgorde in
                # de frontend. "naam" komt uit de bestaande product-/
                # bijnaam-kolom in transacties_df, geen extra yfinance-call.
        }
    """
    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    bron_namen = {}
    if "product" in transacties_df.columns:
        bron_namen = (
            transacties_df.dropna(subset=["ticker"])
            .drop_duplicates(subset=["ticker"], keep="last")
            .set_index("ticker")["product"]
            .to_dict()
        )

    bedrijven = {}
    totaal_waarde = 0.0
    gedekte_waarde = 0.0

    def voeg_toe(key, weergavenaam, bron_ticker, bedrag):
        entry = bedrijven.setdefault(key, {"naam": weergavenaam, "waarde": 0.0, "per_bron": {}})
        entry["waarde"] += bedrag
        entry["per_bron"][bron_ticker] = entry["per_bron"].get(bron_ticker, 0.0) + bedrag

    for ticker, aantal in huidige_holdings.items():
        if ticker not in price_data.columns:
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        if waarde <= 0:
            continue
        totaal_waarde += waarde

        if is_etf_map.get(ticker, False):
            holdings = get_etf_holdings(ticker)
            gedekt_gewicht = min(sum(h["gewicht"] for h in holdings), 1.0)
            gedekte_waarde += waarde * gedekt_gewicht
            for h in holdings:
                key = _normaliseer_bedrijfsnaam(h["holding_naam"])
                if not key:
                    continue
                voeg_toe(key, h["holding_naam"], ticker, waarde * h["gewicht"])
        else:
            weergavenaam = ticker
            aandeel_rijen = transacties_df.loc[transacties_df["ticker"] == ticker, "echte_naam"] \
                if "echte_naam" in transacties_df.columns else None
            if aandeel_rijen is not None and aandeel_rijen.notna().any():
                weergavenaam = aandeel_rijen.dropna().iloc[-1]
            key = _normaliseer_bedrijfsnaam(weergavenaam) or ticker
            gedekte_waarde += waarde
            voeg_toe(key, weergavenaam, ticker, waarde)

    gesorteerd = sorted(bedrijven.values(), key=lambda e: e["waarde"], reverse=True)
    top = gesorteerd[:top_n]
    overig = sum(e["waarde"] for e in gesorteerd[top_n:]) + (totaal_waarde - gedekte_waarde)

    def naar_pct(bedrag):
        return (bedrag / totaal_waarde * 100) if totaal_waarde > 0 else 0.0

    bron_totalen = {}
    top_resultaat = []
    for e in top:
        for bron_ticker, bedrag in e["per_bron"].items():
            bron_totalen[bron_ticker] = bron_totalen.get(bron_ticker, 0.0) + bedrag
        top_resultaat.append({
            "bedrijf": e["naam"],
            "waarde": round(e["waarde"], 2),
            "totaal_pct": round(naar_pct(e["waarde"]), 4),
            "per_bron": {k: round(naar_pct(v), 4) for k, v in e["per_bron"].items()},
        })

    bronnen_gesorteerd = sorted(bron_totalen.keys(), key=lambda t: bron_totalen[t], reverse=True)

    return {
        "top": top_resultaat,
        "overig": round(overig, 2),
        "dekking_pct": (gedekte_waarde / totaal_waarde) if totaal_waarde > 0 else 0.0,
        "totaal_waarde": round(totaal_waarde, 2),
        "bronnen": [{"ticker": t, "naam": bron_namen.get(t, t)} for t in bronnen_gesorteerd],
    }


def bereken_etf_overlap(transacties_df, price_data, is_etf_map):
    """
    Overlap-matrix tussen alle aangehouden ETF's: per paar het percentage
    gedeelde onderliggende bedrijven, gewogen op holdings-gewicht (de
    gangbare "portfolio overlap %"-maat: som over gedeelde bedrijven van
    min(gewicht_i, gewicht_j)). Volledig symmetrisch ({a:{b:...}, b:{a:...}}
    met dezelfde waarde) zodat de frontend niet zelf hoeft te spiegelen;
    geen entry voor een fonds tegen zichzelf.

    Minder dan 2 aangehouden ETF's -> lege dict (de frontend toont dan een
    duidelijke melding i.p.v. een lege/kapotte matrix).
    """
    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()

    tickers = [t for t in huidige_holdings.index if t in price_data.columns]
    etf_tickers = [t for t in tickers if is_etf_map.get(t, False)]

    if len(etf_tickers) < 2:
        return {}

    gewichten_per_etf = {}
    for ticker in etf_tickers:
        gewichten = {}
        for h in get_etf_holdings(ticker):
            key = _normaliseer_bedrijfsnaam(h["holding_naam"])
            if not key:
                continue
            gewichten[key] = gewichten.get(key, 0.0) + h["gewicht"]
        gewichten_per_etf[ticker] = gewichten

    matrix = {t: {} for t in etf_tickers}
    for i, etf_a in enumerate(etf_tickers):
        for etf_b in etf_tickers[i + 1:]:
            gew_a = gewichten_per_etf[etf_a]
            gew_b = gewichten_per_etf[etf_b]
            gedeeld = set(gew_a) & set(gew_b)
            overlap = sum(min(gew_a[k], gew_b[k]) for k in gedeeld)
            matrix[etf_a][etf_b] = overlap
            matrix[etf_b][etf_a] = overlap

    return matrix


def _voeg_kleine_landen_samen(land_dict, drempel=LAND_OVERIG_DREMPEL, uitgezonderd=frozenset()):
    """Voegt landen met een aandeel onder 'drempel' (fractie van het totaal,
    dus 0.005 = 0.5%) samen tot één 'Overig'-post — voorkomt een taart met
    tientallen verwaarloosbare taartpunten in de legenda.

    'Unknown' is GEEN uitzondering: valt die zelf ook onder de drempel, dan
    telt 'ie gewoon mee in de Overig-som net als elk ander klein land; is
    Unknown >= drempel, dan blijft die als eigen categorie bestaan naast
    Overig (frontend geeft beide dezelfde neutrale grijze stijl + plek
    onderaan de legenda, zie ONBEKEND_GRIJS in app.js).

    'uitgezonderd' zijn sleutels die NOOIT in Overig terechtkomen, ongeacht
    hun aandeel — gebruikt door compute_land_sector_verdeling() om de
    (bewust door de gebruiker aangezette) "Europe"-post altijd als eigen
    taartpunt te tonen, ook als die toevallig <0.5% is: dat is dan een
    expliciete keuze van de gebruiker, geen toevallig verwaarloosbaar land.

    Geeft GEEN 'Overig'-sleutel terug als niets onder de drempel valt (dus
    nooit een lege/0%-Overig-punt). Bij een leeg/nul-totaal wordt de dict
    ongewijzigd teruggegeven (kan niet zinnig een percentage berekenen)."""
    totaal = sum(land_dict.values())
    if totaal <= 0:
        return dict(land_dict)

    resultaat = {}
    overig = 0.0
    for land, bedrag in land_dict.items():
        if land not in uitgezonderd and bedrag / totaal < drempel:
            overig += bedrag
        else:
            resultaat[land] = bedrag

    if overig > 0:
        resultaat["Overig"] = resultaat.get("Overig", 0.0) + overig
    return resultaat


def _groepeer_europa_samen(land_dict, europese_landen=EUROPESE_LANDEN):
    """Voegt alle landen uit 'europese_landen' samen tot één 'Europe'-post;
    niet-Europese landen (en 'Unknown') blijven ongewijzigd los staan.

    Geeft GEEN 'Europe'-sleutel terug als geen enkel land in land_dict
    Europees is (dus nooit een lege/0%-Europe-punt)."""
    resultaat = {}
    europa_totaal = 0.0
    for land, bedrag in land_dict.items():
        if land in europese_landen:
            europa_totaal += bedrag
        else:
            resultaat[land] = bedrag

    if europa_totaal > 0:
        resultaat["Europe"] = resultaat.get("Europe", 0.0) + europa_totaal
    return resultaat


def _groepeer_europa_samen_per_bron(land_per_bron_dict, europese_landen=EUROPESE_LANDEN):
    """Zelfde idee als _groepeer_europa_samen(), maar dan op de per-bron-
    uitgesplitste land_per_bron-structuur ({land: {bron: bedrag}}) --
    gebruikt door de Europa-samenvoeg-toggle op de staafgrafiek-weergave
    van het Land-tabblad (renderGestapeldeStaafgrafiek in app.js). De
    per-bron-bedragen van elk Europees land worden per bron opgeteld onder
    een gezamenlijke "Europe"-rij; niet-Europese landen blijven ongewijzigd."""
    resultaat = {}
    europa_per_bron = {}
    for land, per_bron in land_per_bron_dict.items():
        if land in europese_landen:
            for bron, bedrag in per_bron.items():
                europa_per_bron[bron] = europa_per_bron.get(bron, 0.0) + bedrag
        else:
            resultaat[land] = dict(per_bron)

    if europa_per_bron:
        resultaat["Europe"] = europa_per_bron
    return resultaat


def compute_land_sector_verdeling(transacties_df, price_data, is_etf_map):
    """
    Land- en sectorverdeling van de hele portfolio (huidige holdings x
    laatste koers — zelfde basis als de ETF/aandeel-verdeling hierboven,
    dus met dezelfde "aantal"-kolom, niet "adj_aantal", zodat de totalen
    van beide verdelingen op elkaar aansluiten), plus dezelfde verdeling
    per ETF afzonderlijk (voor de per-ETF-drill-down).

    Geeft terug:
        {
            "land":   {"United States": 1234.56, ..., "Unknown": 88.00},
            "land_europa": {"United States": 1234.56, ..., "Europe": 456.00},
                # zelfde als "land", maar met alle EUROPESE_LANDEN samengevoegd
                # tot één "Europe"-post — voor de Europa-samenvoeg-toggle op
                # het Land-tabblad (frontend kiest tussen de twee, geen
                # her-berekening nodig bij het aan/uit-zetten van de toggle)
            "sector": {"Technology": 999.00, ..., "Unknown": 45.00},
            "per_etf": {
                "CSPX.AS": {"land": {...}, "sector": {...}},   # fracties 0-1, dit fonds z'n eigen verdeling
                ...
            },
            "land_per_bron": {
                "United States": {"CSPX.AS": 800.0, "AAPL": 200.0}, ...
            },  # zelfde totalen als "land", maar per categorie uitgesplitst
                # naar welke positie (ETF-ticker of los aandeel) 'm inbrengt
                # -- voor de gestapelde-staafgrafiek-weergave op het Land-
                # tabblad (renderGestapeldeStaafgrafiek in app.js). LET OP:
                # dit is de RUWE, ongegroepeerde verdeling (geen Overig-
                # samenvoeging zoals bij "land"/"land_europa" -- die
                # drempel-groepering slaat een keuze in het totaal-bedrag,
                # niet in de per-bron-uitsplitsing, dus daar los van
                # gehouden; de Overig-balk in de staafgrafiek wordt i.p.v.
                # daarvan client-side bepaald op basis van top-10, zie
                # opts.maxCategorieen in renderGestapeldeStaafgrafiek).
            "land_per_bron_europa": {...},  # zelfde als "land_per_bron",
                # maar met alle EUROPESE_LANDEN samengevoegd tot één
                # "Europe"-rij (per bron opgeteld) -- zodat de Europa-
                # samenvoeg-toggle ook in de staafgrafiek-weergave werkt,
                # niet alleen in de taart/platte weergave.
            "sector_per_bron": {...},  # zelfde idee, voor sector
        }
    """
    def optellen(dct, key, bedrag):
        key = key or "Unknown"
        dct[key] = dct.get(key, 0.0) + bedrag

    def optellen_per_bron(dct, key, bron_ticker, bedrag):
        key = key or "Unknown"
        rij = dct.setdefault(key, {})
        rij[bron_ticker] = rij.get(bron_ticker, 0.0) + bedrag

    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    land = {}
    sector = {}
    per_etf = {}
    land_per_bron = {}
    sector_per_bron = {}

    for ticker, aantal in huidige_holdings.items():
        if ticker not in price_data.columns:
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        if waarde <= 0:
            continue

        if is_etf_map.get(ticker, False):
            # Sectorverdeling van het fonds zelf, als fracties 0-1 die samen
            # ~1.0 optellen; het niet-gedekte restant (mislukte/lege call,
            # of gewoon een sector die Yahoo niet meegeeft) gaat naar Unknown.
            sector_verdeling = get_etf_sector_verdeling(ticker)
            etf_sector_pct = dict(sector_verdeling)
            restant_sector = max(0.0, 1.0 - sum(sector_verdeling.values()))
            if restant_sector > 1e-9:
                etf_sector_pct["Unknown"] = etf_sector_pct.get("Unknown", 0.0) + restant_sector

            # Landverdeling via de holdings-lijst; alles wat niet gedekt is
            # (bij yfinance: alles buiten de top 10; bij een provider-CSV
            # normaal maar een klein restje "cash"/niet-herkende posities)
            # gaat naar Unknown. "bron" laat zien welke van de twee het was
            # — bepalend voor hoe compleet deze landverdeling is.
            holdings = get_etf_holdings(ticker)
            land_bron = holdings[0]["bron"] if holdings else "yfinance_top10"
            etf_land_pct = {}
            for h in holdings:
                etf_land_pct[h["land"] or "Unknown"] = etf_land_pct.get(h["land"] or "Unknown", 0.0) + h["gewicht"]
            restant_land = max(0.0, 1.0 - sum(h["gewicht"] for h in holdings))
            if restant_land > 1e-9:
                etf_land_pct["Unknown"] = etf_land_pct.get("Unknown", 0.0) + restant_land

            for naam, gewicht in etf_sector_pct.items():
                optellen(sector, naam, waarde * gewicht)
                optellen_per_bron(sector_per_bron, naam, ticker, waarde * gewicht)
            for naam, gewicht in etf_land_pct.items():
                optellen(land, naam, waarde * gewicht)
                optellen_per_bron(land_per_bron, naam, ticker, waarde * gewicht)

            per_etf[ticker] = {"land": etf_land_pct, "sector": etf_sector_pct, "land_bron": land_bron}
        else:
            aandeel_land, aandeel_sector = get_land_sector(ticker)
            optellen(land, aandeel_land, waarde)
            optellen_per_bron(land_per_bron, aandeel_land, ticker, waarde)
            optellen(sector, aandeel_sector, waarde)
            optellen_per_bron(sector_per_bron, aandeel_sector, ticker, waarde)

    land_europa_gegroepeerd = _groepeer_europa_samen(land)
    return {
        "land": _voeg_kleine_landen_samen(land),
        "land_europa": _voeg_kleine_landen_samen(
            land_europa_gegroepeerd,
            uitgezonderd={"Europe"} if "Europe" in land_europa_gegroepeerd else frozenset(),
        ),
        "sector": sector,
        "per_etf": per_etf,
        "land_per_bron": land_per_bron,
        "land_per_bron_europa": _groepeer_europa_samen_per_bron(land_per_bron),
        "sector_per_bron": sector_per_bron,
    }


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

    Let op: ticker_info had oorspronkelijk alleen een is_etf-kolom; deze
    extra velden kwamen er later bij (ALTER TABLE ADD COLUMN, geen backfill
    voor bestaande rijen). Een rij die van vóór die uitbreiding dateert heeft
    dus is_etf gezet maar alle nieuwe velden NULL — dat is niet hetzelfde
    als "succesvol gecontroleerd en er is gewoon geen data" (bv. land/sector
    zijn voor een ETF legitiem None). valuta en quote_type zijn vrijwel
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


def _haal_slotkoers_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Haalt de slotkoers van 'ticker' op de eerste geldige handelsdag op of ná
    'datum' op (buffer voor weekend/feestdagen waarop de markt dicht was),
    in de eigen valuta van de ticker — GEEN EUR-conversie, dit is puur een
    identiteitscheck (klopt de prijs), geen waardeberekening. Retry/backoff
    bij rate limiting via _met_rate_limit_retry (zelfde patroon als
    _fetch_yf_info). Geeft None terug als het na alle retries niet lukt of
    er geen koersdata is.
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(slotkoers)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)["Close"]

    raw, fout = _met_rate_limit_retry(_actie, "prijscheck", f"'{ticker}'", pogingen, wachttijd)
    if fout is not None:
        # print(f"[prijscheck] ❌ kon historische koers niet ophalen voor '{ticker}' rond {datum}: {fout}")
        return None

    if isinstance(raw, pd.DataFrame):
        # yf.download geeft bij 1 ticker soms toch een DataFrame terug i.p.v. een Series
        raw = raw[ticker] if ticker in raw.columns else raw.iloc[:, 0]

    geldig = raw.dropna()
    if geldig.empty:
        # print(f"[prijscheck] ⚠️ geen koersdata gevonden voor '{ticker}' rond {datum}")
        return None

    return float(geldig.iloc[0])


def _haal_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Zelfde als _haal_slotkoers_op hierboven (retry/backoff + weekend/
    feestdag-buffer), maar geeft (high, low) van de handelsdag terug i.p.v.
    de slotkoers -- voor de dagrange-check op de Ticker-zekerheid-pagina
    (staat de Excel-transactieprijs tussen het intraday-high en -low). Losse
    functie i.p.v. _haal_slotkoers_op uit te breiden: die wordt ook gebruikt
    voor FX-koersen (via _fx_prijzen_serie -> get_prices()), waar een
    dagrange niet relevant is.
    Geeft (None, None) terug bij dezelfde faalcondities als _haal_slotkoers_op.
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(dagrange)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)[["High", "Low"]]

    raw, fout = _met_rate_limit_retry(_actie, "prijscheck", f"dagrange '{ticker}'", pogingen, wachttijd)
    if fout is not None:
        # print(f"[prijscheck] ❌ kon dagrange niet ophalen voor '{ticker}' rond {datum}: {fout}")
        return None, None

    if isinstance(raw.columns, pd.MultiIndex):
        # yf.download geeft bij 1 ticker soms toch multi-index-kolommen terug.
        raw.columns = raw.columns.get_level_values(0)

    geldig = raw.dropna()
    if geldig.empty:
        # print(f"[prijscheck] ⚠️ geen dagrange gevonden voor '{ticker}' rond {datum}")
        return None, None

    eerste = geldig.iloc[0]
    return float(eerste["High"]), float(eerste["Low"])


def _haal_koers_en_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Combinatie van _haal_slotkoers_op() en _haal_dagrange_op() hierboven in
    ÉÉN yf.download()-call i.p.v. twee losse downloads voor exact dezelfde
    ticker + periode -- gebruikt door vergelijk_prijs_op_datum() in het pad
    waar altijd zowel de slotkoers als de dagrange nodig zijn (de eerste,
    verse fetch). _haal_slotkoers_op()/_haal_dagrange_op() zelf blijven
    ongewijzigd bestaan voor plekken die er maar één van nodig hebben: het
    FX-pad (_fx_prijzen_serie(), dagrange niet relevant) en de
    ticker_prijscheck-cache-backfill in vergelijk_prijs_op_datum() (daar is
    de slotkoers al bekend uit de cache, alleen de dagrange ontbreekt nog).

    Geeft (slotkoers, high, low) terug, of (None, None, None) bij dezelfde
    faalcondities als _haal_slotkoers_op/_haal_dagrange_op (mislukte
    download na alle retries, of geen koersdata in de periode).
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(slotkoers+dagrange)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)[["Close", "High", "Low"]]

    raw, fout = _met_rate_limit_retry(_actie, "prijscheck", f"'{ticker}'", pogingen, wachttijd)
    if fout is not None:
        # print(f"[prijscheck] ❌ kon historische koers/dagrange niet ophalen voor '{ticker}' rond {datum}: {fout}")
        return None, None, None

    if isinstance(raw.columns, pd.MultiIndex):
        # yf.download geeft bij 1 ticker soms toch multi-index-kolommen terug.
        raw.columns = raw.columns.get_level_values(0)

    geldig = raw.dropna()
    if geldig.empty:
        # print(f"[prijscheck] ⚠️ geen koersdata gevonden voor '{ticker}' rond {datum}")
        return None, None, None

    eerste = geldig.iloc[0]
    return float(eerste["Close"]), float(eerste["High"]), float(eerste["Low"])


def _haal_splits_op(ticker):
    """
    Haalt de bekende aandelensplitsingen van 'ticker' op via yfinance,
    gecachet (tabel ticker_splits, max 30 dagen oud — anders dan
    ticker_prijscheck kan een ticker in de TOEKOMST een nieuwe split doen,
    dus deze cache mag niet voor altijd blijven staan). Geeft {iso_datum:
    ratio} terug — een leeg dict betekent "voor zover bekend geen splits",
    en wordt net als bij ticker_prijscheck gewoon gecachet. Bij een
    mislukte lookup ook een leeg dict, maar dan NIET gecached (geen crash,
    gewoon geen correctie toepassen; wel opnieuw proberen bij de volgende
    aanroep in plaats van een tijdelijke netwerkfout te bevriezen).
    """
    cached = get_cached_splits(ticker)
    if cached is not None:
        dprint(f"[splits] '{ticker}': uit cache -> {len(cached)} split(s)")
        return cached
    try:
        _tel_yahoo_call("yf.Ticker.splits")
        splits = yf.Ticker(ticker).splits
    except Exception as e:
        # print(f"[splits] kon split-geschiedenis niet ophalen voor '{ticker}': {e}")
        return {}
    resultaat = {pd.Timestamp(datum).date().isoformat(): float(ratio) for datum, ratio in splits.items()}
    # print(f"[splits] '{ticker}': opgehaald -> {len(resultaat)} split(s)")
    save_splits(ticker, resultaat)
    return resultaat


def _cumulatieve_split_factor(ticker, vanaf_datum):
    """
    Cumulatieve vermenigvuldigingsfactor van alle splits die voor 'ticker'
    hebben plaatsgevonden NA 'vanaf_datum' (tot nu).

    Nodig omdat _haal_slotkoers_op met auto_adjust=True werkt: een
    historische Yahoo-slotkoers van vóór een latere split komt terug op de
    HUIDIGE aandelen-basis (dus bv. 1/3e van de destijds werkelijk
    verhandelde prijs na een 3-voor-1-split), terwijl de Excel/DEGIRO-
    transactieprijs de ruwe, ongecorrigeerde prijs van dat moment is.
    Zonder deze correctie lijkt elke split op een (soms drastisch) foute
    ticker — zie het BYD/BY6.MU-voorbeeld waar één oude transactiedatum
    71% "afweek" terwijl een recentere datum prima klopte.
    """
    splits = _haal_splits_op(ticker)
    if not splits:
        return 1.0
    vanaf_datum = pd.Timestamp(vanaf_datum)
    factor = 1.0
    for datum_str, ratio in splits.items():
        if pd.Timestamp(datum_str) > vanaf_datum:
            factor *= ratio
    return factor


def _fx_koers_op_datum(valuta, datum, dagen_buffer=7, verversen=True):
    """
    FX-koers (valuta -> EUR) op de eerste geldige handelsdag op of ná
    'datum' (zelfde weekend/feestdag-buffer als _haal_slotkoers_op), voor
    het omrekenen van een LOSSE historische Yahoo-slotkoers in
    vergelijk_prijs_op_datum() naar EUR. Haalt de ruwe FX-reeks op via
    _fx_prijzen_serie() (persistent gecached via prijzen/get_prices(), zie
    daar) i.p.v. zelf een download te doen -- zelfde valutaset als
    _converteer_naar_eur() (die get_prices() gebruikt): alleen USD/GBP/GBp
    worden herkend, dat dekt de fondsen/aandelen die dit project tot nu toe
    tegenkomt. Geeft None terug bij een onbekende valuta of een mislukte
    lookup — de aanroeper behandelt dat dan als "geen betrouwbare
    vergelijking mogelijk", niet als een (mogelijk misleidende) rauwe
    cross-currency-vergelijking.

    `verversen` wordt ongewijzigd doorgegeven aan _fx_prijzen_serie() --
    vergelijk_prijs_op_datum() geeft hier bewust verversen=False door.
    """
    fx_pair = FX_PAAR_PER_VALUTA.get(valuta)
    if fx_pair is None:
        # print(f"[prijscheck] ⚠️ onbekende valuta '{valuta}' voor FX-conversie, geen conversie toegepast")
        return None

    datum = pd.Timestamp(datum)
    einddatum = datum + pd.Timedelta(days=dagen_buffer)
    reeks = _fx_prijzen_serie(valuta, verversen=verversen)
    geldig = reeks[(reeks.index >= datum) & (reeks.index <= einddatum)].dropna()
    if geldig.empty:
        # print(f"[prijscheck] ⚠️ kon FX-koers ({fx_pair}) niet ophalen voor {datum}")
        return None
    return float(geldig.iloc[0])


def vergelijk_prijs_op_datum(ticker, datum, bekende_koers):
    """
    Vergelijkt de DEGIRO-transactieprijs (bekende_koers, altijd EUR — DEGIRO
    boekt alles in EUR, ook bij een niet-EUR-genoteerde ticker zoals NFLX
    via Tradegate) met de historische Yahoo-slotkoers van 'ticker' op
    diezelfde datum, na conversie naar EUR (zie _fx_koers_op_datum) en
    gecorrigeerd voor eventuele splits sindsdien (zie
    _cumulatieve_split_factor). Een grote afwijking is een sterker signaal
    dat de ticker fout is dan beurs-string-matching alleen — een verkeerde
    ticker op de "juiste" beurs geeft alsnog een compleet andere koers.
    Zonder de valutaconversie leek een prima ticker als NFLX (Yahoo-valuta
    USD) een verkeerde match: 68,38 (EUR) vs 82,23 (USD) wijkt puur door de
    ontbrekende EUR/USD-omrekening ~17% af.

    Drie afwijkingsniveaus (zie PRIJSCHECK_DREMPEL_OK/_WAARSCHUWING
    bovenaan dit bestand) i.p.v. simpelweg goed/fout: Yahoo's SLOTkoers
    wordt vergeleken met een intraday-transactieprijs, dus een kleine
    afwijking (tot een paar procent) is normaal en geen teken van een
    foute ticker. 'match' (bool) blijft bestaan voor de bestaande
    zeker/onzeker- en kandidaat-vergelijkingslogica: True voor "ok"/"mild",
    False alleen voor een echte "waarschuwing".

    Permanent gecached (tabel ticker_prijscheck) — zie db.save_prijscheck
    voor waarom ook een mislukte lookup hier wél gecached wordt, anders dan
    bij de overige caches in dit project.

    Haalt ook het intraday-high/low van diezelfde handelsdag op (zie
    _haal_dagrange_op) en geeft in het resultaat "binnen_dagrange" terug:
    of bekende_koers (na dezelfde EUR/split-correctie als yahoo_koers)
    tussen dat low en high valt. None als er geen high/low beschikbaar is
    (bv. een mislukte fetch, of geen vergelijking mogelijk — zie de
    early-returns hieronder) — de aanroeper valt dan terug op de bestaande
    %-afwijkingsdrempel.
    """
    datum = pd.Timestamp(datum).date()
    cached = get_cached_prijscheck(ticker, datum)
    if cached is not None:
        yahoo_koers, valuta, high, low = cached
        dprint(f"[prijscheck] '{ticker}' op {datum}: uit cache -> yahoo_koers={yahoo_koers}")
        if yahoo_koers is not None and high is None and low is None:
            # Rij van vóór de dagrange-uitbreiding, of een eerder mislukte
            # dagrange-fetch -- alsnog proberen aan te vullen (zelfde soort
            # stale-cache-fix als bij ticker_info, zie CLAUDE.md).
            high, low = _haal_dagrange_op(ticker, datum)
            save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)
    else:
        yahoo_koers, high, low = _haal_koers_en_dagrange_op(ticker, datum)
        valuta = _ticker_details_met_cache(ticker).get("valuta")
        # print(f"[prijscheck] '{ticker}' op {datum}: opgehaald -> yahoo_koers={yahoo_koers} ({valuta})")
        save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)

    if yahoo_koers is None or not bekende_koers:
        return {
            "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
            "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
            "high": high, "low": low, "binnen_dagrange": None,
        }

    valuta_conversie_toegepast = False
    yahoo_koers_eur = yahoo_koers
    high_eur, low_eur = high, low
    fx_koers = None
    if valuta not in (None, "EUR"):
        # Deze vergelijking is altijd tegen een HISTORISCHE transactiedatum
        # -- een verse FX-koers van vandaag is hier nooit relevant, dus
        # onvoorwaardelijk verversen=False (zie _fx_prijzen_serie()).
        fx_koers = _fx_koers_op_datum(valuta, datum, verversen=False)
        if fx_koers is None:
            # Geen betrouwbare EUR-vergelijking mogelijk (net zo'n signaal
            # als "geen koersdata" hierboven) -- NIET stilzwijgend de rauwe,
            # niet-vergelijkbare bedragen tegen elkaar afzetten, dat zou een
            # valse waarschuwing (of een valse "OK") kunnen opleveren.
            return {
                "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
                "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
                "high": high, "low": low, "binnen_dagrange": None,
            }
        divisor = 100 if valuta == "GBp" else 1
        yahoo_koers_eur = yahoo_koers / divisor * fx_koers
        if high_eur is not None and low_eur is not None:
            high_eur = high_eur / divisor * fx_koers
            low_eur = low_eur / divisor * fx_koers
        valuta_conversie_toegepast = True
        # print(f"[prijscheck] '{ticker}' op {datum}: valutaconversie toegepast ({valuta} -> EUR, "
              # f"FX-koers {fx_koers:.4f}) -> yahoo_koers {yahoo_koers} wordt {yahoo_koers_eur:.4f}")

    split_factor = _cumulatieve_split_factor(ticker, datum)
    yahoo_koers_gecorrigeerd = yahoo_koers_eur * split_factor
    if high_eur is not None and low_eur is not None:
        high_eur = high_eur * split_factor
        low_eur = low_eur * split_factor
    if split_factor != 1.0:
        pass
        # print(f"[prijscheck] '{ticker}' op {datum}: split-correctie toegepast (factor {split_factor:.4f}) "
              # f"-> yahoo_koers {yahoo_koers_eur} wordt {yahoo_koers_gecorrigeerd} voor de vergelijking")

    binnen_dagrange = (
        low_eur * (1 - DAGRANGE_TOLERANTIE) <= bekende_koers <= high_eur * (1 + DAGRANGE_TOLERANTIE)
        if (high_eur is not None and low_eur is not None) else None
    )

    afwijking_pct = abs(yahoo_koers_gecorrigeerd - bekende_koers) / bekende_koers * 100
    afwijking_fractie = afwijking_pct / 100
    if afwijking_fractie < PRIJSCHECK_DREMPEL_OK:
        niveau = "ok"
    elif afwijking_fractie < PRIJSCHECK_DREMPEL_WAARSCHUWING:
        niveau = "mild"
    else:
        niveau = "waarschuwing"

    toon_gecorrigeerd = split_factor != 1.0 or valuta_conversie_toegepast
    dprint(
        f"[prijscheck-debug] ticker={ticker} datum={datum} "
        f"yahoo_koers={yahoo_koers} valuta={valuta} "
        f"fx_koers={fx_koers} yahoo_koers_eur={yahoo_koers_eur:.4f} "
        f"split_factor={split_factor} yahoo_koers_gecorrigeerd={yahoo_koers_gecorrigeerd:.4f} "
        f"bekende_koers={bekende_koers} afwijking_pct={afwijking_pct:.2f} niveau={niveau}"
    )
    return {
        "yahoo_koers": yahoo_koers,
        "yahoo_koers_gecorrigeerd": yahoo_koers_gecorrigeerd if toon_gecorrigeerd else None,
        "split_factor": split_factor,
        "bekende_koers": bekende_koers,
        "afwijking_pct": afwijking_pct,
        "niveau": niveau,
        "match": niveau != "waarschuwing",
        "high": high_eur if toon_gecorrigeerd else high,
        "low": low_eur if toon_gecorrigeerd else low,
        "binnen_dagrange": binnen_dagrange,
    }


def _kies_steekproef_transacties(transacties_van_dit_isin, aantal=3):
    """
    Kiest tot 'aantal' transacties (eerste, middelste, laatste) met koers > 0
    (dus geen corporate-action-/splitrijen) uit een lijst dicts met minimaal
    'datum' en 'koers' — representatief genoeg om een ticker te verifiëren,
    zonder voor elke transactie een Yahoo-call te hoeven doen.
    """
    kandidaten = sorted(
        (t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0),
        key=lambda t: t["datum"],
    )
    if len(kandidaten) <= aantal:
        return kandidaten
    indices = sorted({0, len(kandidaten) // 2, len(kandidaten) - 1})
    return [kandidaten[i] for i in indices]


def _sector_samenvatting(ticker, top_n=3):
    """Top-N sectoren van een ETF als leesbare tekst, bv. 'Technology (37%),
    Financial Services (12%), Consumer Cyclical (10%)'. Sectoren op 0%
    worden niet meegeteld — een fonds dat vrijwel volledig in 1 sector zit
    (bv. GDX.L: 100% Basic Materials) moet niet aangevuld worden met
    0%-sectoren alleen omdat sector_weightings die toevallig ook meegeeft."""
    verdeling = get_etf_sector_verdeling(ticker)
    if not verdeling:
        return None
    top = sorted(
        (item for item in verdeling.items() if item[1] > 0),
        key=lambda kv: kv[1], reverse=True,
    )[:top_n]
    if not top:
        return None
    return ", ".join(f"{naam} ({gewicht * 100:.0f}%)" for naam, gewicht in top)


def _top_holding_land(ticker):
    """Land van de grootste top-10-holding van een ETF — puur informatief
    (welk land heeft de zwaarste weging), geen volledige landverdeling
    (dat is compute_land_sector_verdeling()'s taak)."""
    holdings = get_etf_holdings(ticker)
    if not holdings:
        return None
    grootste = max(holdings, key=lambda h: h["gewicht"])
    return grootste.get("land")


def _land_sector_voor_weergave(ticker):
    """
    Land/sector-info voor de Ticker-zekerheid-pagina. Voor een los aandeel
    is get_land_sector() (via yfinance .info) prima. Voor een ETF is
    info["country"]/info["sector"] structureel leeg — dat is geen
    toevallige lookup-fout, een fonds heeft simpelweg geen eigen land/sector
    — dus daarvoor hergebruiken we de sectorverdeling-/holdings-cache van de
    Land/Sector-verdelingsfunctie (compute_land_sector_verdeling) in plaats
    van te blijven proberen een los-aandeel-veld te lezen dat voor een ETF
    nooit gevuld raakt.

    Geeft (land, sector, top_holding_land) terug — voor een ETF is 'land'
    bewust None (één land suggereert een precisie die een wereldwijd fonds
    niet heeft) en is 'top_holding_land' een aparte, expliciet zo genoemde
    losse info-regel.
    """
    if classify_ticker(ticker):
        return None, _sector_samenvatting(ticker), _top_holding_land(ticker)

    land, sector = get_land_sector(ticker)
    return land, sector, None


def _verzamel_extra_kandidaten(product, isin, bestaande_alternatieven, uitgesloten_ticker):
    """
    Extra, gerichte zoekopdracht naar mogelijke alternatieve tickers — alleen
    gebruikt door verifieer_ticker_met_prijs() wanneer de kandidatenlijst uit
    find_ticker_detailed() leeg is (zie de aanroep verderop). Die lijst is
    namelijk geen eigen zoekopdracht naar alternatieven, maar simpelweg de
    restlijst die toevallig al meekwam uit de zoekopdracht die de GEKOZEN
    ticker vond — leverde die zoekopdracht daar maar 1 resultaat op (zoals
    bij BYD: "BYD COMPANY LIMITED" vindt alleen 4BY1.F), dan is er niets om
    te tonen, ook al staat de ticker op "Onzeker".

    Zoekt op de VOLLEDIGE (niet-ingekorte) productnaam én op de ISIN, zonder
    de beurs-beperking ('targets') die _zoek_product_progressief()/
    _kies_beurs_match() al toepasten — juist om ook kandidaten op ANDERE
    beurzen te vinden dan de oorspronkelijke zoekopdracht overwoog.

    Sluit 'uitgesloten_ticker' (de al gekozen ticker) uit en dedupliceert op
    'symbol', zowel onderling als tegen 'bestaande_alternatieven', zodat de
    uiteindelijke lijst geen dubbele kandidaten bevat.

    Geeft een lijst van {"symbol", "exchange"}-dicts terug, in hetzelfde
    formaat als find_ticker_detailed()'s 'alternatieven' — rechtstreeks door
    te geven aan _zoek_betere_alternatieven().
    """
    bekende_symbols = {uitgesloten_ticker} | {a.get("symbol") for a in bestaande_alternatieven}

    extra = []
    for query in (product, isin):
        quotes = _yahoo_search(query)
        for q in quotes:
            symbol = q.get("symbol")
            if not symbol or symbol in bekende_symbols:
                continue
            bekende_symbols.add(symbol)
            extra.append({"symbol": symbol, "exchange": q.get("exchange")})

    dprint(
        f"[alternatieven] '{product}' ({isin}): extra zoekopdracht (zonder beurs-beperking) "
        f"vond {len(extra)} nieuwe kandidaat/kandidaten: "
        f"{[(e['symbol'], e['exchange']) for e in extra]}"
    )
    return extra


def _zoek_betere_alternatieven(alternatieven_kandidaten, steekproef, verwachte_beurzen):
    """
    Rekent kandidaat-tickers (vorm {'symbol','exchange'}, zoals
    find_ticker_detailed()'s 'alternatieven', eventueel aangevuld met
    _verzamel_extra_kandidaten()'s resultaten) één voor één door tegen de
    prijssteekproef, en stopt zodra een kandidaat een overtuigende match
    oplevert (juiste beurs + alle steekproefdatums kloppen) — anders wordt
    de hele lijst doorgerekend. Geëxtraheerd uit verifieer_ticker_met_prijs()
    zodat zowel die volledige (lui, alleen op de Ticker-zekerheid-pagina)
    verificatie als de lichte, standaard find_ticker_met_snelle_prijscheck()
    (stap 3, alleen bij een forse afwijking) dezelfde logica hergebruiken.

    Geeft (alternatieven, aanbevolen_alternatief) terug:
      alternatieven: lijst van {"ticker","beurs","land","sector","valuta",
        "gemiddelde_afwijking_pct","aantal_matches"} — voor weergave op de
        Ticker-zekerheid-pagina.
      aanbevolen_alternatief: ticker-symbool van de eerste kandidaat die op
        alle geteste datums matcht, of None.
    """
    alternatieven = []
    aanbevolen_alternatief = None
    for alt in alternatieven_kandidaten:
        alt_ticker = alt.get("symbol")
        if not alt_ticker:
            continue

        alt_checks = []
        for t in steekproef:
            check = vergelijk_prijs_op_datum(alt_ticker, t["datum"], float(t["koers"]))
            alt_checks.append(check)
            # Geen koersdata voor deze datum (bv. '4BY1.F': "Data doesn't
            # exist for startDate/endDate") betekent meestal dat Yahoo
            # helemaal geen historie heeft voor deze kandidaat — de overige
            # steekproefdatums nog proberen kost dan alleen tijd zonder kans
            # op een match.
            if check["yahoo_koers"] is None:
                break

        alt_matches = [c["match"] for c in alt_checks if c["match"] is not None]
        afwijkingen = [c["afwijking_pct"] for c in alt_checks if c["afwijking_pct"] is not None]
        alt_details = _ticker_details_met_cache(alt_ticker)
        alt_is_etf = classify_ticker(alt_ticker)
        alt_land, alt_sector, _alt_top_holding_land = _land_sector_voor_weergave(alt_ticker)
        alt_beurs_klopt = (alt.get("exchange") in verwachte_beurzen) if verwachte_beurzen else None
        # Meest recente check met een bekende dagrange (steekproef is
        # chronologisch eerste/middelste/laatste) -- voor de ETF-weergave op
        # de Ticker-zekerheid-pagina (High/Low i.p.v. land/sector, zie
        # CLAUDE.md/opdracht_ticker_zekerheid_dagrange_performance.md).
        alt_high, alt_low = next(
            ((c["high"], c["low"]) for c in reversed(alt_checks)
             if c.get("high") is not None and c.get("low") is not None),
            (None, None),
        )

        alternatieven.append({
            "ticker": alt_ticker,
            "beurs": alt.get("exchange"),
            "is_etf": alt_is_etf,
            "land": alt_land,
            "sector": alt_sector,
            "high": alt_high,
            "low": alt_low,
            "valuta": alt_details.get("valuta"),
            "gemiddelde_afwijking_pct": (sum(afwijkingen) / len(afwijkingen)) if afwijkingen else None,
            "aantal_matches": sum(1 for m in alt_matches if m),
        })

        wordt_aanbevolen = aanbevolen_alternatief is None and alt_matches and all(alt_matches)
        dprint(
            f"[alternatieven-debug] alt_ticker={alt_ticker} exchange={alt.get('exchange')} "
            f"land={alt_land} sector={alt_sector} valuta={alt_details.get('valuta')} "
            f"alt_matches={alt_matches} gemiddelde_afwijking_pct="
            f"{(sum(afwijkingen) / len(afwijkingen)) if afwijkingen else None} "
            f"wordt_aanbevolen={wordt_aanbevolen}"
        )

        if aanbevolen_alternatief is None and alt_matches and all(alt_matches):
            aanbevolen_alternatief = alt_ticker

        if alt_beurs_klopt and alt_matches and all(alt_matches):
            # Overtuigende match (juiste beurs + kloppende prijs op alle
            # gecheckte datums) — de overige kandidaten checken kan het
            # resultaat niet meer verbeteren, alleen nog meer Yahoo-calls
            # kosten.
            break

    return alternatieven, aanbevolen_alternatief


def _prijscheck_is_probleem(check):
    """
    Of één prijscheck als 'probleem' telt voor de samenvattende
    waarschuwingsmeldingen (verifieer_ticker_met_prijs hieronder,
    prijswaarschuwing_voor_ticker verderop): primair op basis van de
    dagrange (valt de Excel-koers buiten het intraday-high/low van die
    handelsdag), met terugval op de bestaande %-afwijkingsdrempel
    (PRIJSCHECK_DREMPEL_WAARSCHUWING, via het al berekende 'match') als er
    geen dagrange beschikbaar is — bv. een mislukte High/Low-fetch, zodat
    geen dekking verloren gaat waar de dagrange-check niet kan draaien.
    """
    binnen_dagrange = check.get("binnen_dagrange")
    if binnen_dagrange is not None:
        return not binnen_dagrange
    return check["match"] is False


def verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin):
    """
    Zoekt de ticker zoals find_ticker_detailed(), maar herbeoordeelt de
    zekerheid met een sterker signaal: de daadwerkelijke DEGIRO-transactie-
    prijs vergeleken met de historische Yahoo-slotkoers op dezelfde datum
    (voor de gekozen ticker én voor elke alternatieve kandidaat). Geeft
    alles terug wat nodig is om de match op de Ticker-zekerheid-pagina te
    beoordelen (land/sector/valuta/... voor gekozen ticker + alternatieven),
    zodat de frontend niets zelf hoeft na te vragen.

    'alternatieven' komt normaliter uit find_ticker_detailed()'s restlijst,
    maar wordt aangevuld met een aparte, gerichte zoekopdracht
    (_verzamel_extra_kandidaten()) als die restlijst leeg is — zie de
    toelichting daar.

    Voegt op ELK return-pad ook een OpenFIGI-root-check toe (zie
    _voeg_openfigi_check_toe()) -- zelfde extra, ISIN-gebaseerde
    validatiesignaal als find_ticker_met_snelle_prijscheck(). Kost dankzij
    de permanente cache per ISIN geen extra externe call zodra deze ISIN al
    eens via de upload-route is opgehaald.
    """
    basis = find_ticker_detailed(product, isin, beurs)
    ticker = basis["ticker"]
    zekerheid = basis["zekerheid"]

    if ticker is None:
        resultaat = {
            "ticker": None, "zekerheid": zekerheid, "waarschuwing": None,
            "is_etf": None, "land": None, "sector": None, "top_holding_land": None, "valuta": None,
            "fondsfamilie": None, "category": None, "quote_type": None,
            "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
            "prijs_checks": [], "alternatieven": [],
        }
        return _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="waarschuwing")

    steekproef = _kies_steekproef_transacties(transacties_van_dit_isin)

    prijs_checks = []
    for t in steekproef:
        check = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        check["datum"] = str(t["datum"])
        prijs_checks.append(check)

    bekende_checks = [c for c in prijs_checks if c["match"] is not None]
    prijs_bekend = len(bekende_checks) > 0
    problemen = [c for c in bekende_checks if _prijscheck_is_probleem(c)]
    prijs_klopt = prijs_bekend and not problemen

    waarschuwing = None
    if zekerheid == "zeker" and (not prijs_bekend or not prijs_klopt):
        zekerheid = "onzeker"
        if not prijs_bekend:
            waarschuwing = (
                f"Geen koersdata gevonden bij Yahoo voor '{ticker}' — "
                f"mogelijk een verkeerde of niet-bestaande ticker."
            )
        else:
            grootste = max(problemen, key=lambda c: c["afwijking_pct"])
            waarschuwing = (
                f"Beurs komt overeen, maar {len(problemen)} van de {len(bekende_checks)} gecontroleerde "
                f"datums valt buiten de dagrange (grootste afwijking "
                f"{grootste['afwijking_pct']:.1f}% op {grootste['datum']}) — mogelijk toch de verkeerde ticker."
            )
        # print(f"[prijscheck] ⚠️ '{ticker}' ({isin}): {waarschuwing}")

    details = _ticker_details_met_cache(ticker)
    is_etf = classify_ticker(ticker)
    land, sector, top_holding_land = _land_sector_voor_weergave(ticker)
    yahoo_beurs = details.get("yahoo_beurs")
    verwachte_beurzen = BEURS_MAP.get(beurs, [])
    beurs_klopt = (yahoo_beurs in verwachte_beurzen) if (beurs and yahoo_beurs and verwachte_beurzen) else None

    # Alternatieven alleen doorrekenen (= extra Yahoo-calls) als de match
    # niet al dubbel bevestigd is — bij een "zeker" resultaat is er niets te
    # winnen met het checken van kandidaten die toch niet gekozen zijn.
    alternatieven = []
    aanbevolen_alternatief = None
    if zekerheid != "zeker":
        alternatieven_kandidaten = list(basis["alternatieven"])
        # 'alternatieven_kandidaten' is de restlijst van find_ticker_detailed()'s
        # eigen zoekopdracht, niet een eigen zoekopdracht naar alternatieven —
        # bij een lege lijst heeft _zoek_betere_alternatieven() dus niets om
        # te beoordelen, ook al is de ticker "onzeker" (zie het BYD-geval in
        # _verzamel_extra_kandidaten()'s docstring). Strikt op leeg (i.p.v.
        # "klein aantal") gecheckt: zodra er al 1+ kandidaten zijn, heeft de
        # pagina al iets te tonen en scheelt dit extra Yahoo-calls.
        if not alternatieven_kandidaten:
            alternatieven_kandidaten += _verzamel_extra_kandidaten(
                product, isin, alternatieven_kandidaten, ticker
            )
        alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
            alternatieven_kandidaten, steekproef, verwachte_beurzen
        )

    result = {
        "ticker": ticker,
        "zekerheid": zekerheid,
        "waarschuwing": waarschuwing,
        "is_etf": is_etf,
        "land": land,
        "sector": sector,
        "top_holding_land": top_holding_land,
        "valuta": details.get("valuta"),
        "fondsfamilie": details.get("fund_family"),
        "category": details.get("category"),
        "quote_type": details.get("quote_type"),
        "excel_beurs": beurs,
        "yahoo_beurs": yahoo_beurs,
        "beurs_klopt": beurs_klopt,
        "prijs_checks": prijs_checks,
        "alternatieven": alternatieven,
    }
    if aanbevolen_alternatief:
        result["aanbevolen_alternatief"] = aanbevolen_alternatief
    return _voeg_openfigi_check_toe(result, isin, waarschuwing_veld="waarschuwing")


def _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="prijswaarschuwing"):
    """
    Past 'resultaat' aan met een extra, ISIN-gebaseerd validatiesignaal:
    staat de ticker-ROOT (zonder Yahoo-beurssuffix) ergens tussen OpenFIGI's
    resultaten voor deze ISIN? Verandert NOOIT automatisch welke ticker
    gebruikt/opgeslagen wordt -- alleen 'zekerheid' en het waarschuwingsveld,
    net als de rest van deze functie (zie ook backfill_verouderde_tickers()
    voor hetzelfde voorzichtige patroon). Dankzij de permanente cache in
    haal_openfigi_resultaten() kost dit bij een warme cache geen extra
    externe call.

    Zet altijd 'openfigi_root_bekend' (True/False/None) en
    'openfigi_root_matches' (aantal matchende OpenFIGI-resultaten, of None)
    op 'resultaat' -- gebruikt door de Ticker-zekerheid-pagina voor de
    samenvattingsregel, ook als het oordeel positief of onbeslist is (in
    tegenstelling tot de waarschuwing hieronder, die alleen bij een
    negatief oordeel wordt gezet).

    waarschuwing_veld: de sleutel in 'resultaat' waarin de bestaande
    prijscontrole-boodschap staat -- find_ticker_met_snelle_prijscheck()
    gebruikt 'prijswaarschuwing', verifieer_ticker_met_prijs() gebruikt
    'waarschuwing'. Bij een negatief oordeel wordt een bestaande boodschap
    aangevuld (nieuwe regel), niet overschreven.
    """
    ticker = resultaat.get("ticker")
    if not ticker:
        resultaat["openfigi_root_bekend"] = None
        resultaat["openfigi_root_matches"] = None
        return resultaat

    openfigi = haal_openfigi_resultaten(isin)
    matches = _openfigi_root_matches(ticker, openfigi["resultaten"])
    root_bekend = None if matches is None else matches > 0
    resultaat["openfigi_root_bekend"] = root_bekend
    resultaat["openfigi_root_matches"] = matches
    if root_bekend is not False:
        return resultaat

    extra_waarschuwing = (
        f"Ticker-root '{ticker.split('.')[0]}' komt niet voor in OpenFIGI's "
        f"resultaten voor deze ISIN — controleer op het Ticker-zekerheid-tabblad."
    )
    # print(f"[openfigi-check] ⚠️ {isin}: {extra_waarschuwing}")

    bestaande = resultaat.get(waarschuwing_veld)
    resultaat[waarschuwing_veld] = f"{bestaande}\n{extra_waarschuwing}" if bestaande else extra_waarschuwing
    if resultaat.get("zekerheid") == "zeker":
        resultaat["zekerheid"] = "onzeker"
    return resultaat


def find_ticker_met_snelle_prijscheck(product, isin, beurs, transacties_van_dit_isin, bekende_ticker=None):
    """
    Lichte, STANDAARD prijscontrole — draait bij ELKE upload (opslaand én
    'niet opslaan'), in tegenstelling tot verifieer_ticker_met_prijs()
    hierboven, die bewust duur is en alleen lui/on-demand draait op de
    Ticker-zekerheid-pagina. Moet daarom in het gangbare geval (geen
    afwijking) maar 1 extra, via ticker_prijscheck gecachete Yahoo-call
    kosten per unieke (ISIN, Beurs) — vergelijkbaar met de kosten die er al
    waren vóór de 'niet opslaan'-timeoutfix (CLAUDE.md, Statistieken-
    incident 2026-08-31).

    'bekende_ticker' (optioneel): als gegeven, wordt find_ticker_detailed()
    -- en dus de onvoorwaardelijke, nooit-gecachete yahooquery-zoekopdracht
    -- overgeslagen; de rest van deze functie (prijscontrole + escalatie)
    draait gewoon door op deze ticker. Voor het "ticker-informatie opnieuw
    bepalen"-vinkje op het uploadscherm (zie app.py/_upload_impl): staat
    het vinkje UIT, dan geeft de aanroeper hier de al bekende ticker van
    een eerdere upload door voor posities die niet écht nieuw zijn. De
    escalatie in stap 3 hieronder heeft dan geen alternatieven om op terug
    te vallen (die kwamen normaal uit de overgeslagen zoekopdracht) -- geen
    probleem: bij een échte ticker-fout signaleert de prijscontrole hier
    het probleem gewoon (net als altijd), en pikt backfill_verouderde_
    tickers() dat direct na deze upload alsnog op met een VOLLEDIGE
    (wél bevraagde) hernieuwde zoekopdracht.

    Voegt op ELK return-pad ook een OpenFIGI-root-check toe (zie
    _voeg_openfigi_check_toe()) — een extra, ISIN-gebaseerd validatiesignaal
    naast de Yahoo-prijscontrole hierboven. Dankzij een permanente DB-cache
    per ISIN kost dit in de praktijk geen extra externe call na de eerste
    upload van een portfolio.

    Escalatietrapje, bij een daadwerkelijke afwijking ÓF bij helemaal geen
    Yahoo-koersdata (net zo verdacht als een grote afwijking — vaak een
    verkeerde of niet-bestaande ticker, dus nooit stilzwijgend als "OK"
    behandelen):
      1. Alleen de LAATSTE transactiedatum controleren.
      2. Dagrange-probleem (Excel-koers buiten Yahoo's intraday-high/low,
         via _prijscheck_is_probleem() — zelfde criterium als de bovenste
         waarschuwingsbalk elders in de app; bij ontbrekende dagrange valt
         dat terug op > PRIJSCHECK_DREMPEL_WAARSCHUWING (6%) afwijking), of
         geen koersdata -> ook de rest van de steekproef (eerste/middelste/
         laatste) controleren — een eenmalige, onschuldige uitschieter
         (bv. een corporate action rond die datum) mag niet meteen als een
         foute ticker gelden.
      3. Nog steeds > PRIJSCHECK_DREMPEL_ALTERNATIEVEN (10%) afwijking (over
         de bredere steekproef), of nog steeds geen koersdata op geen
         enkele steekproefdatum -> ook alternatieve tickers doorrekenen,
         via dezelfde _zoek_betere_alternatieven() als de volledige check —
         maar dan alleen voor DEZE positie, niet voor de hele portfolio.

    Geeft basis (ticker/zekerheid/alternatieven van find_ticker_detailed())
    terug, aangevuld met 'prijs_checks' (lijst, 1-3 checks naargelang de
    escalatie) en 'prijswaarschuwing' (None als er niets aan de hand is).

    Bij escalatie naar stap 3 wordt een alternatieve ticker in twee gevallen
    automatisch overgenomen (ticker/zekerheid worden dan overschreven en
    'automatisch_gecorrigeerd_van' bevat de oorspronkelijke ticker):
      - Tier 1: het alternatief staat op een VERWACHTE beurs (BEURS_MAP) en
        de prijs klopt op >= MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE
        steekproefdatums.
      - Tier 2 (alleen als tier 1 niets oplevert): het alternatief staat op
        een andere beurs dan verwacht, maar de prijs klopt op ALLE
        gecontroleerde steekproefdatums — het Vanguard/iShares-scenario
        waarbij de juiste UCITS-notering structureel op een andere beurs
        staat dan DEGIRO's beurscode doet vermoeden.
    Voldoet geen enkel alternatief aan tier 1 of tier 2, dan blijft het
    bestaande gedrag: hooguit een 'aanbevolen_alternatief' als suggestie,
    niets wordt automatisch overgenomen.
    """
    if bekende_ticker:
        basis = {"ticker": bekende_ticker, "zekerheid": "zeker", "alternatieven": []}
    else:
        basis = find_ticker_detailed(product, isin, beurs)
    ticker = basis["ticker"]

    # Corporate-action-/splitrijen (koers 0 of leeg) horen niet in de
    # prijscontrole thuis — zelfde filter als _kies_steekproef_transacties.
    geldige_transacties = [
        t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0
    ]
    if ticker is None or not geldige_transacties:
        resultaat = {**basis, "prijs_checks": [], "prijswaarschuwing": None}
        return _voeg_openfigi_check_toe(resultaat, isin)

    laatste = max(geldige_transacties, key=lambda t: t["datum"])
    check_laatste = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    check_laatste["datum"] = str(laatste["datum"])
    prijs_checks = [check_laatste]

    # Dagrange-bewust i.p.v. alleen de ruwe %-afwijking -- zelfde criterium
    # als _prijscheck_is_probleem() elders in het bestand (bv. de bovenste
    # waarschuwingsbalk). "Geen koersdata" blijft apart escaleren
    # (regressie t.o.v. het G2X.MU-geval, zie CLAUDE.md):
    # _prijscheck_is_probleem() geeft bij ontbrekende data GEEN probleem
    # terug (match=None), dus die check hier expliciet ervoor houden.
    escaleert = check_laatste["afwijking_pct"] is None or _prijscheck_is_probleem(check_laatste)
    # print(f"[snelle-prijscheck] '{ticker}' ({isin}, beurs={beurs}) laatste={check_laatste['datum']} "
          # f"afwijking={check_laatste['afwijking_pct']} binnen_dagrange={check_laatste.get('binnen_dagrange')} "
          # f"-> escaleert={escaleert}")

    if not escaleert:
        # Koers klopt -- het gangbare geval, klaar na 1 (gecachete) call.
        resultaat = {**basis, "prijs_checks": prijs_checks, "prijswaarschuwing": None}
        return _voeg_openfigi_check_toe(resultaat, isin)

    # Stap 2: dagrange-probleem (of, bij ontbrekende dagrange, >6%
    # afwijking) op de laatste datum, of helemaal geen koersdata gevonden
    # -- ook de rest van de steekproef controleren. Geen koersdata is
    # minstens zo verdacht als een grote afwijking (vaak een verkeerde of
    # niet-bestaande ticker), dus géén early-return meer als "OK".
    steekproef = _kies_steekproef_transacties(geldige_transacties)
    for t in steekproef:
        if str(t["datum"]) == check_laatste["datum"]:
            continue  # laatste datum al gecheckt hierboven
        c = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        c["datum"] = str(t["datum"])
        prijs_checks.append(c)

    grootste_afwijking = max(
        (c["afwijking_pct"] for c in prijs_checks if c["afwijking_pct"] is not None),
        default=None,
    )

    zekerheid = "onzeker" if basis["zekerheid"] == "zeker" else basis["zekerheid"]
    if grootste_afwijking is None:
        prijswaarschuwing = (
            f"Geen koersdata gevonden bij Yahoo voor '{ticker}' — "
            f"controleer op het Ticker-zekerheid-tabblad."
        )
    else:
        prijswaarschuwing = (
            f"Koers van {ticker} wijkt {grootste_afwijking:.1f}% af van Yahoo — "
            f"controleer op het Ticker-zekerheid-tabblad."
        )
    # print(f"[snelle-prijscheck] ⚠️ '{ticker}' ({isin}): {prijswaarschuwing}")

    resultaat = {
        **basis, "zekerheid": zekerheid, "prijs_checks": prijs_checks,
        "prijswaarschuwing": prijswaarschuwing,
    }

    # Stap 3: nog steeds fors afwijkend (>10%) na de bredere steekproef, of
    # helemaal geen koersdata gevonden -- nu pas de duurdere kandidaten-
    # doorrekening, en alleen voor DEZE positie (niet voor de hele
    # portfolio).
    if grootste_afwijking is None or grootste_afwijking > PRIJSCHECK_DREMPEL_ALTERNATIEVEN * 100:
        verwachte_beurzen = BEURS_MAP.get(beurs, [])
        alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
            basis["alternatieven"], steekproef, verwachte_beurzen
        )

        # Tier 1: beurs klopt + prijs klopt op >= MIN_MATCHES_VOOR_AUTOMATISCHE_
        # CORRECTIE datums. Let op: 'alternatieven'-entries hebben geen eigen
        # 'beurs_klopt'-veld, dus zelf tegen verwachte_beurzen vergelijken.
        beurs_bevestigd = next(
            (a for a in alternatieven
             if a.get("beurs") in verwachte_beurzen
             and a.get("aantal_matches", 0) >= MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE),
            None,
        )

        # Tier 2: alleen proberen als tier 1 niets opleverde.
        volledig_prijs_bevestigd = None
        if beurs_bevestigd is None and len(steekproef) >= MIN_STEEKPROEF_VOOR_VOLLEDIGE_MATCH:
            volledig_prijs_bevestigd = next(
                (a for a in alternatieven if a.get("aantal_matches") == len(steekproef)),
                None,
            )

        gekozen = beurs_bevestigd or volledig_prijs_bevestigd
        if gekozen:
            if gekozen is beurs_bevestigd:
                reden = f"beurs ({gekozen['beurs']}) + prijs bevestigd ({gekozen['aantal_matches']} datums)"
            else:
                reden = f"beurs niet bevestigd, maar prijs klopt op alle {len(steekproef)} gecontroleerde datums"
            # print(f"[snelle-prijscheck] ✅ '{ticker}' ({isin}) automatisch vervangen door "
                  # f"'{gekozen['ticker']}' ({reden})")
            resultaat["ticker"] = gekozen["ticker"]
            resultaat["zekerheid"] = "zeker"
            resultaat["prijswaarschuwing"] = None
            resultaat["automatisch_gecorrigeerd_van"] = ticker
        elif aanbevolen_alternatief:
            # Geen van beide tiers voldoende bewijs -- bestaand gedrag: alleen
            # tonen als suggestie, niets automatisch overnemen.
            # print(f"[snelle-prijscheck] ℹ️ '{ticker}' ({isin}): alternatief '{aanbevolen_alternatief}' "
                  # f"gevonden maar onvoldoende bewijs voor automatische correctie -- alleen als suggestie getoond")
            resultaat["aanbevolen_alternatief"] = aanbevolen_alternatief
        else:
            pass
            # print(f"[snelle-prijscheck] ℹ️ '{ticker}' ({isin}): geëscaleerd, maar geen enkel alternatief gevonden")

    return _voeg_openfigi_check_toe(resultaat, isin)


def _ticker_heeft_prijsprobleem(ticker, transacties_van_dit_isin):
    """
    Of een AL GEVONDEN ticker een prijsprobleem heeft op de laatste
    transactiedatum: een dagrange-probleem (zelfde criterium als
    _prijscheck_is_probleem(), bv. de bovenste waarschuwingsbalk elders in
    de app) OF helemaal geen koersdata bij Yahoo — net zo verdacht als een
    grote afwijking, zie de "geen koersdata"-escalatie hierboven in
    verifieer_ticker_met_prijs()/find_ticker_met_snelle_prijscheck() (het
    G2X.MU-geval). Gebruikt door backfill_verouderde_tickers() om te
    bepalen of een AL OPGESLAGEN ticker een backfill-kandidaat is. Geen
    ticker (None) telt altijd als een probleem.
    """
    if not ticker:
        return True
    geldige = [t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0]
    if not geldige:
        return False
    laatste = max(geldige, key=lambda t: t["datum"])
    check = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    probleem = check["afwijking_pct"] is None or _prijscheck_is_probleem(check)
    # print(f"[backfill-check] '{ticker}' laatste={laatste['datum']} afwijking={check['afwijking_pct']} "
          # f"binnen_dagrange={check.get('binnen_dagrange')} -> probleem={probleem}")
    return probleem


def backfill_verouderde_tickers(code, forceer=False):
    """
    Herbeoordeelt voor elke AL OPGESLAGEN (ISIN, Beurs)-groep van 'code' de
    ticker met find_ticker_met_snelle_prijscheck() — een verbeterde ticker-
    resolutielogica (bv. de progressieve-productnaam-inkorting, of "geen
    koersdata = verdacht" i.p.v. stilzwijgend OK, zie het G2X.MU-geval)
    corrigeert anders alleen NIEUWE rijen: de hoofdpagina gebruikt de al
    opgeslagen transacties.ticker-waarde, geen verse herberekening.

    Standaard (forceer=False) wordt een groep alleen daadwerkelijk herzocht
    als de OUDE ticker een prijsprobleem heeft (_ticker_heeft_prijsprobleem)
    -- dit is het automatische self-healing-vangnet en blijft ongewijzigd.
    forceer=True (het "ticker-informatie opnieuw bepalen"-vinkje op het
    uploadscherm, zie app.py) slaat die check over en herzoekt ALTIJD elke
    groep, ook zonder gedetecteerd prijsprobleem -- voor de gevallen waarin
    de gebruiker zelf al weet dat er iets mis is en niet op de automatische
    detectie wil wachten.

    Overschrijft de opgeslagen ticker in BEIDE gevallen alleen als:
      - de NIEUWE kandidaat GEEN prijsprobleem heeft (_ticker_heeft_prijsprobleem).
    Nooit een werkende ticker vervangen door een onzekerdere; bij twijfel
    (de nieuwe kandidaat heeft zelf ook een prijsprobleem) wordt NIET
    overschreven, maar wel gelogd zodat het zichtbaar blijft. Bedoeld om
    aan te roepen ná elke upload die bij een bestaande portfolio-code komt
    (een nieuwe upload van dezelfde ISIN's levert de prijsdata om te
    herbeoordelen). Geeft het aantal daadwerkelijk gecorrigeerde
    (ISIN, Beurs)-groepen terug.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT isin, beurs, ticker, product, echte_naam, datum, koers FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()

    groepen = {}
    for isin, beurs, ticker, product, echte_naam, datum, koers in rows:
        groep = groepen.setdefault(
            (isin, beurs), {"ticker": ticker, "naam": echte_naam or product, "transacties": []}
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    gecorrigeerd = 0
    for (isin, beurs), info in groepen.items():
        oude_ticker = info["ticker"]
        transacties = info["transacties"]
        if not forceer and not _ticker_heeft_prijsprobleem(oude_ticker, transacties):
            # print(f"[backfill-ticker] ISIN={isin} (beurs={beurs}): '{oude_ticker}' heeft geen "
                  # f"prijsprobleem -- niets te backfillen")
            continue  # oude ticker werkt prima, niets te backfillen

        nieuw = find_ticker_met_snelle_prijscheck(info["naam"], isin, beurs, transacties)
        nieuwe_ticker = nieuw["ticker"]
        if not nieuwe_ticker or nieuwe_ticker == oude_ticker:
            continue

        if _ticker_heeft_prijsprobleem(nieuwe_ticker, transacties):
            # print(f"[backfill-ticker] ISIN={isin} (beurs={beurs}): oude ticker '{oude_ticker}' had een "
                  # f"prijsprobleem, maar kandidaat '{nieuwe_ticker}' ook -- NIET overschreven, "
                  # f"handmatige controle nodig.")
            continue

        cur.execute(
            "UPDATE transacties SET ticker = %s WHERE code = %s AND isin = %s AND beurs = %s",
            (nieuwe_ticker, code, isin, beurs),
        )
        gecorrigeerd += 1
        # print(f"[backfill-ticker] ISIN={isin} (beurs={beurs}): ticker gecorrigeerd van '{oude_ticker}' "
              # f"naar '{nieuwe_ticker}' ({cur.rowcount} rij(en)) -- oude ticker had een prijsprobleem, "
              # f"nieuwe niet.")

    conn.commit()
    cur.close()
    conn.close()
    return gecorrigeerd


def prijswaarschuwing_voor_ticker(ticker, transacties_van_dit_isin, isin=None):
    """
    Leest (via vergelijk_prijs_op_datum's eigen ticker_prijscheck-cache) of
    de laatste transactieprijs van deze positie afwijkt van Yahoo — voor
    gebruik bij ELK bezoek aan een opgeslagen portfolio (analyze_transacties
    in app.py), niet alleen direct na de upload. Roept BEWUST
    find_ticker_detailed() niet aan (dat doet altijd een live yahooquery-
    zoekopdracht, nooit gecached) en doet geen kandidaten-escalatie (die
    heeft dezelfde beperking) — de ticker is hier al bekend (opgeslagen in
    de transacties-tabel), dus dat is niet nodig. In de praktijk is dit een
    cache-hit: find_ticker_met_snelle_prijscheck() heeft de cache voor de
    laatste transactiedatum meestal al gevuld bij upload.

    isin (optioneel): als gegeven, wordt ook de OpenFIGI-root-check
    toegepast (zie _voeg_openfigi_check_toe elders in dit bestand) — zelfde
    extra validatiesignaal als de upload-route en de Ticker-zekerheid-
    pagina, zodat de permanente banner bovenaan een opgeslagen portfolio
    ook een root-mismatch laat zien (bv. VWCE.AS) ook als de prijscontrole
    zelf niets bijzonders zag. Zonder isin (bv. bestaande aanroepen) wordt
    deze check overgeslagen -- bestaand gedrag blijft ongewijzigd.
    """
    geldige_transacties = [
        t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0
    ]
    if not ticker or not geldige_transacties:
        boodschap = None
    else:
        laatste = max(geldige_transacties, key=lambda t: t["datum"])
        check = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
        if check["afwijking_pct"] is None or not _prijscheck_is_probleem(check):
            boodschap = None
        elif check.get("binnen_dagrange") is False:
            boodschap = (
                f"Koers van {ticker} valt op {laatste['datum']} buiten de dagrange (high/low) van "
                f"Yahoo — controleer op het Ticker-zekerheid-tabblad."
            )
        else:
            boodschap = (
                f"Koers van {ticker} wijkt {check['afwijking_pct']:.1f}% af van Yahoo — "
                f"controleer op het Ticker-zekerheid-tabblad."
            )

    if not ticker or not isin:
        return boodschap

    openfigi = haal_openfigi_resultaten(isin)
    matches = _openfigi_root_matches(ticker, openfigi["resultaten"])
    if matches is None or matches > 0:
        return boodschap

    extra_waarschuwing = (
        f"Ticker-root '{ticker.split('.')[0]}' komt niet voor in OpenFIGI's "
        f"resultaten voor deze ISIN — controleer op het Ticker-zekerheid-tabblad."
    )
    # print(f"[openfigi-check] ⚠️ {isin}: {extra_waarschuwing}")
    return f"{boodschap}\n{extra_waarschuwing}" if boodschap else extra_waarschuwing


def ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen):
    """
    Verzamelt prijswaarschuwing_voor_ticker()-meldingen voor elke unieke
    ticker in transacties_df — gebruikt door analyze_transacties() in app.py
    bij ELK bezoek aan een portfolio (niet alleen direct na de upload), zie
    prijswaarschuwing_voor_ticker() hierboven voor waarom dat in het
    gangbare geval geen nieuwe Yahoo-calls kost.

    transacties_df: moet minstens de kolommen 'ticker', 'datum', 'koers'
    bevatten ('isin' optioneel, voor de OpenFIGI-root-check -- ontbreekt 'ie,
    dan wordt die check overgeslagen voor alle tickers). ticker_namen:
    {ticker: weergavenaam}, voor de UI. Geeft een lijst van {"ticker",
    "naam", "boodschap"} terug (leeg als niets afwijkt).
    """
    heeft_isin_kolom = "isin" in transacties_df.columns
    waarschuwingen = []
    for ticker, groep in transacties_df.dropna(subset=["ticker"]).groupby("ticker"):
        transacties_van_ticker = [{"datum": d, "koers": k} for d, k in zip(groep["datum"], groep["koers"])]
        isin = groep["isin"].iloc[0] if heeft_isin_kolom and not groep.empty else None
        boodschap = prijswaarschuwing_voor_ticker(ticker, transacties_van_ticker, isin)
        if boodschap:
            waarschuwingen.append({
                "ticker": ticker, "naam": ticker_namen.get(ticker, ticker), "boodschap": boodschap,
            })
    return waarschuwingen


def vind_tickers_met_snelle_prijscheck_parallel(posities, bekende_tickers=None,
                                                 max_workers=TICKER_RESOLUTIE_POOL_GROOTTE):
    """
    Voert find_ticker_met_snelle_prijscheck() voor meerdere posities
    tegelijk uit (ThreadPoolExecutor), zelfde patroon als
    verifieer_tickers_met_prijs_parallel() hieronder. Een hoger standaard
    max_workers dan die functie: het gangbare geval hier is maar 1 Yahoo-
    call per positie (i.p.v. tot wel 1 (eigen) + N (kandidaten) x 3
    (steekproef) bij de volledige check), dus meer gelijktijdige workers
    kosten geen extra risico op rate-limiting per positie.

    posities: lijst van (product, isin, beurs, transacties_van_dit_isin).
    bekende_tickers (optioneel): {(isin, beurs): ticker}-dict van al eerder
    opgeloste posities uit een vorige upload -- per positie doorgegeven als
    'bekende_ticker' aan find_ticker_met_snelle_prijscheck() (zie daar),
    die dan de zoekopdracht overslaat. Leeg/None (standaard) betekent: geen
    enkele positie overslaan, ongewijzigd bestaand gedrag.
    Geeft een lijst van resultaat-dicts terug, in dezelfde volgorde als
    'posities' (dus niet per se de volgorde waarin ze klaar zijn).
    """
    bekende_tickers = bekende_tickers or {}
    resultaten = [None] * len(posities)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_naar_index = {
            executor.submit(
                find_ticker_met_snelle_prijscheck, product, isin, beurs, transacties,
                bekende_tickers.get((isin, beurs)),
            ): i
            for i, (product, isin, beurs, transacties) in enumerate(posities)
        }
        for future in as_completed(future_naar_index):
            resultaten[future_naar_index[future]] = future.result()
    return resultaten


def verifieer_tickers_met_prijs_parallel(posities, max_workers=6):
    """
    Voert verifieer_ticker_met_prijs() voor meerdere posities tegelijk uit
    (ThreadPoolExecutor) i.p.v. na elkaar in een for-loop — dit is vrijwel
    allemaal I/O-wachttijd (Yahoo-calls + DB-round-trips naar Neon), geen
    zware CPU-berekening, dus meerdere posities tegelijk verwerken levert
    een groot deel van de tijdswinst zonder de logica per positie aan te
    hoeven passen.

    Nodig bovenop spoor 1 (stop bij overtuigende match) en spoor 2
    (prijscheck-cache) voor de Ticker-zekerheid-worker-timeout: bij een
    fonds dat op meerdere beurzen genoteerd staat (bv. Vanguard/iShares-
    varianten als VWCE.AS/VWCE.DE/VWCE.MI) liggen de koersen vaak zo dicht
    bij elkaar dat GEEN enkele kandidaat een "overtuigende" match oplevert
    (elke datum net onder de 2%-drempel, maar nooit alle datums tegelijk) —
    dan wordt alsnog de hele kandidatenlijst doorgerekend en helpt spoor 1
    niet. Gemeten op de echte portfolio (12 posities, kandidatenlijsten tot
    7 kandidaten): zelfs met een volledig warme cache (geen Yahoo-calls
    meer nodig, puur DB-round-trips) duurde de sequentiële versie ~84s —
    al ruim boven de standaard gunicorn-timeout van 30s. Parallel over de
    posities (elke positie is onafhankelijk, geen gedeelde staat behalve de
    database, en elke DB-functie opent zijn eigen connectie) is dan de
    volgende logische stap.

    Geeft een lijst van resultaat-dicts terug, in dezelfde volgorde als
    'posities' (dus niet per se de volgorde waarin ze klaar zijn). Logt de
    tijd per positie apart (i.p.v. alleen de totale duur, die de aanroeper
    zelf al logt) zodat zichtbaar wordt welke specifieke positie traag is.
    """
    def _verifieer_met_timing(naam, isin, beurs, transacties):
        t0 = time.time()
        resultaat = verifieer_ticker_met_prijs(naam, isin, beurs, transacties)
        # print(f"[ticker-zekerheid] positie {isin} ({beurs}) klaar in {time.time() - t0:.1f}s")
        return resultaat

    resultaten = [None] * len(posities)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_naar_index = {
            executor.submit(_verifieer_met_timing, naam, isin, beurs, transacties): i
            for i, (naam, isin, beurs, transacties) in enumerate(posities)
        }
        for future in as_completed(future_naar_index):
            resultaten[future_naar_index[future]] = future.result()
    return resultaten


