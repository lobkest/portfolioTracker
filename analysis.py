import random
import re
import string
import io
import hashlib
import pandas as pd
import requests
import yfinance as yf
from pyxirr import xirr
from yahooquery import search
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import (get_db_connection, save_prices, get_cached_classifications, save_classification,
                 get_cached_land_sector, save_land_sector, get_cached_etf_sector_verdeling,
                 save_etf_sector_verdeling, get_cached_etf_holdings, save_etf_holdings,
                 get_ticker_details, get_cached_prijscheck, save_prijscheck, get_dividenden,
                 get_cached_splits, save_splits)
import time

# Zet op True om overal in dit bestand debug-prints aan te zetten.
DEBUG = True

def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


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

# Drempel voor de standaard, LICHTE prijscontrole (find_ticker_met_snelle_
# prijscheck, i.t.t. de volledige verifieer_ticker_met_prijs hierboven):
# pas boven deze afwijking (ná de stap-2-steekproef) worden ook alternatieve
# tickers doorgerekend -- zie find_ticker_met_snelle_prijscheck().
PRIJSCHECK_DREMPEL_ALTERNATIEVEN = 0.10

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
    "TDG": ["GER", "MUN", "FRA"], "LSE": ["LSE"], "XLON": ["LSE"],
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
    "CNDX.AS": {
        "provider": "ishares",
        "url": "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data/api/v1/get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=ishares-uk&locale=en_GB&portfolioId=253741&userType=individual&component=holdings",
    },
    "GDX.L": {
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
    print(f"[etf-holdings-provider] ⚠️ onbekende Nederlandse landnaam '{land}' — "
          f"NL_LAND_VERTALING aanvullen, blijft voor nu onvertaald staan")
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
        print(f"[etf-holdings-provider] ❌ onbekende provider '{provider}' voor '{etf_ticker}'")
        return None

    try:
        response = requests.get(url, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=30)
        response.raise_for_status()
        holdings = parser(response.content, locale=locale)
    except Exception as e:
        print(f"[etf-holdings-provider] ❌ kon holdings niet ophalen/parsen voor '{etf_ticker}' "
              f"(provider={provider}, url={url}): {e}")
        return None

    if not holdings:
        print(f"[etf-holdings-provider] ⚠️ lege holdings-lijst voor '{etf_ticker}' (provider={provider})")
        return None

    aantal_voor_dedup = len(holdings)
    holdings = _dedupliceer_holdings(holdings)
    if len(holdings) != aantal_voor_dedup:
        print(f"[etf-holdings-provider] '{etf_ticker}': {aantal_voor_dedup - len(holdings)} "
              f"dubbele holding-naam/namen samengevoegd ({aantal_voor_dedup} -> {len(holdings)})")

    totaal_gewicht = sum(h["gewicht"] for h in holdings)
    print(f"[etf-holdings-provider] '{etf_ticker}': {len(holdings)} holdings opgehaald via {provider}, "
          f"totaal gewicht {totaal_gewicht:.1f}%")
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
        print(f"[test-holdings-url] onbekende provider '{provider}', kies uit: {list(_PROVIDER_PARSERS)}")
        return None

    response = requests.get(url, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=30)
    response.raise_for_status()
    holdings = parser(response.content, locale=locale)

    print(f"[test-holdings-url] {len(holdings)} holdings gevonden")
    print(f"[test-holdings-url] som van gewichten: {sum(h['gewicht'] for h in holdings):.2f}%")
    print("[test-holdings-url] eerste 5 rijen:")
    for h in holdings[:5]:
        print("  ", h)
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
            dprint(f"[split-detect]   ⚠️ GEEN conversion-rij gevonden (real trade met koers=0 en "
                   f"aantal>0) ondanks {len(ca_rows)} corporate-action rij(en) — deze split wordt "
                   f"NIET verwerkt! Aandelenaantal/rendement voor '{product_naam}' klopt dan niet "
                   f"vanaf hier.")

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
            print(f"[split] {isin} ('{product_naam}'): split gedetecteerd op {conv_date}, "
                  f"{shares_before:.4f} -> {shares_before + new_shares:.4f} (ratio {ratio:.4f}x)")

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
    if beurs == "DEG":  # corporate-action rij, geen echt aandeel/ETF
        return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}

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

    print(f"[ticker] ❌ GEEN ticker gevonden voor '{product}' (ISIN={isin}, beurs={beurs})")
    return {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}


def find_ticker(product, isin, beurs):
    """Backwards-compatible wrapper rond find_ticker_detailed() die alleen de ticker teruggeeft."""
    return find_ticker_detailed(product, isin, beurs)["ticker"]


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
        "land": None, "sector": None, "top_holding_land": None, "valuta": None,
        "fondsfamilie": None, "category": None, "quote_type": None,
        "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
        "prijs_checks": resultaat.get("prijs_checks", []), "alternatieven": [],
        "basis_alleen": True,
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


def basis_ticker_zekerheid_parallel(posities, max_workers=8):
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


