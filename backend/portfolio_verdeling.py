"""Portfoliobrede aggregaties: verdeling, land, sector, top-N bedrijven en ETF-overlap."""
import math
import re

import pandas as pd

from backend.etf_holdings_provider import normaliseer_land
from backend.ticker_classificatie import get_etf_holdings, get_etf_sector_verdeling, get_land_sector, get_valutas
from backend.transactie_utils import _is_corporate_action_row

# Fractie van het totaal (0.005 = 0,5%); alleen voor de taart.
LAND_OVERIG_DREMPEL = 0.005

# De staaf gebruikt bewust een andere "Overig" dan de taart (zie CLAUDE.md: Data en rekenen).
LAND_STAAF_TOP_N = 10

# Diagnostiek: boven dit percentage onbekend land per ETF een LET_OP-melding met de holdings.
DREMPEL_ONBEKEND_LAND_PCT = 50

# Synoniemen ("Czechia"/"Czech Republic") omdat Yahoo en pycountry verschillen.
# Rusland en Turkije bewust niet: Emerging Markets.
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

# Oost-, Zuid- en Zuidoost-Azië plus Centraal-Azië; Midden-Oosten, Rusland en Turkije bewust niet.
ASIATISCHE_LANDEN = frozenset({
    "China", "Hong Kong", "Macau", "Taiwan", "Taiwan, Province of China", "Japan", "South Korea",
    "Korea", "Korea, Republic of", "Mongolia", "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal",
    "Singapore", "Thailand", "Malaysia", "Indonesia", "Philippines", "Vietnam", "Viet Nam", "Cambodia",
    "Myanmar", "Laos", "Brunei", "Kazakhstan", "Uzbekistan",
})

# Voor namen die de normalisatie niet samenbrengt; sleutel en waarde al genormaliseerd.
# Aanvullen bij een dubbele rij in Top-N bedrijven of ETF-overlap.
BEDRIJF_NAAM_OVERRIDES = {
    "asml holding": "asml holding nv",
    "asml": "asml holding nv",
}

# DeGiro-beurscode -> leesbare naam; een onbekende code wordt rauw getoond.
BEURS_NAMEN = {
    "EAM": "Euronext Amsterdam", "XAMS": "Euronext Amsterdam",
    "EPA": "Euronext Parijs", "EBR": "Euronext Brussel", "ELI": "Euronext Lissabon",
    "BIT": "Euronext Milaan", "OSL": "Euronext Oslo",
    "XET": "Xetra", "FRA": "Frankfurt", "TDG": "Tradegate",
    "LSE": "London Stock Exchange", "XLON": "London Stock Exchange",
    "NDQ": "Nasdaq", "NASDAQ": "Nasdaq", "NSY": "NYSE", "NYSE": "NYSE", "ARCA": "NYSE Arca",
    "BME": "Madrid (BME)", "SWX": "SIX Swiss Exchange", "TSE": "Toronto", "ASX": "ASX (Australië)",
}

VERDELING_OVER_TIJD_TOP_N = 10
# Pandas-periode: per week de laatste echte koersdag als meetpunt.
VERDELING_OVER_TIJD_FREQUENTIE = "W"
VERDELING_OVERIG_SLEUTEL = "__overig__"

# De frontend knipt zelf in; BEDRIJVEN_TOP_N_KNOPPEN (bedrijven.js) moet <= het maximum blijven.
BEDRIJVEN_TOP_N_STANDAARD = 10
BEDRIJVEN_TOP_N_MAX = 50


def _normaliseer_bedrijfsnaam(naam):
    """Lowercase, leestekens weg ("N.V." -> "nv"). "" voor een lege naam: aanroepers slaan die over."""
    if not naam:
        return ""
    schoon = re.sub(r"[^a-z0-9\s]", "", naam.lower())
    schoon = re.sub(r"\s+", " ", schoon).strip()
    return BEDRIJF_NAAM_OVERRIDES.get(schoon, schoon)


def _sorteer_tickers_voor_dropdown(per_ticker):
    """Eerst posities in bezit (op huidige waarde), dan verkochte (op piekwaarde)."""
    def sleutel(ticker):
        reeks = per_ticker[ticker]["waarde"]
        huidige_waarde = reeks[-1] if reeks else 0.0
        piekwaarde = max(reeks) if reeks else 0.0
        if per_ticker[ticker]["nog_in_bezit"]:
            return (0, -huidige_waarde)
        return (1, -piekwaarde)

    return sorted(per_ticker.keys(), key=sleutel)


def _sorteer_verdeling_groot_naar_klein(verdeling):
    return sorted(verdeling, key=lambda x: x["waarde"], reverse=True)


