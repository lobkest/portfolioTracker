"""Volledige ETF-holdings bij de fondsprovider (iShares/VanEck) i.p.v. yfinance's top 10."""
import io
import re

import pandas as pd
import requests

from debug_utils import dprint

# Nieuwe URL toevoegen: zie docs/CODE_OVERZICHT.md, 8.5. Zonder entry valt een ticker terug op yfinance.
ETF_HOLDINGS_BRON = {
    # Geen asOfDate in blackrock.com-URL's; "locale" hoort bij de bron-URL
    # (zie CLAUDE.md: Yahoo en tickers).
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
    # Zelfde fonds als EMIM.AS, andere notering.
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
    # Zelfde fonds als GDX.L, andere notering.
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
    # Engelstalige VanEck-site (/nl/en/), dus locale "en".
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

# Vanguard (VWCE.AS, VUSA.AS) bewust niet: hun download loopt via een fragiele GraphQL-API.

_PROVIDER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _schone_tekst(waarde):
    return str(waarde).strip() if waarde is not None and pd.notna(waarde) and str(waarde).strip() else None


def _holding_rij(naam, gewicht, land, sector, ticker=None):
    """Leeg land wordt 'Unknown', zodat bv. een Cash-rij zichtbaar blijft in de landverdeling."""
    return {
        "naam": str(naam).strip(),
        "gewicht": float(gewicht),
        "land": _schone_tekst(land) or "Unknown",
        "sector": _schone_tekst(sector),
        "ticker": _schone_tekst(ticker),
    }


def _parse_percentage_waarde(waarde, locale="en"):
    """'10,74%' of 7.68 -> float; zonder opschoning geeft pd.to_numeric stil NaN."""
    if isinstance(waarde, str):
        waarde = waarde.strip().rstrip("%").strip()
        if locale == "nl":
            waarde = waarde.replace(".", "").replace(",", ".")
    return pd.to_numeric(waarde, errors="coerce")


def _parse_ishares_holdings(content, locale="en"):
    """Bij locale 'nl' ook Nederlands getalformaat en landnamen (die worden vertaald)."""
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
            naam, gewicht, land, row.get(sector_kolom) if sector_kolom else None, row.get("Ticker"),
        ))
    return holdings


# Gangbare Engelse namen zoals yfinance ze geeft ("South Korea"), niet de ISO-namen van pycountry.
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
    """Een onbekende naam blijft onvertaald (zichtbaar) i.p.v. 'Unknown'."""
    if land in NL_LAND_VERTALING:
        return NL_LAND_VERTALING[land]
    return land


def _land_via_isin(isin):
    """Land van registratie uit de ISIN-prefix: een benadering, niet waar het bedrijf actief is."""
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
    """Kolomnamen verschillen per fonds/site; zonder landkolom komt het land uit de ISIN."""
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

AANBIEDER_NAMEN = {"ishares": "iShares", "vaneck": "VanEck"}


def aanbieder_naam(etf_ticker):
    """Een provider-CSV van een ETF buiten ETF_HOLDINGS_BRON komt uit de iShares-screener (op ISIN)."""
    provider = ETF_HOLDINGS_BRON.get(etf_ticker, {}).get("provider", "ishares")
    return AANBIEDER_NAMEN.get(provider, provider)


def _dedupliceer_holdings(holdings):
    """Telt gewichten van gelijke namen op; anders UniqueViolation op de PK van etf_holdings."""
    per_naam = {}
    for h in holdings:
        bestaand = per_naam.get(h["naam"])
        if bestaand is None:
            per_naam[h["naam"]] = dict(h)
        else:
            bestaand["gewicht"] += h["gewicht"]
    return list(per_naam.values())


def fetch_provider_holdings(etf_ticker):
    """None bij geen URL of elke fout; mag nooit crashen (de aanroeper valt terug op yfinance)."""
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



