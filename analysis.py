import random
import re
import string
import io
import hashlib
import os
import threading
from contextlib import contextmanager
import pandas as pd
import requests
import yfinance as yf
from flask import g, has_app_context
from pyxirr import xirr
from yahooquery import search
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import (get_db_connection, save_prices, upsert_prices, get_cached_classifications, save_classification,
                 get_cached_land_sector, save_land_sector, get_cached_etf_sector_verdeling,
                 save_etf_sector_verdeling, get_cached_etf_holdings, save_etf_holdings,
                 get_ticker_details, get_cached_prijscheck, save_prijscheck, get_dividenden,
                 get_cached_splits, save_splits, get_cached_openfigi, save_openfigi)
import time

# Zet op True om overal in dit bestand debug-prints aan te zetten.
DEBUG = True

def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


@contextmanager
def meet_tijd(label):
    """Herbruikbare timing-helper voor de performance-meting van de upload/
    analyse-flow: logt de verstreken tijd van het omsloten codeblok met een
    [timing]-prefix, in lijn met de bestaande [upload]/[koersen]/[split]-
    prefix-conventie. Eén centrale plek i.p.v. losse
    `t0 = time.time(); ...; time.time() - t0`-boilerplate in elke functie
    die een fase wil timen."""
    start = time.time()
    try:
        yield
    finally:
        print(f"[timing] {label}: {time.time() - start:.2f}s")


# Telt individuele Yahoo-calls (yfinance + yahooquery) per type, voor de
# performance-meting van de upload-flow. Lock nodig omdat ticker-resolutie/
# koersen/verrijking deels parallel draaien via ThreadPoolExecutor.
_yahoo_call_lock = threading.Lock()
_yahoo_call_teller = {}


def reset_yahoo_call_teller():
    """Zet de Yahoo-call-teller terug naar 0 -- aangeroepen aan het begin
    van _upload_impl() zodat elke upload zijn EIGEN call-aantal rapporteert,
    niet een cumulatief totaal sinds het opstarten van de server."""
    with _yahoo_call_lock:
        _yahoo_call_teller.clear()


def _tel_yahoo_call(soort):
    """Registreert één Yahoo-call van het gegeven type (bv. 'yf.download',
    'yahooquery.search', 'yf.Ticker.info')."""
    with _yahoo_call_lock:
        _yahoo_call_teller[soort] = _yahoo_call_teller.get(soort, 0) + 1


def log_yahoo_call_samenvatting():
    """Logt de Yahoo-call-tellingen sinds de laatste reset, gegroepeerd per
    type call, plus het totaal."""
    with _yahoo_call_lock:
        samenvatting = dict(_yahoo_call_teller)
    totaal = sum(samenvatting.values())
    print(f"[timing] Yahoo-calls sinds laatste reset: {totaal} totaal -> {samenvatting}")


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

# FX-paar per valuta, gebruikt door zowel _converteer_naar_eur() (via
# get_prices()) als _fx_koers_op_datum() (via vergelijk_prijs_op_datum) --
# één plek voor de mapping i.p.v. 'fx_pair = "USDEUR=X" if ... else
# "GBPEUR=X"' op twee plekken herhaald.
FX_PAAR_PER_VALUTA = {"USD": "USDEUR=X", "GBP": "GBPEUR=X", "GBp": "GBPEUR=X"}

# Vaste ankerdatum voor de FX-reeks-cache (zie _fx_prijzen_serie). Bewust
# een VASTE datum i.p.v. per aanroep de eigen 'vanaf'/'datum' van de
# aanroeper doorgeven: get_prices() beschouwt een cache die tot 5 dagen na
# de gevraagde startdatum begint al als "goed genoeg" (onschuldig voor een
# doorlopende koersreeks, die dan een paar dagen later begint) -- voor een
# PUNT-in-tijd FX-opzoeking zou dat net de verkeerde handelsdag kunnen
# opleveren als twee aanroepen met een net iets andere datum na elkaar
# komen. Met een vaste ankerdatum is de cache na de eerste keer altijd
# voor alle aanroepen ver genoeg terug.
#
# LET OP: deze datum moet op/na de ECHTE eerste Yahoo-datum van elk
# FX-paar liggen (leeg getest: USDEUR=X vanaf 2003-12-01, GBPEUR=X vanaf
# 2003-09-17) -- eerder dan dat zou get_prices() z'n eigen cache altijd
# als "niet ver genoeg terug" blijven zien (eerste > start_date + 5 dagen
# gaat dan NOOIT weg) en dus bij ELKE aanroep opnieuw laten downloaden,
# precies het duplicate-call-probleem dat deze fix moest oplossen.
# 2005-01-01 zit ruim ná die echte Yahoo-startdatums, en ruim VÓÓR elke
# denkbare DeGiro-transactiedatum (DeGiro bestaat pas sinds 2008).
FX_ANKER_DATUM = pd.Timestamp("2005-01-01")

# get_prices() ververst de cache-rij van "vandaag" voortaan bij ELKE
# aanroep (i.p.v. pas als de cache >4 dagen achterloopt, zie CLAUDE.md,
# "koersen bij élke portfolio-opening verversen") — deze drempel voorkomt
# dat de meerdere endpoints van ÉÉN portfolio-opening (home, verrijking,
# ticker-zekerheid) Yahoo binnen dezelfde paar seconden meermaals voor
# dezelfde ticker bevragen.
DREMPEL_HERGEBRUIK_KOERS = pd.Timedelta(minutes=2)

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

# Handmatig opgezochte directe download-links naar de volledige
# holdings-CSV/XLSX van de ETF-provider (voor landverdeling — sector blijft
# via yfinance sector_weightings, dat dekt al ~100%). yfinance's
# funds_data.top_holdings geeft maar de top 10, wat voor een breed gespreid
# fonds (bv. CSPX.AS, 500+ posities) maar ~35-40% dekking geeft; de
# provider zelf publiceert de volledige lijst.
#
# Zoek dit op via de fondspagina van de provider (iShares/Vanguard/VanEck
# etc.) -> knop "Holdings downloaden" / "Full holdings" -> rechtermuisklik
# op de downloadknop -> "Kopieer linkadres". Test een gevonden link eerst
# met test_holdings_url(url, provider) voordat je 'm hier toevoegt. Vul
# stap voor stap aan naarmate je meer ETF's tegenkomt; een ticker die hier
# niet in staat valt automatisch terug op de yfinance-top-10-aanpak.
ETF_HOLDINGS_BRON = {
    # LET OP: geen asOfDate-parameter in de blackrock.com-URL's — die moet
    # exact de huidige datum zijn (getest: een andere/oude datum geeft een
    # lege CSV terug, geen fout), dus een hardcoded datum zou na vandaag
    # stil stuk gaan. Zonder asOfDate geeft BlackRock automatisch de meest
    # recente holdings terug.
    #
    # "locale" (optioneel, default "en" — zie fetch_provider_holdings()):
    # bepaalt zowel het GETALFORMAAT (Nederlands: punt=duizendtal,
    # komma=decimaal; Engels: komma=duizendtal, punt=decimaal) als de TAAL
    # van landnamen in de brondata. Is een eigenschap van de bron-URL/site,
    # niet van het fonds — de blackrock.com/varnish-api-bron (CSPX.AS,
    # CNDX.AS, expliciet locale=en_GB in de URL) is Engels; de
    # ishares.com/nl/-site (IWDA.AS, IMAE.AS, EMIM.AS) en VanEck se
    # Nederlandse site (GDX.L) zijn Nederlands. Dit ontdekten we pas door
    # test_holdings_url() te draaien: zonder locale="nl" werd bv. "5,25%"
    # stilzwijgend als 525 gelezen (komma weggehaald als duizendtal-
    # scheidingsteken) — een gewichten-som van ~10000% i.p.v. ~100%. Zie
    # _parse_ishares_holdings()/_parse_vaneck_holdings() voor de details.
    "CSPX.AS": {
        "provider": "ishares",
        "url": "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/"
               "get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=ishares-uk"
               "&locale=en_GB&portfolioId=253743&userType=individual&component=holdings",
    },
    "IWDA.AS": {
        "provider": "ishares",
        "locale": "nl",
        "url": "https://www.ishares.com/nl/particuliere-belegger/nl/producten/251882/ishares-msci-world-ucits-etf-acc-fund/1497735778849.ajax?fileType=csv&fileName=IWDA_holdings&dataType=fund",
    },
    "IMAE.AS": {
        "provider": "ishares",
        "locale": "nl",
        "url": "https://www.ishares.com/nl/particuliere-belegger/nl/producten/251861/ishares-msci-europe-ucits-etf-acc-fund/1497735778849.ajax?fileType=csv&fileName=IMAE_holdings&dataType=fund",
    },
    "EMIM.AS": {
        "provider": "ishares",
        "locale": "nl",
        "url": "https://www.ishares.com/nl/particuliere-belegger/nl/producten/264659/ishares-msci-emerging-markets-imi-ucits-etf/1497735778849.ajax?fileType=csv&fileName=EMIM_holdings&dataType=fund",
    },
    # IS3N.DE = zelfde ISIN (IE00BKM4GZ66) als EMIM.AS, alleen een andere
    # notering (Xetra i.p.v. Amsterdam) van hetzelfde fonds — zelfde bron-URL.
    "IS3N.DE": {
        "provider": "ishares",
        "locale": "nl",
        "url": "https://www.ishares.com/nl/particuliere-belegger/nl/producten/264659/ishares-msci-emerging-markets-imi-ucits-etf/1497735778849.ajax?fileType=csv&fileName=EMIM_holdings&dataType=fund",
    },
    "CNDX.AS": {
        "provider": "ishares",
        "url": "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=ishares-uk&locale=en_GB&portfolioId=253741&userType=individual&component=holdings",
    },
    "GDX.L": {
        "provider": "vaneck",
        "locale": "nl",
        "url": "https://www.vaneck.com/nl/nl/investments/gold-miners-etf/downloads/holdings/",
    },
    # G2X.DE = zelfde ISIN (IE00BQQP9F84) als GDX.L, alleen een andere
    # notering (Xetra i.p.v. Londen) van hetzelfde fonds — zelfde bron-URL.
    "G2X.DE": {
        "provider": "vaneck",
        "locale": "nl",
        "url": "https://www.vaneck.com/nl/nl/investments/gold-miners-etf/downloads/holdings/",
    },
    "EUEA.AS": {
        "provider": "ishares",
        "locale": "nl",
        "url": "https://www.ishares.com/nl/particuliere-belegger/nl/producten/251781/ishares-euro-stoxx-50-ucits-etf-inc-fund/1497735778849.ajax?fileType=csv&fileName=EUEA_holdings&dataType=fund",
    },
    # TDT.AS' bron is de Engelstalige VanEck NL-site (url-pad /nl/en/), dus
    # locale="en" — i.t.t. GDX.L/VE6I.DE die via de Nederlandstalige site
    # (/nl/nl/) gaan. Kolomkop is hier ook net anders ("Holding Name" i.p.v.
    # "Naam positie"/"Naam"), zie _parse_vaneck_holdings().
    "TDT.AS": {
        "provider": "vaneck",
        "locale": "en",
        "url": "https://www.vaneck.com/nl/en/investments/aex-etf/downloads/holdings",
    },
    "VE6I.DE": {
        "provider": "vaneck",
        "locale": "nl",
        "url": "https://www.vaneck.com/nl/nl/investments/food-etf/downloads/holdings/",
    },
}

# Bewust NIET toegevoegd: VWCE.AS en VUSA.AS (Vanguard). Vanguard's site
# haalt de holdings-download op via een GraphQL-API met een complexe query
# in de request-body, niet via een simpele GET-URL zoals bij iShares/VanEck
# — te fragiel (kan breken bij elke Vanguard-site-update) en te complex
# voor de meerwaarde. Deze twee draaien bewust op de yfinance-top-10-
# fallback voor land (~30-40% dekking); dit is een geaccepteerde beperking,
# geen openstaande bug.

_PROVIDER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _holding_rij(naam, gewicht, land, sector):
    """Eén genormaliseerde holdings-rij. Rijen zonder geldig gewicht worden
    door de aanroeper overgeslagen; land/sector worden expliciet 'Unknown'
    i.p.v. leeg/None, zodat een 'Cash'- of 'Futures'-rij in de landverdeling
    zichtbaar in een eigen bucket terechtkomt in plaats van de optelsom
    stilzwijgend te verstoren."""
    return {
        "naam": str(naam).strip(),
        "gewicht": float(gewicht),
        "land": (str(land).strip() if pd.notna(land) and str(land).strip() else "Unknown"),
        "sector": (str(sector).strip() if sector is not None and pd.notna(sector) and str(sector).strip() else None),
    }


def _parse_percentage_waarde(waarde, locale="en"):
    """Zet een gewicht-celwaarde om naar een float-percentage (bv. 5.25
    voor 5,25%). Twee vormen komen voor: een kant-en-klaar getal (Engelse
    bronnen, bv. iShares' blackrock.com-CSV geeft al '7.68'), of een string
    met een %-teken en Nederlandse komma-decimaal (bv. VanEck se
    Nederlandse XLSX geeft '10,74%') — die laatste wordt eerst opgeschoond
    (%-teken eraf, duizendtal-punten eraf, komma -> punt) voordat
    pd.to_numeric() 'm kan parsen. Zonder deze opschoning leest
    pd.to_numeric zo'n string simpelweg niet (geeft NaN, geen fout) —
    precies zo werd de eerdere iShares-locale-bug pas zichtbaar via
    test_holdings_url()'s gewichtensom (~10000% i.p.v. ~100%)."""
    if isinstance(waarde, str):
        waarde = waarde.strip().rstrip("%").strip()
        if locale == "nl":
            waarde = waarde.replace(".", "").replace(",", ".")
    return pd.to_numeric(waarde, errors="coerce")


def _parse_ishares_holdings(content, locale="en"):
    """iShares full-holdings CSV. Header staat meestal vanaf regel 3
    (skiprows=2). Kolommen: Name, Weight (%), Sector, Location (of
    Country) — afhankelijk van het fonds.

    'locale' is een eigenschap van de BRON-URL (zie ETF_HOLDINGS_BRON), niet
    van het fonds: dezelfde iShares-CSV-structuur komt terug in twee
    varianten. De blackrock.com/varnish-api-bron (expliciet locale=en_GB in
    de URL) geeft Engels getalformaat (komma=duizendtal, punt=decimaal) en
    Engelse landnamen ('United States'). De ishares.com/nl/-site geeft
    Nederlands getalformaat (punt=duizendtal, komma=decimaal) én
    Nederlandse landnamen ('Verenigde Staten') — die laatste worden via
    _vertaal_land_nl() vertaald, anders zou hetzelfde land in de
    portfoliobrede landverdeling (compute_land_sector_verdeling, die alle
    ETF's optelt op landnaam) als twee aparte taartpunten verschijnen
    afhankelijk van welk fonds het aanlevert."""
    if locale == "nl":
        df = pd.read_csv(io.BytesIO(content), skiprows=2, thousands=".", decimal=",")
    else:
        df = pd.read_csv(io.BytesIO(content), skiprows=2, thousands=",")
    df.columns = df.columns.str.strip()

    land_kolom = "Location" if "Location" in df.columns else "Country"
    sector_kolom = "Sector" if "Sector" in df.columns else None

    holdings = []
    for _, row in df.iterrows():
        naam = row.get("Name")
        if pd.isna(naam) or not str(naam).strip():
            continue
        gewicht = _parse_percentage_waarde(row.get("Weight (%)"), locale)
        if pd.isna(gewicht):
            continue
        land = row.get(land_kolom)
        if locale == "nl" and pd.notna(land) and str(land).strip():
            land = _vertaal_land_nl(str(land).strip())
        holdings.append(_holding_rij(
            naam, gewicht, land, row.get(sector_kolom) if sector_kolom else None,
        ))
    return holdings