def bereken_verdeling_samenvatting(verdeling):
    """ETF- en aandeelpercentage (0-100); 0.0 bij een leeg totaal."""
    etf_waarde = 0.0
    aandeel_waarde = 0.0
    for item in verdeling:
        waarde = item.get("waarde")
        # NaN overslaan: vergiftigt anders de som, en breekt res.json().
        if waarde is None or not pd.notna(waarde):
            continue
        waarde = float(waarde)
        if waarde <= 0:
            continue
        if item.get("is_etf"):
            etf_waarde += waarde
        else:
            aandeel_waarde += waarde

    totaal = etf_waarde + aandeel_waarde

    def pct(deel):
        return round(deel / totaal * 100, 2) if totaal > 0 else 0.0

    return {
        "totaal": round(totaal, 2),
        "etf_pct": pct(etf_waarde),
        "aandeel_pct": pct(aandeel_waarde),
    }


def waarde_per_ticker_nu(transacties_df, price_data):
    """{ticker: waarde in EUR} van de huidige posities: aantal x laatste koers; tickers zonder koers, met een
    niet-eindige waarde of met waarde <= 0 vallen af."""
    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]
    waarden = {}
    for ticker, aantal in huidige_holdings.items():
        if ticker not in price_data.columns:
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        # NaN (geen koers) is nooit <= 0 en vergiftigt elke som.
        if not math.isfinite(waarde) or waarde <= 0:
            continue
        waarden[ticker] = waarde
    return waarden


def bedrijf_waarden(waarde_per_ticker, is_etf_map, holdings_per_etf, aandeel_namen):
    """({key: {naam, waarde, per_bron: {ticker: EUR}}}, gedekte_waarde) voor alle bedrijven, niet alleen de top-N.
    Invoer zoals bedrijven_verdeling_uit(); key = _normaliseer_bedrijfsnaam."""
    bedrijven = {}
    gedekte_waarde = 0.0

    def voeg_toe(key, weergavenaam, bron_ticker, bedrag):
        entry = bedrijven.setdefault(key, {"naam": weergavenaam, "waarde": 0.0, "per_bron": {}})
        entry["waarde"] += bedrag
        entry["per_bron"][bron_ticker] = entry["per_bron"].get(bron_ticker, 0.0) + bedrag

    for ticker, waarde in waarde_per_ticker.items():
        if is_etf_map.get(ticker, False):
            holdings = holdings_per_etf[ticker]
            gedekt_gewicht = min(sum(h["gewicht"] for h in holdings), 1.0)
            gedekte_waarde += waarde * gedekt_gewicht
            for h in holdings:
                key = _normaliseer_bedrijfsnaam(h["holding_naam"])
                if not key:
                    continue
                voeg_toe(key, h["holding_naam"], ticker, waarde * h["gewicht"])
        else:
            weergavenaam = aandeel_namen.get(ticker, ticker)
            key = _normaliseer_bedrijfsnaam(weergavenaam) or ticker
            gedekte_waarde += waarde
            voeg_toe(key, weergavenaam, ticker, waarde)
    return bedrijven, gedekte_waarde


def bedrijven_verdeling_uit(waarde_per_ticker, is_etf_map, holdings_per_etf, aandeel_namen, bron_namen, top_n):
    """Top-N onderliggende bedrijven (via ETF's en losse aandelen), per bron uitgesplitst.
    Invoer: waarde_per_ticker {ticker: EUR}, holdings_per_etf {ticker: [{holding_naam, gewicht}]},
    aandeel_namen {ticker: weergavenaam} (ontbrekend = ticker), bron_namen {ticker: naam voor de legenda}.
    Sleutels:
      top: [{bedrijf, waarde, per_bron: {ticker: % van totaal_waarde}}], aflopend
      overig: bedrijven buiten de top-N + niet-gedekt ETF-restant (in EUR)
      dekking_pct: fractie 0-1 die aan een bekend bedrijf is toegewezen
      totaal_waarde, top_n_standaard
      bronnen: [{ticker, naam}], aflopend op bijdrage (legenda-volgorde)
    """
    bedrijven, gedekte_waarde = bedrijf_waarden(waarde_per_ticker, is_etf_map, holdings_per_etf, aandeel_namen)
    totaal_waarde = sum(waarde_per_ticker.values())

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
            "per_bron": {k: round(naar_pct(v), 4) for k, v in e["per_bron"].items()},
        })

    bronnen_gesorteerd = sorted(bron_totalen.keys(), key=lambda t: bron_totalen[t], reverse=True)

    return {
        "top": top_resultaat,
        "overig": round(overig, 2),
        "dekking_pct": (gedekte_waarde / totaal_waarde) if totaal_waarde > 0 else 0.0,
        "totaal_waarde": round(totaal_waarde, 2),
        "top_n_standaard": BEDRIJVEN_TOP_N_STANDAARD,
        "bronnen": [{"ticker": t, "naam": bron_namen.get(t, t)} for t in bronnen_gesorteerd],
    }


