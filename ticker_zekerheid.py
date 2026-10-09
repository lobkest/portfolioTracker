"""Hoe zeker is een ticker: lichte check bij elke upload, volledige check op de Ticker-zekerheid-pagina."""
import cProfile
import io
import itertools
import os
import pstats
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

import pandas as pd

from db import (
    db_connect, db_get_portfolio_naam_en_transacties, db_wijzig_ticker_voor_isins, db_verbinding_teller_stand,
    TRANSACTIE_KOLOMMEN,
)
from ticker_matching import (
    find_ticker_detailed, BEURS_MAP, AMERIKAANSE_BEURZEN, OTC_BEURZEN, _yahoo_search, haal_openfigi_resultaten, _openfigi_root_matches,
)
from ticker_prijscheck import vergelijk_prijs_op_datum, vergelijk_prijzen_op_datums, _prijscheck_is_probleem
from split_correctie import isin_ketens, vind_wisselparen
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl
from ticker_classificatie import (
    classify_ticker, get_land_sector, get_etf_sector_verdeling, get_etf_holdings, _ticker_details_met_cache,
    haal_long_names, bewaar_long_names,
)
from naam_verkorting import uitkeringsvorm, uitkeringsvorm_strijdig
from yahoo_client import yahoo_teller_stand


# Op True: per positie de 30 duurste eigen functies (cProfile). Alleen voor analyse, kost zelf ook tijd.
TZ_PROFIEL = False
TZ_PROFIEL_REGELS = 30
_PROJECT_MAP = os.path.dirname(os.path.abspath(__file__))


def _tz_stand():
    return yahoo_teller_stand(), db_verbinding_teller_stand()


def _tz_print(isin, stap, start, vanaf):
    # Beide tellers zijn globaal per proces: bij gelijktijdige requests tellen andere posities mee.
    (yahoo_vanaf, db_vanaf), (yahoo_nu, db_nu) = vanaf, _tz_stand()
    calls, retries, mislukt, wachttijd = (nu - toen for nu, toen in zip(yahoo_nu, yahoo_vanaf))
    db_aantal, db_seconden = (nu - toen for nu, toen in zip(db_nu, db_vanaf))
    print(f"[ticker-zekerheid] {isin}: {stap} {time.time() - start:.2f}s "
          f"(yahoo calls {calls}, retries {retries}, mislukt {mislukt}, wacht {wachttijd:.1f}s; "
          f"db verbindingen {db_aantal}, {db_seconden:.2f}s)")


@contextmanager
def _tz_profiel(isin):
    if not TZ_PROFIEL:
        yield
        return
    profiler = cProfile.Profile()
    try:
        profiler.enable()
    except ValueError:
        # Sinds Python 3.12 mag er maar één profiler tegelijk actief zijn: zet de frontend op 1 positie tegelijk.
        print(f"[ticker-zekerheid] {isin}: profiel overgeslagen (er loopt al een profiler)")
        yield
        return
    try:
        yield
    finally:
        profiler.disable()
        uitvoer = io.StringIO()
        stats = pstats.Stats(profiler, stream=uitvoer).sort_stats("cumulative")
        # Op Render staat .venv in de projectmap: die bibliotheken niet meetellen als eigen code.
        stats.print_stats(re.escape(_PROJECT_MAP) + r"[\\/](?!\.venv[\\/])", TZ_PROFIEL_REGELS)
        for regel in uitvoer.getvalue().splitlines():
            if regel.strip():
                print(f"[ticker-zekerheid] {isin} profiel: {regel.replace(_PROJECT_MAP + os.sep, '')}")

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
        "zoekstappen": resultaat.get("zoekstappen", []),
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

    return extra