def download_met_retry(ticker_of_pair, start_date, pogingen=3, wachttijd=5):
    """yf.download met automatische retry bij rate limiting."""
    for poging in range(1, pogingen + 1):
        try:
            return yf.download(ticker_of_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
        except Exception as e:
            print(f"[koersen] poging {poging}/{pogingen} mislukt voor {ticker_of_pair}: {e}")
            if poging < pogingen:
                time.sleep(wachttijd)
            else:
                print(f"[koersen] definitief mislukt voor {ticker_of_pair}, sla over")
                return pd.Series(dtype=float)


def _converteer_naar_eur(raw, tickers_kolommen, vanaf):
    """Past USD/GBP/GBp -> EUR-conversie toe op raw[t] voor elke t in
    tickers_kolommen, in-place. 'vanaf' is de startdatum voor de FX-reeks."""
    for t in tickers_kolommen:
        if t not in raw.columns:
            continue
        try:
            currency = yf.Ticker(t).info.get("currency")
        except Exception:
            currency = "EUR"
        if currency in ("USD", "GBP", "GBp"):
            fx_pair = "USDEUR=X" if currency == "USD" else "GBPEUR=X"
            fx = download_met_retry(fx_pair, vanaf).squeeze()
            fx = fx.reindex(raw.index).ffill()
            divisor = 100 if currency == "GBp" else 1
            raw[t] = raw[t] / divisor * fx


def get_prices(tickers, start_date):
    """Haalt koersen (in EUR) op voor een lijst tickers, met caching via de database."""
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
            print(f"[koersen] ⚠️ '{t}' zit in cache maar pas vanaf {eerste.date()}, terwijl "
                  f"vanaf {start_date.date()} nodig is — cache lijkt incompleet (eerdere "
                  f"download waarschijnlijk mislukt/afgebroken), wordt opnieuw volledig "
                  f"gedownload")
            continue
        # marge van een paar dagen voor weekenden/feestdagen rond vandaag
        if laatste < vandaag - pd.Timedelta(days=4):
            stale[t] = laatste + pd.Timedelta(days=1)
            print(f"[koersen] ⚠️ '{t}' cache loopt tot {laatste.date()}, ververst tot vandaag")

    if missing:
        raw = download_met_retry(missing, start_date)
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(name=missing[0])
        raw = raw.ffill()

        for t in missing:
            if t not in raw.columns:
                print(f"[koersen] ⚠️ '{t}' zit niet in yfinance-download resultaat "
                      f"(mogelijk ongeldige/onbekende ticker)")
                continue
            eerste_ruw = raw[t].first_valid_index()
            dprint(f"[koersen] '{t}': ruwe (niet-EUR-gecorrigeerde) data vanaf {eerste_ruw}, "
                   f"gevraagd vanaf {start_date}")

        _converteer_naar_eur(raw, missing, start_date)

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

    if stale:
        # Per ticker apart gedownload (i.p.v. één bulk-call zoals bij
        # 'missing') omdat elke stale ticker een eigen 'vanaf'-datum heeft
        # (zijn eigen laatst gecachte datum + 1 dag) — een bulk-download
        # met yfinance ondersteunt geen per-ticker startdatum.
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
            _converteer_naar_eur(raw_t, [t], vanaf)
            for datum, koers in raw_t[t].dropna().items():
                stale_rows.append((t, datum.date(), float(koers)))

        if stale_rows:
            save_prices(stale_rows)
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
            print(f"[koersen] ❌ GEEN data gevonden voor ticker {t} (helemaal niet in pivot)")
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")
        if eerste_geldige is not None and pd.Timestamp(eerste_geldige) > pd.Timestamp(start_date) + pd.Timedelta(days=10):
            print(f"[koersen] ⚠️ {t}: eerste geldige koers ({eerste_geldige}) ligt >10 dagen na "
                  f"gevraagde startdatum ({start_date}) — 'waarde' voor deze ticker zal 0 zijn vóór "
                  f"die datum, terwijl 'geïnvesteerd' wel al kan oplopen. Vaak een teken van een "
                  f"verkeerde/onvolledige ticker.")

    return pivot


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
        print(f"[waarde] ⚠️ tickers zonder koersdata, worden genegeerd in totale waarde: {ontbrekend}")

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

    for date in price_data.index:
        while trade_i < len(transacties_df) and pd.Timestamp(transacties_df.loc[trade_i, "datum"]) <= date:
            row = transacties_df.loc[trade_i]
            if row["ticker"] in holdings:
                holdings[row["ticker"]] += float(row["adj_aantal"])
            invested += -float(row["totaal_eur"])
            trade_i += 1

        waarde = sum(
            holdings[t] * price_data.loc[date, t]
            for t in tickers
            if pd.notna(price_data.loc[date, t])
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
            print(f"[per-ticker] ⚠️ {len(na_laatste_koers)} transactie(s) met datum ná de laatste "
                  f"beschikbare koersdatum ({laatste_koersdatum.date()}) — deze tellen NIET mee "
                  f"in de per-ticker-tijdreeks. Mogelijk is de koersencache verouderd.")

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        invested = 0.0
        trade_i = 0
        rows = []
        prev_waarde = None
        prev_invested = None

        for date in price_data.index:
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])
                invested += -float(row["totaal_eur"])
                trade_i += 1
            prijs = price_data.loc[date, ticker]
            waarde = holdings * prijs if pd.notna(prijs) else 0.0

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

            rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})
            prev_waarde = waarde
            prev_invested = invested

        df_t = pd.DataFrame(rows).set_index("datum")

        nonzero_idx = df_t.index[df_t["geinvesteerd"] > 0]
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = all_dates.index(nonzero_idx[0])
            end_pos = all_dates.index(nonzero_idx[-1])

            # 1 dag ervoor erbij, zodat de sprong vanaf 0 zichtbaar is
            start_pos = max(0, start_pos - 1)

            # 1 dag erna erbij, maar alleen als de laatste investeringsdag niet
            # de laatste (= meest recente/vandaag) datum in de dataset is —
            # anders wordt er niets zinnigs toegevoegd, je bezit het nog gewoon.
            is_still_held = nonzero_idx[-1] == all_dates[-1]
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)

            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            "waarde": df_t["waarde"].round(2).tolist(),
            "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
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