def bereken_bedrijven_verdeling(transacties_df, price_data, is_etf_map, top_n=BEDRIJVEN_TOP_N_STANDAARD):
    """Top-N onderliggende bedrijven uit de transacties; zie bedrijven_verdeling_uit() voor de sleutels."""
    invoer = bouw_verdeling_invoer(transacties_df, price_data, is_etf_map, onderdelen={ONDERDEEL_BEDRIJVEN})
    return bedrijven_verdeling_uit(invoer["waarde_per_ticker"], is_etf_map, invoer["holdings_per_etf"],
                                   invoer["aandeel_namen"], invoer["bron_namen"], top_n)


def _groepeer_holdings_per_bedrijf(holdings):
    """({key: gewicht}, {key: weergavenaam}); de gedeelde 'zelfde bedrijf'-definitie voor overlap en holdingslijst."""
    gewichten = {}
    namen = {}
    for h in holdings:
        key = _normaliseer_bedrijfsnaam(h["holding_naam"])
        if not key:
            continue
        gewichten[key] = gewichten.get(key, 0.0) + h["gewicht"]
        namen.setdefault(key, h["holding_naam"])
    return gewichten, namen


def _holdings_gewicht_en_naam_per_bedrijf(ticker):
    return _groepeer_holdings_per_bedrijf(get_etf_holdings(ticker))


def holdings_lijst_uit(holdings, positie_waarde=None):
    """{bron, aantal, gedekt_gewicht, holdings: [{naam, gewicht, bedrag_eur}]}, aflopend op gewicht."""
    # NaN overslaan: vergiftigt de som en breekt res.json().
    geldig = [h for h in holdings if pd.notna(h["gewicht"])]
    gewichten, namen = _groepeer_holdings_per_bedrijf(geldig)

    rijen = [
        {
            "naam": namen[key],
            "gewicht": gewicht,
            "bedrag_eur": round(gewicht * float(positie_waarde), 2) if positie_waarde else None,
        }
        for key, gewicht in gewichten.items()
    ]
    rijen.sort(key=lambda r: r["gewicht"], reverse=True)
    return {
        "bron": holdings[0]["bron"] if holdings else None,
        "aantal": len(rijen),
        "gedekt_gewicht": sum(gewichten.values()),
        "holdings": rijen,
    }


def bereken_etf_holdings_lijst(ticker, positie_waarde=None):
    return holdings_lijst_uit(get_etf_holdings(ticker), positie_waarde)


def etf_overlap_uit(gewichten_per_etf):
    """Symmetrische matrix {a: {b: overlap}} uit gewichten_per_etf {ticker: {bedrijfssleutel: gewicht}}
    (_groepeer_holdings_per_bedrijf); {} bij minder dan 2 ETF's."""
    etf_tickers = list(gewichten_per_etf)
    if len(etf_tickers) < 2:
        return {}

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


def etf_tickers_voor_overlap(transacties_df, price_data, is_etf_map):
    """Alle ETF's met een koerskolom, ook verkochte (aantal 0): bewust niet waarde_per_ticker_nu(), want het
    overlap-tabblad heeft een knop "Verkochte ETF's meenemen" die rekent op verkochte ETF's in de matrix."""
    tickers = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum().index
    return [t for t in tickers if t in price_data.columns and is_etf_map.get(t, False)]


def bereken_etf_overlap(transacties_df, price_data, is_etf_map):
    """Symmetrische matrix {a: {b: overlap}}; {} bij minder dan 2 ETF's. Telt ook verkochte ETF's mee, zie
    etf_tickers_voor_overlap()."""
    invoer = bouw_verdeling_invoer(transacties_df, price_data, is_etf_map, onderdelen={ONDERDEEL_OVERLAP})
    return etf_overlap_uit(invoer["gewichten_per_etf"])


def bereken_etf_overlap_detail(etf_a, etf_b):
    """[{holding_naam, gewicht_a, gewicht_b}], None als het bedrijf niet in dat fonds zit."""
    gew_a, namen_a = _holdings_gewicht_en_naam_per_bedrijf(etf_a)
    gew_b, namen_b = _holdings_gewicht_en_naam_per_bedrijf(etf_b)

    rijen = []
    for key in set(gew_a) | set(gew_b):
        rijen.append({
            "holding_naam": namen_a.get(key, namen_b.get(key)),
            "gewicht_a": gew_a.get(key),
            "gewicht_b": gew_b.get(key),
        })

    rijen.sort(key=lambda r: max(r["gewicht_a"] or 0.0, r["gewicht_b"] or 0.0), reverse=True)
    return rijen


def _voeg_kleine_landen_samen(land_dict, drempel=LAND_OVERIG_DREMPEL, uitgezonderd=frozenset()):
    """'Unknown' valt gewoon mee onder de drempel; 'uitgezonderd' (bv. een aangezette 'Europe') nooit."""
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