def _openfigi_bronnen(alternatieven_kandidaten, gekozen_ticker, isin, openfigi=None):
    """Per nieuwe OpenFIGI-ticker-root een functie die die root bij Yahoo zoekt (Yahoo kiest het koersbare symbool),
    zodat _zoek_betere_alternatieven() pas zoekt als de root aan de beurt is. Roots met de meeste OpenFIGI-noteringen
    eerst. Geeft (bronnen, debug); debug is __TIJDELIJK, diagnostisch__ (zie CLAUDE.md: Yahoo en tickers)."""
    if openfigi is None:
        openfigi = haal_openfigi_resultaten(isin)
    debug = {"roots": [], "nieuwe_roots": [], "overgeslagen_roots": [], "yahoo_resultaten": {}}
    if not openfigi["resultaten"]:
        return [], debug

    alle_roots = [(r.get("ticker") or "").upper() for r in openfigi["resultaten"]]
    alle_roots = [r for r in alle_roots if r]
    unieke_roots = list(dict.fromkeys(alle_roots))

    bekende_symbols = {gekozen_ticker} | {a.get("symbol") for a in alternatieven_kandidaten}
    bekende_roots = {s.split(".")[0].upper() for s in bekende_symbols if s}
    nieuwe_roots = [r for r in unieke_roots if r not in bekende_roots]
    debug.update({
        "roots": unieke_roots,
        "nieuwe_roots": nieuwe_roots,
        "overgeslagen_roots": [r for r in unieke_roots if r in bekende_roots],
    })

    def _bron(root):
        def _zoek():
            quotes = _yahoo_search(root)
            debug["yahoo_resultaten"][root] = [{"symbol": q.get("symbol"), "exchange": q.get("exchange")} for q in quotes]
            nieuw = []
            for q in quotes:
                symbol = q.get("symbol")
                if not symbol or symbol in bekende_symbols:
                    continue
                bekende_symbols.add(symbol)
                nieuw.append({"symbol": symbol, "exchange": q.get("exchange")})
            return nieuw
        return _zoek

    zoekvolgorde = sorted(nieuwe_roots, key=lambda r: -alle_roots.count(r))
    return [_bron(root) for root in zoekvolgorde], debug


def _verrijk_met_openfigi_kandidaten(alternatieven_kandidaten, gekozen_ticker, isin, openfigi=None):
    """Alle OpenFIGI-roots in één keer zoeken (zie _openfigi_bronnen()). Geeft (kandidaten, debug)."""
    bronnen, debug = _openfigi_bronnen(alternatieven_kandidaten, gekozen_ticker, isin, openfigi)
    extra = list(alternatieven_kandidaten)
    for bron in bronnen:
        extra += bron()
    return extra, debug


def _sorteer_kandidaten(kandidaten, verwachte_beurzen, openfigi):
    """Verwachte beurs eerst, daarbinnen een root die OpenFIGI kent; verder Yahoo's volgorde."""
    return sorted(kandidaten, key=lambda k: (
        k.get("exchange") not in verwachte_beurzen, not _root_in_openfigi(k.get("symbol"), openfigi),
    ))


def _alternatief_acceptabel(alt, aantal_steekproef, verwachte_beurzen, openfigi, prijsprobleem):
    """Tiers bij een prijsprobleem, anders alle datums + beurs of root (zie CLAUDE.md: Yahoo en tickers)."""
    if alt.get("uitkeringsvorm_strijdig"):
        return False
    matches = alt.get("aantal_matches", 0)
    alle_datums = aantal_steekproef > 0 and matches == aantal_steekproef
    beurs_klopt = alt.get("beurs") in verwachte_beurzen
    if not prijsprobleem:
        return alle_datums and (beurs_klopt or _root_in_openfigi(alt["ticker"], openfigi))
    if beurs_klopt and matches >= MIN_MATCHES_VOOR_AUTOMATISCHE_CORRECTIE:
        return True
    return alle_datums and aantal_steekproef >= MIN_STEEKPROEF_VOOR_VOLLEDIGE_MATCH


def kies_alternatief(alternatieven, aantal_steekproef, verwachte_beurzen, openfigi=None, prijsprobleem=True):
    """De ene keuzeregel voor upload en Ticker-zekerheid: eerste acceptabele op de verwachte beurs, anders de eerste."""
    acceptabel = [
        a for a in alternatieven
        if _alternatief_acceptabel(a, aantal_steekproef, verwachte_beurzen, openfigi, prijsprobleem)
    ]
    return next((a for a in acceptabel if a.get("beurs") in verwachte_beurzen), None) or next(iter(acceptabel), None)