# Vertaaltabel Nederlandse -> Engelse landnamen, voor iShares-bronnen met
# locale="nl" (zie _parse_ishares_holdings) — Engelse namen omdat de rest
# van het project (yfinance's land-veld, de blackrock.com/varnish-api-
# Engelse CSV's) al die conventie gebruikt, en dezelfde-land-twee-buckets-
# bug (zie hierboven) alleen voorkomen wordt als ALLE bronnen naar één
# gemeenschappelijke taal vertalen. Bewust de informele/gangbare Engelse
# namen (bv. "South Korea", niet ISO's officiële "Korea, Republic of") om
# aan te sluiten bij yfinance's conventie, niet bij pycountry's ISO-namen.
# Samengesteld uit de daadwerkelijke landnamen in de IWDA/IMAE/EMIM-CSV's
# (opgehaald en gecontroleerd tijdens het toevoegen van deze bronnen) —
# vul aan als een nieuw fonds een landnaam gebruikt die hier nog niet in
# staat (_vertaal_land_nl hieronder waarschuwt dan expliciet).
NL_LAND_VERTALING = {
    "-": "Unknown",
    "Australië": "Australia",
    "België": "Belgium",
    "Brazilië": "Brazil",
    "Canada": "Canada",
    "Chili": "Chile",
    "China": "China",
    "Colombia": "Colombia",
    "Denemarken": "Denmark",
    "Duitsland": "Germany",
    "Egypte": "Egypt",
    "Europese Unie": "European Union",
    "Filipijnen": "Philippines",
    "Finland": "Finland",
    "Frankrijk": "France",
    "Griekenland": "Greece",
    "Hong Kong": "Hong Kong",
    "Hongarije": "Hungary",
    "Ierland": "Ireland",
    "India": "India",
    "Indonesië": "Indonesia",
    "Israël": "Israel",
    "Italië": "Italy",
    "Japan": "Japan",
    "Koeweit": "Kuwait",
    "Maleisië": "Malaysia",
    "Mexico": "Mexico",
    "Nederland": "Netherlands",
    "Nieuw-Zeeland": "New Zealand",
    "Noorwegen": "Norway",
    "Oostenrijk": "Austria",
    "Peru": "Peru",
    "Polen": "Poland",
    "Portugal": "Portugal",
    "Qatar": "Qatar",
    "Rusland": "Russia",
    "Saoedi-Arabië": "Saudi Arabia",
    "Singapore": "Singapore",
    "Spanje": "Spain",
    "Taiwan": "Taiwan",
    "Thailand": "Thailand",
    "Tsjechië": "Czech Republic",
    "Turkije": "Turkey",
    "Verenigd Koninkrijk": "United Kingdom",
    "Verenigde Arabische Emiraten": "United Arab Emirates",
    "Verenigde Staten": "United States",
    "Zuid-Afrika": "South Africa",
    "Zuid-Korea": "South Korea",
    "Zweden": "Sweden",
    "Zwitserland": "Switzerland",
}


def _vertaal_land_nl(land):
    """Vertaalt een Nederlandse landnaam (uit een ishares.com/nl/- of
    VanEck-NL-bron) naar de Engelse naam die de rest van het project
    gebruikt (zie NL_LAND_VERTALING hierboven). Een onbekende naam blijft
    bewust ONVERTAALD i.p.v. stilzwijgend 'Unknown' te worden — zo blijft
    hij als aparte, herkenbare bucket zichtbaar in de UI i.p.v. op te gaan
    in een verkeerde categorie, en de waarschuwing hieronder maakt
    duidelijk dat NL_LAND_VERTALING aangevuld moet worden."""
    if land in NL_LAND_VERTALING:
        return NL_LAND_VERTALING[land]
    # print(f"[etf-holdings-provider] ⚠️ onbekende Nederlandse landnaam '{land}' — "
          # f"NL_LAND_VERTALING aanvullen, blijft voor nu onvertaald staan")
    return land


def _land_via_isin(isin):
    """Land afgeleid van de eerste 2 tekens van een ISIN — het land van
    registratie van de uitgevende instelling, een wereldwijd
    gestandaardiseerde conventie (ISO 6166). Gebruikt als een holdings-
    bron geen aparte land/country-kolom heeft (bv. VanEck's GDX-bestand,
    dat alleen Ticker/ISIN geeft, geen Land) — een redelijke proxy, geen
    garantie dat het land van registratie exact overeenkomt met waar een
    bedrijf economisch actief is (bv. een Britse ISIN voor een
    Zuid-Afrikaans mijnbouwbedrijf, zie AngloGold Ashanti), maar veel beter
    dan alles op 'Unknown' laten staan."""
    if not isin or not isinstance(isin, str) or len(isin) < 2:
        return "Unknown"
    code = isin[:2].upper()
    try:
        import pycountry
        land = pycountry.countries.get(alpha_2=code)
        if land:
            return land.name
    except Exception as e:
        dprint(f"[etf-holdings-provider] pycountry-lookup faalde voor ISIN-prefix '{code}': {e}")
    return "Unknown"


def _regio_naar_land(regio_code):
    """Zet een Vanguard-regiocode om naar een leesbare landnaam via
    pycountry (Vanguard gebruikt vaak 2-letter ISO-landcodes in de
    'Region'-kolom). Geeft de code zelf terug als pycountry 'm niet kent
    (bv. al een leesbare naam, of een bredere regio zoals 'Europe')."""
    if pd.isna(regio_code) or not str(regio_code).strip():
        return "Unknown"
    regio_code = str(regio_code).strip()
    try:
        import pycountry
        land = pycountry.countries.get(alpha_2=regio_code.upper())
        if land:
            return land.name
    except Exception as e:
        dprint(f"[etf-holdings-provider] pycountry-lookup faalde voor '{regio_code}': {e}")
    return regio_code


def _parse_vanguard_holdings(content, locale="en"):
    """Vanguard full-holdings XLSX. Header staat vanaf regel 7
    (skiprows=6). Kolommen: Holding name, % of market value, Sector,
    Region (regiocode, omgezet naar landnaam via _regio_naar_land).
    'locale' wordt (nog) niet gebruikt door deze parser — parameter erbij
    voor een uniforme aanroep-signatuur met de andere parsers, zie
    fetch_provider_holdings()."""
    df = pd.read_excel(io.BytesIO(content), skiprows=6)
    df.columns = df.columns.str.strip()

    holdings = []
    for _, row in df.iterrows():
        naam = row.get("Holding name")
        if pd.isna(naam) or not str(naam).strip():
            continue
        gewicht = pd.to_numeric(row.get("% of market value"), errors="coerce")
        if pd.isna(gewicht):
            continue
        holdings.append(_holding_rij(
            naam, gewicht, _regio_naar_land(row.get("Region")), row.get("Sector"),
        ))
    return holdings


def _parse_vaneck_holdings(content, locale="en"):
    """VanEck full-holdings XLSX. Header staat vanaf regel 3 (skiprows=2).
    Kolomnamen EN getalformaat variëren per fonds/site-locale — probeer
    bekende varianten i.p.v. er blind 1 aan te nemen (zie
    _parse_percentage_waarde() voor het getalformaat).

    Sommige VanEck-exports (bv. GDX via de Nederlandse site) hebben GEEN
    aparte land/country-kolom, alleen Ticker/ISIN — land wordt dan
    afgeleid uit de ISIN via _land_via_isin() (zie die functie voor de
    kanttekening). Is er wél een expliciete land-kolom, dan heeft die
    voorrang (preciezer dan een ISIN-afleiding)."""
    df = pd.read_excel(io.BytesIO(content), skiprows=2)
    df.columns = df.columns.str.strip()

    gewicht_kolom = next(
        (k for k in ("% van beheerd vermogen", "% of Net Assets", "Weight (%)") if k in df.columns), None,
    )
    if gewicht_kolom is None:
        raise ValueError(f"geen bekende gewicht-kolom gevonden in VanEck-bestand: {list(df.columns)}")
    naam_kolom = next(
        (k for k in ("Naam positie", "Naam", "Name", "Holding", "Holding Name") if k in df.columns), None,
    )
    land_kolom = next((k for k in ("Land", "Country", "Location") if k in df.columns), None)
    isin_kolom = "ISIN" if "ISIN" in df.columns else None

    holdings = []
    for _, row in df.iterrows():
        naam = row.get(naam_kolom) if naam_kolom else None
        if pd.isna(naam) or not str(naam).strip():
            continue
        gewicht = _parse_percentage_waarde(row.get(gewicht_kolom), locale)
        if pd.isna(gewicht):
            continue
        if land_kolom:
            land = row.get(land_kolom)
        elif isin_kolom:
            land = _land_via_isin(row.get(isin_kolom))
        else:
            land = None
        holdings.append(_holding_rij(
            naam, gewicht, land, row.get("Sector"),
        ))
    return holdings


_PROVIDER_PARSERS = {
    "ishares": _parse_ishares_holdings,
    "vanguard": _parse_vanguard_holdings,
    "vaneck": _parse_vaneck_holdings,
}


def _dedupliceer_holdings(holdings):
    """Voegt holdings met dezelfde naam samen (som van hun gewicht) — sommige
    providers geven meerdere posities onder exact dezelfde (vaak afgekapte)
    naam terug: bv. verschillende aandelenklassen van hetzelfde bedrijf, of
    meerdere FX-hedge-contracten met verschillende looptijd/tranche onder
    dezelfde naam (ontdekt bij EMIM.AS: 'INDUSTRIAL AND COMMERCIAL BANK OF'
    2x, 'INR/USD' zelfs 29x). etf_holdings' primary key is (etf_ticker,
    holding_naam) — zonder deze samenvoeging crasht het opslaan met een
    UniqueViolation zodra een fonds dit patroon heeft. Voor de landverdeling
    (waar dit uiteindelijk voor gebruikt wordt) maakt het niet uit of zulke
    duplicaten apart blijven of samengevoegd worden — het gewicht per land
    telt sowieso bij elkaar op."""
    per_naam = {}
    for h in holdings:
        bestaand = per_naam.get(h["naam"])
        if bestaand is None:
            per_naam[h["naam"]] = dict(h)
        else:
            bestaand["gewicht"] += h["gewicht"]
    return list(per_naam.values())


def fetch_provider_holdings(etf_ticker):
    """
    Haalt de VOLLEDIGE holdings-lijst van een ETF op bij de fondsprovider
    zelf (i.p.v. yfinance's top-10), als er een URL voor bekend is in
    ETF_HOLDINGS_BRON. Dekking: bijna 100%, tegenover de ~35-40% die
    yfinance's top_holdings geeft voor een breed gespreid fonds.

    Geeft None terug als er geen URL bekend is voor deze ticker, of als het
    ophalen/parsen om wat voor reden dan ook mislukt — de aanroeper
    (get_etf_holdings) valt dan terug op de yfinance-top-10-aanpak. Deze
    functie mag daarom nooit crashen: een provider die zijn bestandsformaat
    wijzigt mag niet de rest van de pagina meenemen.
    """
    bron = ETF_HOLDINGS_BRON.get(etf_ticker)
    if bron is None:
        return None

    provider = bron["provider"]
    url = bron["url"]
    locale = bron.get("locale", "en")
    parser = _PROVIDER_PARSERS.get(provider)
    if parser is None:
        # print(f"[etf-holdings-provider] ❌ onbekende provider '{provider}' voor '{etf_ticker}'")
        return None

    try:
        response = requests.get(url, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=30)
        response.raise_for_status()
        holdings = parser(response.content, locale=locale)
    except Exception as e:
        # print(f"[etf-holdings-provider] ❌ kon holdings niet ophalen/parsen voor '{etf_ticker}' "
              # f"(provider={provider}, url={url}): {e}")
        return None

    if not holdings:
        # print(f"[etf-holdings-provider] ⚠️ lege holdings-lijst voor '{etf_ticker}' (provider={provider})")
        return None

    aantal_voor_dedup = len(holdings)
    holdings = _dedupliceer_holdings(holdings)
    if len(holdings) != aantal_voor_dedup:
        pass
        # print(f"[etf-holdings-provider] '{etf_ticker}': {aantal_voor_dedup - len(holdings)} "
              # f"dubbele holding-naam/namen samengevoegd ({aantal_voor_dedup} -> {len(holdings)})")

    totaal_gewicht = sum(h["gewicht"] for h in holdings)
    # print(f"[etf-holdings-provider] '{etf_ticker}': {len(holdings)} holdings opgehaald via {provider}, "
          # f"totaal gewicht {totaal_gewicht:.1f}%")
    return holdings


def test_holdings_url(url, provider, locale="en"):
    """
    Test-helper: haalt een provider-URL op en parset 'm, ZONDER 'm aan
    ETF_HOLDINGS_BRON toe te voegen of iets te cachen — gebruik dit om een
    gevonden download-link te checken vóórdat je 'm toevoegt, bv.:

        test_holdings_url("https://www.ishares.com/.../download", "ishares", locale="nl")

    Print het aantal gevonden holdings en de som van de gewichten (moet
    dicht bij 100 liggen) plus de eerste paar rijen, zodat meteen duidelijk
    is of de kolom-aannames (skiprows, kolomnamen, getalformaat) voor dit
    bestand kloppen.
    """
    parser = _PROVIDER_PARSERS.get(provider)
    if parser is None:
        # print(f"[test-holdings-url] onbekende provider '{provider}', kies uit: {list(_PROVIDER_PARSERS)}")
        return None

    response = requests.get(url, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=30)
    response.raise_for_status()
    holdings = parser(response.content, locale=locale)

    # print(f"[test-holdings-url] {len(holdings)} holdings gevonden")
    # print(f"[test-holdings-url] som van gewichten: {sum(h['gewicht'] for h in holdings):.2f}%")
    # print("[test-holdings-url] eerste 5 rijen:")
    for h in holdings[:5]:
        pass
        # print("  ", h)
    return holdings


def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