def _beperk_tot_top_n_per_bron(per_bron_dict, top_n=LAND_STAAF_TOP_N):
    """Top_n categorieën op totaal, de rest per bron in 'Overig'; nooit een lege Overig."""
    def schoon(bedrag):
        # NaN overslaan: vergiftigt anders de som, en breekt res.json().
        return float(bedrag) if bedrag is not None and pd.notna(bedrag) else None

    rijen = {
        categorie: {bron: b for bron, b in ((bron, schoon(x)) for bron, x in per_bron.items()) if b is not None}
        for categorie, per_bron in per_bron_dict.items()
    }
    if len(rijen) <= top_n:
        return rijen

    volgorde = sorted(rijen, key=lambda c: sum(rijen[c].values()), reverse=True)
    resultaat = {c: rijen[c] for c in volgorde[:top_n]}
    overig = {}
    for categorie in volgorde[top_n:]:
        for bron, bedrag in rijen[categorie].items():
            overig[bron] = overig.get(bron, 0.0) + bedrag
    resultaat["Overig"] = overig
    return resultaat


def _groepeer_regios_samen(land_dict, regios):
    """regios: {naam: landenverzameling}; een land in geen enkele regio blijft staan."""
    resultaat = {}
    totalen = {}
    for land, bedrag in land_dict.items():
        regio = next((naam for naam, landen in regios.items() if land in landen), None)
        if regio:
            totalen[regio] = totalen.get(regio, 0.0) + bedrag
        else:
            resultaat[land] = bedrag

    for regio, totaal in totalen.items():
        if totaal > 0:
            resultaat[regio] = resultaat.get(regio, 0.0) + totaal
    return resultaat


def _groepeer_regios_samen_per_bron(land_per_bron_dict, regios):
    resultaat = {}
    per_regio = {}
    for land, per_bron in land_per_bron_dict.items():
        regio = next((naam for naam, landen in regios.items() if land in landen), None)
        if regio:
            rij = per_regio.setdefault(regio, {})
            for bron, bedrag in per_bron.items():
                rij[bron] = rij.get(bron, 0.0) + bedrag
        else:
            resultaat[land] = dict(per_bron)

    resultaat.update(per_regio)
    return resultaat


def _gekozen_regios(europa, azie):
    regios = {}
    if europa:
        regios["Europe"] = EUROPESE_LANDEN
    if azie:
        regios["Asia"] = ASIATISCHE_LANDEN
    return regios


def _groepeer_europa_samen(land_dict, europese_landen=EUROPESE_LANDEN):
    return _groepeer_regios_samen(land_dict, {"Europe": europese_landen})


def _groepeer_europa_samen_per_bron(land_per_bron_dict, europese_landen=EUROPESE_LANDEN):
    return _groepeer_regios_samen_per_bron(land_per_bron_dict, {"Europe": europese_landen})


def _is_bekend(waarde):
    return bool(waarde) and waarde != "Unknown"


def bereken_land_dekking(holdings):
    """holdings: [{naam, gewicht (fractie 0-1), land, sector}] van één ETF. Geeft
    {onbekend_pct, dekking_pct, rijen: [[naam, gewicht_pct, land, sector], ...]}, zwaarste eerst,
    met een restrij "Niet in holdingsdata" als de gewichten samen onder 100% blijven."""
    gesorteerd = sorted(holdings or [], key=lambda h: h.get("gewicht") or 0.0, reverse=True)
    rijen = []
    dekking_pct = 0.0
    bekend_pct = 0.0
    for h in gesorteerd:
        gewicht_pct = float(h.get("gewicht") or 0.0) * 100
        land = normaliseer_land(h.get("land"))
        sector = h.get("sector") if _is_bekend(h.get("sector")) else "Unknown"
        rijen.append([h.get("naam"), gewicht_pct, land, sector])
        dekking_pct += gewicht_pct
        if land != "Unknown":
            bekend_pct += gewicht_pct
    restant_pct = 100 - dekking_pct
    if restant_pct > 1e-9:
        rijen.append(["Niet in holdingsdata", restant_pct, "–", "–"])
    return {"onbekend_pct": max(0.0, 100 - bekend_pct), "dekking_pct": dekking_pct, "rijen": rijen}


ONBEKEND_DREMPEL = 0.999


def verdeling_is_onbekend(verdeling):
    """True bij een lege verdeling of als (vrijwel) alles Unknown is; een gedeeltelijk Unknown blijft gewoon een rij."""
    return not verdeling or verdeling.get("Unknown", 0.0) >= ONBEKEND_DREMPEL


def sector_pct_met_restant(sector_verdeling):
    """Niet-gedekt restant naar Unknown, zodat de totalen kloppen."""
    sector_pct = dict(sector_verdeling)
    restant_sector = max(0.0, 1.0 - sum(sector_verdeling.values()))
    if restant_sector > 1e-9:
        sector_pct["Unknown"] = sector_pct.get("Unknown", 0.0) + restant_sector
    return sector_pct