def _long_name(ticker):
    """Uit ticker_info, anders één Yahoo-call (en opslaan); None als Yahoo geen naam geeft."""
    long_name = _ticker_details_met_cache(ticker).get("long_name")
    if not long_name:
        long_name = haal_long_names([ticker]).get(ticker)
        if long_name:
            bewaar_long_names({ticker: long_name})
    return long_name


def _uitkeringsvorm_strijdig_bij_yahoo(degiro_naam, ticker):
    """Alleen als de DeGiro-naam DIS of ACC noemt."""
    if not uitkeringsvorm(degiro_naam):
        return False, None
    long_name = _long_name(ticker)
    return uitkeringsvorm_strijdig(degiro_naam, long_name), long_name


def bijnaam_na_tickerwissel(bijnaam, oude_ticker, nieuwe_ticker):
    """De longName van de nieuwe ticker als de bijnaam nog die van de oude is (dus niet zelf gekozen), anders None."""
    if not bijnaam or not oude_ticker:
        return None
    oude_naam = _long_name(oude_ticker)
    if not oude_naam or bijnaam.strip() != oude_naam.strip():
        return None
    return _long_name(nieuwe_ticker)


def _reken_alternatief_door(alt, steekproef):
    alt_ticker = alt["symbol"]
    t_alt, y_alt = time.time(), _tz_stand()
    alt_checks = []
    for t in steekproef:
        check = vergelijk_prijs_op_datum(alt_ticker, t["datum"], float(t["koers"]))
        alt_checks.append(check)
        # Geen koersdata betekent meestal helemaal geen historie; verder proberen kost alleen tijd.
        if check["yahoo_koers"] is None:
            break

    alt_matches = [not _prijscheck_is_probleem(c) for c in alt_checks if c["match"] is not None]
    if any(c["yahoo_koers"] is not None for c in alt_checks):
        alt_details = _ticker_details_met_cache(alt_ticker)
        alt_is_etf = classify_ticker(alt_ticker)
        alt_land, alt_sector, _alt_top_holding_land = _land_sector_voor_weergave(alt_ticker)
    else:
        # Zonder koersdata mislukken deze lookups meestal ook en dat wordt niet gecachet: elke keer rate-limit-retries.
        alt_details, alt_is_etf, alt_land, alt_sector = {}, None, None, None

    _tz_print(alt_ticker, f"  alternatief op {alt.get('exchange')}: "
                          f"{sum(1 for m in alt_matches if m)}/{len(alt_matches)} kloppen", t_alt, y_alt)
    return {
        "ticker": alt_ticker,
        "beurs": alt.get("exchange"),
        "is_etf": alt_is_etf,
        "land": alt_land,
        "sector": alt_sector,
        "valuta": alt_details.get("valuta"),
        "aantal_matches": sum(1 for m in alt_matches if m),
        "aantal_gecontroleerd": len(alt_matches),
    }


def _zoek_betere_alternatieven(alternatieven_kandidaten, steekproef, verwachte_beurzen, openfigi=None,
                               prijsprobleem=True, degiro_naam=None, extra_bronnen=()):
    """Rekent kandidaten in _sorteer_kandidaten()-volgorde door, daarna die uit extra_bronnen (pas opgehaald als ze
    aan de beurt zijn), tot een acceptabele op de verwachte beurs; DIS/ACC pas als laatste. Een acceptabele op een
    andere beurs is de reserve. Geeft (alternatieven, aanbevolen_alternatief of None)."""
    alternatieven = []
    reserve = None
    batches = itertools.chain([alternatieven_kandidaten], (bron() for bron in extra_bronnen))
    for batch in batches:
        for alt in _sorteer_kandidaten(batch, verwachte_beurzen, openfigi):
            alt_ticker = alt.get("symbol")
            beurs_klopt = alt.get("exchange") in verwachte_beurzen
            # Na een reserve kan alleen een kandidaat op de verwachte beurs nog winnen.
            if not alt_ticker or (reserve and not beurs_klopt):
                continue

            resultaat_alt = _reken_alternatief_door(alt, steekproef)
            alternatieven.append(resultaat_alt)
            if not _alternatief_acceptabel(resultaat_alt, len(steekproef), verwachte_beurzen, openfigi, prijsprobleem):
                continue
            strijdig, long_name = _uitkeringsvorm_strijdig_bij_yahoo(degiro_naam, alt_ticker)
            if strijdig:
                resultaat_alt["uitkeringsvorm_strijdig"] = True
                print(f"[ticker-zekerheid] {alt_ticker}: prijs klopt, maar DIS/ACC wijkt af ({long_name!a}) "
                      f"-> verder zoeken")
                continue
            if beurs_klopt or not verwachte_beurzen:
                return alternatieven, alt_ticker
            reserve = alt_ticker

    return alternatieven, reserve