ISHARES_BASIS_URL = "https://www.ishares.com"
# Alleen de Nederlandse site geeft deze JSON; de Engelse/UK-variant geeft 404.
ISHARES_SCREENER_URL = (
    ISHARES_BASIS_URL + "/nl/particuliere-belegger/nl/product-screener/product-screener-v3.1.jsn"
    "?dcrPath=/templatedata/config/product-screener-v3/data/nl/nl/product-screener/"
    "ishares-product-screener-backend-config&siteEntryPassthrough=true"
)

_HOLDINGS_CSV_LINK = re.compile(
    r"""[^"'\s]*\.ajax\?fileType=csv&(?:amp;)?fileName=[^"'&]*_holdings&(?:amp;)?dataType=fund""")


def _codes_uit_lijsttekst(tekst):
    """'[50567, 50569]' -> ['50567', '50569']; '-' -> []."""
    if not isinstance(tekst, str):
        return []
    return [c.strip() for c in tekst.strip("[]").split(",") if c.strip() and c.strip() != "-"]


def _parse_ishares_screener(data):
    """Alleen ETF's; codes i.p.v. de Nederlandse labels (taalonafhankelijk). [{portfolio_id, isin, naam, product_url, asset_class, regio, markt_type,
    sub_asset_class, strategie_codes, fondsgrootte}]."""
    fondsen = []
    for portfolio_id, f in (data or {}).items():
        if not isinstance(f, dict) or "etf" not in (f.get("productView") or []):
            continue
        if not f.get("isin") or not f.get("productPageUrl"):
            continue
        grootte = f.get("totalFundSizeInMillions")
        fondsen.append({
            "portfolio_id": str(portfolio_id),
            "isin": f["isin"],
            "naam": f.get("fundName"),
            "product_url": f["productPageUrl"],
            "asset_class": f.get("aladdinAssetClassCode"),
            "regio": f.get("aladdinRegionCode"),
            "markt_type": f.get("aladdinMarketTypeCode"),
            "sub_asset_class": f.get("aladdinSubAssetClassCode"),
            "strategie_codes": _codes_uit_lijsttekst(f.get("aladdinStrategyCode")),
            "fondsgrootte": grootte.get("r") if isinstance(grootte, dict) else None,
        })
    return fondsen


def fetch_ishares_fondsenlijst():
    """Fondsenlijst uit de iShares-screener; None bij een fout of lege lijst."""
    try:
        response = requests.get(ISHARES_SCREENER_URL, headers={"User-Agent": _PROVIDER_USER_AGENT}, timeout=60)
        response.raise_for_status()
        fondsen = _parse_ishares_screener(response.json())
    except Exception as e:
        print(f"[etf-proxy] WARN iShares-screener niet opgehaald ({e!a})")
        return None
    return fondsen or None


def _vind_holdings_csv_link(html):
    """Absolute URL van de holdings-CSV uit een iShares-productpagina, of None."""
    match = _HOLDINGS_CSV_LINK.search(html or "")
    if not match:
        return None
    pad = match.group(0).replace("&amp;", "&")
    return pad if pad.startswith("http") else ISHARES_BASIS_URL + pad


def fetch_ishares_holdings_via_productpagina(product_url):
    """[{naam, gewicht (%), land, sector, ticker}] via de CSV-link op de productpagina; None bij elke fout.
    siteEntryPassthrough slaat de beleggerstype-keuze over (anders een landingspagina zonder link)."""
    headers = {"User-Agent": _PROVIDER_USER_AGENT}
    try:
        pagina = requests.get(f"{ISHARES_BASIS_URL}{product_url}?siteEntryPassthrough=true",
                              headers=headers, timeout=30)
        pagina.raise_for_status()
        csv_url = _vind_holdings_csv_link(pagina.text)
        if csv_url is None:
            dprint(f"[etf-proxy] geen holdings-CSV-link op {product_url}")
            return None
        response = requests.get(csv_url, headers=headers, timeout=30)
        response.raise_for_status()
        holdings = _parse_ishares_holdings(response.content, locale="nl")
    except Exception as e:
        dprint(f"[etf-proxy] holdings van {product_url} niet opgehaald: {e}")
        return None
    return _dedupliceer_holdings(holdings) or None