def land_pct_uit_holdings(holdings):
    land_pct = {}
    for h in holdings:
        land = normaliseer_land(h["land"])
        land_pct[land] = land_pct.get(land, 0.0) + h["gewicht"]
    restant_land = max(0.0, 1.0 - sum(h["gewicht"] for h in holdings))
    if restant_land > 1e-9:
        land_pct["Unknown"] = land_pct.get("Unknown", 0.0) + restant_land
    return land_pct


def land_sector_fracties(ticker, is_etf, proxy=None):
    """Land- en sectorverdeling van één ticker. proxy: etf_proxy-rij of None. Sleutels:
      land_pct, sector_pct: {naam: fractie}, met het niet-gedekte restant als "Unknown"
      land_bron: "proxy", de holdings-bron of None (aandeel); land_proxy: {naam, max_afwijking_pp} of None
    """
    if not is_etf:
        land, sector = get_land_sector(ticker)
        return {"land_pct": {land: 1.0}, "sector_pct": {sector: 1.0}, "land_bron": None, "land_proxy": None}

    sector_pct = sector_pct_met_restant(get_etf_sector_verdeling(ticker))

    land_proxy = None
    if proxy and proxy.get("proxy_isin") and proxy.get("proxy_land"):
        land_bron = "proxy"
        land_pct = dict(proxy["proxy_land"])
        land_proxy = {"naam": proxy["proxy_naam"], "max_afwijking_pp": proxy["max_afwijking_pp"]}
    else:
        holdings = get_etf_holdings(ticker)
        land_bron = holdings[0]["bron"] if holdings else "yfinance_top10"
        land_pct = land_pct_uit_holdings(holdings)
    return {"land_pct": land_pct, "sector_pct": sector_pct, "land_bron": land_bron, "land_proxy": land_proxy}


def _aandeel_land_sector(ticker):
    fracties = land_sector_fracties(ticker, False)
    return next(iter(fracties["land_pct"])), next(iter(fracties["sector_pct"]))


ONDERDEEL_LAND_SECTOR = "land_sector"
ONDERDEEL_BEDRIJVEN = "bedrijven"
ONDERDEEL_OVERLAP = "overlap"
VERDELING_ONDERDELEN = frozenset({ONDERDEEL_LAND_SECTOR, ONDERDEEL_BEDRIJVEN, ONDERDEEL_OVERLAP})


def bouw_verdeling_invoer(transacties_df, price_data, is_etf_map, land_proxies=None, onderdelen=VERDELING_ONDERDELEN):
    """De invoer van de pure kernen, opgehaald uit de transacties. Per onderdeel apart, zodat een wrapper niet meer
    Yahoo/cache-calls doet dan hij nodig heeft. Altijd: waarde_per_ticker (leeg als alleen de overlap wordt gevraagd:
    die heeft een eigen selectie), is_etf_map. Sleutels per onderdeel:
      land_sector: fracties_per_ticker {ticker: land_sector_fracties()}, extra_aandelen {ticker: (land, sector)}
      bedrijven: holdings_per_etf, aandeel_namen, bron_namen
      overlap: gewichten_per_etf {ticker: {bedrijfssleutel: gewicht}} ({} bij minder dan 2 ETF's)
    """
    transacties_df = transacties_df.dropna(subset=["ticker"])
    waarde_per_ticker = waarde_per_ticker_nu(transacties_df, price_data) if onderdelen - {ONDERDEEL_OVERLAP} else {}
    invoer = {"waarde_per_ticker": waarde_per_ticker, "is_etf_map": is_etf_map}
    etf_tickers = [t for t in waarde_per_ticker if is_etf_map.get(t, False)]

    if ONDERDEEL_LAND_SECTOR in onderdelen:
        invoer["fracties_per_ticker"] = {
            ticker: land_sector_fracties(ticker, is_etf_map.get(ticker, False), (land_proxies or {}).get(ticker))
            for ticker in waarde_per_ticker
        }
        invoer["extra_aandelen"] = {
            ticker: _aandeel_land_sector(ticker)
            for ticker in sorted(transacties_df["ticker"].unique())
            if ticker not in waarde_per_ticker and not is_etf_map.get(ticker, False)
        }

    if ONDERDEEL_BEDRIJVEN in onderdelen:
        bron_namen = {}
        if "product" in transacties_df.columns:
            bron_namen = (
                transacties_df.drop_duplicates(subset=["ticker"], keep="last")
                .set_index("ticker")["product"]
                .to_dict()
            )
        invoer["bron_namen"] = bron_namen
        invoer["holdings_per_etf"] = {t: get_etf_holdings(t) for t in etf_tickers}
        aandeel_namen = {}
        if "echte_naam" in transacties_df.columns:
            for ticker in waarde_per_ticker:
                if is_etf_map.get(ticker, False):
                    continue
                namen = transacties_df.loc[transacties_df["ticker"] == ticker, "echte_naam"].dropna()
                if not namen.empty:
                    aandeel_namen[ticker] = namen.iloc[-1]
        invoer["aandeel_namen"] = aandeel_namen

    if ONDERDEEL_OVERLAP in onderdelen:
        overlap_tickers = etf_tickers_voor_overlap(transacties_df, price_data, is_etf_map)
        invoer["gewichten_per_etf"] = (
            {t: _holdings_gewicht_en_naam_per_bedrijf(t)[0] for t in overlap_tickers} if len(overlap_tickers) >= 2 else {})
    return invoer