def _leeg_resultaat(zekerheid, beurs):
    """Alle velden die de kaart op de Ticker-zekerheid-pagina toont, nog zonder inhoud."""
    return {
        "ticker": None, "zekerheid": zekerheid, "waarschuwing": None,
        "is_etf": None, "land": None, "sector": None, "top_holding_land": None, "valuta": None,
        "fondsfamilie": None, "category": None, "quote_type": None,
        "excel_beurs": beurs, "yahoo_beurs": None, "beurs_klopt": None,
        "prijs_checks": [], "alternatieven": [],
    }


def _voeg_prijsoordeel_toe(resultaat, steekproef, transacties_van_dit_isin):
    """Checkt de steekproef, en alle transacties zodra één steekproefdatum buiten de dagrange valt; een "zekere"
    ticker zonder (kloppende) koersdata wordt "onzeker" met een waarschuwing."""
    ticker = resultaat["ticker"]
    prijs_checks = []
    for t in steekproef:
        check = vergelijk_prijs_op_datum(ticker, t["datum"], float(t["koers"]))
        check["datum"] = str(t["datum"])
        prijs_checks.append(check)

    if any(c.get("binnen_dagrange") is False for c in prijs_checks):
        al_gecheckt = {id(t) for t in steekproef}
        rest = [t for t in _geldige_transacties(transacties_van_dit_isin) if id(t) not in al_gecheckt]
        print(f"[ticker-zekerheid] {ticker}: steekproef valt buiten dagrange -> nog {len(rest)} datums checken")
        if rest:
            for t, check in zip(rest, vergelijk_prijzen_op_datums(ticker, rest)):
                check["datum"] = str(t["datum"])
                prijs_checks.append(check)
            prijs_checks.sort(key=lambda c: c["datum"])

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
BEURS_GEEN_KOERSHISTORIE = "geen_koershistorie_verwachte_beurs"


def beurs_status(excel_beurs, yahoo_beurs, prijs_checks, alternatieven=None):
    """True/False/None, of BEURS_OTC_NA_DELISTING: Amerikaanse beurs in Excel, OTC bij Yahoo
    en alle bekende prijschecks kloppen (zoals XELA na de delisting van Nasdaq), of BEURS_GEEN_KOERSHISTORIE:
    de prijs klopt en alle doorgerekende alternatieven op de verwachte beurs hadden geen koersdata."""
    verwachte_beurzen = BEURS_MAP.get(excel_beurs, [])
    if not (excel_beurs and yahoo_beurs and verwachte_beurzen):
        return None
    if yahoo_beurs in verwachte_beurzen:
        return True
    bekende_checks = [c for c in prijs_checks if c["match"] is not None]
    prijs_klopt = bool(bekende_checks) and not any(_prijscheck_is_probleem(c) for c in bekende_checks)
    if prijs_klopt and excel_beurs in AMERIKAANSE_BEURZEN and yahoo_beurs in OTC_BEURZEN:
        return BEURS_OTC_NA_DELISTING
    op_verwachte_beurs = [a for a in alternatieven or [] if a.get("beurs") in verwachte_beurzen]
    if prijs_klopt and op_verwachte_beurs and not any(a.get("aantal_gecontroleerd") for a in op_verwachte_beurs):
        return BEURS_GEEN_KOERSHISTORIE
    return False