def _fetch_yf_info(ticker, pogingen=3, wachttijd=8):
    """
    Haalt yf.Ticker(ticker).info op met retry/backoff bij rate limiting.
    Gedeeld door classify_ticker() en get_land_sector() zodat beide niet
    onafhankelijk van elkaar dezelfde Yahoo-call voor dezelfde ticker doen
    (rate limiting is een bekend pijnpunt in dit project). Geeft None terug
    bij een definitieve fout (rate limit na alle retries).
    """
    for poging in range(1, pogingen + 1):
        try:
            return yf.Ticker(ticker).info
        except Exception as e:
            is_rate_limit = "rate limit" in str(e).lower() or "too many requests" in str(e).lower()
            if is_rate_limit and poging < pogingen:
                wacht = wachttijd * poging  # oplopende backoff: 8s, 16s, 24s...
                print(f"[yf-info] rate limited voor '{ticker}' (poging {poging}/{pogingen}), "
                      f"{wacht}s wachten...")
                time.sleep(wacht)
                continue
            print(f"[yf-info] ❌ kon info niet ophalen voor '{ticker}': {e}")
            return None


def _classify_ticker_uncached(ticker, pogingen=3, wachttijd=8):
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
        print(f"[classify] ⚠️ quoteType onbekend voor '{ticker}', gok ETF={is_etf} "
              f"(country={country}, sector={sector}, totalAssets={total_assets}, "
              f"fundFamily={fund_family}, category={category})")

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
        print(f"[land-sector] ⚠️ '{ticker}': kon niet opgehaald worden, Unknown voor nu")
        return ("Unknown", "Unknown")

    land = info.get("country")
    sector = info.get("sector")
    print(f"[land-sector] '{ticker}': opgehaald -> land={land}, sector={sector}")
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
        weightings = yf.Ticker(ticker).funds_data.sector_weightings
    except Exception as e:
        print(f"[etf-sector] ❌ kon sectorverdeling niet ophalen voor '{ticker}': {e}")
        return {}

    if not weightings:
        print(f"[etf-sector] ⚠️ lege sectorverdeling voor '{ticker}', niet gecached")
        return {}

    sector_dict = {_sector_naam(sector_key): float(gewicht) for sector_key, gewicht in weightings.items()}
    print(f"[etf-sector] '{ticker}': opgehaald -> {sector_dict}")
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
        print(f"[etf-holdings] '{ticker}': provider-holdings ophalen mislukt, terugvallen op yfinance-top-10")

    try:
        top_holdings = yf.Ticker(ticker).funds_data.top_holdings
    except Exception as e:
        print(f"[etf-holdings] ❌ kon top-holdings niet ophalen voor '{ticker}': {e}")
        return cached or []

    if top_holdings is None or top_holdings.empty:
        print(f"[etf-holdings] ⚠️ geen top-holdings gevonden voor '{ticker}', niet gecached")
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

    print(f"[etf-holdings] '{ticker}': opgehaald -> {len(holdings)} holdings (yfinance_top10)")
    save_etf_holdings(ticker, holdings)
    return holdings


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


