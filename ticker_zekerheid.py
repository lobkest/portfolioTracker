"""Hoe zeker is een ticker: lichte check bij elke upload, volledige check op de Ticker-zekerheid-pagina."""
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

# Lichte check: pas boven deze afwijking worden alternatieven doorgerekend.
PRIJSCHECK_DREMPEL_ALTERNATIEVEN = 0.10

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


def _kies_steekproef_transacties(transacties_van_dit_isin, aantal=3):
    """Eerste, middelste en laatste transactie met koers > 0."""
    kandidaten = sorted(
        (t for t in transacties_van_dit_isin if t.get("koers") and float(t["koers"]) > 0),
        key=lambda t: t["datum"],
    )
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


def _verrijk_met_openfigi_kandidaten(alternatieven_kandidaten, gekozen_ticker, isin):
    """Per nieuwe OpenFIGI-ticker-root één Yahoo-zoekopdracht; Yahoo kiest het koersbare symbool.
    Geeft (kandidaten, debug); debug is __TIJDELIJK, diagnostisch__ (zie CLAUDE.md: Yahoo en tickers)."""
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

        alt_matches = [c["match"] for c in alt_checks if c["match"] is not None]
        afwijkingen = [c["afwijking_pct"] for c in alt_checks if c["afwijking_pct"] is not None]
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
            break  # beter wordt het niet; verder zoeken kost alleen Yahoo-calls

    return alternatieven, aanbevolen_alternatief


def verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin):
    """Volledige check (duur; alleen op de Ticker-zekerheid-pagina): alles wat de kaart toont,
    inclusief doorgerekende alternatieven."""
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

    # Alternatieven (extra Yahoo-calls) alleen als de match niet "zeker" is.
    alternatieven = []
    aanbevolen_alternatief = None
    # __TIJDELIJK, diagnostisch__: samen met maakOpenfigiKandidatenDebugBlok (app.js) verwijderen.
    openfigi_kandidaten_debug = {
        "aangeroepen": False,
        "reden": "ticker al 'zeker' -- alternatieven worden niet doorgerekend",
    }
    if zekerheid != "zeker":
        alternatieven_kandidaten = list(basis["alternatieven"])
        # De restlijst van de zoekopdracht kan leeg zijn (BYD); alleen dan extra zoeken.
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
    """Zet altijd openfigi_root_bekend/_matches. Root niet gevonden: waarschuwing erbij (nieuwe regel)
    en "zeker" wordt "onzeker"; de ticker zelf verandert nooit."""
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
    """Lichte check bij elke upload; normaal 1 gecachete Yahoo-call. Escalatie en tiers: zie CLAUDE.md: Yahoo en tickers.
    Met bekende_ticker wordt de zoekopdracht overgeslagen (dan geen alternatieven; de backfill vangt fouten op).
    Geeft find_ticker_detailed()-velden plus prijs_checks en prijswaarschuwing."""
    if bekende_ticker:
        basis = {"ticker": bekende_ticker, "zekerheid": "zeker", "alternatieven": []}
    else:
        basis = find_ticker_detailed(product, isin, beurs)
    ticker = basis["ticker"]

    # Zonder splitrijen (koers 0 of leeg).
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

    # "Geen koersdata" apart: _prijscheck_is_probleem() ziet dat niet als probleem.
    escaleert = check_laatste["afwijking_pct"] is None or _prijscheck_is_probleem(check_laatste)

    if not escaleert:
        resultaat = {**basis, "prijs_checks": prijs_checks, "prijswaarschuwing": None}
        return _voeg_openfigi_check_toe(resultaat, isin)

    # Stap 2: de hele steekproef, zodat één uitschieter geen foute ticker maakt.
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

    # Stap 3: pas nu alternatieven doorrekenen, alleen voor deze positie.
    if grootste_afwijking is None or grootste_afwijking > PRIJSCHECK_DREMPEL_ALTERNATIEVEN * 100:
        verwachte_beurzen = BEURS_MAP.get(beurs, [])
        alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
            basis["alternatieven"], steekproef, verwachte_beurzen
        )

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
            resultaat["ticker"] = gekozen["ticker"]
            resultaat["zekerheid"] = "zeker"
            resultaat["prijswaarschuwing"] = None
        elif aanbevolen_alternatief:
            # Onvoldoende bewijs: alleen een suggestie.
            resultaat["aanbevolen_alternatief"] = aanbevolen_alternatief

    return _voeg_openfigi_check_toe(resultaat, isin)


def _ticker_heeft_prijsprobleem(ticker, transacties_van_dit_isin):
    """Probleem op de laatste transactiedatum, inclusief 'geen koersdata'. Geen ticker telt als probleem."""
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
    """Herbeoordeelt opgeslagen tickers: zonder forceer alleen bij een prijsprobleem, met forceer altijd.
    Vervangt alleen door een kandidaat zonder prijsprobleem. Geeft het aantal gecorrigeerde groepen."""
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
            continue

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
    """Waarschuwingstekst of None, voor elk bezoek: geen live zoekopdracht, normaal een cache-hit.
    Met isin ook de OpenFIGI-root-check."""
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
    """[{ticker, naam, boodschap}]; zonder 'isin'-kolom geen OpenFIGI-check."""
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


