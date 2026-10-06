"""Hoe zeker is een ticker: lichte check bij elke upload, volledige check op de Ticker-zekerheid-pagina."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import db_connect, db_get_transacties_voor_tickercheck, db_wijzig_ticker
from debug_utils import dprint
from ticker_matching import (
    find_ticker_detailed, BEURS_MAP, AMERIKAANSE_BEURZEN, OTC_BEURZEN, _yahoo_search, haal_openfigi_resultaten, _openfigi_root_matches,
)
from ticker_prijscheck import vergelijk_prijs_op_datum, _prijscheck_is_probleem
from transactie_utils import formatteer_datum_nl
from ticker_classificatie import (
    classify_ticker, get_land_sector, get_etf_sector_verdeling, get_etf_holdings, _ticker_details_met_cache,
)

# Tier 1: verwachte beurs en de prijs klopt op minstens zoveel datums.
MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE = 2

# Tier 2 (andere beurs, alle datums kloppen): pas vanaf zoveel datums, anders telt één toevalstreffer.
MIN_STEEKPROEF_VOOR_VOLLEDIGE_MATCH = 2


def _naar_basis_vorm(beurs, resultaat):
    """Lichte-check-resultaat in de vorm van verifieer_ticker_met_prijs(); velden van de volledige check op None."""
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


# Gemeten, zie docs/CODE_OVERZICHT.md (6.2).
TICKER_RESOLUTIE_POOL_GROOTTE = 12


def basis_ticker_zekerheid_parallel(posities, max_workers=TICKER_RESOLUTIE_POOL_GROOTTE):
    """Lichte check voor 'niet opslaan', parallel; ook sequentieel 1 call per positie kan de timeout halen."""
    ruwe_resultaten = vind_tickers_met_snelle_prijscheck_parallel(posities, max_workers=max_workers)
    return [
        _naar_basis_vorm(beurs, resultaat)
        for (_product, _isin, beurs, _transacties), resultaat in zip(posities, ruwe_resultaten)
    ]


def _geldige_transacties(transacties_van_dit_isin):
    """Zonder splitrijen (koers 0 of leeg)."""
    return [t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0]


def _grootste_afwijking(prijs_checks):
    return max((c["afwijking_pct"] for c in prijs_checks if c["afwijking_pct"] is not None), default=None)


def _kies_steekproef_transacties(transacties_van_dit_isin, aantal=3):
    """Eerste, middelste en laatste transactie met koers > 0."""
    kandidaten = sorted(_geldige_transacties(transacties_van_dit_isin), key=lambda t: t["datum"])
    if len(kandidaten) <= aantal:
        return kandidaten
    indices = sorted({0, len(kandidaten) // 2, len(kandidaten) - 1})
    return [kandidaten[i] for i in indices]


def _sector_samenvatting(ticker, top_n=3):
    """Bv. 'Technology (37%), Financial Services (12%)'; sectoren op 0% tellen niet mee."""
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
    holdings = get_etf_holdings(ticker)
    if not holdings:
        return None
    grootste = max(holdings, key=lambda h: h["gewicht"])
    return grootste.get("land")


def _land_sector_voor_weergave(ticker):
    """(land, sector, top_holding_land). Een ETF heeft geen eigen land: dan land None en sectoren als tekst."""
    if classify_ticker(ticker):
        return None, _sector_samenvatting(ticker), _top_holding_land(ticker)

    land, sector = get_land_sector(ticker)
    return land, sector, None


def _verzamel_extra_kandidaten(product, isin, bestaande_alternatieven, uitgesloten_ticker):
    """Nieuwe [{symbol, exchange}] uit een zoekopdracht op naam en ISIN zonder beurs-beperking."""
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


def _verrijk_met_openfigi_kandidaten(alternatieven_kandidaten, gekozen_ticker, isin, openfigi=None):
    """Per nieuwe OpenFIGI-ticker-root één Yahoo-zoekopdracht; Yahoo kiest het koersbare symbool.
    Geeft (kandidaten, debug); debug is __TIJDELIJK, diagnostisch__ (zie CLAUDE.md: Yahoo en tickers)."""
    if openfigi is None:
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
    """Rekent kandidaten door tot een overtuigende match (juiste beurs, alle datums kloppen).
    Geeft (alternatieven, aanbevolen_alternatief: eerste die op alle datums klopt, of None)."""
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
            # Geen koersdata betekent meestal helemaal geen historie; verder proberen kost alleen tijd.
            if check["yahoo_koers"] is None:
                break

        alt_matches = [not _prijscheck_is_probleem(c) for c in alt_checks if c["match"] is not None]
        alt_details = _ticker_details_met_cache(alt_ticker)
        alt_is_etf = classify_ticker(alt_ticker)
        alt_land, alt_sector, _alt_top_holding_land = _land_sector_voor_weergave(alt_ticker)
        alt_beurs_klopt = (alt.get("exchange") in verwachte_beurzen) if verwachte_beurzen else None
        # Meest recente dagrange, voor de ETF-weergave (high/low i.p.v. land/sector).
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
            "aantal_matches": sum(1 for m in alt_matches if m),
            "aantal_gecontroleerd": len(alt_matches),
        })

        wordt_aanbevolen = aanbevolen_alternatief is None and alt_matches and all(alt_matches)
        dprint(
            f"[alternatieven-debug] alt_ticker={alt_ticker} exchange={alt.get('exchange')} "
            f"land={alt_land} sector={alt_sector} valuta={alt_details.get('valuta')} "
            f"alt_matches={alt_matches} wordt_aanbevolen={wordt_aanbevolen}"
        )

        if aanbevolen_alternatief is None and alt_matches and all(alt_matches):
            aanbevolen_alternatief = alt_ticker

        if alt_beurs_klopt and alt_matches and all(alt_matches):
            break  # beter wordt het niet; verder zoeken kost alleen Yahoo-calls

    return alternatieven, aanbevolen_alternatief


def _leeg_resultaat(zekerheid, beurs):
    """Alle velden die de kaart op de Ticker-zekerheid-pagina toont, nog zonder inhoud."""
    return {
        "ticker": None, "zekerheid": zekerheid, "waarschuwing": None,
        "is_etf": None, "land": None, "sector": None, "top_holding_land": None, "valuta": None,
        "fondsfamilie": None, "category": None, "quote_type": None,
        "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
        "prijs_checks": [], "alternatieven": [],
    }


def _voeg_prijsoordeel_toe(resultaat, steekproef):
    """Checkt de steekproef; een "zekere" ticker zonder (kloppende) koersdata wordt "onzeker" met een waarschuwing."""
    ticker = resultaat["ticker"]
    prijs_checks = []
    for t in steekproef:
        check = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        check["datum"] = str(t["datum"])
        prijs_checks.append(check)

    bekende_checks = [c for c in prijs_checks if c["match"] is not None]
    problemen = [c for c in bekende_checks if _prijscheck_is_probleem(c)]

    zekerheid = resultaat["zekerheid"]
    waarschuwing = None
    if zekerheid == "zeker" and not bekende_checks:
        zekerheid = "onzeker"
        waarschuwing = (
            f"Geen koersdata gevonden bij Yahoo voor '{ticker}' — "
            f"mogelijk een verkeerde of niet-bestaande ticker."
        )
    elif zekerheid == "zeker" and problemen:
        zekerheid = "onzeker"
        buiten = [c for c in problemen if c.get("binnen_dagrange") is False]
        if buiten:
            vroegste = min(buiten, key=lambda c: c["datum"])
            waarschuwing = (
                f"Beurs komt overeen, maar {len(buiten)} van de {len(bekende_checks)} gecontroleerde "
                f"datums valt buiten de dagrange (o.a. op {formatteer_datum_nl(vroegste['datum'])}) "
                f"— mogelijk toch de verkeerde ticker."
            )
        else:
            grootste = max(problemen, key=lambda c: c["afwijking_pct"])
            waarschuwing = (
                f"Beurs komt overeen, maar de koers wijkt op {len(problemen)} van de {len(bekende_checks)} "
                f"gecontroleerde datums af van Yahoo (grootste afwijking {grootste['afwijking_pct']:.1f}% op "
                f"{formatteer_datum_nl(grootste['datum'])}) — mogelijk toch de verkeerde ticker."
            )
    return {**resultaat, "prijs_checks": prijs_checks, "zekerheid": zekerheid, "waarschuwing": waarschuwing}


BEURS_OTC_NA_DELISTING = "otc_na_delisting"


def beurs_status(excel_beurs, yahoo_beurs, prijs_checks):
    """True/False/None, of BEURS_OTC_NA_DELISTING: Amerikaanse beurs in Excel, OTC bij Yahoo
    en alle bekende prijschecks kloppen (zoals XELA na de delisting van Nasdaq)."""
    verwachte_beurzen = BEURS_MAP.get(excel_beurs, [])
    if not (excel_beurs and yahoo_beurs and verwachte_beurzen):
        return None
    if yahoo_beurs in verwachte_beurzen:
        return True
    if excel_beurs in AMERIKAANSE_BEURZEN and yahoo_beurs in OTC_BEURZEN:
        bekende_checks = [c for c in prijs_checks if c["match"] is not None]
        if bekende_checks and not any(_prijscheck_is_probleem(c) for c in bekende_checks):
            return BEURS_OTC_NA_DELISTING
    return False


def _voeg_kaartvelden_toe(resultaat, beurs):
    ticker = resultaat["ticker"]
    details = _ticker_details_met_cache(ticker)
    land, sector, top_holding_land = _land_sector_voor_weergave(ticker)
    yahoo_beurs = details.get("yahoo_beurs")
    beurs_klopt = beurs_status(beurs, yahoo_beurs, resultaat["prijs_checks"])
    zekerheid = resultaat["zekerheid"]
    # "onzeker" kwam dan alleen doordat het zoeken geen beurs-match vond; de prijs is al bevestigd.
    if beurs_klopt == BEURS_OTC_NA_DELISTING and zekerheid == "onzeker":
        zekerheid = "zeker"
    return {
        **resultaat,
        "zekerheid": zekerheid,
        "is_etf": classify_ticker(ticker),
        "land": land,
        "sector": sector,
        "top_holding_land": top_holding_land,
        "valuta": details.get("valuta"),
        "fondsfamilie": details.get("fund_family"),
        "category": details.get("category"),
        "quote_type": details.get("quote_type"),
        "yahoo_beurs": yahoo_beurs,
        "beurs_klopt": beurs_klopt,
    }


def _voeg_alternatieven_toe(resultaat, basis, product, isin, beurs, steekproef, openfigi=None):
    """Extra Yahoo-calls, dus alleen als de ticker niet "zeker" is."""
    # __TIJDELIJK, diagnostisch__: samen met maakOpenfigiKandidatenDebugBlok (tabs/ticker_zekerheid.js) verwijderen.
    if resultaat["zekerheid"] == "zeker":
        return {**resultaat, "openfigi_kandidaten_debug": {
            "aangeroepen": False,
            "reden": "ticker al 'zeker' -- alternatieven worden niet doorgerekend",
        }}

    kandidaten = list(basis["alternatieven"])
    # De restlijst van de zoekopdracht kan leeg zijn (BYD); alleen dan extra zoeken.
    if not kandidaten:
        kandidaten += _verzamel_extra_kandidaten(product, isin, kandidaten, resultaat["ticker"])
    kandidaten, openfigi_debug_info = _verrijk_met_openfigi_kandidaten(
        kandidaten, resultaat["ticker"], isin, openfigi
    )
    alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
        kandidaten, steekproef, BEURS_MAP.get(beurs, [])
    )
    uitgebreid = {
        **resultaat,
        "alternatieven": alternatieven,
        "openfigi_kandidaten_debug": {"aangeroepen": True, **openfigi_debug_info},
    }
    if aanbevolen_alternatief:
        uitgebreid["aanbevolen_alternatief"] = aanbevolen_alternatief
    return uitgebreid


def verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin):
    """Volledige check (duur; alleen op de Ticker-zekerheid-pagina): alles wat de kaart toont,
    inclusief doorgerekende alternatieven."""
    basis = find_ticker_detailed(product, isin, beurs)
    resultaat = _leeg_resultaat(basis["zekerheid"], beurs)
    if basis["ticker"] is None:
        return _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="waarschuwing")

    steekproef = _kies_steekproef_transacties(transacties_van_dit_isin)
    resultaat = {**resultaat, "ticker": basis["ticker"]}
    resultaat = _voeg_prijsoordeel_toe(resultaat, steekproef)
    resultaat = _voeg_kaartvelden_toe(resultaat, beurs)
    # Vóór de alternatieven: een ontbrekende root maakt "zeker" onzeker, en dan moeten ze wél doorgerekend.
    openfigi = haal_openfigi_resultaten(isin)
    resultaat = _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="waarschuwing", openfigi=openfigi)
    return _voeg_alternatieven_toe(resultaat, basis, product, isin, beurs, steekproef, openfigi)


def _openfigi_root_oordeel(ticker, openfigi):
    """(matches, root_bekend); root_bekend None = geen oordeel (geen ticker, geen resultaten of fout)."""
    if not ticker or openfigi is None:
        return None, None
    matches = _openfigi_root_matches(ticker, openfigi["resultaten"])
    return matches, (None if matches is None else matches > 0)


def _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="prijswaarschuwing", openfigi=None):
    """Zet altijd openfigi_root_bekend/_matches. Root niet gevonden: waarschuwing erbij (nieuwe regel)
    en "zeker" wordt "onzeker"; de ticker zelf verandert nooit."""
    ticker = resultaat.get("ticker")
    if not ticker:
        resultaat["openfigi_root_bekend"] = None
        resultaat["openfigi_root_matches"] = None
        return resultaat

    if openfigi is None:
        openfigi = haal_openfigi_resultaten(isin)
    matches, root_bekend = _openfigi_root_oordeel(ticker, openfigi)
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


def _begin_resultaat(product, isin, beurs, bekende_ticker):
    if bekende_ticker:
        basis = {"ticker": bekende_ticker, "zekerheid": "zeker", "alternatieven": []}
    else:
        basis = find_ticker_detailed(product, isin, beurs)
    return {**basis, "prijs_checks": [], "prijswaarschuwing": None}


def _voeg_prijscheck_laatste_toe(resultaat, geldige_transacties):
    if resultaat["ticker"] is None or not geldige_transacties:
        return resultaat
    laatste = max(geldige_transacties, key=lambda t: t["datum"])
    check = vergelijk_prijs_op_datum(resultaat["ticker"], laatste["datum"], float(laatste["koers"]))
    check["datum"] = str(laatste["datum"])
    return {**resultaat, "prijs_checks": [check]}


def _moet_escaleren(resultaat):
    # "Geen koersdata" apart: _prijscheck_is_probleem() ziet dat niet als probleem.
    if not resultaat["prijs_checks"]:
        return False
    check_laatste = resultaat["prijs_checks"][0]
    return check_laatste["afwijking_pct"] is None or _prijscheck_is_probleem(check_laatste)


def _voeg_steekproef_toe(resultaat, geldige_transacties):
    """Checkt de rest van de steekproef, zodat één uitschieter geen foute ticker maakt, en zet de waarschuwing."""
    ticker = resultaat["ticker"]
    prijs_checks = list(resultaat["prijs_checks"])
    laatste_datum = prijs_checks[0]["datum"]
    for t in _kies_steekproef_transacties(geldige_transacties):
        if str(t["datum"]) == laatste_datum:
            continue
        c = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        c["datum"] = str(t["datum"])
        prijs_checks.append(c)

    return {
        **resultaat,
        "zekerheid": "onzeker" if resultaat["zekerheid"] == "zeker" else resultaat["zekerheid"],
        "prijs_checks": prijs_checks,
        "prijswaarschuwing": _steekproef_waarschuwing(ticker, prijs_checks),
    }


def _steekproef_waarschuwing(ticker, prijs_checks):
    """Dagrange-tekst waar mogelijk; de %-tekst alleen als geen enkele check een dagrange heeft."""
    buiten = [c for c in prijs_checks if c.get("binnen_dagrange") is False]
    if buiten:
        vroegste = min(buiten, key=lambda c: c["datum"])
        return (
            f"Koers van {ticker} valt op {formatteer_datum_nl(vroegste['datum'])} buiten de dagrange (high/low) van "
            f"Yahoo — controleer op het Ticker-zekerheid-tabblad."
        )
    grootste_afwijking = _grootste_afwijking(prijs_checks)
    if grootste_afwijking is None:
        return (
            f"Geen koersdata gevonden bij Yahoo voor '{ticker}' — "
            f"controleer op het Ticker-zekerheid-tabblad."
        )
    if not any(c.get("binnen_dagrange") is not None for c in prijs_checks):
        return (
            f"Koers van {ticker} wijkt {grootste_afwijking:.1f}% af van Yahoo — "
            f"controleer op het Ticker-zekerheid-tabblad."
        )
    return (
        f"Koers van {ticker} kon niet op alle gecontroleerde datums met de dagrange van Yahoo "
        f"vergeleken worden — controleer op het Ticker-zekerheid-tabblad."
    )


def _root_in_openfigi(ticker, openfigi):
    return _openfigi_root_oordeel(ticker, openfigi)[1] is True


def _corrigeer_met_alternatief(resultaat, geldige_transacties, beurs, isin=None, openfigi=None):
    """Rekent bij een prijsprobleem (buiten de dagrange of geen koersdata) of een ontbrekende OpenFIGI-root de
    alternatieven door; vervangt de ticker automatisch bij tier 1 of 2, anders hooguit een suggestie (zie CLAUDE.md:
    Yahoo en tickers). Alleen een root-mismatch: vervangen pas als alle datums kloppen én beurs of root klopt."""
    bekende_checks = [c for c in resultaat["prijs_checks"] if c["match"] is not None]
    prijsprobleem = not bekende_checks or any(_prijscheck_is_probleem(c) for c in bekende_checks)
    root_ontbreekt = _openfigi_root_oordeel(resultaat["ticker"], openfigi)[1] is False
    if not prijsprobleem and not root_ontbreekt:
        return resultaat

    steekproef = _kies_steekproef_transacties(geldige_transacties)
    verwachte_beurzen = BEURS_MAP.get(beurs, [])
    kandidaten = list(resultaat["alternatieven"])
    if root_ontbreekt:
        kandidaten, _debug = _verrijk_met_openfigi_kandidaten(kandidaten, resultaat["ticker"], isin, openfigi)
    alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(kandidaten, steekproef, verwachte_beurzen)

    if not prijsprobleem:
        gekozen = next(
            (a for a in alternatieven
             if steekproef and a.get("aantal_matches") == len(steekproef)
             and (a.get("beurs") in verwachte_beurzen or _root_in_openfigi(a["ticker"], openfigi))),
            None,
        )
        if gekozen:
            return {**resultaat, "ticker": gekozen["ticker"], "zekerheid": "zeker", "prijswaarschuwing": None}
        if aanbevolen_alternatief:
            return {**resultaat, "aanbevolen_alternatief": aanbevolen_alternatief}
        return resultaat

    # Tier 1. Alternatieven hebben geen 'beurs_klopt'-veld, dus zelf vergelijken.
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
        return {**resultaat, "ticker": gekozen["ticker"], "zekerheid": "zeker", "prijswaarschuwing": None}
    if aanbevolen_alternatief:
        return {**resultaat, "aanbevolen_alternatief": aanbevolen_alternatief}
    return resultaat


def find_ticker_met_snelle_prijscheck(product, isin, beurs, transacties_van_dit_isin, bekende_ticker=None):
    """Lichte check bij elke upload; normaal 1 gecachete Yahoo-call. Escalatie en tiers: zie CLAUDE.md: Yahoo en tickers.
    Met bekende_ticker wordt de zoekopdracht overgeslagen (dan geen alternatieven; de backfill vangt fouten op).
    Geeft find_ticker_detailed()-velden plus prijs_checks en prijswaarschuwing."""
    geldige_transacties = _geldige_transacties(transacties_van_dit_isin)

    resultaat = _begin_resultaat(product, isin, beurs, bekende_ticker)
    resultaat = _voeg_prijscheck_laatste_toe(resultaat, geldige_transacties)
    openfigi = haal_openfigi_resultaten(isin) if resultaat["ticker"] else None
    root_ontbreekt = _openfigi_root_oordeel(resultaat["ticker"], openfigi)[1] is False
    escaleren = _moet_escaleren(resultaat)
    if escaleren:
        resultaat = _voeg_steekproef_toe(resultaat, geldige_transacties)
    if escaleren or (root_ontbreekt and geldige_transacties):
        resultaat = _corrigeer_met_alternatief(resultaat, geldige_transacties, beurs, isin, openfigi)
    # Pas hier: het oordeel moet over de uiteindelijke (eventueel vervangen) ticker gaan.
    return _voeg_openfigi_check_toe(resultaat, isin, openfigi=openfigi)


def _ticker_heeft_prijsprobleem(ticker, transacties_van_dit_isin):
    """Probleem op de laatste transactiedatum, inclusief 'geen koersdata'. Geen ticker telt als probleem."""
    if not ticker:
        return True
    geldige = _geldige_transacties(transacties_van_dit_isin)
    if not geldige:
        return False
    laatste = max(geldige, key=lambda t: t["datum"])
    check = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    probleem = check["afwijking_pct"] is None or _prijscheck_is_probleem(check)
    return probleem


def backfill_verouderde_tickers(code):
    """Herzoekt elke opgeslagen ticker; vervangt alleen door een kandidaat zonder prijsprobleem.
    Geeft het aantal gecorrigeerde groepen."""
    conn = db_connect()
    cur = conn.cursor()
    rows = db_get_transacties_voor_tickercheck(cur, code)

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
        nieuw = find_ticker_met_snelle_prijscheck(info["naam"], isin, beurs, transacties)
        nieuwe_ticker = nieuw["ticker"]
        if not nieuwe_ticker or nieuwe_ticker == oude_ticker:
            continue

        if _ticker_heeft_prijsprobleem(nieuwe_ticker, transacties):
            continue

        db_wijzig_ticker(cur, code, isin, beurs, nieuwe_ticker)
        gecorrigeerd += 1

    conn.commit()
    cur.close()
    conn.close()
    return gecorrigeerd


def prijscheck_laatste(ticker, transacties_van_dit_isin):
    """Prijscheck (met 'datum') op de laatste geldige transactie, of None. Normaal een cache-hit."""
    geldige_transacties = _geldige_transacties(transacties_van_dit_isin)
    if not ticker or not geldige_transacties:
        return None
    laatste = max(geldige_transacties, key=lambda t: t["datum"])
    check = vergelijk_prijs_op_datum(ticker, laatste["datum"], float(laatste["koers"]))
    return {**check, "datum": laatste["datum"]}


def prijswaarschuwing_delen(ticker, transacties_van_dit_isin, isin=None, check=None):
    """{koers, openfigi}: per reden een tekst of None. Voor elk bezoek: geen live zoekopdracht, normaal een cache-hit.
    Met isin ook de OpenFIGI-root-check; check: al berekende prijscheck_laatste()."""
    if check is None:
        check = prijscheck_laatste(ticker, transacties_van_dit_isin)
    if check is None or check["afwijking_pct"] is None or not _prijscheck_is_probleem(check):
        boodschap = None
    elif check.get("binnen_dagrange") is False:
        boodschap = (
            f"Koers van {ticker} valt op {formatteer_datum_nl(check['datum'])} buiten de dagrange (high/low) van "
            f"Yahoo — controleer op het Ticker-zekerheid-tabblad."
        )
    else:
        boodschap = (
            f"Koers van {ticker} wijkt {check['afwijking_pct']:.1f}% af van Yahoo — "
            f"controleer op het Ticker-zekerheid-tabblad."
        )

    delen = {"koers": boodschap, "openfigi": None}
    if not ticker or not isin:
        return delen

    openfigi = haal_openfigi_resultaten(isin)
    matches = _openfigi_root_matches(ticker, openfigi["resultaten"])
    if matches is None or matches > 0:
        return delen

    delen["openfigi"] = (
        f"Ticker-root '{ticker.split('.')[0]}' komt niet voor in OpenFIGI's "
        f"resultaten voor deze ISIN — controleer op het Ticker-zekerheid-tabblad."
    )
    return delen


def prijswaarschuwing_voor_ticker(ticker, transacties_van_dit_isin, isin=None):
    """Waarschuwingstekst (alle redenen onder elkaar) of None."""
    delen = prijswaarschuwing_delen(ticker, transacties_van_dit_isin, isin)
    return "\n".join(t for t in delen.values() if t) or None


def ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen):
    """(waarschuwingen, prijs_checks). waarschuwingen: [{ticker, naam, boodschap, redenen}], redenen ⊆
    ["koers", "openfigi"]; prijs_checks: {ticker: [prijscheck_laatste()]} voor Diagnostiek.
    Zonder 'isin'-kolom geen OpenFIGI-check."""
    heeft_isin_kolom = "isin" in transacties_df.columns
    waarschuwingen = []
    prijs_checks = {}
    for ticker, groep in transacties_df.dropna(subset=["ticker"]).groupby("ticker"):
        transacties_van_ticker = [{"datum": d, "koers": k} for d, k in zip(groep["datum"], groep["koers"])]
        isin = groep["isin"].iloc[0] if heeft_isin_kolom and not groep.empty else None
        check = prijscheck_laatste(ticker, transacties_van_ticker)
        prijs_checks[ticker] = [check] if check else []
        delen = prijswaarschuwing_delen(ticker, transacties_van_ticker, isin, check=check)
        redenen = [reden for reden, tekst in delen.items() if tekst]
        if redenen:
            waarschuwingen.append({
                "ticker": ticker, "naam": ticker_namen.get(ticker, ticker),
                "boodschap": "\n".join(delen[r] for r in redenen), "redenen": redenen,
            })
    return waarschuwingen, prijs_checks


def vind_tickers_met_snelle_prijscheck_parallel(posities, bekende_tickers=None,
                                                 max_workers=TICKER_RESOLUTIE_POOL_GROOTTE):
    """posities: (product, isin, beurs, transacties); bekende_tickers: {(isin, beurs): ticker}.
    Resultaten in de volgorde van 'posities'."""
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
    """Parallel: sequentieel liep dit over de gunicorn-timeout (zie docs/CODE_OVERZICHT.md, 6.2).
    Resultaten in de volgorde van 'posities'."""
    resultaten = [None] * len(posities)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_naar_index = {
            executor.submit(verifieer_ticker_met_prijs, naam, isin, beurs, transacties): i
            for i, (naam, isin, beurs, transacties) in enumerate(posities)
        }
        for future in as_completed(future_naar_index):
            resultaten[future_naar_index[future]] = future.result()
    return resultaten