def compute_land_sector_verdeling(transacties_df, price_data):
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
        }
    """
    def optellen(dct, key, bedrag):
        key = key or "Unknown"
        dct[key] = dct.get(key, 0.0) + bedrag

    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    tickers = [t for t in huidige_holdings.index if t in price_data.columns]
    is_etf_map = classify_tickers(tickers)

    land = {}
    sector = {}
    per_etf = {}

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
            for naam, gewicht in etf_land_pct.items():
                optellen(land, naam, waarde * gewicht)

            per_etf[ticker] = {"land": etf_land_pct, "sector": etf_sector_pct, "land_bron": land_bron}
        else:
            aandeel_land, aandeel_sector = get_land_sector(ticker)
            optellen(land, aandeel_land, waarde)
            optellen(sector, aandeel_sector, waarde)

    land_europa_gegroepeerd = _groepeer_europa_samen(land)
    return {
        "land": _voeg_kleine_landen_samen(land),
        "land_europa": _voeg_kleine_landen_samen(
            land_europa_gegroepeerd,
            uitgezonderd={"Europe"} if "Europe" in land_europa_gegroepeerd else frozenset(),
        ),
        "sector": sector,
        "per_etf": per_etf,
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


def _haal_slotkoers_op(ticker, datum, dagen_buffer=7, pogingen=3, wachttijd=8):
    """
    Haalt de slotkoers van 'ticker' op de eerste geldige handelsdag op of ná
    'datum' op (buffer voor weekend/feestdagen waarop de markt dicht was),
    in de eigen valuta van de ticker — GEEN EUR-conversie, dit is puur een
    identiteitscheck (klopt de prijs), geen waardeberekening. Retry/backoff
    bij rate limiting, zelfde patroon als _fetch_yf_info. Geeft None terug
    als het na alle retries niet lukt of er geen koersdata is.
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)
    for poging in range(1, pogingen + 1):
        try:
            raw = yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)["Close"]
            break
        except Exception as e:
            is_rate_limit = "rate limit" in str(e).lower() or "too many requests" in str(e).lower()
            if is_rate_limit and poging < pogingen:
                wacht = wachttijd * poging
                print(f"[prijscheck] rate limited voor '{ticker}' (poging {poging}/{pogingen}), "
                      f"{wacht}s wachten...")
                time.sleep(wacht)
                continue
            print(f"[prijscheck] ❌ kon historische koers niet ophalen voor '{ticker}' rond {datum}: {e}")
            return None

    if isinstance(raw, pd.DataFrame):
        # yf.download geeft bij 1 ticker soms toch een DataFrame terug i.p.v. een Series
        raw = raw[ticker] if ticker in raw.columns else raw.iloc[:, 0]

    geldig = raw.dropna()
    if geldig.empty:
        print(f"[prijscheck] ⚠️ geen koersdata gevonden voor '{ticker}' rond {datum}")
        return None

    return float(geldig.iloc[0])


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
        splits = yf.Ticker(ticker).splits
    except Exception as e:
        print(f"[splits] kon split-geschiedenis niet ophalen voor '{ticker}': {e}")
        return {}
    resultaat = {pd.Timestamp(datum).date().isoformat(): float(ratio) for datum, ratio in splits.items()}
    print(f"[splits] '{ticker}': opgehaald -> {len(resultaat)} split(s)")
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


