"""Portfoliobrede aggregaties: verdeling, land, sector, top-N bedrijven en ETF-overlap."""
import re

import pandas as pd

from ticker_classificatie import get_etf_holdings, get_etf_sector_verdeling, get_land_sector

# Fractie van het totaal (0.005 = 0,5%); alleen voor de taart.
LAND_OVERIG_DREMPEL = 0.005

# De staaf gebruikt bewust een andere "Overig" dan de taart (zie CLAUDE.md: Data en rekenen).
LAND_STAAF_TOP_N = 10

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

# Voor namen die de normalisatie niet samenbrengt; sleutel en waarde al genormaliseerd.
# Aanvullen bij een dubbele rij in Top-N bedrijven of ETF-overlap.
BEDRIJF_NAAM_OVERRIDES = {
    "asml holding": "asml holding nv",
    "asml": "asml holding nv",
}

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


def bereken_bedrijven_verdeling(transacties_df, price_data, is_etf_map, top_n=BEDRIJVEN_TOP_N_STANDAARD):
    """Top-N onderliggende bedrijven (via ETF's en losse aandelen), per bron uitgesplitst.
    Sleutels:
      top: [{bedrijf, waarde, per_bron: {ticker: % van totaal_waarde}}], aflopend
      overig: bedrijven buiten de top-N + niet-gedekt ETF-restant (in €)
      dekking_pct: fractie 0-1 die aan een bekend bedrijf is toegewezen
      totaal_waarde, top_n_standaard
      bronnen: [{ticker, naam}], aflopend op bijdrage (legenda-volgorde)
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


def _holdings_gewicht_en_naam_per_bedrijf(ticker):
    """({key: gewicht}, {key: weergavenaam}); de gedeelde 'zelfde bedrijf'-definitie voor de overlap."""
    gewichten = {}
    namen = {}
    for h in get_etf_holdings(ticker):
        key = _normaliseer_bedrijfsnaam(h["holding_naam"])
        if not key:
            continue
        gewichten[key] = gewichten.get(key, 0.0) + h["gewicht"]
        namen.setdefault(key, h["holding_naam"])
    return gewichten, namen


def bereken_etf_overlap(transacties_df, price_data, is_etf_map):
    """Symmetrische matrix {a: {b: overlap}}; {} bij minder dan 2 ETF's."""
    transacties_df = transacties_df.dropna(subset=["ticker"])
    huidige_holdings = transacties_df.groupby("ticker")["aantal"].sum()

    tickers = [t for t in huidige_holdings.index if t in price_data.columns]
    etf_tickers = [t for t in tickers if is_etf_map.get(t, False)]

    if len(etf_tickers) < 2:
        return {}

    gewichten_per_etf = {}
    for ticker in etf_tickers:
        gewichten_per_etf[ticker], _ = _holdings_gewicht_en_naam_per_bedrijf(ticker)

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


def _groepeer_europa_samen(land_dict, europese_landen=EUROPESE_LANDEN):
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
    """Bedragen in €. Sleutels:
      land, land_europa (Europa samengevoegd): {land: €}, kleine landen in 'Overig' (taart)
      sector: {sector: €}
      per_etf: {ticker: {land, sector (fracties 0-1), land_bron}}
      land_per_bron_top, land_per_bron_europa_top: {land: {bron: €}}, top 10 + 'Overig' (staaf)
      sector_per_bron: {sector: {bron: €}}
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
            # Niet-gedekt restant naar Unknown, zodat de totalen kloppen.
            sector_verdeling = get_etf_sector_verdeling(ticker)
            etf_sector_pct = dict(sector_verdeling)
            restant_sector = max(0.0, 1.0 - sum(sector_verdeling.values()))
            if restant_sector > 1e-9:
                etf_sector_pct["Unknown"] = etf_sector_pct.get("Unknown", 0.0) + restant_sector

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
    land_per_bron_europa = _groepeer_europa_samen_per_bron(land_per_bron)
    return {
        "land": _voeg_kleine_landen_samen(land),
        "land_europa": _voeg_kleine_landen_samen(
            land_europa_gegroepeerd,
            uitgezonderd={"Europe"} if "Europe" in land_europa_gegroepeerd else frozenset(),
        ),
        "sector": sector,
        "per_etf": per_etf,
        "land_per_bron_top": _beperk_tot_top_n_per_bron(land_per_bron),
        "land_per_bron_europa_top": _beperk_tot_top_n_per_bron(land_per_bron_europa),
        "sector_per_bron": sector_per_bron,
    }