def _beurs_zonder_koershistorie(resultaat, beurs, beurs_waarschuwing):
    """Na de alternatieven: had geen enkele notering op de verwachte beurs koersdata, dan is de afwijkende beurs
    geen fout. Zonder andere waarschuwing wordt het "zeker", net als bij OTC na delisting."""
    status = beurs_status(beurs, resultaat["yahoo_beurs"], resultaat["prijs_checks"], resultaat["alternatieven"])
    if status != BEURS_GEEN_KOERSHISTORIE:
        return resultaat
    regels = [r for r in (resultaat["waarschuwing"] or "").split("\n") if r and r != beurs_waarschuwing]
    return {
        **resultaat,
        "beurs_klopt": status,
        "waarschuwing": "\n".join(regels) or None,
        "zekerheid": resultaat["zekerheid"] if regels else "zeker",
    }


def _voeg_kaartvelden_toe(resultaat, beurs):
    ticker = resultaat["ticker"]
    t, y = time.time(), _tz_stand()
    details = _ticker_details_met_cache(ticker)
    _tz_print(ticker, "  ticker_details", t, y)
    t, y = time.time(), _tz_stand()
    land, sector, top_holding_land = _land_sector_voor_weergave(ticker)
    _tz_print(ticker, "  land/sector/holdings", t, y)
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
    bronnen, openfigi_debug_info = _openfigi_bronnen(kandidaten, resultaat["ticker"], isin, openfigi)
    print(f"[ticker-zekerheid] {isin}: {len(kandidaten)} kandidaten {[k.get('symbol') for k in kandidaten]}, "
          f"daarna zo nodig {len(bronnen)} OpenFIGI-roots {openfigi_debug_info['nieuwe_roots']}")
    bekende_checks = [c for c in resultaat["prijs_checks"] if c["match"] is not None]
    prijsprobleem = not bekende_checks or any(_prijscheck_is_probleem(c) for c in bekende_checks)
    alternatieven, aanbevolen_alternatief = _zoek_betere_alternatieven(
        kandidaten, steekproef, BEURS_MAP.get(beurs, []), openfigi, prijsprobleem, product, bronnen,
    )
    uitgebreid = {
        **resultaat,
        "alternatieven": alternatieven,
        "openfigi_kandidaten_debug": {"aangeroepen": True, **openfigi_debug_info},
    }
    if aanbevolen_alternatief:
        uitgebreid["aanbevolen_alternatief"] = aanbevolen_alternatief
    return uitgebreid


def verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin, opgeslagen_ticker=None):
    """Volledige check (duur; alleen op de Ticker-zekerheid-pagina): alles wat de kaart toont, inclusief
    doorgerekende alternatieven. Met opgeslagen_ticker wordt die gecontroleerd in plaats van opnieuw gezocht."""
    with _tz_profiel(isin):
        return _verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin, opgeslagen_ticker)


def _met_waarschuwing(resultaat, tekst):
    bestaande = resultaat.get("waarschuwing")
    return {**resultaat, "waarschuwing": f"{bestaande}\n{tekst}" if bestaande else tekst, "zekerheid": "onzeker"}


def _voeg_uitkeringsvorm_check_toe(resultaat, product):
    """DIS/ACC als laatste check: geen officiële bron, dus alleen een duidelijke strijdigheid telt."""
    strijdig, long_name = _uitkeringsvorm_strijdig_bij_yahoo(product, resultaat["ticker"])
    resultaat = {**resultaat, "uitkeringsvorm_strijdig": strijdig, "yahoo_long_name": long_name}
    if not strijdig:
        return resultaat
    return _met_waarschuwing(resultaat, (
        f"DeGiro noemt dit fonds {uitkeringsvorm(product)}, maar Yahoo noemt '{resultaat['ticker']}' "
        f"{uitkeringsvorm(long_name)} ('{long_name}') — waarschijnlijk de verkeerde share class."
    ))