def land_sector_verdeling_uit(waarde_per_ticker, is_etf_map, fracties_per_ticker, extra_aandelen=None):
    """Bedragen in EUR. Invoer: waarde_per_ticker {ticker: EUR}, fracties_per_ticker {ticker: land_sector_fracties()},
    extra_aandelen {ticker: (land, sector)} voor aandelen zonder positiewaarde (alleen in per_aandeel). Sleutels:
      land, land_europa, land_azie, land_europa_azie (regio's samengevoegd): {land: EUR}, kleine landen in 'Overig' (taart)
      regio_verdeling: {"Europe": {land: EUR}, "Asia": {land: EUR}}, onderverdeling binnen de regio (kleine taarten)
      sector: {sector: EUR}
      per_etf: {ticker: {land, sector (fracties 0-1), land_bron, land_proxy: {naam, max_afwijking_pp} of None}}
      per_aandeel: {ticker: {land, sector}}, alle niet-ETF-tickers, ook gesloten posities
      land_per_bron_top, land_per_bron_{europa,azie,europa_azie}_top: {land: {bron: EUR}}, top 10 + 'Overig' (staaf)
      sector_per_bron: {sector: {bron: EUR}}
      land_totaal, sector_totaal: ongegroepeerde {naam: EUR} (zonder 'Overig'), voor een verschil tussen twee situaties
    """
    def optellen(dct, key, bedrag):
        key = key or "Unknown"
        dct[key] = dct.get(key, 0.0) + bedrag

    def optellen_per_bron(dct, key, bron_ticker, bedrag):
        key = key or "Unknown"
        rij = dct.setdefault(key, {})
        rij[bron_ticker] = rij.get(bron_ticker, 0.0) + bedrag

    land = {}
    sector = {}
    per_etf = {}
    per_aandeel = {}
    land_per_bron = {}
    sector_per_bron = {}

    for ticker, waarde in waarde_per_ticker.items():
        fracties = fracties_per_ticker[ticker]
        if is_etf_map.get(ticker, False):
            for naam, gewicht in fracties["sector_pct"].items():
                optellen(sector, naam, waarde * gewicht)
                optellen_per_bron(sector_per_bron, naam, ticker, waarde * gewicht)
            for naam, gewicht in fracties["land_pct"].items():
                optellen(land, naam, waarde * gewicht)
                optellen_per_bron(land_per_bron, naam, ticker, waarde * gewicht)

            per_etf[ticker] = {"land": fracties["land_pct"], "sector": fracties["sector_pct"],
                               "land_bron": fracties["land_bron"], "land_proxy": fracties["land_proxy"]}
        else:
            aandeel_land, aandeel_sector = next(iter(fracties["land_pct"])), next(iter(fracties["sector_pct"]))
            per_aandeel[ticker] = {"land": aandeel_land, "sector": aandeel_sector}
            optellen(land, aandeel_land, waarde)
            optellen_per_bron(land_per_bron, aandeel_land, ticker, waarde)
            optellen(sector, aandeel_sector, waarde)
            optellen_per_bron(sector_per_bron, aandeel_sector, ticker, waarde)

    for ticker, (aandeel_land, aandeel_sector) in (extra_aandelen or {}).items():
        if ticker not in per_aandeel:
            per_aandeel[ticker] = {"land": aandeel_land, "sector": aandeel_sector}

    def samengevoegd(europa, azie):
        regios = _gekozen_regios(europa, azie)
        gegroepeerd = _groepeer_regios_samen(land, regios)
        return _voeg_kleine_landen_samen(gegroepeerd, uitgezonderd=set(regios) & set(gegroepeerd))

    def samengevoegd_per_bron(europa, azie):
        return _beperk_tot_top_n_per_bron(
            _groepeer_regios_samen_per_bron(land_per_bron, _gekozen_regios(europa, azie)))

    def binnen_regio(landen):
        return _voeg_kleine_landen_samen({l: b for l, b in land.items() if l in landen and b > 0})

    return {
        "land": _voeg_kleine_landen_samen(land),
        "regio_verdeling": {"Europe": binnen_regio(EUROPESE_LANDEN), "Asia": binnen_regio(ASIATISCHE_LANDEN)},
        "land_europa": samengevoegd(True, False),
        "land_azie": samengevoegd(False, True),
        "land_europa_azie": samengevoegd(True, True),
        "sector": sector,
        "land_totaal": dict(land),
        "sector_totaal": dict(sector),
        "per_etf": per_etf,
        "per_aandeel": per_aandeel,
        "land_per_bron_top": _beperk_tot_top_n_per_bron(land_per_bron),
        "land_per_bron_europa_top": samengevoegd_per_bron(True, False),
        "land_per_bron_azie_top": samengevoegd_per_bron(False, True),
        "land_per_bron_europa_azie_top": samengevoegd_per_bron(True, True),
        "sector_per_bron": sector_per_bron,
    }