def compute_split_adjusted_shares(transacties_df):
    """
    Corrigeert aandelenaantallen voor stock splits, gedetecteerd via DEGIRO's
    NON TRADEABLE/DEG-rijen. Voegt een 'adj_aantal' kolom toe die gebruikt moet
    worden i.p.v. 'aantal' bij alle waarde-berekeningen.
    """
    df = transacties_df.copy()
    df["adj_aantal"] = df["aantal"].astype(float)
    df["koers"] = df["koers"].fillna(0).astype(float)

    for isin, groep in df.groupby("isin"):
        groep = groep.sort_values("datum")
        ca_rows = groep[groep.apply(_is_corporate_action_row, axis=1)]
        if ca_rows.empty:
            continue

        product_naam = groep["product"].iloc[0] if "product" in groep.columns else "?"
        dprint(f"\n[split-detect] ISIN={isin} ('{product_naam}') heeft {len(ca_rows)} "
               f"corporate-action rij(en), onderzoeken...")
        dprint(f"[split-detect]   alle rijen voor deze ISIN:")
        for _, r in groep.iterrows():
            dprint(f"    {r['datum']} | beurs={r.get('beurs')} | product={str(r.get('product'))[:50]} "
                   f"| aantal={r.get('aantal')} | koers={r.get('koers')} | totaal_eur={r.get('totaal_eur')}")

        real_trades = groep[~groep.apply(_is_corporate_action_row, axis=1)]
        conversion_rows = real_trades[
            (real_trades["koers"] == 0) & (real_trades["adj_aantal"] > 0)
        ].sort_values("datum")

        if conversion_rows.empty:
            # Bewust een normale print (niet dprint): dit signaleert een
            # STILLE fout in de rendementsberekening (aandelenaantal klopt
            # vanaf hier niet meer) en hoort daarom net zo zichtbaar te zijn
            # als de andere ⚠️-waarschuwingen elders in het project, i.p.v.
            # alleen zichtbaar met debug-logging aan.
            pass
            # print(f"[split-detect]   ⚠️ GEEN conversion-rij gevonden (real trade met koers=0 en "
                  # f"aantal>0) ondanks {len(ca_rows)} corporate-action rij(en) voor ISIN={isin} "
                  # f"('{product_naam}') — deze split wordt NIET verwerkt! Aandelenaantal/rendement "
                  # f"voor '{product_naam}' klopt dan niet vanaf hier.")

        for _, conv in conversion_rows.iterrows():
            conv_date = conv["datum"]
            eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
            shares_before = float(eerdere_trades["adj_aantal"].sum())
            if shares_before <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: shares_before={shares_before} "
                       f"(<=0) — overgeslagen, kan geen ratio berekenen")
                continue

            last_real_date = eerdere_trades["datum"].max()
            new_shares = float(ca_rows.loc[
                (ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date) & (ca_rows["adj_aantal"] > 0),
                "adj_aantal",
            ].sum())
            if new_shares <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: new_shares={new_shares} (<=0) "
                       f"— overgeslagen")
                continue

            ratio = (shares_before + new_shares) / shares_before
            # print(f"[split] {isin} ('{product_naam}'): split gedetecteerd op {conv_date}, "
                  # f"{shares_before:.4f} -> {shares_before + new_shares:.4f} (ratio {ratio:.4f}x)")

            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            dprint(f"[split-detect]   pas ratio {ratio:.4f}x toe op {mask.sum()} eerdere rij(en)")
            df.loc[mask, "adj_aantal"] *= ratio

    return df


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


# Gedeelde retry/backoff-instellingen voor _fetch_yf_info/_haal_slotkoers_op/
# _haal_dagrange_op (via _met_rate_limit_retry hieronder). Losstaand van
# BULK_DOWNLOAD_POGINGEN/_WACHTTIJD hieronder: dat is een functioneel ANDER
# retry-patroon (zie download_met_retry).
RATE_LIMIT_POGINGEN = 3
RATE_LIMIT_WACHTTIJD_BASIS = 8  # seconden; oplopende backoff per poging: 8s, 16s, 24s, ...


def _is_rate_limit_fout(e):
    """Herkent Yahoo's rate-limit-foutmeldingen, ongeacht exacte
    formulering/hoofdlettergebruik. Telt ook een 'Invalid Crumb'-fout of een
    HTTP 401 mee: bij een grotere gelijktijdige belasting (zie de
    28-posities-pooltest bij TICKER_RESOLUTIE_POOL_GROOTTE) faalt Yahoo soms
    hiermee i.p.v. een letterlijke rate-limit-melding, maar de remedie
    (retry met oplopende backoff via _met_rate_limit_retry) is hetzelfde --
    voorheen faalde dit in één keer definitief, zonder retry-poging."""
    tekst = str(e).lower()
    return (
        "rate limit" in tekst
        or "too many requests" in tekst
        or "invalid crumb" in tekst
        or "error 401" in tekst
    )