def _verifieer_ticker_met_prijs(product, isin, beurs, transacties_van_dit_isin, opgeslagen_ticker=None):
    print(f"[ticker-zekerheid] {isin}: start '{product}' op {beurs}, {len(transacties_van_dit_isin)} transacties")
    t0, y0 = time.time(), _tz_stand()
    if opgeslagen_ticker:
        basis = {"ticker": opgeslagen_ticker, "zekerheid": "zeker", "alternatieven": []}
        _tz_print(isin, f"opgeslagen ticker {opgeslagen_ticker}", t0, y0)
    else:
        basis = find_ticker_detailed(product, isin, beurs)
        _tz_print(isin, f"find_ticker_detailed -> {basis['ticker']} ({basis['zekerheid']}, "
                        f"{len(basis.get('alternatieven') or [])} alternatieven)", t0, y0)
    resultaat = _leeg_resultaat(basis["zekerheid"], beurs)
    if basis["ticker"] is None:
        return _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="waarschuwing")

    steekproef = _kies_steekproef_transacties(transacties_van_dit_isin)
    resultaat = {**resultaat, "ticker": basis["ticker"]}
    t, y = time.time(), _tz_stand()
    resultaat = _voeg_prijsoordeel_toe(resultaat, steekproef, transacties_van_dit_isin)
    _tz_print(isin, f"prijsoordeel ({len(resultaat['prijs_checks'])} checks, {resultaat['zekerheid']})", t, y)
    t, y = time.time(), _tz_stand()
    resultaat = _voeg_kaartvelden_toe(resultaat, beurs)
    _tz_print(isin, f"kaartvelden (is_etf {resultaat['is_etf']})", t, y)
    # Bij het zoeken was "zeker" al een beurs-match; een opgeslagen ticker moet dat nog bewijzen.
    beurs_waarschuwing = None
    if opgeslagen_ticker and resultaat["beurs_klopt"] is False:
        beurs_waarschuwing = (
            f"Opgeslagen ticker '{opgeslagen_ticker}' staat bij Yahoo op {resultaat['yahoo_beurs']}, "
            f"de transacties op {beurs}."
        )
        resultaat = _met_waarschuwing(resultaat, beurs_waarschuwing)
    # Vóór de alternatieven: een ontbrekende root maakt "zeker" onzeker, en dan moeten ze wél doorgerekend.
    t, y = time.time(), _tz_stand()
    openfigi = haal_openfigi_resultaten(isin)
    resultaat = _voeg_openfigi_check_toe(resultaat, isin, waarschuwing_veld="waarschuwing", openfigi=openfigi)
    _tz_print(isin, f"openfigi (root_bekend {resultaat['openfigi_root_bekend']}, {resultaat['zekerheid']})", t, y)
    t, y = time.time(), _tz_stand()
    resultaat = _voeg_uitkeringsvorm_check_toe(resultaat, product)
    _tz_print(isin, f"DIS/ACC (strijdig {resultaat['uitkeringsvorm_strijdig']}, {resultaat['zekerheid']}, "
                    f"longName {resultaat['yahoo_long_name']!a})", t, y)
    t, y = time.time(), _tz_stand()
    resultaat = _voeg_alternatieven_toe(resultaat, basis, product, isin, beurs, steekproef, openfigi)
    _tz_print(isin, f"alternatieven ({len(resultaat['alternatieven'])} doorgerekend)", t, y)
    resultaat = _beurs_zonder_koershistorie(resultaat, beurs, beurs_waarschuwing)
    _tz_print(isin, "TOTAAL", t0, y0)
    return resultaat


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


def _corrigeer_met_alternatief(resultaat, geldige_transacties, beurs, isin=None, openfigi=None, degiro_naam=None):
    """Rekent bij een prijsprobleem (buiten de dagrange of geen koersdata) of een ontbrekende OpenFIGI-root de
    alternatieven door en vervangt de ticker door kies_alternatief(), als die er een vindt."""
    bekende_checks = [c for c in resultaat["prijs_checks"] if c["match"] is not None]
    prijsprobleem = not bekende_checks or any(_prijscheck_is_probleem(c) for c in bekende_checks)
    root_ontbreekt = _openfigi_root_oordeel(resultaat["ticker"], openfigi)[1] is False
    if not prijsprobleem and not root_ontbreekt:
        return resultaat

    steekproef = _kies_steekproef_transacties(geldige_transacties)
    verwachte_beurzen = BEURS_MAP.get(beurs, [])
    kandidaten = list(resultaat["alternatieven"])
    bronnen = []
    if root_ontbreekt:
        bronnen, _debug = _openfigi_bronnen(kandidaten, resultaat["ticker"], isin, openfigi)
    alternatieven, _aanbevolen = _zoek_betere_alternatieven(
        kandidaten, steekproef, verwachte_beurzen, openfigi, prijsprobleem, degiro_naam, bronnen,
    )
    gekozen = kies_alternatief(alternatieven, len(steekproef), verwachte_beurzen, openfigi, prijsprobleem)
    if gekozen:
        return {**resultaat, "ticker": gekozen["ticker"], "zekerheid": "zeker", "prijswaarschuwing": None}
    return resultaat