def compute_land_sector_verdeling(transacties_df, price_data, is_etf_map, land_proxies=None):
    """Land- en sectorverdeling uit de transacties; zie land_sector_verdeling_uit() voor de sleutels.
    land_proxies: {ticker: etf_proxy-rij} (etf_proxy.py); met een proxy_isin komt het land van de ETF uit de proxy
    (land_bron 'proxy'), sector blijft van Yahoo."""
    invoer = bouw_verdeling_invoer(transacties_df, price_data, is_etf_map, land_proxies, {ONDERDEEL_LAND_SECTOR})
    return land_sector_verdeling_uit(invoer["waarde_per_ticker"], is_etf_map, invoer["fracties_per_ticker"],
                                     invoer["extra_aandelen"])


def gewichten_per_positie(tickers):
    return {t: {t: 1.0} for t in tickers}


def gewichten_per_valuta(tickers):
    return {ticker: {valuta: 1.0} for ticker, valuta in get_valutas(tickers).items()}


def _als_gewichten(pct):
    """Lege of None-sleutel → "Unknown"; een som boven 1 (afronding bij de bron) terug naar 1, anders klopt het totaal niet."""
    gewichten = {}
    for naam, fractie in pct.items():
        if fractie and fractie > 0:
            gewichten[naam or "Unknown"] = gewichten.get(naam or "Unknown", 0.0) + float(fractie)
    som = sum(gewichten.values())
    if som <= 0:
        return {"Unknown": 1.0}
    return {naam: f / som for naam, f in gewichten.items()} if som > 1 else gewichten


def gewichten_per_land(fracties_per_ticker, europa_samenvoegen=False, azie_samenvoegen=False):
    """fracties_per_ticker: {ticker: land_sector_fracties()}; regio's met dezelfde landenlijsten als de taart."""
    regios = _gekozen_regios(europa_samenvoegen, azie_samenvoegen)
    return {
        ticker: _als_gewichten(_groepeer_regios_samen(f["land_pct"], regios) if regios else f["land_pct"])
        for ticker, f in fracties_per_ticker.items()
    }


def gewichten_per_sector(fracties_per_ticker):
    return {ticker: _als_gewichten(f["sector_pct"]) for ticker, f in fracties_per_ticker.items()}


def _beurs_codes(transacties_df):
    # Lege beurs niet laten wegvallen in een groupby: het totaal moet gelijk blijven aan Verdeling.
    return transacties_df["beurs"].fillna("Onbekend")


def beurs_naam(code):
    return BEURS_NAMEN.get(code, code)


def gewichten_per_beurs(transacties_df):
    """Een ticker op meerdere beursnamen naar verhouding van de gekochte stuks (zie CLAUDE.md: Data en rekenen)."""
    return _gewichten_per_beurssleutel(
        transacties_df, lambda codes: codes.map(beurs_naam))


def gewichten_per_beurscode(transacties_df):
    """Zoals gewichten_per_beurs, maar op de beurscode (hoofdletters, zonder spaties)."""
    return _gewichten_per_beurssleutel(transacties_df, lambda codes: codes.astype(str).str.strip().str.upper())


def _gewichten_per_beurssleutel(transacties_df, sleutel_van_codes):
    df = transacties_df.dropna(subset=["ticker"]).reset_index(drop=True)
    namen = sleutel_van_codes(_beurs_codes(df))
    gekocht = df["aantal"].astype(float).clip(lower=0)
    # Splitboekingen (DEG) zijn geen aankoop; alleen als er niets anders is tellen ze mee.
    corporate_action = df.apply(_is_corporate_action_row, axis=1).astype(bool)

    gewichten = {}
    for ticker, rijen in df.groupby("ticker").groups.items():
        stuks = gekocht[rijen]
        aankopen = stuks[~corporate_action[rijen]]
        if aankopen.sum() > 0:
            stuks = aankopen
        per_naam = stuks.groupby(namen[stuks.index]).sum()
        if per_naam.sum() <= 0:
            per_naam = pd.Series(1.0, index=namen[rijen].unique())
        totaal = float(per_naam.sum())
        gewichten[ticker] = {naam: float(n) / totaal for naam, n in per_naam.items() if n > 0}
    return gewichten


