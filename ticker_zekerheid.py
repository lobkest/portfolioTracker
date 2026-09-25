"""
Ticker-zekerheid: de hoog-niveau orchestratie die bepaalt hoe zeker we zijn
dat een geresolveerde ticker de juiste is, op basis van prijsvergelijking
(ticker_prijscheck.py) en aanvullende OpenFIGI-/classificatiesignalen
(ticker_matching.py/ticker_classificatie.py). Voedt zowel de upload-flow
(lichte, standaard check) als de Ticker-zekerheid-pagina (volledige,
lui geladen check).

Losgetrokken uit analysis.py.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import get_db_connection
from debug_utils import dprint
from ticker_matching import (
    find_ticker_detailed, BEURS_MAP, _yahoo_search, haal_openfigi_resultaten, _openfigi_root_matches,
)
from ticker_prijscheck import vergelijk_prijs_op_datum, _prijscheck_is_probleem
from ticker_classificatie import (
    classify_ticker, get_land_sector, get_etf_sector_verdeling, get_etf_holdings, _ticker_details_met_cache,
)

# Drempel voor de standaard, LICHTE prijscontrole (find_ticker_met_snelle_
# prijscheck, i.t.t. de volledige verifieer_ticker_met_prijs verderop):
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


def _naar_basis_vorm(beurs, resultaat):
    """
    Wikkelt een find_ticker_met_snelle_prijscheck()-resultaat in dezelfde
    placeholder-vorm als verifieer_ticker_met_prijs(), zodat de frontend-
    kaart (maakTickerZekerheidKaart) dit zonder aanpassing kan tonen. Velden
    die alleen de VOLLEDIGE prijsverificatie kan invullen (land, sector,
    valuta, ...) staan hier bewust op None i.p.v. weggelaten.
    'prijs_checks'/'waarschuwing'/'aanbevolen_alternatief' komen wél uit de
    lichte check, dus een positie met een echte afwijking laat dat hier
    alsnog zien. Gebruikt door basis_ticker_zekerheid_parallel().
    """
    basis = {
        "ticker": resultaat["ticker"],
        "zekerheid": resultaat["zekerheid"],
        "waarschuwing": resultaat.get("prijswaarschuwing"),
        "is_etf": None, "land": None, "sector": None, "top_holding_land": None, "valuta": None,
        "fondsfamilie": None, "category": None, "quote_type": None,
        "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
        "prijs_checks": resultaat.get("prijs_checks", []), "alternatieven": [],
        "openfigi_root_bekend": resultaat.get("openfigi_root_bekend"),
        "openfigi_root_matches": resultaat.get("openfigi_root_matches"),
    }
    if resultaat.get("aanbevolen_alternatief"):
        basis["aanbevolen_alternatief"] = resultaat["aanbevolen_alternatief"]
    return basis


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
    Lichtgewicht ticker-zekerheid voor het 'niet opslaan'-pad in app.py:
    dezelfde vorm als verifieer_ticker_met_prijs() maar zonder de dure,
    volledige Yahoo-prijsverificatie (die alle 3 steekproefdatums én alle
    kandidaten doorrekent) -- gebruikt find_ticker_met_snelle_prijscheck(),
    dat in het gangbare geval (geen afwijking) maar 1 extra, gecachete
    Yahoo-call per positie kost. De VOLLEDIGE check mag daar niet
    standaard/synchroon voor de hele portfolio draaien (kan bij een grotere
    portfolio met een koude cache ruim over de gunicorn-timeout heen lopen);
    die blijft beschikbaar als losse, door de gebruiker aangevraagde actie.

    Parallel (ThreadPoolExecutor) -- zie
    vind_tickers_met_snelle_prijscheck_parallel() hieronder voor de reden:
    de lichte prijscheck draait bij ELKE upload, dus bij een portfolio met
    veel unieke, nog nooit gecontroleerde tickers (koude ticker_prijscheck-cache)
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


def _verrijk_met_openfigi_kandidaten(alternatieven_kandidaten, gekozen_ticker, isin):
    """
    Vult 'alternatieven_kandidaten' aan met kandidaten die via OpenFIGI's
    ISIN-lookup bekend zijn, maar die de bestaande Yahoo-zoekopdrachten
    (find_ticker_detailed()'s restlijst, eventueel al aangevuld door
    _verzamel_extra_kandidaten()) nog niet opleverden — voor fondsen die
    Yahoo's zoekindex op productnaam nauwelijks vindt (bv. een minder
    liquide AEX-fonds), terwijl OpenFIGI de notering wel kent.

    Voor elke unieke OpenFIGI-ticker-root die nog niet voorkomt tussen de
    roots van 'gekozen_ticker' + 'alternatieven_kandidaten' (root = het deel
    vóór een eventuele Yahoo-beurssuffix, bv. 'BY6' uit 'BY6.MU' — zelfde
    root-vorm als _openfigi_root_matches() in ticker_matching.py gebruikt),
    wordt één Yahoo-zoekopdracht gedaan MET DIE ROOT als zoekterm. Geen
    handmatige Bloomberg-exchange-code -> Yahoo-suffix-tabel (die twee
    systemen komen niet 1-op-1 overeen, zie de toelichting bij
    MANUAL_TICKER_OVERRIDES_ISIN in ticker_matching.py) — Yahoo zelf bepaalt
    welk koersbaar symbool bij die root hoort. Maximaal 1 Yahoo-call per
    unieke OpenFIGI-root, niet per OpenFIGI-resultaat.

    Alleen aangeroepen vanuit verifieer_ticker_met_prijs() wanneer de match
    al 'onzeker' is -- zelfde terughoudendheid als _verzamel_extra_
    kandidaten() hierboven, om niet bij elke upload extra calls te maken
    (deze functie wordt uitsluitend gebruikt door de lui geladen, volledige
    Ticker-zekerheid-pagina-check, niet door find_ticker_met_snelle_
    prijscheck()). Dankzij haal_openfigi_resultaten()'s permanente cache
    kost de OpenFIGI-lookup zelf hier geen extra externe call zodra deze
    ISIN al eens opgehaald is.

    Geeft (extra, debug) terug:
      extra: NIEUWE lijst (alternatieven_kandidaten + eventuele OpenFIGI-
        gevonden extra's), in hetzelfde {"symbol","exchange"}-formaat.
      debug: __TIJDELIJK, diagnostisch__ (makkelijk te verwijderen samen
        met het 'openfigi_kandidaten_debug'-veld in verifieer_ticker_met_prijs()) —
        dict met 'roots' (alle unieke OpenFIGI-roots voor deze ISIN),
        'nieuwe_roots' (roots die een Yahoo-zoekopdracht triggerden),
        'overgeslagen_roots' (roots die al bekend waren, dus overgeslagen),
        'yahoo_resultaten' ({root: ruwe [{"symbol","exchange"}, ...]} per
        doorzochte nieuwe root, vóór filtering op reeds-bekende symbolen).
    """
    openfigi = haal_openfigi_resultaten(isin)
    if not openfigi["resultaten"]:
        return list(alternatieven_kandidaten), {
            "roots": [], "nieuwe_roots": [], "overgeslagen_roots": [], "yahoo_resultaten": {},
        }

    alle_roots = []
    for r in openfigi["resultaten"]:
        root = (r.get("ticker") or "").upper()
        if root and root not in alle_roots:
            alle_roots.append(root)

    bekende_symbols = {gekozen_ticker} | {a.get("symbol") for a in alternatieven_kandidaten}
    bekende_roots = {s.split(".")[0].upper() for s in bekende_symbols if s}

    nieuwe_roots = [r for r in alle_roots if r not in bekende_roots]
    overgeslagen_roots = [r for r in alle_roots if r in bekende_roots]

    extra = list(alternatieven_kandidaten)
    yahoo_resultaten = {}
    for root in nieuwe_roots:
        quotes = _yahoo_search(root)
        yahoo_resultaten[root] = [{"symbol": q.get("symbol"), "exchange": q.get("exchange")} for q in quotes]
        for q in quotes:
            symbol = q.get("symbol")
            if not symbol or symbol in bekende_symbols:
                continue
            bekende_symbols.add(symbol)
            extra.append({"symbol": symbol, "exchange": q.get("exchange")})

    dprint(
        f"[alternatieven-openfigi] ISIN={isin}: {len(nieuwe_roots)} nieuwe OpenFIGI-root(s) "
        f"{nieuwe_roots} doorzocht, {len(extra) - len(alternatieven_kandidaten)} nieuwe "
        f"kandidaat/kandidaten gevonden."
    )
    debug = {
        "roots": alle_roots,
        "nieuwe_roots": nieuwe_roots,
        "overgeslagen_roots": overgeslagen_roots,
        "yahoo_resultaten": yahoo_resultaten,
    }
    return extra, debug


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
        # de Ticker-zekerheid-pagina (High/Low i.p.v. land/sector).
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
    toelichting daar — en vervolgens met kandidaten uit OpenFIGI's
    ISIN-lookup die nog niet in de lijst zitten (_verrijk_met_openfigi_
    kandidaten()), voor fondsen die Yahoo's zoekindex op productnaam
    nauwelijks vindt. 'openfigi_kandidaten_debug' (__TIJDELIJK__, zie
    _verrijk_met_openfigi_kandidaten()'s docstring) laat zien of/hoe die
    laatste stap draaide, voor de Ticker-zekerheid-pagina.

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
    # __TIJDELIJK, diagnostisch__: laat op de Ticker-zekerheid-pagina zien
    # of _verrijk_met_openfigi_kandidaten() hieronder daadwerkelijk draait,
    # en zo ja met welke roots/resultaten -- makkelijk te verwijderen samen
    # met het 'openfigi_kandidaten_debug'-veld in 'result' hieronder en het
    # bijbehorende blok in static/js/app.js (maakOpenfigiKandidatenDebugBlok).
    openfigi_kandidaten_debug = {
        "aangeroepen": False,
        "reden": "ticker al 'zeker' -- alternatieven worden niet doorgerekend",
    }
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
        alternatieven_kandidaten, openfigi_debug_info = _verrijk_met_openfigi_kandidaten(
            alternatieven_kandidaten, ticker, isin
        )
        openfigi_kandidaten_debug = {"aangeroepen": True, **openfigi_debug_info}
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
        "openfigi_kandidaten_debug": openfigi_kandidaten_debug,
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
    bepalen"-vinkje op het uploadscherm (zie _ticker_resolutie_opslaan_pad
    in upload_verwerking.py): staat
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
    automatisch overgenomen (ticker/zekerheid worden dan overschreven):
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
    # (regressie t.o.v. het G2X.MU-geval):
    # _prijscheck_is_probleem() geeft bij ontbrekende data GEEN probleem
    # terug (match=None), dus die check hier expliciet ervoor houden.
    escaleert = check_laatste["afwijking_pct"] is None or _prijscheck_is_probleem(check_laatste)

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
            resultaat["ticker"] = gekozen["ticker"]
            resultaat["zekerheid"] = "zeker"
            resultaat["prijswaarschuwing"] = None
        elif aanbevolen_alternatief:
            # Geen van beide tiers voldoende bewijs -- bestaand gedrag: alleen
            # tonen als suggestie, niets automatisch overnemen.
            resultaat["aanbevolen_alternatief"] = aanbevolen_alternatief

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
            continue  # oude ticker werkt prima, niets te backfillen

        nieuw = find_ticker_met_snelle_prijscheck(info["naam"], isin, beurs, transacties)
        nieuwe_ticker = nieuw["ticker"]
        if not nieuwe_ticker or nieuwe_ticker == oude_ticker:
            continue

        if _ticker_heeft_prijsprobleem(nieuwe_ticker, transacties):
            continue

        cur.execute(
            "UPDATE transacties SET ticker = %s WHERE code = %s AND isin = %s AND beurs = %s",
            (nieuwe_ticker, code, isin, beurs),
        )
        gecorrigeerd += 1

    conn.commit()
    cur.close()
    conn.close()
    return gecorrigeerd


def prijswaarschuwing_voor_ticker(ticker, transacties_van_dit_isin, isin=None):
    """
    Leest (via vergelijk_prijs_op_datum's eigen ticker_prijscheck-cache) of
    de laatste transactieprijs van deze positie afwijkt van Yahoo — voor
    gebruik bij ELK bezoek aan een opgeslagen portfolio (via
    ticker_waarschuwingen_voor_transacties), niet alleen direct na de upload. Roept BEWUST
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
    return f"{boodschap}\n{extra_waarschuwing}" if boodschap else extra_waarschuwing


def ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen):
    """
    Verzamelt prijswaarschuwing_voor_ticker()-meldingen voor elke unieke
    ticker in transacties_df — gebruikt door analyze_transacties_kern()
    (portfolio_orchestratie.py) bij ELK bezoek aan een portfolio (niet
    alleen direct na de upload), zie
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