def vergelijk_prijs_op_datum(ticker, datum, bekende_koers):
    """
    Vergelijkt de DEGIRO-transactieprijs (bekende_koers) met de historische
    Yahoo-slotkoers van 'ticker' op diezelfde datum (gecorrigeerd voor
    eventuele splits sindsdien, zie _cumulatieve_split_factor). Een grote
    afwijking is een sterker signaal dat de ticker fout is dan beurs-
    string-matching alleen — een verkeerde ticker op de "juiste" beurs
    geeft alsnog een compleet andere koers.

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
    """
    datum = pd.Timestamp(datum).date()
    cached = get_cached_prijscheck(ticker, datum)
    if cached is not None:
        yahoo_koers, _valuta = cached
        dprint(f"[prijscheck] '{ticker}' op {datum}: uit cache -> yahoo_koers={yahoo_koers}")
    else:
        yahoo_koers = _haal_slotkoers_op(ticker, datum)
        valuta = _ticker_details_met_cache(ticker).get("valuta")
        print(f"[prijscheck] '{ticker}' op {datum}: opgehaald -> yahoo_koers={yahoo_koers} ({valuta})")
        save_prijscheck(ticker, datum, yahoo_koers, valuta)

    if yahoo_koers is None or not bekende_koers:
        return {
            "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
            "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
        }

    split_factor = _cumulatieve_split_factor(ticker, datum)
    yahoo_koers_gecorrigeerd = yahoo_koers * split_factor
    if split_factor != 1.0:
        print(f"[prijscheck] '{ticker}' op {datum}: split-correctie toegepast (factor {split_factor:.4f}) "
              f"-> yahoo_koers {yahoo_koers} wordt {yahoo_koers_gecorrigeerd} voor de vergelijking")

    afwijking_pct = abs(yahoo_koers_gecorrigeerd - bekende_koers) / bekende_koers * 100
    afwijking_fractie = afwijking_pct / 100
    if afwijking_fractie < PRIJSCHECK_DREMPEL_OK:
        niveau = "ok"
    elif afwijking_fractie < PRIJSCHECK_DREMPEL_WAARSCHUWING:
        niveau = "mild"
    else:
        niveau = "waarschuwing"

    return {
        "yahoo_koers": yahoo_koers,
        "yahoo_koers_gecorrigeerd": yahoo_koers_gecorrigeerd if split_factor != 1.0 else None,
        "split_factor": split_factor,
        "bekende_koers": bekende_koers,
        "afwijking_pct": afwijking_pct,
        "niveau": niveau,
        "match": niveau != "waarschuwing",
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


def _zoek_betere_alternatieven(alternatieven_kandidaten, steekproef, verwachte_beurzen):
    """
    Rekent kandidaat-tickers (vorm {'symbol','exchange'}, zoals
    find_ticker_detailed()'s 'alternatieven') één voor één door tegen de
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
        alt_land, alt_sector, _alt_top_holding_land = _land_sector_voor_weergave(alt_ticker)
        alt_beurs_klopt = (alt.get("exchange") in verwachte_beurzen) if verwachte_beurzen else None

        alternatieven.append({
            "ticker": alt_ticker,
            "beurs": alt.get("exchange"),
            "land": alt_land,
            "sector": alt_sector,
            "valuta": alt_details.get("valuta"),
            "gemiddelde_afwijking_pct": (sum(afwijkingen) / len(afwijkingen)) if afwijkingen else None,
            "aantal_matches": sum(1 for m in alt_matches if m),
        })

        if aanbevolen_alternatief is None and alt_matches and all(alt_matches):
            aanbevolen_alternatief = alt_ticker

        if alt_beurs_klopt and alt_matches and all(alt_matches):
            # Overtuigende match (juiste beurs + kloppende prijs op alle
            # gecheckte datums) — de overige kandidaten checken kan het
            # resultaat niet meer verbeteren, alleen nog meer Yahoo-calls
            # kosten.
            break

    return alternatieven, aanbevolen_alternatief


def verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin):
    """
    Zoekt de ticker zoals find_ticker_detailed(), maar herbeoordeelt de
    zekerheid met een sterker signaal: de daadwerkelijke DEGIRO-transactie-
    prijs vergeleken met de historische Yahoo-slotkoers op dezelfde datum
    (voor de gekozen ticker én voor elke alternatieve kandidaat). Geeft
    alles terug wat nodig is om de match op de Ticker-zekerheid-pagina te
    beoordelen (land/sector/valuta/... voor gekozen ticker + alternatieven),
    zodat de frontend niets zelf hoeft na te vragen.
    """
    basis = find_ticker_detailed(product, isin, beurs)
    ticker = basis["ticker"]
    zekerheid = basis["zekerheid"]

    if ticker is None:
        return {
            "ticker": None, "zekerheid": zekerheid, "waarschuwing": None,
            "land": None, "sector": None, "top_holding_land": None, "valuta": None,
            "fondsfamilie": None, "category": None, "quote_type": None,
            "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
            "prijs_checks": [], "alternatieven": [],
        }

    steekproef = _kies_steekproef_transacties(transacties_van_dit_isin)

    prijs_checks = []
    for t in steekproef:
        check = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        check["datum"] = str(t["datum"])
        prijs_checks.append(check)

    bekende_matches = [c["match"] for c in prijs_checks if c["match"] is not None]
    prijs_bekend = len(bekende_matches) > 0
    prijs_klopt = prijs_bekend and all(bekende_matches)

    waarschuwing = None
    if zekerheid == "zeker" and prijs_bekend and not prijs_klopt:
        zekerheid = "onzeker"
        afwijkende = [c for c in prijs_checks if c["match"] is False]
        grootste = max(afwijkende, key=lambda c: c["afwijking_pct"])
        waarschuwing = (
            f"Beurs komt overeen, maar {len(afwijkende)} van de {len(bekende_matches)} gecontroleerde "
            f"datums wijkt meer dan {PRIJSCHECK_DREMPEL_WAARSCHUWING * 100:.0f}% af (grootste afwijking "
            f"{grootste['afwijking_pct']:.1f}% op {grootste['datum']}) — mogelijk toch de verkeerde ticker."
        )
        print(f"[prijscheck] ⚠️ '{ticker}' ({isin}): {waarschuwing}")

    details = _ticker_details_met_cache(ticker)
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
        alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
            basis["alternatieven"], steekproef, verwachte_beurzen
        )

    result = {
        "ticker": ticker,
        "zekerheid": zekerheid,
        "waarschuwing": waarschuwing,
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
    return result


def find_ticker_met_snelle_prijscheck(product, isin, beurs, transacties_van_dit_isin):
    """
    Lichte, STANDAARD prijscontrole — draait bij ELKE upload (opslaand én
    'niet opslaan'), in tegenstelling tot verifieer_ticker_met_prijs()
    hierboven, die bewust duur is en alleen lui/on-demand draait op de
    Ticker-zekerheid-pagina. Moet daarom in het gangbare geval (geen
    afwijking) maar 1 extra, via ticker_prijscheck gecachete Yahoo-call
    kosten per unieke (ISIN, Beurs) — vergelijkbaar met de kosten die er al
    waren vóór de 'niet opslaan'-timeoutfix (CLAUDE.md, Statistieken-
    incident 2026-08-31).

    Escalatietrapje, alleen bij een daadwerkelijke afwijking:
      1. Alleen de LAATSTE transactiedatum controleren.
      2. > PRIJSCHECK_DREMPEL_WAARSCHUWING (6%) afwijking -> ook de rest van
         de steekproef (eerste/middelste/laatste) controleren — een
         eenmalige, onschuldige uitschieter (bv. een corporate action rond
         die datum) mag niet meteen als een foute ticker gelden.
      3. Nog steeds > PRIJSCHECK_DREMPEL_ALTERNATIEVEN (10%) afwijking (over
         de bredere steekproef) -> ook alternatieve tickers doorrekenen,
         via dezelfde _zoek_betere_alternatieven() als de volledige check —
         maar dan alleen voor DEZE positie, niet voor de hele portfolio.

    Geeft basis (ticker/zekerheid/alternatieven van find_ticker_detailed())
    terug, aangevuld met 'prijs_checks' (lijst, 1-3 checks naargelang de
    escalatie) en 'prijswaarschuwing' (None als er niets aan de hand is).
    Bij escalatie naar stap 3 met een geslaagd alternatief staat er ook een
    'aanbevolen_alternatief' in — puur informatief, er wordt nooit
    automatisch een andere ticker gekozen.
    """
    basis = find_ticker_detailed(product, isin, beurs)
    ticker = basis["ticker"]

    # Corporate-action-/splitrijen (koers 0 of leeg) horen niet in de
    # prijscontrole thuis — zelfde filter als _kies_steekproef_transacties.
    geldige_transacties = [
        t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0
    ]
    if ticker is None or not geldige_transacties:
        return {**basis, "prijs_checks": [], "prijswaarschuwing": None}

    laatste = max(geldige_transacties, key=lambda t: t["datum"])
    check_laatste = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    check_laatste["datum"] = str(laatste["datum"])
    prijs_checks = [check_laatste]

    if check_laatste["afwijking_pct"] is None or check_laatste["afwijking_pct"] <= PRIJSCHECK_DREMPEL_WAARSCHUWING * 100:
        # Geen Yahoo-data om te vergelijken, of de koers klopt -- het
        # gangbare geval, klaar na 1 (gecachete) call.
        return {**basis, "prijs_checks": prijs_checks, "prijswaarschuwing": None}

    # Stap 2: afwijking >6% op de laatste datum -- ook de rest van de
    # steekproef controleren.
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
    if grootste_afwijking is None:
        return {**basis, "prijs_checks": prijs_checks, "prijswaarschuwing": None}

    zekerheid = "onzeker" if basis["zekerheid"] == "zeker" else basis["zekerheid"]
    prijswaarschuwing = (
        f"Koers van {ticker} wijkt {grootste_afwijking:.1f}% af van Yahoo — "
        f"controleer op het Ticker-zekerheid-tabblad."
    )
    print(f"[snelle-prijscheck] ⚠️ '{ticker}' ({isin}): {prijswaarschuwing}")

    resultaat = {
        **basis, "zekerheid": zekerheid, "prijs_checks": prijs_checks,
        "prijswaarschuwing": prijswaarschuwing,
    }

    # Stap 3: nog steeds fors afwijkend (>10%) na de bredere steekproef --
    # nu pas de duurdere kandidaten-doorrekening, en alleen voor DEZE
    # positie (niet voor de hele portfolio).
    if grootste_afwijking > PRIJSCHECK_DREMPEL_ALTERNATIEVEN * 100:
        verwachte_beurzen = BEURS_MAP.get(beurs, [])
        _alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
            basis["alternatieven"], steekproef, verwachte_beurzen
        )
        if aanbevolen_alternatief:
            resultaat["aanbevolen_alternatief"] = aanbevolen_alternatief

    return resultaat


def prijswaarschuwing_voor_ticker(ticker, transacties_van_dit_isin):
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
    """
    geldige_transacties = [
        t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0
    ]
    if not ticker or not geldige_transacties:
        return None

    laatste = max(geldige_transacties, key=lambda t: t["datum"])
    check = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    if check["afwijking_pct"] is None or check["afwijking_pct"] <= PRIJSCHECK_DREMPEL_WAARSCHUWING * 100:
        return None

    return (
        f"Koers van {ticker} wijkt {check['afwijking_pct']:.1f}% af van Yahoo — "
        f"controleer op het Ticker-zekerheid-tabblad."
    )


def ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen):
    """
    Verzamelt prijswaarschuwing_voor_ticker()-meldingen voor elke unieke
    ticker in transacties_df — gebruikt door analyze_transacties() in app.py
    bij ELK bezoek aan een portfolio (niet alleen direct na de upload), zie
    prijswaarschuwing_voor_ticker() hierboven voor waarom dat in het
    gangbare geval geen nieuwe Yahoo-calls kost.

    transacties_df: moet minstens de kolommen 'ticker', 'datum', 'koers'
    bevatten. ticker_namen: {ticker: weergavenaam}, voor de UI. Geeft een
    lijst van {"ticker", "naam", "boodschap"} terug (leeg als niets afwijkt).
    """
    waarschuwingen = []
    for ticker, groep in transacties_df.dropna(subset=["ticker"]).groupby("ticker"):
        transacties_van_ticker = [{"datum": d, "koers": k} for d, k in zip(groep["datum"], groep["koers"])]
        boodschap = prijswaarschuwing_voor_ticker(ticker, transacties_van_ticker)
        if boodschap:
            waarschuwingen.append({
                "ticker": ticker, "naam": ticker_namen.get(ticker, ticker), "boodschap": boodschap,
            })
    return waarschuwingen


