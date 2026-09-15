"""
Verdeling/Land/Sector/Bedrijven/ETF-overlap: portfoliobrede aggregaties over
alle holdings heen, voor de Verdeling-, Land-, Sector-, Top-20-bedrijven- en
ETF-overlap-tabbladen.

Losgetrokken uit analysis.py; ongewijzigd overgenomen.
"""
import re

from ticker_classificatie import get_etf_holdings, get_etf_sector_verdeling, get_land_sector

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


def bereken_bedrijven_verdeling(transacties_df, price_data, is_etf_map, top_n=10):
    """
    Top-N onderliggende bedrijven van de hele portfolio (via ETF's + losse
    aandelen), met per bedrijf een uitsplitsing van via welke posities
    (ETF-ticker of los aandeel) die blootstelling ontstaat -- zo blijft
    "dubbele blootstelling" zichtbaar als hetzelfde bedrijf zowel via een
    of meer ETF's als los wordt aangehouden. Zelfde basis als
    compute_land_sector_verdeling() hierboven: huidige holdings
    (aantal x laatste koers, de "aantal"-kolom, niet "adj_aantal") zodat
    de totalen op elkaar aansluiten. Voor de gestapelde-staafgrafiek-
    weergave op het "Top 10 bedrijven"-tabblad (zie static/js/app.js,
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


def _holdings_gewicht_en_naam_per_bedrijf(ticker):
    """
    Holdings van één ETF, samengevoegd per genormaliseerde bedrijfsnaam
    (_normaliseer_bedrijfsnaam) tot ({key: gewicht}, {key: weergavenaam}).
    Gedeeld door bereken_etf_overlap() (percentage-matrix) en
    bereken_etf_overlap_detail() (holdings-lijst voor één ETF-paar, zie
    opdracht "klikbaar overlap-percentage") zodat beide precies dezelfde
    "gedeeld bedrijf"-definitie gebruiken.
    """
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
    """
    Samengevoegde holdings-lijst voor één specifiek ETF-paar -- de
    detailtabel die verschijnt bij een klik op een percentage-cel in de
    overlap-matrix (zie opdracht "klikbaar overlap-percentage"). Per
    bedrijf dat in etf_a en/of etf_b zit: gewicht in elk van de twee
    (None als het bedrijf niet in dat fonds zit). Hergebruikt dezelfde
    _holdings_gewicht_en_naam_per_bedrijf()-opbouw als bereken_etf_overlap(),
    zodat een bedrijf hier als "gedeeld" telt precies wanneer het ook in de
    percentage-berekening meetelt.

    Gesorteerd aflopend op max(gewicht_a, gewicht_b) -- een ontbrekende kant
    telt daarbij als 0, niet als groter dan alles.
    """
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