def _meetpunten(dagen):
    """Per week de laatste dag uit `dagen`; de laatste dag van de reeks zit er dus altijd in."""
    laatste_per_week = pd.Series(dagen, index=dagen).groupby(dagen.to_period(VERDELING_OVER_TIJD_FREQUENTIE)).max()
    return pd.DatetimeIndex(laatste_per_week.to_numpy())


def bereken_verdeling_over_tijd(waarde_df, gewichten, namen, top_n=VERDELING_OVER_TIJD_TOP_N):
    """waarde_df: € per ticker (kolommen) per dag. gewichten: {ticker: {categorie: fractie}}, zonder → "Unknown".
    Sleutels: labels (YYYY-MM-DD), totaal (€), reeksen [{sleutel, naam, waarde (€), pct (None bij totaal ≤ 0)}]."""
    leeg = {"labels": [], "totaal": [], "reeksen": []}
    if waarde_df.empty:
        return leeg
    waarde_df = waarde_df.fillna(0.0)
    positief = waarde_df.sum(axis=1) > 0
    if not positief.any():
        return leeg
    waarde_df = waarde_df.loc[positief.idxmax():]
    meting = waarde_df.loc[_meetpunten(waarde_df.index)]

    per_categorie = {}
    for ticker in meting.columns:
        for categorie, fractie in (gewichten.get(ticker) or {"Unknown": 1.0}).items():
            per_categorie[categorie] = per_categorie.get(categorie, 0.0) + meting[ticker] * fractie
    per_categorie = {c: reeks for c, reeks in per_categorie.items() if (reeks != 0).any()}

    # Op €-som over alle meetpunten, niet op max %: de eerste aankoop is anders altijd een keer 100%.
    volgorde = sorted(per_categorie, key=lambda c: (-float(per_categorie[c].sum()), str(c)))
    reeksen = [(c, namen.get(c, c), per_categorie[c]) for c in volgorde[:top_n]]
    if len(volgorde) > top_n:
        overig = sum((per_categorie[c] for c in volgorde[top_n:]), pd.Series(0.0, index=meting.index))
        reeksen.append((VERDELING_OVERIG_SLEUTEL, "Overig", overig))

    totaal = meting.sum(axis=1)

    def pct(reeks):
        return [round(float(w) / float(t) * 100, 2) if t > 0 else None for w, t in zip(reeks, totaal)]

    return {
        "labels": meting.index.strftime("%Y-%m-%d").tolist(),
        "totaal": [round(float(t), 2) for t in totaal],
        "reeksen": [
            {"sleutel": sleutel, "naam": naam, "waarde": [round(float(w), 2) for w in reeks], "pct": pct(reeks)}
            for sleutel, naam, reeks in reeksen
        ],
    }


def compute_valuta_verdeling(transacties_df, price_data):
    """Per noteringsvaluta (ook van een ETF), bedragen in €. Sleutels:
      valuta: {valuta: €}, kleine valuta's in 'Overig' (taart)
      valuta_per_bron: {valuta: {bron: €}} (staaf)
    """
    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    waarden = {}
    for ticker, aantal in huidige_holdings.items():
        if ticker not in price_data.columns:
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        if waarde > 0:
            waarden[ticker] = waarde
    valuta_per_ticker = get_valutas(waarden)

    valuta = {}
    valuta_per_bron = {}
    for ticker, waarde in waarden.items():
        naam = valuta_per_ticker[ticker]
        valuta[naam] = valuta.get(naam, 0.0) + waarde
        rij = valuta_per_bron.setdefault(naam, {})
        rij[ticker] = rij.get(ticker, 0.0) + waarde

    return {"valuta": _voeg_kleine_landen_samen(valuta), "valuta_per_bron": valuta_per_bron}


def compute_beurs_verdeling(transacties_df, price_data):
    """Per DeGiro-beurs, bedragen in €. Geen "Overig": het gaat juist om het aantal beurzen. Sleutels:
      beurs: {naam: €} (taart)
      beurs_per_bron: {naam: {ticker: €}} (staaf)
      aantal_beurzen
    """
    transacties_df = transacties_df.dropna(subset=["ticker"])
    holdings = transacties_df.groupby([transacties_df["ticker"], _beurs_codes(transacties_df)])["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    beurs, beurs_per_bron = {}, {}
    for (ticker, code), aantal in holdings.items():
        if ticker not in price_data.columns or not pd.notna(laatste_prijzen[ticker]):
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        if waarde <= 0:
            continue
        naam = beurs_naam(code)
        beurs[naam] = beurs.get(naam, 0.0) + waarde
        rij = beurs_per_bron.setdefault(naam, {})
        rij[ticker] = rij.get(ticker, 0.0) + waarde

    return {
        "beurs": beurs,
        "beurs_per_bron": beurs_per_bron,
        "aantal_beurzen": len(beurs),
    }