def vind_tickers_met_snelle_prijscheck_parallel(posities, max_workers=8):
    """
    Voert find_ticker_met_snelle_prijscheck() voor meerdere posities
    tegelijk uit (ThreadPoolExecutor), zelfde patroon als
    verifieer_tickers_met_prijs_parallel() hieronder. Een hoger standaard
    max_workers dan die functie: het gangbare geval hier is maar 1 Yahoo-
    call per positie (i.p.v. tot wel 1 (eigen) + N (kandidaten) x 3
    (steekproef) bij de volledige check), dus meer gelijktijdige workers
    kosten geen extra risico op rate-limiting per positie.

    posities: lijst van (product, isin, beurs, transacties_van_dit_isin).
    Geeft een lijst van resultaat-dicts terug, in dezelfde volgorde als
    'posities' (dus niet per se de volgorde waarin ze klaar zijn).
    """
    resultaten = [None] * len(posities)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_naar_index = {
            executor.submit(find_ticker_met_snelle_prijscheck, product, isin, beurs, transacties): i
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
    'posities' (dus niet per se de volgorde waarin ze klaar zijn).
    """
    resultaten = [None] * len(posities)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_naar_index = {
            executor.submit(verifieer_ticker_met_prijs, naam, isin, beurs, transacties): i
            for i, (naam, isin, beurs, transacties) in enumerate(posities)
        }
        for future in as_completed(future_naar_index):
            resultaten[future_naar_index[future]] = future.result()
    return resultaten


def _koppel_valutaconversie_paren(df):
    """
    Bouwt de lijst van valutaconversie-'paren' uit een rekeningoverzicht:
    een 'Valuta Debitering'-rij (vreemde valuta, negatief bedrag) en een
    'Valuta Creditering'-rij (EUR, positief bedrag) die bij elkaar horen.

    De koppeling gaat via een EXACT gelijke (Datum, Tijd) — DEGIRO boekt
    zo'n conversie altijd als twee rijen met identiek tijdstip. Dit is
    bewust NIET gekoppeld via Valutadatum: de 'Dividend'-rij die tot deze
    conversie leidde heeft vaak een Valutadatum van 1 (bank-)dag eerder dan
    de conversie zelf (de conversie wordt pas de volgende werkdag
    afgewikkeld) — matchen op Valutadatum was precies de eerdere bug (zie
    verwerk_rekeningoverzicht).

    Geeft een lijst van dicts terug: {datum, tijd, valuta, vreemd_bedrag
    (positief), eur_bedrag (positief), gebruikt (bool, wordt True gezet
    zodra een dividendgroep hem claimt)}.
    """
    fx_rows = df[df["Omschrijving"].isin(["Valuta Debitering", "Valuta Creditering"])]
    paren = []
    for (datum, tijd), groep in fx_rows.groupby(["Datum", "Tijd"]):
        debitering = groep[groep["Omschrijving"] == "Valuta Debitering"]
        creditering = groep[groep["Omschrijving"] == "Valuta Creditering"]
        if debitering.empty or creditering.empty:
            print(f"[dividend-debug] ⚠️ onvolledig valutaconversie-paar op {datum.date()} {tijd}: "
                  f"{len(debitering)}x Debitering, {len(creditering)}x Creditering — overgeslagen")
            continue
        deb = debitering.iloc[0]
        cred = creditering.iloc[0]
        paar = {
            "datum": datum,
            "tijd": tijd,
            "valuta": deb["valuta_mutatie"],
            "vreemd_bedrag": abs(float(deb["mutatie"])),
            "eur_bedrag": float(cred["mutatie"]),
            "gebruikt": False,
        }
        paren.append(paar)
        print(f"[dividend-debug] valutaconversie-paar: {datum.date()} {tijd} — "
              f"{paar['vreemd_bedrag']:.2f} {paar['valuta']} -> €{paar['eur_bedrag']:.2f}")
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
    - alle 'Dividend'- en 'Dividendbelasting'-rijen worden genet (inclusief
      eventuele negatieve correctierijen) tot één bedrag in de eigen valuta
    - is die valuta EUR, dan is dat meteen het EUR-bedrag
    - is die valuta NIET EUR, dan wordt het GEKOPPELDE 'Valuta Creditering'-
      bedrag gebruikt (via _match_valutaconversie) — NIET een eigen
      FX-herberekening. Bruto/belasting worden naar rato van hun aandeel in
      het netto ruwe bedrag verdeeld over dat EUR-bedrag, zodat bruto_eur +
      belasting_eur altijd optelt tot netto_eur.
    - is er geen gekoppeld conversieparen gevonden, dan blijven bruto_eur/
      belasting_eur/netto_eur expliciet None ('onbekend') — nooit een gok.
    """
    conversie_paren = _koppel_valutaconversie_paren(df)

    dividend_rows = df[df["Omschrijving"].isin(["Dividend", "Dividendbelasting"])]
    print(f"[dividend-debug] {len(dividend_rows)} ruwe Dividend/Dividendbelasting-rij(en) gevonden")
    for _, r in dividend_rows.iterrows():
        print(f"[dividend-debug]   {r['Datum'].date()} | {r['Omschrijving']} | {r.get('Product')} | "
              f"{r['mutatie']} {r['valuta_mutatie']}")

    records = []
    for (datum, isin), groep in dividend_rows.groupby(["Datum", "ISIN"]):
        bruto_rijen = groep[groep["Omschrijving"] == "Dividend"]
        belasting_rijen = groep[groep["Omschrijving"] == "Dividendbelasting"]

        product = groep["Product"].iloc[0]
        valuta = groep["valuta_mutatie"].dropna().iloc[0] if groep["valuta_mutatie"].notna().any() else "EUR"

        bruto_ruw = float(bruto_rijen["mutatie"].sum()) if not bruto_rijen.empty else 0.0
        belasting_ruw = float(belasting_rijen["mutatie"].sum()) if not belasting_rijen.empty else 0.0
        netto_ruw = bruto_ruw + belasting_ruw

        print(f"[dividend-debug] groep {datum.date()} / {isin} ({product}): "
              f"{len(bruto_rijen)}x Dividend + {len(belasting_rijen)}x Dividendbelasting -> "
              f"netto {netto_ruw:.2f} {valuta} (bruto {bruto_ruw:.2f}, belasting {belasting_ruw:.2f})")

        if valuta == "EUR":
            bruto_eur, belasting_eur, netto_eur = bruto_ruw, belasting_ruw, netto_ruw
            print(f"[dividend-debug]   -> al in EUR, netto_eur=€{netto_eur:.2f}")
        else:
            match = _match_valutaconversie(conversie_paren, valuta, netto_ruw, datum)
            if match is None:
                bruto_eur = belasting_eur = netto_eur = None
                print(f"[dividend-debug]   ❌ GEEN valutaconversie-paar gevonden voor {netto_ruw:.2f} {valuta} "
                      f"— netto_eur=None, deze uitkering wordt niet meegeteld in de totalen")
            else:
                netto_eur = match["eur_bedrag"]
                if netto_ruw != 0:
                    bruto_eur = netto_eur * (bruto_ruw / netto_ruw)
                    belasting_eur = netto_eur * (belasting_ruw / netto_ruw)
                else:
                    bruto_eur = belasting_eur = 0.0
                print(f"[dividend-debug]   ✓ gekoppeld aan conversie {match['datum'].date()} {match['tijd']} "
                      f"-> netto_eur=€{netto_eur:.2f}")

        # Ruwe (niet-EUR-geconverteerde) bedragen in de dividend_id, zodat die
        # stabiel blijft ongeacht welk valutaconversie-paar er (opnieuw)
        # aan gekoppeld wordt bij een herhaalde upload.
        dividend_id = "DIV-" + hashlib.md5(
            f"{datum.date()}|{isin}|{bruto_ruw:.6f}|{belasting_ruw:.6f}".encode()
        ).hexdigest()[:16]

        records.append({
            "datum": datum.date(),
            "product": product,
            "isin": isin,
            "valuta": valuta,
            "bruto_eur": bruto_eur,
            "belasting_eur": belasting_eur,
            "netto_eur": netto_eur,
            "dividend_id": dividend_id,
        })

    totaal = sum(r["netto_eur"] for r in records if r["netto_eur"] is not None)
    print(f"[dividend-debug] TOTAAL: {len(records)} dividendgroep(en), "
          f"€{totaal:.2f} netto (som van de rijen met een bekend netto_eur)")
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
        print(f"[statistieken] XIRR-berekening mislukt: {e}")
        return None


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

        for _, row in groep.iterrows():
            delta_aantal = float(row["aantal"])
            delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
            if delta_aantal > 0:
                aantal_lopend += delta_aantal
                kostprijs_lopend += delta_cash
                if delta_cash != 0:
                    totaal_gekocht_aantal += delta_aantal
                    totaal_gekocht_bedrag += delta_cash
            elif delta_aantal < 0:
                if delta_cash != 0 and aantal_lopend > 0:
                    gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                    verkocht_nu = min(-delta_aantal, aantal_lopend)
                    kostprijs_lopend -= gak_op_dat_moment * verkocht_nu
                    totaal_verkocht_aantal += verkocht_nu
                    totaal_verkocht_bedrag += -delta_cash  # delta_cash negatief bij verkoop
                aantal_lopend += delta_aantal

        if aantal_lopend > 1e-9:
            open_posities[ticker] = {"aantal": aantal_lopend, "gak": kostprijs_lopend / aantal_lopend}
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
            "aantal_jaren": aantal_jaren,
        },
    }