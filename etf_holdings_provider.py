"""
Ophalen en parsen van de VOLLEDIGE holdings-lijst van een ETF bij de
fondsprovider zelf (iShares/VanEck), i.p.v. yfinance's top-10 —
zie ETF_HOLDINGS_BRON hieronder voor de achtergrond. Losgetrokken uit
analysis.py (was daar het eerste, volledig zelfstandige blok functies).
"""
import io

import pandas as pd
import requests

from debug_utils import dprint

# Handmatig opgezochte directe download-links naar de volledige
# holdings-CSV/XLSX van de ETF-provider (voor landverdeling — sector blijft
# via yfinance sector_weightings, dat dekt al ~100%). yfinance's
# funds_data.top_holdings geeft maar de top 10, wat voor een breed gespreid
# fonds (bv. CSPX.AS, 500+ posities) maar ~35-40% dekking geeft; de
# provider zelf publiceert de volledige lijst.
#
# Zoek dit op via de fondspagina van de provider (iShares/Vanguard/VanEck
# etc.) -> knop "Holdings downloaden" / "Full holdings" -> rechtermuisklik
# op de downloadknop -> "Kopieer linkadres". Controleer een gevonden link
# eerst door het bestand los door de parser uit _PROVIDER_PARSERS te halen
# (aantal holdings, som van de gewichten moet dicht bij 100 liggen) voordat
# je 'm hier toevoegt. Vul
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
    # Nederlandse site (GDX.L) zijn Nederlands. Dit ontdekten we pas aan de
    # gewichtensom van een testdownload: zonder locale="nl" werd bv. "5,25%"
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
    precies zo werd de eerdere iShares-locale-bug pas zichtbaar via de
    gewichtensom van een testdownload (~10000% i.p.v. ~100%)."""
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
    in een verkeerde categorie."""
    if land in NL_LAND_VERTALING:
        return NL_LAND_VERTALING[land]
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
        return None

    try:
        response = requests.get(url, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=30)
        response.raise_for_status()
        holdings = parser(response.content, locale=locale)
    except Exception:
        return None

    if not holdings:
        return None

    return _dedupliceer_holdings(holdings)