def find_ticker_met_snelle_prijscheck(product, isin, beurs, transacties_van_dit_isin, bekende_ticker=None):
    """Lichte check bij elke upload; normaal 1 gecachete Yahoo-call. Escalatie en tiers: zie CLAUDE.md: Yahoo en tickers.
    Met bekende_ticker wordt de zoekopdracht overgeslagen en de ticker nooit vervangen (alleen een waarschuwing).
    Geeft find_ticker_detailed()-velden plus prijs_checks en prijswaarschuwing."""
    geldige_transacties = _geldige_transacties(transacties_van_dit_isin)

    resultaat = _begin_resultaat(product, isin, beurs, bekende_ticker)
    resultaat = _voeg_prijscheck_laatste_toe(resultaat, geldige_transacties)
    openfigi = haal_openfigi_resultaten(isin) if resultaat["ticker"] else None
    root_ontbreekt = _openfigi_root_oordeel(resultaat["ticker"], openfigi)[1] is False
    escaleren = _moet_escaleren(resultaat)
    if escaleren:
        resultaat = _voeg_steekproef_toe(resultaat, geldige_transacties)
    # Hergebruikte ticker nooit vervangen: alleen de nieuwe rijen kregen hem dan (de knop op Ticker-zekerheid doet ze allemaal).
    if not bekende_ticker and (escaleren or (root_ontbreekt and geldige_transacties)):
        resultaat = _corrigeer_met_alternatief(resultaat, geldige_transacties, beurs, isin, openfigi, product)
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