def _met_rate_limit_retry(actie, log_prefix, beschrijving,
                           pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Voert 'actie' (een callable zonder argumenten die de eigenlijke Yahoo-
    call doet) uit met retry en oplopende backoff bij rate limiting --
    gedeeld door _fetch_yf_info, _haal_slotkoers_op en _haal_dagrange_op
    (dit patroon stond voorheen drie keer bijna-identiek uitgeschreven,
    zie CLAUDE.md).

    'log_prefix' is de []-logprefix (bv. 'yf-info', 'prijscheck'),
    'beschrijving' de tekst die in de retry-logregel na "rate limited voor"
    komt (bv. "'AAPL'" of "dagrange 'AAPL'") -- de aanroeper bepaalt de
    exacte formulering, want die verschilt per aanroeper.

    Geeft (resultaat, None) terug bij succes, of (None, fout) terug bij een
    definitieve mislukking na alle pogingen -- de aanroeper bepaalt zelf de
    juiste "leeg"-teruggave (None, (None, None), ...) en de exacte
    foutmelding, want die verschillen per aanroeper.
    """
    for poging in range(1, pogingen + 1):
        try:
            return actie(), None
        except Exception as e:
            if _is_rate_limit_fout(e) and poging < pogingen:
                wacht = wachttijd * poging
                # print(f"[{log_prefix}] rate limited voor {beschrijving} (poging {poging}/{pogingen}), "
                      # f"{wacht}s wachten...")
                time.sleep(wacht)
                continue
            return None, e
    return None, None


# BULK_DOWNLOAD_*: eigen, kleinere retry-instellingen voor download_met_retry
# hieronder -- functioneel anders dan _met_rate_limit_retry hierboven (zie
# de docstring van download_met_retry voor het verschil), dus bewust NIET
# via dezelfde helper geïmplementeerd.
BULK_DOWNLOAD_POGINGEN = 3
BULK_DOWNLOAD_WACHTTIJD = 5  # seconden; vast (niet oplopend)


def download_met_retry(ticker_of_pair, start_date, pogingen=BULK_DOWNLOAD_POGINGEN, wachttijd=BULK_DOWNLOAD_WACHTTIJD):
    """
    yf.download met automatische retry, bewust NIET via
    _met_rate_limit_retry (het gedeelde rate-limit-specifieke patroon
    hierboven): deze functie retryt op ELKE fout (niet alleen rate
    limiting), met een VASTE wachttijd (geen oplopende backoff), en geeft
    bij een definitieve mislukking een lege Series terug in plaats van
    None -- aanroepers (get_prices()) rekenen al op die lege-Series-vorm.
    Gebruikt voor bulk-downloads van (mogelijk meerdere) tickers tegelijk,
    waar een kortstondige netwerkhapering nog de moeite van een retry
    waard is zonder eerst op een rate-limit-specifieke foutmelding te
    wachten.
    """
    for poging in range(1, pogingen + 1):
        try:
            _tel_yahoo_call("yf.download")
            return yf.download(ticker_of_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
        except Exception as e:
            # print(f"[koersen] poging {poging}/{pogingen} mislukt voor {ticker_of_pair}: {e}")
            if poging < pogingen:
                time.sleep(wachttijd)
            else:
                # print(f"[koersen] definitief mislukt voor {ticker_of_pair}, sla over")
                return pd.Series(dtype=float)


def _converteer_naar_eur(raw, tickers_kolommen, verversen=True):
    """Past USD/GBP/GBp -> EUR-conversie toe op raw[t] voor elke t in
    tickers_kolommen, in-place. FX-reeks komt uit _fx_prijzen_serie()
    (persistent gecached via prijzen/get_prices(), zie daar) i.p.v. bij
    elke aanroep een eigen download te doen -- vóór deze fix werd dezelfde
    FX-koers soms meermaals per upload opnieuw gedownload (zie CLAUDE.md,
    performance-meting upload/analyse-flow).

    `verversen` wordt ongewijzigd doorgegeven aan _fx_prijzen_serie(): dit
    pad hoort het gedrag van zijn aanroeper (get_prices(), voor het
    converteren van actuele aandelenkoersen) te volgen, niet een eigen vaste
    keuze te maken zoals vergelijk_prijs_op_datum() dat wel doet."""
    for t in tickers_kolommen:
        if t not in raw.columns:
            continue
        try:
            _tel_yahoo_call("yf.Ticker.info(currency)")
            currency = yf.Ticker(t).info.get("currency")
        except Exception:
            currency = "EUR"
        if currency in ("USD", "GBP", "GBp"):
            fx = _fx_prijzen_serie(currency, verversen=verversen)
            fx = fx.reindex(raw.index).ffill()
            divisor = 100 if currency == "GBp" else 1
            raw[t] = raw[t] / divisor * fx


def get_prices(tickers, start_date, verversen=True):
    """Haalt koersen (in EUR) op voor een lijst tickers, met caching via de database.

    verversen=False slaat de incrementele "stale"-verversing over (behandelt
    zulke tickers als cache-hit) — gebruikt door bijnaam/code wijzigen, wat
    geen koersdata raakt en dus niets aan Yahoo hoeft te vragen. Tickers die
    nog helemaal niet gecached zijn ('missing') worden altijd gedownload,
    ongeacht deze parameter.
    """
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()

    start_date = pd.Timestamp(start_date)
    vandaag = pd.Timestamp.now().normalize()

    conn = get_db_connection()
    cur = conn.cursor()

    # Vroegste én laatste gecachte datum per ticker (ongefilterd op
    # start_date!) — de vroegste om te kunnen zien of de cache al ver
    # genoeg teruggaat, de laatste om te zien of de cache nog ACTUEEL is.
    # Zonder de eerste check bleef een ticker met een eerdere, onvolledige
    # download (bv. door rate limiting) voor altijd "incompleet" gecachet,
    # met waarde=0 voor alle datums vóór de eerst gecachte datum als gevolg.
    # Zonder de tweede check werd een ticker die eenmaal ver genoeg terugging
    # nooit meer ververst, waardoor nieuwe transacties na de laatst gecachte
    # datum stilzwijgend buiten price_data.index vielen (zie compute_value_
    # over_time/compute_per_ticker, die simpelweg over price_data.index
    # itereren).
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM prijzen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums_cache = {row[0]: (pd.Timestamp(row[1]), pd.Timestamp(row[2])) for row in cur.fetchall()}

    # Wanneer is de rij van "vandaag" (indien aanwezig) voor het laatst
    # ververst — t.b.v. de hergebruik-drempel hieronder, die voorkomt dat
    # meerdere endpoints van één portfolio-opening (home, verrijking,
    # ticker-zekerheid) Yahoo binnen dezelfde paar seconden meermaals voor
    # dezelfde ticker bevragen.
    cur.execute(
        "SELECT ticker, bijgewerkt_op FROM prijzen WHERE ticker = ANY(%s) AND datum = %s",
        (tickers, vandaag.date()),
    )
    laatst_ververst_vandaag = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_date.date()),
    )
    cached = pd.DataFrame(cur.fetchall(), columns=["ticker", "datum", "koers_eur"])
    cur.close()
    conn.close()

    missing = []
    # ticker -> datum vanaf waar incrementeel ververst moet worden
    stale = {}
    for t in tickers:
        if t not in datums_cache:
            missing.append(t)
            dprint(f"[koersen] '{t}' nog niet in cache, wordt gedownload")
            continue
        eerste, laatste = datums_cache[t]
        # kleine marge voor weekenden/feestdagen rond de gevraagde startdatum
        if eerste > start_date + pd.Timedelta(days=5):
            missing.append(t)
            # print(f"[koersen] ⚠️ '{t}' zit in cache maar pas vanaf {eerste.date()}, terwijl "
                  # f"vanaf {start_date.date()} nodig is — cache lijkt incompleet (eerdere "
                  # f"download waarschijnlijk mislukt/afgebroken), wordt opnieuw volledig "
                  # f"gedownload")
            continue
        # Was: alleen verversen als de cache >4 dagen achterloopt. Nu: bij
        # ELKE portfolio-opening verversen (zie CLAUDE.md, "koersen bij
        # élke portfolio-opening verversen") — een rij voor "vandaag" die
        # tijdens handelstijd is opgehaald (tussentijdse, niet-definitieve
        # koers) bleef anders de rest van de dag ongewijzigd staan, ook na
        # sluiting. DREMPEL_HERGEBRUIK_KOERS voorkomt dat de meerdere
        # endpoints van ÉÉN opening (home, verrijking, ticker-zekerheid)
        # Yahoo binnen dezelfde paar seconden meermaals bevragen.
        laatste_fetch_vandaag = laatst_ververst_vandaag.get(t)
        net_ververst = (
            laatste_fetch_vandaag is not None
            and pd.Timestamp.now() - pd.Timestamp(laatste_fetch_vandaag) < DREMPEL_HERGEBRUIK_KOERS
        )
        if not net_ververst:
            stale[t] = laatste
            dprint(f"[koersen] '{t}' wordt ververst vanaf {laatste.date()} "
                   f"(bij elke opening, tenzij <2 min geleden al ververst)")

    cache_hits = len(tickers) - len(missing) - len(stale)
    # print(f"[koersen] cache-samenvatting: {cache_hits} ticker(s) volledig uit cache, "
          # f"{len(missing)} nieuw te downloaden, {len(stale)} incrementeel te verversen "
          # f"(totaal {len(tickers)} gevraagd)")

    if missing:
        with meet_tijd(f"koersen_download_nieuw ({len(missing)} ticker(s))"):
            raw = download_met_retry(missing, start_date)
            if isinstance(raw, pd.Series):
                raw = raw.to_frame(name=missing[0])
            raw = raw.ffill()

            for t in missing:
                if t not in raw.columns:
                    # print(f"[koersen] ⚠️ '{t}' zit niet in yfinance-download resultaat "
                          # f"(mogelijk ongeldige/onbekende ticker)")
                    continue
                eerste_ruw = raw[t].first_valid_index()
                dprint(f"[koersen] '{t}': ruwe (niet-EUR-gecorrigeerde) data vanaf {eerste_ruw}, "
                       f"gevraagd vanaf {start_date}")

            _converteer_naar_eur(raw, missing, verversen=verversen)

            fresh_rows = []
            for t in missing:
                if t not in raw.columns:
                    continue
                for datum, koers in raw[t].dropna().items():
                    fresh_rows.append((t, datum.date(), float(koers)))
            save_prices(fresh_rows)

            fresh_df = pd.DataFrame(fresh_rows, columns=["ticker", "datum", "koers_eur"])
            # fresh_df kan datums bevatten die al in 'cached' zaten (opnieuw
            # gedownload voor tickers die deels al gecachet waren) — bij overlap
            # de verse waarde houden, en concat kan anders duplicate
            # (ticker, datum) combinaties opleveren waar pivot() straks op stukloopt.
            cached = pd.concat([cached, fresh_df], ignore_index=True)
            cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if stale and verversen:
        # Per ticker apart gedownload (i.p.v. één bulk-call zoals bij
        # 'missing') omdat elke stale ticker een eigen 'vanaf'-datum heeft
        # (zijn eigen laatst gecachte datum + 1 dag) — een bulk-download
        # met yfinance ondersteunt geen per-ticker startdatum.
        with meet_tijd(f"koersen_download_incrementeel ({len(stale)} ticker(s))"):
            stale_rows = []
            for t, vanaf in stale.items():
                raw_t = download_met_retry(t, vanaf)
                if isinstance(raw_t, pd.Series):
                    raw_t = raw_t.to_frame(name=t)
                raw_t = raw_t.ffill()
                if t not in raw_t.columns or raw_t[t].dropna().empty:
                    dprint(f"[koersen] '{t}': incrementele ververs-download leverde geen nieuwe "
                           f"koersen op (mogelijk geen nieuwe handelsdagen sinds {vanaf.date()})")
                    continue
                _converteer_naar_eur(raw_t, [t], verversen=verversen)
                for datum, koers in raw_t[t].dropna().items():
                    stale_rows.append((t, datum.date(), float(koers)))

            if stale_rows:
                upsert_prices(stale_rows)
                stale_df = pd.DataFrame(stale_rows, columns=["ticker", "datum", "koers_eur"])
                cached = pd.concat([cached, stale_df], ignore_index=True)
                cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if cached.empty:
        return pd.DataFrame()

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

    for t in tickers:
        if t not in pivot.columns:
            # print(f"[koersen] ❌ GEEN data gevonden voor ticker {t} (helemaal niet in pivot)")
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")
        if eerste_geldige is not None and pd.Timestamp(eerste_geldige) > pd.Timestamp(start_date) + pd.Timedelta(days=10):
            pass
            # print(f"[koersen] ⚠️ {t}: eerste geldige koers ({eerste_geldige}) ligt >10 dagen na "
                  # f"gevraagde startdatum ({start_date}) — 'waarde' voor deze ticker zal 0 zijn vóór "
                  # f"die datum, terwijl 'geïnvesteerd' wel al kan oplopen. Vaak een teken van een "
                  # f"verkeerde/onvolledige ticker.")

    return pivot


# Eén lock per FX-paar (niet één globale lock): ticker-resolutie/
# prijscontrole draait deels parallel via ThreadPoolExecutor, en zonder
# deze locks kunnen meerdere threads TEGELIJK zien dat bv. 'USDEUR=X' nog
# niet gecached is en dus allemaal hun eigen download starten -- precies
# het duplicate-call-probleem dat _fx_prijzen_serie moest oplossen (de
# database-cache alleen is niet genoeg: de race zit tussen het lezen en
# het schrijven, niet in de cache zelf). Met de lock wacht een tweede
# thread voor hetzelfde paar tot de eerste klaar is en pakt daarna gewoon
# de inmiddels gevulde cache. Vooraf aangemaakt (i.p.v. lazy) omdat de set
# FX-paren vast en klein is (zie FX_PAAR_PER_VALUTA).
_fx_serie_locks = {fx_pair: threading.Lock() for fx_pair in set(FX_PAAR_PER_VALUTA.values())}


def _fx_prijzen_serie(valuta, verversen=True):
    """
    Ruwe FX-koersreeks (valuta -> EUR) vanaf FX_ANKER_DATUM, persistent
    gecached via de prijzen-tabel/get_prices() -- een FX-paar zoals
    'USDEUR=X' is voor yfinance gewoon een ticker, dus hergebruikt dit
    dezelfde cache-/download-infrastructuur als aandelenkoersen, i.p.v.
    een eigen parallelle cache te bouwen. Gedeeld door _converteer_naar_eur
    (via get_prices()) en _fx_koers_op_datum (via vergelijk_prijs_op_datum)
    -- vóór deze fix downloadde elke aanroeper z'n eigen FX-koers apart,
    ook binnen dezelfde upload voor exact dezelfde (valuta, datum) (zie
    CLAUDE.md, performance-meting upload/analyse-flow).

    `verversen` wordt doorgegeven aan get_prices(): vergelijk_prijs_op_datum()
    vergelijkt altijd tegen een HISTORISCHE datum en geeft hier bewust
    verversen=False door (een verse FX-koers van vandaag is voor die
    vergelijking nooit relevant), terwijl _converteer_naar_eur() (actuele
    aandelenkoersen omrekenen) het gedrag van zijn eigen aanroeper volgt.

    Geeft een lege Series terug bij een onbekende valuta of ontbrekende
    koersdata (aanroepers behandelen dat hetzelfde als voorheen: "geen
    conversie mogelijk").

    Binnen één Flask-requestcontext wordt het resultaat per fx_pair
    gememoized op `g` -- ticker_waarschuwingen_voor_transacties() roept dit
    per unieke ticker aan, en zonder deze memo herhaalt elke aanroep dezelfde
    DB-query + pivot/ffill voor exact dezelfde (fx_pair, FX_ANKER_DATUM).
    De memo onthoudt ook MET welke verversen-waarde hij gevuld is: een
    eerdere aanroep met verversen=False heeft nooit geprobeerd te
    verversen, dus een latere aanroep binnen hetzelfde request die wél wil
    verversen (verversen=True) mag daar niet blindelings op vertrouwen --
    zonder dit onderscheid zou bv. tijdens /upload de (verversen=False)
    prijscontrole van een net-opgeloste ticker de FX-verversing voor de
    (verversen=True) aandelenkoers-conversie verderop in diezelfde request
    stilzwijgend blokkeren. Andersom (cache al met verversen=True gevuld)
    is een latere verversen=False-aanroep altijd veilig te hergebruiken.
    Geen module-level cache: dat zou tussen requests/workers heen de
    2-minuten-staleness-check van get_prices() omzeilen. Buiten een
    requestcontext (unittests, losse scripts) valt dit terug op het oude
    gedrag -- gewoon elke keer get_prices() aanroepen.
    """
    fx_pair = FX_PAAR_PER_VALUTA.get(valuta)
    if fx_pair is None:
        return pd.Series(dtype=float)

    cache = None
    if has_app_context():
        cache_attr = "_fx_serie_cache"
        if not hasattr(g, cache_attr):
            setattr(g, cache_attr, {})
        cache = getattr(g, cache_attr)
        cached_entry = cache.get(fx_pair)
        if cached_entry is not None:
            cached_reeks, cached_verversen = cached_entry
            if cached_verversen or not verversen:
                return cached_reeks

    with _fx_serie_locks[fx_pair]:
        prijzen = get_prices([fx_pair], FX_ANKER_DATUM, verversen=verversen)
    reeks = prijzen[fx_pair] if not prijzen.empty and fx_pair in prijzen.columns else pd.Series(dtype=float)

    if cache is not None:
        cache[fx_pair] = (reeks, verversen)
    return reeks


def _sorteer_chronologisch(df, datum_kolom="datum", tijd_kolom="tijd"):
    """Sorteert transactierijen chronologisch op datum+tijd samen, niet
    alleen op datum. Nodig voor same-day transacties: de 'transacties'-tabel
    slaat alleen een DATE op, geen tijdstip (zie ALTER TABLE ... ADD COLUMN
    tijd in db.py) — zonder tijd kon een verkoop op dezelfde dag als de
    bijbehorende koop in de verkeerde volgorde verwerkt worden (afhankelijk
    van de willekeurige SELECT-volgorde uit de database, niet van de
    werkelijke uitvoeringstijd). Dit gaf bv. een 'onbekende' verkoopkoers
    op het Statistieken-tabblad wanneer bereken_holdings_en_gesloten() de
    verkoop verwerkte vóórdat de koop van diezelfde dag geregistreerd was.

    Rijen zonder tijd (tijd_kolom ontbreekt, of tijd IS NULL — bv. data van
    vóór de tijd-migratie die nog niet is teruggehaald via een herüpload)
    krijgen bewust 00:00:00 als fallback: dat is geen garantie voor de
    juiste volgorde, maar wel een stabiele, voorspelbare sortering die niet
    slechter is dan de oude datum-only sortering (mergesort is stable, dus
    de relatieve volgorde van rijen zonder tijd blijft ongewijzigd t.o.v.
    hoe ze zijn aangeleverd)."""
    if tijd_kolom not in df.columns:
        return df.sort_values(datum_kolom, kind="mergesort")

    def _naar_timedelta(t):
        if pd.isna(t):
            return pd.Timedelta(0)
        # pd.to_timedelta eist 'hh:mm:ss' -- een string rechtstreeks uit
        # Excel ("13:39") mist vaak de seconden, een datetime.time-object
        # heeft ze via str() altijd al ("13:39:00").
        tekst = str(t)
        if tekst.count(":") == 1:
            tekst += ":00"
        return pd.to_timedelta(tekst)

    tijd_offset = df[tijd_kolom].apply(_naar_timedelta)
    chronologisch = pd.to_datetime(df[datum_kolom]) + tijd_offset
    return (
        df.assign(_chronologisch=chronologisch)
        .sort_values("_chronologisch", kind="mergesort")
        .drop(columns="_chronologisch")
    )


def compute_value_over_time(transacties_df, price_data):
    """Berekent per dag: portfoliowaarde, totaal geïnvesteerd en rendement."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    ontbrekend = [t for t in transacties_df["ticker"].unique() if t not in price_data.columns]
    if ontbrekend:
        pass
        # print(f"[waarde] ⚠️ tickers zonder koersdata, worden genegeerd in totale waarde: {ontbrekend}")

    if not price_data.empty:
        laatste_koersdatum = price_data.index.max()
        na_laatste_koers = transacties_df[pd.to_datetime(transacties_df["datum"]) > laatste_koersdatum]
        if not na_laatste_koers.empty:
            print(f"[waarde] ⚠️ {len(na_laatste_koers)} transactie(s) met datum ná de laatste "
                  f"beschikbare koersdatum ({laatste_koersdatum.date()}) — deze tellen NIET mee "
                  f"in de waarde-tijdreeks (price_data.index loopt niet ver genoeg door). "
                  f"Mogelijk is de koersencache verouderd.")

    holdings = {t: 0.0 for t in tickers}
    invested = 0.0
    rows = []
    trade_i = 0

    # Snelle dict/array-toegang i.p.v. price_data.loc[date, t] per iteratie
    # (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde loop-structuur en
    # -volgorde, alleen de koerslookup is nu een goedkope array-index.
    prijs_per_ticker = {t: price_data[t].to_numpy() for t in tickers}

    for i, date in enumerate(price_data.index):
        while trade_i < len(transacties_df) and pd.Timestamp(transacties_df.loc[trade_i, "datum"]) <= date:
            row = transacties_df.loc[trade_i]
            if row["ticker"] in holdings:
                holdings[row["ticker"]] += float(row["adj_aantal"])
            invested += -float(row["totaal_eur"])
            trade_i += 1

        waarde = sum(
            holdings[t] * prijs_per_ticker[t][i]
            for t in tickers
            if pd.notna(prijs_per_ticker[t][i])
        )
        rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

    result = pd.DataFrame(rows).set_index("datum")
    result["rendement"] = result["waarde"] - result["geinvesteerd"]
    return result


def compute_per_ticker(transacties_df, price_data):
    """Per ticker: waarde en geïnvesteerd bedrag over tijd."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    if not price_data.empty:
        laatste_koersdatum = price_data.index.max()
        na_laatste_koers = transacties_df[pd.to_datetime(transacties_df["datum"]) > laatste_koersdatum]
        if not na_laatste_koers.empty:
            pass
            # print(f"[per-ticker] ⚠️ {len(na_laatste_koers)} transactie(s) met datum ná de laatste "
                  # f"beschikbare koersdatum ({laatste_koersdatum.date()}) — deze tellen NIET mee "
                  # f"in de per-ticker-tijdreeks. Mogelijk is de koersencache verouderd.")

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        aantal_lopend = 0.0       # raw aantal, los van adj_aantal — voor kostenbasis (GAK)
        kostprijs_lopend = 0.0    # kostenbasis van de NU aangehouden stukken
        trade_i = 0
        rows = []
        prev_waarde = None
        prev_invested = None

        # Snelle array-toegang i.p.v. price_data.loc[date, ticker] per
        # iteratie (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde
        # loop-structuur en -volgorde, alleen de koerslookup is nu een
        # goedkope array-index.
        prijzen_array = price_data[ticker].to_numpy()

        for i, date in enumerate(price_data.index):
            activiteit = False
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])

                # Zelfde lopende-gemiddelde-kostprijs-methode (GAK) als
                # bereken_holdings_en_gesloten(): bij een verkoop gaat alleen
                # de kostenbasis van de VERKOCHTE stukken eraf (evenredig aan
                # het gemiddelde op dat moment), niet de volledige
                # verkoopopbrengst. Zo daalt "geïnvesteerd" bij een
                # gedeeltelijke verkoop evenredig mee met het aantal
                # resterende stukken i.p.v. met de volledige cashflow.
                delta_aantal = float(row["aantal"])
                # totaal_eur incl. AutoFX/transactiekosten; alleen gebruikt
                # voor de cashflow-check (corporate-action-rijen hebben
                # totaal_eur=0) en de verkoopkant, die bewust ongewijzigd
                # blijft — zie CLAUDE.md, "GAK gebruikt verkeerde kolom".
                delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
                if delta_aantal > 0:
                    # Kostenbasis o.b.v. de kale Waarde EUR (aantal x koers,
                    # zonder kosten), niet totaal_eur — DEGIRO's eigen GAK
                    # gebruikt ook de kale waarde. Valt terug op totaal_eur
                    # als waarde_eur (nog) niet gevuld is (oudere, nog niet
                    # gebackfillde rijen of een Excel-export zonder de kolom).
                    waarde_bron = row["waarde_eur"] if pd.notna(row.get("waarde_eur")) else row["totaal_eur"]
                    delta_cash_aankoop = -float(waarde_bron)
                    aantal_lopend += delta_aantal
                    kostprijs_lopend += delta_cash_aankoop
                elif delta_aantal < 0:
                    if delta_cash != 0 and aantal_lopend > 0:
                        gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                        verkocht_nu = min(-delta_aantal, aantal_lopend)
                        kostprijs_lopend -= gak_op_dat_moment * verkocht_nu
                    aantal_lopend += delta_aantal

                trade_i += 1
                activiteit = True
            prijs = prijzen_array[i]
            waarde = holdings * prijs if pd.notna(prijs) else 0.0
            invested = max(kostprijs_lopend, 0.0)  # epsilon-afronding kan net onder 0 uitkomen

            # Spike-detector: grote sprong in waarde of geinvesteerd op 1 dag zonder
            # duidelijke oorzaak (helpt ISIN-migraties / verkeerde splits opsporen)
            if prev_waarde is not None and prev_invested not in (None, 0):
                if abs(invested - prev_invested) > 0.5 * abs(prev_invested) + 50:
                    dprint(f"[per-ticker:{ticker}] grote sprong in geïnvesteerd op {date.date()}: "
                           f"{prev_invested:.2f} -> {invested:.2f}")
                if pd.notna(prijs) and prev_waarde > 0 and abs(waarde - prev_waarde) > 0.5 * prev_waarde + 50 \
                        and holdings != 0:
                    dprint(f"[per-ticker:{ticker}] grote sprong in waarde op {date.date()}: "
                           f"{prev_waarde:.2f} -> {waarde:.2f} (holdings={holdings:.4f}, prijs={prijs})")

            rows.append({
                "datum": date, "waarde": waarde, "geinvesteerd": invested,
                "holdings": holdings, "activiteit": activiteit,
            })
            prev_waarde = waarde
            prev_invested = invested

        df_t = pd.DataFrame(rows).set_index("datum")

        # LET OP: "nog in bezit" moet op het AANDELENAANTAL bepaald worden,
        # niet op "geinvesteerd" — dat laatste is een cumulatieve netto
        # cashflow (aankopen min verkopen) die na een volledige verkoop
        # permanent > 0 blijft staan zodra er ooit winst/verlies is gemaakt
        # (het gerealiseerde resultaat), ook al is holdings dan allang 0. Met
        # geinvesteerd als signaal liep de grafiek van een verkochte positie
        # dus onterecht door tot vandaag (bug: "per-aandeel-grafiek loopt
        # door na volledige verkoop"). Epsilon i.p.v. exact 0 i.v.m.
        # float-afrondingen in de cumulatieve holdings-som.
        #
        # De crop-range (nonzero_idx) mag NIET uitsluitend op holdings != 0
        # afgaan: een koop + volledige verkoop binnen dezelfde (dagelijks
        # bemonsterde) datum eindigt ook op holdings == 0 voor die datum,
        # terwijl er wel degelijk een echte transactie was — "activiteit"
        # (er is die datum minstens 1 transactie verwerkt) vangt dat geval
        # mee, ook al is de holdings-verandering per saldo 0.
        nonzero_idx = df_t.index[(df_t["holdings"].abs() > 1e-6) | df_t["activiteit"]]
        is_still_held = abs(df_t["holdings"].iloc[-1]) > 1e-6 if len(df_t) else False
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = all_dates.index(nonzero_idx[0])
            end_pos = all_dates.index(nonzero_idx[-1])

            # 1 dag ervoor erbij, zodat de sprong vanaf 0 zichtbaar is
            start_pos = max(0, start_pos - 1)

            # 1 dag erna erbij, maar alleen als de positie niet meer
            # aangehouden wordt — anders wordt er niets zinnigs toegevoegd,
            # je bezit het nog gewoon.
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)

            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            "waarde": df_t["waarde"].round(2).tolist(),
            "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
            "nog_in_bezit": bool(is_still_held),
        }
    return result


def compute_per_ticker_koers_en_aankopen(transacties_df, price_data):
    """
    Per ticker: de kale koers per aandeel over tijd (niet vermenigvuldigd
    met het aantal, in tegenstelling tot compute_per_ticker()'s 'waarde'),
    het aantal aangehouden aandelen over tijd, en apart de datums van
    aankopen (adj_aantal > 0) en verkopen (adj_aantal < 0) -- in
    tegenstelling tot het oude trading_degiro.py-script dat alle
    transactiedatums door elkaar als 'Aankoop' labelde. T.b.v. het 'Per
    aandeel aankoop'-tabblad.

    Gebruikt dezelfde crop-range-logica als compute_per_ticker() (rond de
    periode dat de positie daadwerkelijk aangehouden werd), zodat beide
    tabbladen consistente start-/einddatums per positie tonen.
    """
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        trade_i = 0
        rows = []

        # Snelle array-toegang i.p.v. price_data.loc[date, ticker] per
        # iteratie (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde
        # loop-structuur en -volgorde, alleen de koerslookup is nu een
        # goedkope array-index.
        prijzen_array = price_data[ticker].to_numpy()

        for i, date in enumerate(price_data.index):
            activiteit = False
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])
                trade_i += 1
                activiteit = True
            prijs = prijzen_array[i]
            rows.append({
                "datum": date,
                "koers": float(prijs) if pd.notna(prijs) else None,
                "holdings": holdings,
                "activiteit": activiteit,
            })

        df_t = pd.DataFrame(rows).set_index("datum")

        # Zelfde crop-logica als compute_per_ticker() (zie die functie voor
        # de uitgebreide toelichting) -- hier bewust NIET herschreven als
        # gedeelde helper, want dat raakt compute_per_ticker() en dat is
        # buiten scope; wel 1-op-1 hetzelfde gedrag.
        nonzero_idx = df_t.index[(df_t["holdings"].abs() > 1e-6) | df_t["activiteit"]]
        is_still_held = abs(df_t["holdings"].iloc[-1]) > 1e-6 if len(df_t) else False
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = max(0, all_dates.index(nonzero_idx[0]) - 1)
            end_pos = all_dates.index(nonzero_idx[-1])
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)
            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        aankopen = trades[trades["adj_aantal"] > 0]
        verkopen = trades[trades["adj_aantal"] < 0]
        if len(df_t) > 0:
            aankoop_datums_dt = pd.to_datetime(aankopen["datum"])
            aankopen = aankopen[(aankoop_datums_dt >= df_t.index[0]) & (aankoop_datums_dt <= df_t.index[-1])]
            verkoop_datums_dt = pd.to_datetime(verkopen["datum"])
            verkopen = verkopen[(verkoop_datums_dt >= df_t.index[0]) & (verkoop_datums_dt <= df_t.index[-1])]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            # LET OP: df_t["koers"] is een pandas-kolom -- als die None-
            # waarden bevat (ontbrekende koers) wordt de kolom float64 en
            # verandert None stilletjes in NaN (numpy-gedrag bij het
            # bouwen van de DataFrame uit rows-dicts). "is not None" mist
            # dat dus altijd; NaN serialiseert vervolgens als het ongeldige
            # JSON-token "NaN" (json.dumps staat dat standaard toe) en
            # breekt fetch()'s response.json() in de browser. pd.notna()
            # herkent zowel None als NaN correct.
            "koers": [round(k, 4) if pd.notna(k) else None for k in df_t["koers"]],
            "holdings": df_t["holdings"].round(6).tolist(),
            "aankoop_datums": sorted(
                d.strftime("%Y-%m-%d")
                for d in pd.to_datetime(aankopen["datum"]).dt.normalize().unique()
            ),
            "verkoop_datums": sorted(
                d.strftime("%Y-%m-%d")
                for d in pd.to_datetime(verkopen["datum"]).dt.normalize().unique()
            ),
        }
    return result


def debug_position(transacties_df, price_data, ticker=None, product_contains=None):
    """
    Handmatige diagnose-helper voor 1 positie. Roep aan met bv.:
        debug_position(transacties_df, price_data, product_contains="S&P 500")
    of
        debug_position(transacties_df, price_data, ticker="VUSA.AS")

    Print: alle ruwe transactierijen, split-adjustment resultaat, en of/vanaf
    wanneer er koersdata is.
    """
    print("\n" + "=" * 70)
    print("DEBUG POSITION")
    print("=" * 70)

    df = transacties_df.copy()
    if product_contains:
        mask = df["product"].astype(str).str.upper().str.contains(product_contains.upper())
        df = df[mask]
    if ticker:
        df = df[df["ticker"] == ticker]

    if df.empty:
        print("Geen transacties gevonden voor dit filter.")
        return

    print(f"\n{len(df)} transactie(s) gevonden. Unieke ISIN's: {df['isin'].unique().tolist()}")
    print(f"Unieke tickers: {df['ticker'].unique().tolist() if 'ticker' in df.columns else '(nog niet toegekend)'}")
    print(f"Unieke product-namen: {df['product'].unique().tolist()}")

    print("\nAlle rijen (gesorteerd op datum):")
    cols_to_show = [c for c in ["datum", "isin", "product", "beurs", "ticker", "aantal",
                                 "adj_aantal", "koers", "totaal_eur"] if c in df.columns]
    for _, r in df.sort_values("datum").iterrows():
        print("  " + " | ".join(f"{c}={r[c]}" for c in cols_to_show))

    if ticker and ticker in price_data.columns:
        serie = price_data[ticker]
        eerste = serie.first_valid_index()
        laatste = serie.last_valid_index()
        n_nan = serie.isna().sum()
        print(f"\nKoersdata voor '{ticker}': eerste geldige waarde op {eerste}, laatste op {laatste}, "
              f"{n_nan} NaN-waarden van de {len(serie)} dagen in price_data.")
    elif ticker:
        print(f"\n⚠️ '{ticker}' zit niet (of nog niet) in price_data.columns: "
              f"{list(price_data.columns)[:20]}...")

    print("=" * 70 + "\n")


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
                # dit is de RUWE, ongegroepeerde verdeling (geen Overig-/
                # Europa-samenvoeging zoals bij "land"/"land_europa" -- die
                # groeperingen slaan een keuze in het totaal-bedrag, niet in
                # de per-bron-uitsplitsing, dus consequent los gehouden).
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


def _koppel_valutaconversie_paren(df):
    """
    Bouwt de lijst van valutaconversie-'paren' uit een rekeningoverzicht:
    een 'Valuta Debitering'-rij en een 'Valuta Creditering'-rij die bij
    elkaar horen.

    De koppeling gaat via een EXACT gelijke (Datum, Tijd) — DEGIRO boekt
    zo'n conversie altijd als twee rijen met identiek tijdstip. Dit is
    bewust NIET gekoppeld via Valutadatum: de 'Dividend'-rij die tot deze
    conversie leidde heeft vaak een Valutadatum van 1 (bank-)dag eerder dan
    de conversie zelf (de conversie wordt pas de volgende werkdag
    afgewikkeld) — matchen op Valutadatum was precies de eerdere bug (zie
    verwerk_rekeningoverzicht).

    Normaliter is de Debitering-rij de vreemde valuta (negatief) en de
    Creditering-rij EUR (positief) — de normale dividend-conversie (vreemd
    -> EUR). Maar soms wisselt DEGIRO de andere kant op: EUR -> vreemd, bv.
    om een buitenlandse dividendbelasting te dekken die niet uit een eerder
    ontvangen vreemde-valuta-dividend betaald kon worden. Dan is juist de
    Debitering-rij EUR. Dit paar bepaalt daarom zelf, per rij, welke van de
    twee EUR is (ongeacht Debitering/Creditering) i.p.v. dat aan te nemen.

    Geeft een lijst van dicts terug: {datum, tijd, valuta (de vreemde
    valuta), vreemd_bedrag (positief), eur_bedrag (het bedrag van de
    EUR-rij MET teken: positief als EUR is bijgeschreven — vreemd->EUR —
    negatief als EUR is afgeschreven — EUR->vreemd), gebruikt (bool, wordt
    True gezet zodra een dividendgroep hem claimt)}.
    """
    fx_rows = df[df["Omschrijving"].isin(["Valuta Debitering", "Valuta Creditering"])]
    paren = []
    for (datum, tijd), groep in fx_rows.groupby(["Datum", "Tijd"]):
        debitering = groep[groep["Omschrijving"] == "Valuta Debitering"]
        creditering = groep[groep["Omschrijving"] == "Valuta Creditering"]
        if debitering.empty or creditering.empty:
            # print(f"[dividend-debug] ⚠️ onvolledig valutaconversie-paar op {datum.date()} {tijd}: "
                  # f"{len(debitering)}x Debitering, {len(creditering)}x Creditering — overgeslagen")
            continue
        deb = debitering.iloc[0]
        cred = creditering.iloc[0]
        if deb["valuta_mutatie"] == "EUR":
            eur_rij, vreemd_rij = deb, cred
        else:
            eur_rij, vreemd_rij = cred, deb
        paar = {
            "datum": datum,
            "tijd": tijd,
            "valuta": vreemd_rij["valuta_mutatie"],
            "vreemd_bedrag": abs(float(vreemd_rij["mutatie"])),
            "eur_bedrag": float(eur_rij["mutatie"]),
            "gebruikt": False,
        }
        paren.append(paar)
        # print(f"[dividend-debug] valutaconversie-paar: {datum.date()} {tijd} — "
              # f"{paar['vreemd_bedrag']:.2f} {paar['valuta']} -> €{paar['eur_bedrag']:.2f}")
    return paren


def _match_valutaconversie(paren, valuta, netto_ruw, datum, tolerantie=0.02):
    """Zoekt in 'paren' (zie _koppel_valutaconversie_paren) het nog niet
    gebruikte paar met dezelfde valuta en (bijna) hetzelfde bedrag als
    'netto_ruw' — dat is het paar dat DEZE dividenduitkering heeft
    omgewisseld naar EUR. Bij meerdere kandidaten (zelfde valuta+bedrag,
    bv. twee identieke dividendbedragen in dezelfde periode) wint de
    kandidaat die qua datum het dichtst bij de dividenddatum ligt. Geeft
    None terug als er geen match binnen tolerantie is."""
    kandidaten = [
        p for p in paren
        if not p["gebruikt"] and p["valuta"] == valuta and abs(p["vreemd_bedrag"] - abs(netto_ruw)) <= tolerantie
    ]
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda p: abs((p["datum"] - datum).days))
    gekozen = kandidaten[0]
    gekozen["gebruikt"] = True
    return gekozen


DIVIDEND_POOL_MAX_DAGEN_VERSCHIL = 3
# Hoeveel dagen twee opeenvolgende (op datum gesorteerde) ongematchte
# dividendgroepen uit elkaar mogen liggen om nog in dezelfde STAP-A-pool
# te vallen (zie verwerk_rekeningoverzicht_df). Bewust ruim genoeg voor
# DeGiro's afwikkeltiming (dividend -> conversie is meestal 1 dag), maar
# begrensd zodat losstaande dividenden van weken uit elkaar niet per
# ongeluk samengevoegd worden.


def _clusters_binnen_venster(items, max_dagen):
    """Groepeert 'items' (dicts met een 'datum'-sleutel) in clusters van
    opeenvolgende (op datum gesorteerde) items, waarbij het verschil
    tussen twee opeenvolgende datums binnen een cluster niet groter is dan
    'max_dagen'. Gebruikt door STAP A hieronder om per valuta alleen
    dividendgroepen te poolen die qua datum dicht genoeg bij elkaar
    liggen."""
    items_gesorteerd = sorted(items, key=lambda x: x["datum"])
    clusters = []
    huidig = []
    for item in items_gesorteerd:
        if huidig and (item["datum"] - huidig[-1]["datum"]).days > max_dagen:
            clusters.append(huidig)
            huidig = []
        huidig.append(item)
    if huidig:
        clusters.append(huidig)
    return clusters


def verwerk_rekeningoverzicht_df(df):
    """
    Doet het eigenlijke werk van verwerk_rekeningoverzicht() op een AL
    ingelezen en hernoemde DataFrame (kolommen: Datum, Tijd, Valutadatum,
    Product, ISIN, Omschrijving, FX, valuta_mutatie, mutatie, valuta_saldo,
    saldo, Order Id — Datum/Valutadatum als datetime, mutatie als float).
    Losgetrokken van het Excel-inlezen zodat dit met een handgemaakte
    DataFrame te unittesten is (zie tests/test_dividend.py), zonder een
    echt .xlsx-bestand te hoeven bouwen.

    Per dividenduitkering (gegroepeerd op Datum+ISIN, want correcties/
    meerdere boekingen voor dezelfde uitkering delen dezelfde Datum):
    - alle 'Dividend'-, 'Dividend Herinvestering'- en 'Dividendbelasting'-
      rijen worden genet (inclusief eventuele negatieve correctierijen) tot
      één bruto- en één belastingbedrag in de eigen valuta. Een 'Dividend
      Herinvestering'-rij heft het bijbehorende 'Dividend'-bedrag geheel of
      gedeeltelijk op (automatisch herbelegd i.p.v. uitgekeerd) — het
      record krijgt een 'herinvesteerd'-vlag zodat de frontend kan tonen
      *waarom* een bedrag klein/nul/negatief is.
    - is die valuta EUR, dan is dat meteen het EUR-bedrag
    - is die valuta NIET EUR, dan wordt EERST geprobeerd deze ÉÉN groep
      1-op-1 te koppelen aan een 'Valuta Debitering'/'Valuta Creditering'-
      paar (via _match_valutaconversie) — NIET een eigen FX-herberekening.
      Lukt dat niet, dan is er een TWEEDE ronde (STAP A hieronder): DeGiro
      boekt soms meerdere dividenden van dezelfde dag/valuta samen in ÉÉN
      conversie (bv. twee ETF-uitkeringen dezelfde dag) — dan wordt zo'n
      conversie nooit door de 1-op-1 match gevonden. Alle nog ongematchte
      groepen per valuta worden daarom geclusterd (zie
      _clusters_binnen_venster, max DIVIDEND_POOL_MAX_DAGEN_VERSCHIL dagen
      uit elkaar) en als cluster (som van hun netto ruwe bedragen) alsnog
      tegen een ongebruikt conversiepaar geprobeerd. Bij een match wordt
      het EUR-bedrag van het paar proportioneel verdeeld over de
      deelnemende groepen naar rato van hun eigen aandeel in de pool-som.
      In beide rondes worden bruto/belasting naar rato van hun eigen aandeel
      in het (groeps- of pool-)netto ruwe bedrag verdeeld over het
      gevonden EUR-bedrag, zodat bruto_eur + belasting_eur altijd optelt
      tot netto_eur.
    - is er ook na STAP A geen conversie gevonden, dan blijven bruto_eur/
      belasting_eur/netto_eur expliciet None ('onbekend') — nooit een gok.
    """
    conversie_paren = _koppel_valutaconversie_paren(df)

    dividend_rows = df[df["Omschrijving"].isin(
        ["Dividend", "Dividend Herinvestering", "Dividendbelasting"]
    )]
    # print(f"[dividend-debug] {len(dividend_rows)} ruwe Dividend/Dividend Herinvestering/"
          # f"Dividendbelasting-rij(en) gevonden")
    for _, r in dividend_rows.iterrows():
        pass
        # print(f"[dividend-debug]   {r['Datum'].date()} | {r['Omschrijving']} | {r.get('Product')} | "
              # f"{r['mutatie']} {r['valuta_mutatie']}")

    herinvesteerd_keys = {
        (datum, isin) for datum, isin in
        df.loc[df["Omschrijving"] == "Dividend Herinvestering", ["Datum", "ISIN"]]
        .itertuples(index=False, name=None)
    }

    tussenresultaten = []
    for (datum, isin), groep in dividend_rows.groupby(["Datum", "ISIN"]):
        bruto_rijen = groep[groep["Omschrijving"].isin(["Dividend", "Dividend Herinvestering"])]
        belasting_rijen = groep[groep["Omschrijving"] == "Dividendbelasting"]

        product = groep["Product"].iloc[0]
        valuta = groep["valuta_mutatie"].dropna().iloc[0] if groep["valuta_mutatie"].notna().any() else "EUR"

        bruto_ruw = float(bruto_rijen["mutatie"].sum()) if not bruto_rijen.empty else 0.0
        belasting_ruw = float(belasting_rijen["mutatie"].sum()) if not belasting_rijen.empty else 0.0
        netto_ruw = bruto_ruw + belasting_ruw
        herinvesteerd = (datum, isin) in herinvesteerd_keys

        # print(f"[dividend-debug] groep {datum.date()} / {isin} ({product}): "
              # f"{len(bruto_rijen)}x Dividend/Dividend Herinvestering + {len(belasting_rijen)}x "
              # f"Dividendbelasting -> netto {netto_ruw:.2f} {valuta} (bruto {bruto_ruw:.2f}, "
              # f"belasting {belasting_ruw:.2f}, herinvesteerd={herinvesteerd})")

        if valuta == "EUR":
            bruto_eur, belasting_eur, netto_eur = bruto_ruw, belasting_ruw, netto_ruw
            # print(f"[dividend-debug]   -> al in EUR, netto_eur=€{netto_eur:.2f}")
        else:
            match = _match_valutaconversie(conversie_paren, valuta, netto_ruw, datum)
            if match is None:
                bruto_eur = belasting_eur = netto_eur = None
                # print(f"[dividend-debug]   ❌ geen 1-op-1 valutaconversie-paar gevonden voor "
                      # f"{netto_ruw:.2f} {valuta} — probeer STAP A (gepoold) hierna")
            else:
                netto_eur = match["eur_bedrag"]
                if netto_ruw != 0:
                    bruto_eur = netto_eur * (bruto_ruw / netto_ruw)
                    belasting_eur = netto_eur * (belasting_ruw / netto_ruw)
                else:
                    bruto_eur = belasting_eur = 0.0
                # print(f"[dividend-debug]   ✓ gekoppeld aan conversie {match['datum'].date()} {match['tijd']} "
                      # f"-> netto_eur=€{netto_eur:.2f}")

        # Ruwe (niet-EUR-geconverteerde) bedragen in de dividend_id, zodat die
        # stabiel blijft ongeacht welk valutaconversie-paar er (opnieuw)
        # aan gekoppeld wordt bij een herhaalde upload.
        dividend_id = "DIV-" + hashlib.md5(
            f"{datum.date()}|{isin}|{bruto_ruw:.6f}|{belasting_ruw:.6f}".encode()
        ).hexdigest()[:16]

        tussenresultaten.append({
            "datum": datum,
            "product": product,
            "isin": isin,
            "valuta": valuta,
            "bruto_ruw": bruto_ruw,
            "belasting_ruw": belasting_ruw,
            "netto_ruw": netto_ruw,
            "bruto_eur": bruto_eur,
            "belasting_eur": belasting_eur,
            "netto_eur": netto_eur,
            "dividend_id": dividend_id,
            "herinvesteerd": herinvesteerd,
        })

    # STAP A — gepoolde valutaconversie (zie docstring hierboven). Alleen
    # groepen die na de 1-op-1 ronde nog geen netto_eur hebben, gegroepeerd
    # per valuta en geclusterd op datumnabijheid.
    onopgelost_per_valuta = {}
    for r in tussenresultaten:
        if r["valuta"] != "EUR" and r["netto_eur"] is None:
            onopgelost_per_valuta.setdefault(r["valuta"], []).append(r)

    for valuta, items in onopgelost_per_valuta.items():
        for cluster in _clusters_binnen_venster(items, DIVIDEND_POOL_MAX_DAGEN_VERSCHIL):
            if len(cluster) < 2:
                # Een cluster van 1 is exact dezelfde poging als de al
                # mislukte 1-op-1 match hierboven — niets te winnen.
                continue
            som_netto_ruw = sum(item["netto_ruw"] for item in cluster)
            referentiedatum = max(item["datum"] for item in cluster)
            match = _match_valutaconversie(conversie_paren, valuta, som_netto_ruw, referentiedatum)
            if match is None:
                # print(f"[dividend-debug]   ❌ STAP A: geen conversie gevonden voor pool van "
                      # f"{len(cluster)} groep(en), som {som_netto_ruw:.2f} {valuta}")
                continue
            for item in cluster:
                aandeel = (item["netto_ruw"] / som_netto_ruw) if som_netto_ruw != 0 else 0.0
                item["netto_eur"] = match["eur_bedrag"] * aandeel
                if item["netto_ruw"] != 0:
                    item["bruto_eur"] = item["netto_eur"] * (item["bruto_ruw"] / item["netto_ruw"])
                    item["belasting_eur"] = item["netto_eur"] * (item["belasting_ruw"] / item["netto_ruw"])
                else:
                    item["bruto_eur"] = item["belasting_eur"] = 0.0
                # print(f"[dividend-debug]   ✓ STAP A: groep {item['datum'].date()} / {item['isin']} "
                      # f"gepoold gematcht -> netto_eur=€{item['netto_eur']:.2f} "
                      # f"(aandeel {aandeel:.1%} van pool €{match['eur_bedrag']:.2f})")

    records = [
        {
            "datum": r["datum"].date(),
            "product": r["product"],
            "isin": r["isin"],
            "valuta": r["valuta"],
            "bruto_eur": r["bruto_eur"],
            "belasting_eur": r["belasting_eur"],
            "netto_eur": r["netto_eur"],
            "dividend_id": r["dividend_id"],
            "herinvesteerd": r["herinvesteerd"],
        }
        for r in tussenresultaten
    ]

    totaal = sum(r["netto_eur"] for r in records if r["netto_eur"] is not None)
    # print(f"[dividend-debug] TOTAAL: {len(records)} dividendgroep(en), "
          # f"€{totaal:.2f} netto (som van de rijen met een bekend netto_eur)")
    return records


def verwerk_rekeningoverzicht(file_object):
    """
    Leest een DeGiro-rekeningoverzicht in en geeft een lijst van
    dividendrecords terug: {datum, product, isin, valuta, bruto_eur,
    belasting_eur, netto_eur, dividend_id}. Het eigenlijke rekenwerk zit in
    verwerk_rekeningoverzicht_df() hierboven; deze functie doet alleen het
    Excel-inlezen en de kolom-normalisatie.

    Kolom-quirk (anders dan bij het transactiebestand): "Mutatie" en
    "Saldo" zijn elk samengevoegde headers over twee kolommen (valutacode +
    bedrag) — pandas geeft de tweede kolom van elk paar de naam
    "Unnamed: 8" / "Unnamed: 10" i.p.v. verkeerd uitgelijnd te zijn, dus die
    hernoemen we hier expliciet naar leesbare namen.
    """
    file_object.seek(0)
    df = pd.read_excel(file_object)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "Mutatie": "valuta_mutatie",
        "Unnamed: 8": "mutatie",
        "Saldo": "valuta_saldo",
        "Unnamed: 10": "saldo",
    })
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["Valutadatum"] = pd.to_datetime(df["Valutadatum"], dayfirst=True)
    df["mutatie"] = pd.to_numeric(df["mutatie"], errors="coerce")

    return verwerk_rekeningoverzicht_df(df)


def bereken_dividend_samenvatting(code):
    """
    Samenvatting van alle opgeslagen dividenden voor deze code: totaal
    netto-ontvangen, per ticker/bijnaam, en een gezamenlijke cumulatieve
    tijdreeks per ticker voor de gestapelde grafiek.

    Geeft None terug als er geen dividenden zijn opgeslagen — de aanroeper
    (app.py) weet dan dat er nooit een rekeningoverzicht is geüpload voor
    deze code, i.p.v. dat te verwarren met "wel geüpload, maar toevallig
    geen dividend ontvangen".
    """
    dividenden = get_dividenden(code)
    if not dividenden:
        return None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT isin, ticker, product FROM transacties WHERE code = %s AND ticker IS NOT NULL",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # ISIN is de sleutel voor de koppeling, niet de naam — zelfde aanpak als
    # elders in dit project (zie find_ticker_detailed/verifieer_ticker_met_prijs).
    # Bij meerdere transactierijen voor dezelfde ISIN wint de eerste
    # (willekeurige volgorde uit de query) — voor dividend-koppeling is dat
    # voldoende precisie, in tegenstelling tot de rendementsberekening is er
    # hier geen aparte behandeling per beursnotering nodig.
    isin_naar_ticker = {}
    isin_naar_bijnaam = {}
    for isin, ticker, product in rows:
        isin_naar_ticker.setdefault(isin, ticker)
        isin_naar_bijnaam.setdefault(isin, product)

    per_ticker_info = {}
    per_ticker_punten = {}
    totaal_netto = 0.0

    for d in dividenden:
        if d["netto_eur"] is None:
            continue
        netto = float(d["netto_eur"])
        totaal_netto += netto

        ticker = isin_naar_ticker.get(d["isin"]) or d["isin"]
        bijnaam = isin_naar_bijnaam.get(d["isin"]) or d["product"] or ticker

        info = per_ticker_info.setdefault(ticker, {"bijnaam": bijnaam, "totaal": 0.0})
        info["totaal"] += netto
        per_ticker_punten.setdefault(ticker, []).append((d["datum"], netto))

    per_ticker = sorted(
        (
            {"ticker": t, "bijnaam": v["bijnaam"], "totaal_netto": round(v["totaal"], 2)}
            for t, v in per_ticker_info.items()
        ),
        key=lambda x: x["totaal_netto"], reverse=True,
    )

    # Losse uitkeringen, ongeaggregeerd, voor de lijst onderaan het
    # Dividend-tabblad — nieuwste eerst. Rijen met netto_eur=None (onbekende
    # valutaconversie) blijven staan i.p.v. weggefilterd te worden, zelfde
    # bewuste "nooit een gok"-gedrag als de rest van deze functie.
    lijst = sorted(
        (
            {
                "datum": d["datum"].strftime("%Y-%m-%d"),
                "ticker": isin_naar_ticker.get(d["isin"]) or d["isin"],
                "bijnaam": isin_naar_bijnaam.get(d["isin"]) or d["product"] or (isin_naar_ticker.get(d["isin"]) or d["isin"]),
                "valuta": d["valuta"],
                "bruto_eur": round(d["bruto_eur"], 2) if d["bruto_eur"] is not None else None,
                "belasting_eur": round(d["belasting_eur"], 2) if d["belasting_eur"] is not None else None,
                "netto_eur": round(d["netto_eur"], 2) if d["netto_eur"] is not None else None,
                "herinvesteerd": d.get("herinvesteerd", False),
            }
            for d in dividenden
        ),
        key=lambda x: x["datum"], reverse=True,
    )

    # Alle tickers uitlijnen op dezelfde datumas (unie van alle dividend-
    # datums) en forward-fillen, zodat de gestapelde grafiek geen gaten heeft.
    alle_datums = sorted({datum for punten in per_ticker_punten.values() for datum, _ in punten})
    cumulatief_per_ticker = {}
    for ticker, punten in per_ticker_punten.items():
        per_datum = {}
        for datum, netto in punten:
            per_datum[datum] = per_datum.get(datum, 0.0) + netto
        cum = 0.0
        reeks = []
        for datum in alle_datums:
            cum += per_datum.get(datum, 0.0)
            reeks.append(round(cum, 2))
        cumulatief_per_ticker[ticker] = reeks

    return {
        "totaal_netto": round(totaal_netto, 2),
        "per_ticker": per_ticker,
        "cumulatief": {
            "datums": [d.strftime("%Y-%m-%d") for d in alle_datums],
            "per_ticker": cumulatief_per_ticker,
        },
        "lijst": lijst,
    }


# ---------------------------------------------------------------------------
# Statistieken-tabblad
#
# De onderstaande "bereken_*"-functies zijn bewust pure functies (getallen in,
# getallen uit, geen DataFrame/DB-toegang) — dat maakt ze met de hand na te
# rekenen en apart te unittesten (zie tests/test_rendement.py) zonder een
# databaseverbinding of live yfinance-data nodig te hebben. bereken_statistieken()
# hieronder is de orkestratie die er transacties_df/price_data/resultaat
# (al berekend in analyze_transacties) voor voedt.
# ---------------------------------------------------------------------------

def bereken_positie_rendement(gak, aantal, huidige_koers):
    """Rendement van 1 positie op basis van GAK (gemiddelde aankoopkoers).
    geinvesteerd = kostenbasis van de nu aangehouden stukken (GAK x aantal),
    NIET het historische netto-ingelegde bedrag (dat kan door eerdere
    verkopen anders zijn) — voor 'wat heb ik betaald voor wat ik nu heb' is
    de kostenbasis van de huidige positie de juiste noemer."""
    geinvesteerd = gak * aantal
    waarde = aantal * huidige_koers
    rendement_pct = ((waarde - geinvesteerd) / geinvesteerd * 100) if geinvesteerd else None
    return {"waarde": waarde, "geinvesteerd": geinvesteerd, "rendement_pct": rendement_pct}


def bereken_totaal_rendement(geinvesteerd, waarde):
    """Rendement% als simpele ratio winst/geïnvesteerd — houdt GEEN rekening
    met WANNEER er is ingelegd (dat is XIRR, zie bereken_xirr)."""
    rendement_eur = waarde - geinvesteerd
    rendement_pct = (rendement_eur / geinvesteerd * 100) if geinvesteerd else None
    return {"rendement_eur": rendement_eur, "rendement_pct": rendement_pct}


def bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde):
    """Winst van 1 kalenderjaar. winst_pct deelt door (startwaarde + ingelegd)
    — het bedrag dat aan het eind van het jaar 'ingezet' is, niet het
    gemiddelde over het hele jaar — zelfde ratio-methode als
    bereken_totaal_rendement, nu toegepast op dit ene jaar i.p.v. de hele
    portefeuille."""
    winst_eur = eindwaarde - startwaarde - ingelegd
    noemer = startwaarde + ingelegd
    winst_pct = (winst_eur / noemer * 100) if noemer else None
    return {"winst_eur": winst_eur, "winst_pct": winst_pct}


def bereken_xirr(cashflows):
    """cashflows: lijst van (datum, bedrag)-tuples vanuit het perspectief van
    de belegger — aankopen negatief, verkopen positief, plus een laatste
    fictieve 'verkoop' van de huidige waarde op vandaag. Geeft de
    geannualiseerde, tijdgewogen rentevoet terug (als fractie, dus 0.10 =
    10%), of None als pyxirr geen oplossing kan vinden (bv. te weinig of
    tegenstrijdige cashflows)."""
    if len(cashflows) < 2:
        return None
    datums = [c[0] for c in cashflows]
    bedragen = [c[1] for c in cashflows]
    try:
        return xirr(datums, bedragen)
    except Exception as e:
        # print(f"[statistieken] XIRR-berekening mislukt: {e}")
        return None


def bereken_twr(transacties_df, resultaat):
    """Time-Weighted Return (TWR): rendementsmaat die, anders dan XIRR, niet
    vertekend wordt door de TIMING van stortingen/onttrekkingen — elke
    sub-periode (tussen twee opeenvolgende datums in `resultaat`) krijgt een
    eigen rendement op basis van de portfoliowaarde, onafhankelijk van
    hoeveel geld er die dag bij kwam. Poort van compute_twr_from_values() uit
    het oude class_degiro.py.

    cf_lookup: per datum de som van externe cashflows (-totaal_eur, positief
    bij een aankoop/storting, net als delta_cash in bereken_holdings_en_
    gesloten) — DEGIRO's corporate-action-boekingsrijen (zie
    _is_corporate_action_row) tellen niet mee, dat is geen geld dat de
    belegger zelf inlegt/onttrekt.

    Sub-periode-rendement r = waarde_eind / (waarde_start + cf) - 1, waarbij
    cf de cashflow van de EIND-datum van de sub-periode is (cash komt binnen
    vóór de koersbeweging van die dag telt, dus telt mee in de noemer).
    Sub-periodes met een noemer van (ongeveer) 0 — bv. vóór de eerste
    aankoop, of een volledige verkoop die de waarde naar 0 brengt — worden
    overgeslagen, geen zinnig rendement te berekenen. De losse sub-periode-
    rendementen worden samen vermenigvuldigd (linking) en aan het eind -1
    gedaan.

    Geeft TWR als fractie terug (0.10 = 10%), of None als er geen enkele
    geldige sub-periode is (zelfde edge case als bereken_xirr)."""
    if resultaat.empty or len(resultaat) < 2:
        return None

    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cf_lookup = {}
    for _, row in df.iterrows():
        cf = -float(row["totaal_eur"])
        if cf == 0:
            continue
        datum = pd.Timestamp(row["datum"]).normalize()
        cf_lookup[datum] = cf_lookup.get(datum, 0.0) + cf

    product = 1.0
    geldige_periode = False
    for i in range(1, len(resultaat)):
        waarde_start = float(resultaat["waarde"].iloc[i - 1])
        waarde_eind = float(resultaat["waarde"].iloc[i])
        cf = cf_lookup.get(pd.Timestamp(resultaat.index[i]).normalize(), 0.0)
        noemer = waarde_start + cf
        if abs(noemer) < 1e-9:
            continue
        product *= waarde_eind / noemer
        geldige_periode = True

    if not geldige_periode:
        return None
    return product - 1


def bereken_holdings_gak(transacties_df):
    """Per ticker: huidige aantal + GAK (gemiddelde aankoopkoers) via de
    lopende-gemiddelde-kostprijs-methode (zelfde methode als DEGIRO zelf
    hanteert).

    ALLE rijen tellen mee voor het aantal — ook DEGIRO's
    corporate-action-boekingsrijen (zie _is_corporate_action_row): die
    overslaan zou bij een split het aandelenaantal dubbel tellen (oude +
    nieuwe stukken blijven dan allebei meetellen). Of een negatieve rij de
    kostenbasis evenredig verlaagt, hangt af van of er een ECHTE cashflow
    bij zit (totaal_eur != 0): bij een verkoop realiseer je een deel van de
    kostenbasis (dat deel gaat eraf), maar bij een split-boekingsrij (aantal
    negatief, totaal_eur=0, geen geld dat van eigenaar wisselt) blijft de
    kostenbasis intact — alleen het aantal daalt tijdelijk, om vervolgens via
    de bijbehorende conversie-rij weer (met meer stukken) aangevuld te
    worden. Zo verdunt een split de GAK per aandeel vanzelf correct, zonder
    de kostenbasis aan te tasten. Posities die volledig verkocht zijn
    (aantal <= 0) komen niet in het resultaat terecht.

    Geeft {ticker: {"aantal": float, "gak": float}} terug. Dunne wrapper om
    bereken_holdings_en_gesloten() — behouden voor bestaande aanroepers/tests
    die alleen de open posities nodig hebben."""
    open_posities, _ = bereken_holdings_en_gesloten(transacties_df)
    return open_posities


def bereken_holdings_en_gesloten(transacties_df):
    """Zelfde lopende-gemiddelde-kostprijs-methode als bereken_holdings_gak()
    hierboven, maar houdt in dezelfde doorloop ook posities bij die
    volledig verkocht zijn (één pas door de data, zodat de boekhoud-logica
    — split-correctie, GAK-methode — niet op twee plekken hoeft te kloppen).

    Geeft (open_posities, gesloten_posities) terug:
    - open_posities: {ticker: {"aantal", "gak"}} — ongewijzigd t.o.v.
      bereken_holdings_gak().
    - gesloten_posities: {ticker: {"aantal", "gemiddelde_aankoopkoers",
      "gemiddelde_verkoopkoers", "gerealiseerd_eur"}} voor tickers die ooit
      een positie hadden (totaal_gekocht_aantal > 0) en nu op (ongeveer)
      nul staan.

    Net als bij de kostenbasis geldt: alleen rijen met een ECHTE cashflow
    (totaal_eur != 0) tellen mee voor de gemiddelde aankoop-/verkoopkoers —
    DEGIRO's split-conversierijen (aantal negatief/positief, totaal_eur=0)
    zijn geen echte koop/verkoop en zouden de gemiddelde prijs vertekenen."""
    open_posities = {}
    gesloten_posities = {}
    df = transacties_df.dropna(subset=["ticker"])
    for ticker, groep in df.groupby("ticker"):
        # Chronologisch (datum+tijd), niet alleen datum: bij een koop en
        # verkoop op dezelfde dag (bv. een beurswissel) kon de verkoop vóór
        # de koop verwerkt worden — de aantal_lopend > 0-check hieronder
        # faalt dan en de verkoopopbrengst wordt stilzwijgend niet meegeteld
        # (zichtbaar als een 'onbekende' verkoopkoers op Statistieken). Zie
        # _sorteer_chronologisch().
        groep = _sorteer_chronologisch(groep)
        aantal_lopend = 0.0
        kostprijs_lopend = 0.0
        totaal_gekocht_aantal = 0.0
        totaal_gekocht_bedrag = 0.0
        totaal_verkocht_aantal = 0.0
        totaal_verkocht_bedrag = 0.0
        totaal_verkochte_kostenbasis = 0.0

        for _, row in groep.iterrows():
            delta_aantal = float(row["aantal"])
            # totaal_eur incl. AutoFX/transactiekosten; alleen gebruikt voor
            # de cashflow-check (corporate-action-rijen hebben totaal_eur=0)
            # en de verkoopkant, die bewust ongewijzigd blijft — zie
            # CLAUDE.md, "GAK gebruikt verkeerde kolom".
            delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
            if delta_aantal > 0:
                # Kostenbasis o.b.v. de kale Waarde EUR, niet totaal_eur —
                # zie compute_per_ticker() hierboven voor dezelfde fix/reden.
                waarde_bron = row["waarde_eur"] if pd.notna(row.get("waarde_eur")) else row["totaal_eur"]
                delta_cash_aankoop = -float(waarde_bron)
                aantal_lopend += delta_aantal
                kostprijs_lopend += delta_cash_aankoop
                if delta_cash != 0:
                    totaal_gekocht_aantal += delta_aantal
                    totaal_gekocht_bedrag += delta_cash_aankoop
            elif delta_aantal < 0:
                if delta_cash != 0 and aantal_lopend > 0:
                    gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                    verkocht_nu = min(-delta_aantal, aantal_lopend)
                    kostenbasis_verkocht_nu = gak_op_dat_moment * verkocht_nu
                    kostprijs_lopend -= kostenbasis_verkocht_nu
                    totaal_verkocht_aantal += verkocht_nu
                    totaal_verkocht_bedrag += -delta_cash  # delta_cash negatief bij verkoop
                    totaal_verkochte_kostenbasis += kostenbasis_verkocht_nu
                aantal_lopend += delta_aantal

        if aantal_lopend > 1e-9:
            positie = {"aantal": aantal_lopend, "gak": kostprijs_lopend / aantal_lopend}
            if totaal_verkocht_aantal > 1e-9:
                # Positie staat nog (deels) open, maar er is onderweg wél
                # verkocht — die gerealiseerde winst/verlies mag niet
                # verloren gaan (zie instructiedocument "gedeeltelijke
                # verkopen"). Zelfde velden als een volledig gesloten
                # positie hieronder, zodat bereken_statistieken() ze
                # uniform kan verwerken.
                positie["deels_verkocht"] = {
                    "aantal": totaal_verkocht_aantal,
                    "gemiddelde_aankoopkoers": (
                        totaal_verkochte_kostenbasis / totaal_verkocht_aantal
                        if totaal_verkocht_aantal > 1e-9 else None
                    ),
                    "gemiddelde_verkoopkoers": totaal_verkocht_bedrag / totaal_verkocht_aantal,
                    "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_verkochte_kostenbasis,
                }
            open_posities[ticker] = positie
        elif totaal_gekocht_aantal > 1e-9:
            gesloten_posities[ticker] = {
                "aantal": totaal_gekocht_aantal,
                "gemiddelde_aankoopkoers": totaal_gekocht_bedrag / totaal_gekocht_aantal,
                "gemiddelde_verkoopkoers": (
                    totaal_verkocht_bedrag / totaal_verkocht_aantal
                    if totaal_verkocht_aantal > 1e-9 else None
                ),
                "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_gekocht_bedrag,
            }

    return open_posities, gesloten_posities


def bereken_jaren_overzicht(resultaat, eerste_datum=None):
    """resultaat: DataFrame zoals compute_value_over_time() teruggeeft
    (index=datum, kolommen 'waarde'/'geinvesteerd'). Geeft per kalenderjaar
    waarin belegd is een overzicht terug (zie bereken_jaar_rendement).

    eerste_datum: de echte eerste transactiedatum (kan iets vóór
    resultaat.index.min() liggen door weekend/feestdag-afronding in
    price_data) — begrenst het eerste jaar zodat dagen_verstreken/
    pct_van_jaar niet ten onrechte een vol jaar tonen. Valt terug op
    resultaat.index.min() als niet meegegeven."""
    if resultaat.empty:
        return []

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    laatste_datum = resultaat.index.max()
    eerste_datum = pd.Timestamp(eerste_datum) if eerste_datum is not None else resultaat.index.min()
    eerste_jaar = eerste_datum.year
    laatste_jaar = laatste_datum.year

    jaren = []
    for jaar in range(eerste_jaar, laatste_jaar + 1):
        jaar_start = pd.Timestamp(year=jaar, month=1, day=1)
        jaar_eind = pd.Timestamp(year=jaar, month=12, day=31)
        dagen_in_jaar = 366 if pd.Timestamp(year=jaar, month=12, day=31).is_leap_year else 365

        periode_start = max(jaar_start, eerste_datum)
        periode_eind = min(jaar_eind, laatste_datum)
        dagen_verstreken = (periode_eind - periode_start).days + 1
        pct_van_jaar = dagen_verstreken / dagen_in_jaar * 100

        startwaarde = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "waarde")
        geinvesteerd_voor = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "geinvesteerd")
        geinvesteerd_na = waarde_op_of_voor(periode_eind, "geinvesteerd")
        ingelegd = geinvesteerd_na - geinvesteerd_voor
        eindwaarde = waarde_op_of_voor(periode_eind, "waarde")

        rendement = bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde)
        jaren.append({
            "jaar": jaar,
            "dagen_verstreken": dagen_verstreken,
            "pct_van_jaar": round(pct_van_jaar, 1),
            "startwaarde": round(startwaarde, 2),
            "ingelegd": round(ingelegd, 2),
            "eindwaarde": round(eindwaarde, 2),
            "winst_eur": round(rendement["winst_eur"], 2),
            "winst_pct": round(rendement["winst_pct"], 2) if rendement["winst_pct"] is not None else None,
        })
    return jaren


def _bouw_xirr_cashflows(transacties_df, resultaat):
    """Bouwt de cashflow-lijst voor bereken_xirr(): elke echte transactie
    (geen corporate-action-boekingsrij, geen €0-splitconversie) plus een
    laatste fictieve cashflow op de laatste bekende datum ter grootte van de
    huidige portfoliowaarde (alsof alles vandaag verkocht wordt — nodig om
    XIRR een eindpunt te geven).

    Nagelopen tegen dezelfde same-day-sorteerbug als
    bereken_holdings_en_gesloten() (zie _sorteer_chronologisch): hier is
    geen fix nodig. XIRR is een NPV-berekening puur op basis van (datum,
    bedrag)-paren — de volgorde van de cashflows-lijst zelf beïnvloedt de
    uitkomst niet (in tegenstelling tot de GAK-boekhouding hierboven, die
    per rij een lopend saldo bijhoudt en dus wél afhankelijk is van de
    verwerkingsvolgorde). Alleen de einddatum sortering (hieronder) is voor
    de leesbaarheid, niet voor de correctheid."""
    if resultaat.empty:
        return []
    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cashflows = [
        (pd.Timestamp(row["datum"]).date(), float(row["totaal_eur"]))
        for _, row in df.iterrows() if float(row["totaal_eur"]) != 0
    ]
    if not cashflows:
        return []
    laatste_datum = resultaat.index.max()
    laatste_waarde = float(resultaat["waarde"].iloc[-1])
    cashflows.append((pd.Timestamp(laatste_datum).date(), laatste_waarde))
    cashflows.sort(key=lambda c: c[0])
    return cashflows


def bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen):
    """Simuleert wat de portfolio waard zou zijn geweest als exact dezelfde
    cashflows (zelfde bedrag, zelfde datum) in een benchmark waren gestoken
    i.p.v. in de echte posities — voor de "Vergelijk met..."-optie op het
    Rendement-tabblad (zie BENCHMARK_TICKERS hierboven).

    `benchmark_koersen`: pd.Series, datum-index -> koers in EUR (al
    opgehaald door de aanroeper via de bestaande get_prices()-cache, zie
    app.py) — deze functie blijft bewust DB/netwerk-vrij, zelfde patroon als
    compute_value_over_time()/compute_per_ticker() die ook al opgehaalde
    price_data als parameter krijgen i.p.v. zelf te fetchen.

    Hergebruikt _bouw_xirr_cashflows() (zelfde cashflows als XIRR): bedrag is
    daar negatief bij een investering (geld uit) en positief bij een
    onttrekking (geld terug) — dus een benchmark-"aankoop" ter grootte van
    het bedrag is `aantal += -bedrag / koers` (bij een onttrekking is bedrag
    positief en -bedrag/koers dus negatief, wat de hypothetische positie
    evenredig verkleint). De laatste entry in die lijst is een FICTIEVE
    'verkoop vandaag'-cashflow (nodig voor XIRR, geen echte transactie) en
    wordt hier expliciet weggelaten.

    Als de benchmark pas later koersdata heeft dan de eerste cashflow (bv.
    AEX/IAEA.AS, pas vanaf 2020-07-29), begint de teruggegeven reeks pas
    vanaf de eerst beschikbare koersdatum -- "onvolledige_dekking"
    signaleert dat aan de aanroeper i.p.v. stilzwijgend een te lage
    hypothetische waarde te tonen (cashflows van vóór die datum tellen dan
    simpelweg niet mee in de simulatie).

    Geeft {"labels", "waarde", "rendement", "vanaf_datum",
    "onvolledige_dekking"} terug ("rendement" = "waarde" - het bijbehorende
    "geinvesteerd" uit `resultaat`, zelfde definitie als de bestaande
    Rendement-lijn), of None als er geen bruikbare cashflows of koersdata
    zijn."""
    if resultaat.empty:
        return None
    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    if len(cashflows) < 2:
        return None
    cashflows = cashflows[:-1]  # laatste = fictieve 'verkoop vandaag', geen echte transactie
    if not cashflows:
        return None

    koersen = benchmark_koersen.dropna()
    if koersen.empty:
        return None

    eerst_beschikbaar = koersen.index.min()
    eerste_cashflow_datum = pd.Timestamp(min(d for d, _ in cashflows))
    labels = [d for d in resultaat.index if d >= eerst_beschikbaar]
    if not labels:
        return None

    cashflows_ts = sorted((pd.Timestamp(d), bedrag) for d, bedrag in cashflows)

    aantal = 0.0
    waarde_per_dag = []
    cf_i = 0
    for datum in labels:
        while cf_i < len(cashflows_ts) and cashflows_ts[cf_i][0] <= datum:
            cf_datum, bedrag = cashflows_ts[cf_i]
            koers_op_cf_datum = koersen.reindex([cf_datum], method="ffill").iloc[0]
            if pd.notna(koers_op_cf_datum) and koers_op_cf_datum > 0:
                aantal += -bedrag / koers_op_cf_datum
            cf_i += 1
        koers_vandaag = koersen.reindex([datum], method="ffill").iloc[0]
        waarde_per_dag.append(aantal * koers_vandaag if pd.notna(koers_vandaag) else None)

    geinvesteerd_per_label = resultaat.loc[labels, "geinvesteerd"]
    rendement_per_dag = [
        (round(w - g, 2) if w is not None else None)
        for w, g in zip(waarde_per_dag, geinvesteerd_per_label)
    ]

    return {
        "labels": [d.strftime("%Y-%m-%d") for d in labels],
        "waarde": [round(w, 2) if w is not None else None for w in waarde_per_dag],
        "rendement": rendement_per_dag,
        "vanaf_datum": labels[0].strftime("%Y-%m-%d"),
        "onvolledige_dekking": eerste_cashflow_datum < eerst_beschikbaar,
    }


def bereken_rendement_over_tijd(transacties_df, resultaat, stap="maand"):
    """Bouwt de drie lijnen voor het "XIRR & rendement"-tabblad: gewoon
    rendement% (bereken_totaal_rendement), XIRR% (bereken_xirr) en TWR%
    (bereken_twr), allemaal op meerdere momenten in de tijd i.p.v. alleen
    het eindcijfer zoals op Statistieken.

    Stapgrootte via `stap`:
      - "maand" (standaard): laatste dag van elke kalendermaand, van de
        eerste tot de laatste datum in `resultaat` — resultaat.index.max()
        is in de praktijk "vandaag" (de laatste beschikbare koersdatum),
        gebruikt i.p.v. een aparte pd.Timestamp.now()-aanroep zodat deze
        functie puur/deterministisch blijft. De laatste (huidige) datum
        wordt altijd als extra stap toegevoegd, ook als die zelf geen
        maand-einde is, zodat de lijn nooit een stuk van de recentste
        periode mist. Licht genoeg om steeds automatisch te herberekenen.
      - "dag": elke datum in `resultaat.index` — preciezer maar
        herberekent bereken_xirr() per dag i.p.v. per maand, wat bij een
        lange historie merkbaar traag kan zijn. Daarom alleen op expliciet
        verzoek van de gebruiker (knop in de UI), niet als standaard.

    Per stapdatum d:
      - waarde/geinvesteerd = de bekende stand op of vóór d (zelfde
        waarde_op_of_voor-patroon als bereken_jaren_overzicht hierboven).
      - rendement_pct via bereken_totaal_rendement — None bij geinvesteerd=0
        (bv. vóór de eerste aankoop), geen verzonnen 0%.
      - xirr_pct: cashflows uit _bouw_xirr_cashflows, MINUS de fictieve
        eind-cashflow van díe functie (die hoort bij de laatste datum in
        resultaat, niet bij d), afgekapt tot en met d, plus een eigen
        fictieve eind-cashflow (waarde op d). bereken_xirr geeft zelf al
        None terug bij te weinig/tegenstrijdige cashflows (bv. de eerste
        maand) — wordt hier gewoon doorgegeven, geen aparte afhandeling
        nodig.
      - twr_pct: bereken_twr() op `resultaat` afgekapt t/m d (resultaat.loc[
        :d]) — TWR is per definitie een gelinkte reeks van sub-periodes
        vanaf het begin, dus herberekent bij elke stap opnieuw vanaf de
        eerste datum (zelfde performance-kanttekening als xirr_pct
        hierboven: prima bij stap="maand", kan bij stap="dag" over een
        lange historie merkbaar trager worden, nog niet geoptimaliseerd).
        Reageert NIET extreem vlak na een storting zoals xirr_pct dat wel
        doet — dat is de reden om 'm ernaast te tonen, geen bug als de
        lijnen dus duidelijk verschillend lopen vlak na een storting.

    Geeft {"labels": [...als YYYY-MM-DD...], "rendement_pct": [...],
    "xirr_pct": [...], "twr_pct": [...]} terug (xirr_pct/twr_pct als
    percentage, dus 10.0 = 10%, niet de fractie 0.10 die bereken_xirr/
    bereken_twr zelf teruggeven). Lege lijsten bij een leeg resultaat."""
    if resultaat.empty:
        return {"labels": [], "rendement_pct": [], "xirr_pct": [], "twr_pct": []}

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    if stap == "dag":
        stap_datums = list(resultaat.index)
    else:
        eerste_datum = resultaat.index.min()
        laatste_datum = resultaat.index.max()
        stap_datums = list(pd.date_range(eerste_datum, laatste_datum, freq="ME"))
        if not stap_datums or stap_datums[-1] < laatste_datum:
            stap_datums.append(laatste_datum)

    alle_cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    # laatste entry is de fictieve 'verkoop op laatste_datum' uit
    # _bouw_xirr_cashflows -- die hoort niet bij tussentijdse stappen, elke
    # stap krijgt hieronder zijn EIGEN fictieve eind-cashflow (op d, niet op
    # laatste_datum).
    echte_cashflows = alle_cashflows[:-1] if alle_cashflows else []

    labels, rendement_pct_lijst, xirr_pct_lijst, twr_pct_lijst = [], [], [], []
    for d in stap_datums:
        waarde = waarde_op_of_voor(d, "waarde")
        geinvesteerd = waarde_op_of_voor(d, "geinvesteerd")

        rendement = bereken_totaal_rendement(geinvesteerd, waarde)

        cashflows_tot_d = [(dat, bedrag) for dat, bedrag in echte_cashflows if pd.Timestamp(dat) <= d]
        xirr = None
        if cashflows_tot_d:
            xirr = bereken_xirr(cashflows_tot_d + [(d.date(), waarde)])

        twr = bereken_twr(transacties_df, resultaat.loc[:d])

        labels.append(d.strftime("%Y-%m-%d"))
        rendement_pct_lijst.append(
            round(rendement["rendement_pct"], 2) if rendement["rendement_pct"] is not None else None
        )
        xirr_pct_lijst.append(round(xirr * 100, 2) if xirr is not None else None)
        twr_pct_lijst.append(round(twr * 100, 2) if twr is not None else None)

    return {
        "labels": labels,
        "rendement_pct": rendement_pct_lijst,
        "xirr_pct": xirr_pct_lijst,
        "twr_pct": twr_pct_lijst,
    }


def bereken_totale_transactiekosten(transacties_df):
    """
    Somt de 'transactiekosten'-kolom op (negatieve waarden in de brondata,
    zie KOSTEN_KOLOM in app.py) tot een positief totaalbedrag. Geeft
    beschikbaar=False terug als de kolom ontbreekt of enkel NaN bevat — bv.
    een ouder DeGiro-exportformaat zonder aparte kostenkolom — zodat de UI
    dan een eerlijke 'data ontbreekt'-melding kan tonen i.p.v. een verzonnen
    €0,00."""
    if "transactiekosten" not in transacties_df.columns:
        return {"totaal": None, "beschikbaar": False}
    kosten = pd.to_numeric(transacties_df["transactiekosten"], errors="coerce").dropna()
    if kosten.empty:
        return {"totaal": None, "beschikbaar": False}
    return {"totaal": round(abs(float(kosten.sum())), 2), "beschikbaar": True}


def bereken_statistieken(transacties_df, price_data, resultaat, dividend_per_ticker=None, ticker_namen=None):
    """
    Bouwt alle data voor het Statistieken-tabblad. Gebruikt uitsluitend data
    die analyze_transacties() (app.py) al berekend heeft (transacties_df ná
    compute_split_adjusted_shares, price_data van get_prices(), resultaat van
    compute_value_over_time()) — geen extra yfinance-calls, dus dit hoeft
    (anders dan Ticker-zekerheid) niet lui/lazy geladen te worden.

    dividend_per_ticker (optioneel): {ticker: totaal_netto} uit
    bereken_dividend_samenvatting() (app.py haalt dit apart op, want dat
    raakt de database aan — deze functie blijft bewust DB-vrij). Leeg/None
    bij een 'niet opslaan'-analyse (geen dividendhistorie mogelijk zonder
    opgeslagen code) — dan krijgt elke positie gewoon 0.0.

    ticker_namen (optioneel): {ticker: naam} voor de "naam"-kolom bij
    gesloten posities (zelfde bron als de rest van analyze_transacties).

    Let op: voor 'huidig aantal per positie' wordt (net als bij de
    Verdeling-taart, zie compute_land_sector_verdeling) de ruwe 'aantal'-
    kolom gebruikt, niet 'adj_aantal' — DEGIRO's splitconversierijen zijn
    al ECHTE transactierijen die het aandelenaantal optellen, adj_aantal is
    alleen nodig om HISTORISCHE (vóór-split) waardepunten te corrigeren.
    """
    dividend_per_ticker = dividend_per_ticker or {}
    ticker_namen = ticker_namen or {}
    laatste_prijzen = price_data.iloc[-1] if not price_data.empty else pd.Series(dtype=float)
    holdings, gesloten_posities = bereken_holdings_en_gesloten(transacties_df)

    posities = []
    for ticker, info in holdings.items():
        if ticker not in price_data.columns or pd.isna(laatste_prijzen.get(ticker)):
            continue
        huidige_koers = float(laatste_prijzen[ticker])
        r = bereken_positie_rendement(info["gak"], info["aantal"], huidige_koers)
        posities.append({
            "ticker": ticker,
            "aantal": round(info["aantal"], 4),
            "gak": round(info["gak"], 4),
            "huidige_koers": round(huidige_koers, 4),
            "huidige_waarde": round(r["waarde"], 2),
            "geinvesteerd": round(r["geinvesteerd"], 2),
            "rendement_eur": round(r["waarde"] - r["geinvesteerd"], 2),
            "rendement_pct": round(r["rendement_pct"], 2) if r["rendement_pct"] is not None else None,
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    posities.sort(key=lambda p: p["huidige_waarde"], reverse=True)

    gesloten_posities_output = []
    for ticker, info in gesloten_posities.items():
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(info["aantal"], 4),
            "resterend_aantal": 0.0,
            "nog_in_bezit": False,
            "gemiddelde_aankoopkoers": round(info["gemiddelde_aankoopkoers"], 4),
            "gemiddelde_verkoopkoers": (
                round(info["gemiddelde_verkoopkoers"], 4)
                if info["gemiddelde_verkoopkoers"] is not None else None
            ),
            # Puur koersrendement, exclusief dividend — dividend staat als
            # apart veld ernaast, bewust niet samengevoegd tot één percentage.
            "rendement_eur": round(info["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(info["gerealiseerd_eur"] / (info["gemiddelde_aankoopkoers"] * info["aantal"]) * 100, 2)
                if info["gemiddelde_aankoopkoers"] else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })

    for ticker, info in holdings.items():
        deels = info.get("deels_verkocht")
        if not deels:
            continue
        kostenbasis_verkocht = (deels["gemiddelde_aankoopkoers"] or 0) * deels["aantal"]
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(deels["aantal"], 4),
            "resterend_aantal": round(info["aantal"], 4),
            "nog_in_bezit": True,
            "gemiddelde_aankoopkoers": (
                round(deels["gemiddelde_aankoopkoers"], 4)
                if deels["gemiddelde_aankoopkoers"] is not None else None
            ),
            "gemiddelde_verkoopkoers": round(deels["gemiddelde_verkoopkoers"], 4),
            "rendement_eur": round(deels["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(deels["gerealiseerd_eur"] / kostenbasis_verkocht * 100, 2)
                if kostenbasis_verkocht else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    gesloten_posities_output.sort(key=lambda p: p["rendement_pct"] or 0, reverse=True)

    totaal_geinvesteerd = float(resultaat["geinvesteerd"].iloc[-1]) if not resultaat.empty else 0.0
    totaal_waarde = float(resultaat["waarde"].iloc[-1]) if not resultaat.empty else 0.0
    totaal = bereken_totaal_rendement(totaal_geinvesteerd, totaal_waarde)

    # All-time high is de hoogste behaalde rendement (waarde - geinvesteerd),
    # niet de hoogste portefeuillewaarde — een hoge waarde vlak na een grote
    # storting hoeft geen hoog rendement te zijn.
    all_time_high = {"waarde": None, "datum": None}
    if not resultaat.empty:
        ath_idx = resultaat["rendement"].idxmax()
        all_time_high = {
            "waarde": round(float(resultaat["rendement"].max()), 2),
            "datum": ath_idx.strftime("%Y-%m-%d"),
        }

    eerste_datum = None
    if not resultaat.empty:
        eerste_datum = transacties_df.dropna(subset=["ticker"])["datum"].min()

    jaren = bereken_jaren_overzicht(resultaat, eerste_datum=eerste_datum)
    geldige_pcts = [j["winst_pct"] for j in jaren if j["winst_pct"] is not None]
    gemiddeld_jaarrendement = round(sum(geldige_pcts) / len(geldige_pcts), 2) if geldige_pcts else None

    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    xirr_fractie = bereken_xirr(cashflows) if cashflows else None
    twr_fractie = bereken_twr(transacties_df, resultaat)

    aantal_jaren = None
    if not resultaat.empty:
        aantal_jaren = round((resultaat.index.max() - pd.Timestamp(eerste_datum)).days / 365.25, 2)

    kosten_info = bereken_totale_transactiekosten(transacties_df)

    return {
        "posities": posities,
        "gesloten_posities": gesloten_posities_output,
        "totalen": {
            "geinvesteerd": round(totaal_geinvesteerd, 2),
            "waarde": round(totaal_waarde, 2),
            "rendement_eur": round(totaal["rendement_eur"], 2),
            "rendement_pct": round(totaal["rendement_pct"], 2) if totaal["rendement_pct"] is not None else None,
            "all_time_high": all_time_high,
            "totale_transactiekosten": kosten_info["totaal"],
            "transactiekosten_beschikbaar": kosten_info["beschikbaar"],
        },
        "jaren": jaren,
        "geavanceerd": {
            "gemiddeld_jaarrendement_pct": gemiddeld_jaarrendement,
            "xirr_pct": round(xirr_fractie * 100, 2) if xirr_fractie is not None else None,
            "twr_pct": round(twr_fractie * 100, 2) if twr_fractie is not None else None,
            "aantal_jaren": aantal_jaren,
        },
    }