def groepeer_posities_per_keten(rijen):
    """rijen in volgorde van TRANSACTIE_KOLOMMEN -> [((eind_isin, beurs), {naam, echte_naam, beurs, isin, isins, ticker,
    transacties})] zonder corporate-action- en wisselrijen. Een ISIN-wissel (ook een keten) is één groep onder de
    nieuwste ISIN; isins = de hele keten, oudste eerst. Zoek op echte_naam: product kan een bijnaam zijn."""
    transacties_df = pd.DataFrame(rijen, columns=TRANSACTIE_KOLOMMEN)
    for kolom in ("aantal", "koers", "transactiekosten"):
        transacties_df[kolom] = transacties_df[kolom].astype(float)
    # Een omboeking bij een ISIN-wissel is geen markttransactie: koers = slot van de dag ervoor.
    paren, _onduidelijk = vind_wisselparen(transacties_df)
    wisselrijen = {label for paar in paren for label in paar.oud_rijen + paar.nieuw_rijen}
    eind_van = isin_ketens(paren)
    eerste_datum = transacties_df.groupby("isin")["datum"].min()
    keten_van = {}
    for isin, eind in sorted(eind_van.items(), key=lambda item: eerste_datum.get(item[0])):
        keten_van.setdefault(eind, []).append(isin)

    # Naam en ticker komen van de eerste rij van de nieuwste ISIN (als die markttransacties heeft): daarop zoekt de check.
    transacties_df["_eind"] = transacties_df["isin"].map(lambda i: eind_van.get(i, i))
    transacties_df["_is_eind"] = transacties_df["isin"] == transacties_df["_eind"]
    transacties_df = transacties_df.sort_values(["_eind", "_is_eind", "datum"], ascending=[True, False, True])

    per_groep = {}
    for label, rij in transacties_df.iterrows():
        eind, product, echte_naam, beurs, datum, koers = (
            rij["_eind"], rij["product"], rij["echte_naam"], rij["beurs"], rij["datum"], rij["koers"],
        )
        if _is_corporate_action_row({"beurs": beurs, "product": product}) or label in wisselrijen:
            continue
        groep = per_groep.setdefault(
            (eind, beurs), {
                "naam": product, "echte_naam": echte_naam, "beurs": beurs, "isin": eind,
                "isins": keten_van.get(eind, [eind]),
                "ticker": rij["ticker"] if pd.notna(rij["ticker"]) else None, "transacties": [],
            }
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    for groep in per_groep.values():
        groep["transacties"].sort(key=lambda t: t["datum"])
    return list(per_groep.items())


def backfill_verouderde_tickers(code):
    """Herzoekt elke opgeslagen ticker per positie (een ISIN-keten is één positie); vervangt alleen door een kandidaat
    zonder prijsprobleem, voor alle ISIN's van de keten. Geeft het aantal gecorrigeerde posities."""
    naam_portfolio, rows = db_get_portfolio_naam_en_transacties(code)
    if naam_portfolio is None:
        return 0
    groepen = groepeer_posities_per_keten(rows)

    conn = db_connect()
    cur = conn.cursor()
    gecorrigeerd = 0
    for (isin, beurs), info in groepen:
        oude_ticker = info["ticker"]
        transacties = info["transacties"]
        nieuw = find_ticker_met_snelle_prijscheck(info["echte_naam"] or info["naam"], isin, beurs, transacties)
        nieuwe_ticker = nieuw["ticker"]
        if not nieuwe_ticker or nieuwe_ticker == oude_ticker:
            continue

        if _ticker_heeft_prijsprobleem(nieuwe_ticker, transacties):
            continue

        db_wijzig_ticker_voor_isins(cur, code, info["isins"], beurs, nieuwe_ticker)
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


def controleer_alle_transactieprijzen(ticker, transacties_van_dit_isin):
    """{prijs_checks (op datum), aantal_binnen, aantal_buiten, aantal_onbekend, max_afstand_pct}: elke transactie
    tegen de dagrange; max_afstand_pct = grootste afstand tot de dagrange zonder marge (None zonder high/low)."""
    geldige_transacties = sorted(_geldige_transacties(transacties_van_dit_isin), key=lambda t: t["datum"])
    prijs_checks = []
    if ticker and geldige_transacties:
        for t, check in zip(geldige_transacties, vergelijk_prijzen_op_datums(ticker, geldige_transacties)):
            check["datum"] = str(t["datum"])
            prijs_checks.append(check)
    return {
        "prijs_checks": prijs_checks,
        "aantal_binnen": sum(1 for c in prijs_checks if c["binnen_dagrange"] is True),
        "aantal_buiten": sum(1 for c in prijs_checks if c["binnen_dagrange"] is False),
        "aantal_onbekend": sum(1 for c in prijs_checks if c["binnen_dagrange"] is None),
        "max_afstand_pct": max(
            (abs(c["afstand_dagrange_pct"]) for c in prijs_checks if c["afstand_dagrange_pct"] is not None),
            default=None,
        ),
    }


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
    eind_van = isin_ketens(vind_wisselparen(transacties_df)[0]) if heeft_isin_kolom else {}
    waarschuwingen = []
    prijs_checks = {}
    for ticker, groep in transacties_df.dropna(subset=["ticker"]).groupby("ticker"):
        transacties_van_ticker = [{"datum": d, "koers": k} for d, k in zip(groep["datum"], groep["koers"])]
        isin = None
        if heeft_isin_kolom:
            # De rijvolgorde is willekeurig: neem de nieuwste rij, en via de keten de nieuwste ISIN.
            laatste_isin = groep.sort_values("datum", kind="stable")["isin"].iloc[-1]
            isin = eind_van.get(laatste_isin, laatste_isin)
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
            i = future_naar_index[future]
            try:
                resultaten[i] = future.result()
            except Exception as e:
                product, isin, beurs, _transacties = posities[i]
                print(f"[ticker] WARN lichte check mislukt voor {ascii(product)} ({isin}, {beurs}): {ascii(e)}")
                resultaten[i] = _geen_ticker_resultaat()
    return resultaten


def _geen_ticker_resultaat():
    """Zelfde vorm als find_ticker_met_snelle_prijscheck() zonder gevonden ticker."""
    return {
        "ticker": None, "zekerheid": "geen_match", "alternatieven": [],
        "prijs_checks": [], "prijswaarschuwing": None,
        "openfigi_root_bekend": None, "openfigi_root_matches": None,
    }


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


