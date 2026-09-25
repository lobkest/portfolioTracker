"""
Lage-niveau Yahoo-prijsvergelijking voor 1 (ticker, datum): historische
slotkoers/dagrange ophalen (met retry/cache), split-correctie, FX-conversie,
en de vergelijking met de bekende DEGIRO-transactieprijs die de basis vormt
voor de Ticker-zekerheid-prijscontrole.

Losgetrokken uit analysis.py.
"""
import pandas as pd
import yfinance as yf

from db import get_cached_splits, save_splits, get_cached_prijscheck, save_prijscheck
from debug_utils import dprint
from yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from prijzen import FX_PAAR_PER_VALUTA, _fx_prijzen_serie
from ticker_classificatie import _ticker_details_met_cache

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

# Tolerantie op de High/Low-dagrange-check (vergelijk_prijs_op_datum): de
# exacte low <= koers <= high bleek te strak -- bekend-goede tickers
# (VUSA.AS, G2X.DE) hadden een Excel-koers die net (~1-2%) buiten Yahoo's
# High/Low viel, vermoedelijk door net iets andere sluitingsmomenten/
# afronding tussen DEGIRO en Yahoo, niet door een foute ticker.
DAGRANGE_TOLERANTIE = 0.05


def _haal_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Haalt (high, low) op van de eerste geldige handelsdag op of ná 'datum'
    (buffer voor weekend/feestdagen waarop de markt dicht was), in de eigen
    valuta van de ticker -- voor de dagrange-check op de Ticker-zekerheid-
    pagina (staat de Excel-transactieprijs tussen het intraday-high en
    -low). Retry/backoff bij rate limiting via _met_rate_limit_retry (zelfde
    patroon als _fetch_yf_info). Gebruikt door vergelijk_prijs_op_datum()
    om een gecachete rij zonder high/low alsnog aan te vullen.
    Geeft (None, None) terug als het na alle retries niet lukt of er geen
    koersdata is.
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(dagrange)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)[["High", "Low"]]

    raw, fout = _met_rate_limit_retry(_actie, pogingen, wachttijd)
    if fout is not None:
        return None, None

    if isinstance(raw.columns, pd.MultiIndex):
        # yf.download geeft bij 1 ticker soms toch multi-index-kolommen terug.
        raw.columns = raw.columns.get_level_values(0)

    geldig = raw.dropna()
    if geldig.empty:
        return None, None

    eerste = geldig.iloc[0]
    return float(eerste["High"]), float(eerste["Low"])


def _haal_koers_en_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Slotkoers + dagrange (high, low) in ÉÉN yf.download()-call -- gebruikt
    door vergelijk_prijs_op_datum() in het pad waar altijd zowel de
    slotkoers als de dagrange nodig zijn (de eerste, verse fetch). Zelfde
    weekend/feestdag-buffer en retry als _haal_dagrange_op() hierboven. De
    slotkoers is in de eigen valuta van de ticker -- GEEN EUR-conversie, dit
    is puur een identiteitscheck (klopt de prijs), geen waardeberekening.
    _haal_dagrange_op() blijft bestaan voor de ticker_prijscheck-cache-
    aanvulling in vergelijk_prijs_op_datum() (daar is de slotkoers al
    bekend uit de cache, alleen de dagrange ontbreekt nog).

    Geeft (slotkoers, high, low) terug, of (None, None, None) bij een
    mislukte download na alle retries, of als er geen koersdata is in de
    periode.
    """
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(slotkoers+dagrange)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=True, progress=False)[["Close", "High", "Low"]]

    raw, fout = _met_rate_limit_retry(_actie, pogingen, wachttijd)
    if fout is not None:
        return None, None, None

    if isinstance(raw.columns, pd.MultiIndex):
        # yf.download geeft bij 1 ticker soms toch multi-index-kolommen terug.
        raw.columns = raw.columns.get_level_values(0)

    geldig = raw.dropna()
    if geldig.empty:
        return None, None, None

    eerste = geldig.iloc[0]
    return float(eerste["Close"]), float(eerste["High"]), float(eerste["Low"])


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
        _tel_yahoo_call("yf.Ticker.splits")
        splits = yf.Ticker(ticker).splits
    except Exception:
        return {}
    resultaat = {pd.Timestamp(datum).date().isoformat(): float(ratio) for datum, ratio in splits.items()}
    save_splits(ticker, resultaat)
    return resultaat


def _cumulatieve_split_factor(ticker, vanaf_datum):
    """
    Cumulatieve vermenigvuldigingsfactor van alle splits die voor 'ticker'
    hebben plaatsgevonden NA 'vanaf_datum' (tot nu).

    Nodig omdat de Yahoo-downloads hierboven met auto_adjust=True werken: een
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


def _fx_koers_op_datum(valuta, datum, dagen_buffer=7, verversen=True):
    """
    FX-koers (valuta -> EUR) op de eerste geldige handelsdag op of ná
    'datum' (zelfde weekend/feestdag-buffer als _haal_koers_en_dagrange_op), voor
    het omrekenen van een LOSSE historische Yahoo-slotkoers in
    vergelijk_prijs_op_datum() naar EUR. Haalt de ruwe FX-reeks op via
    _fx_prijzen_serie() (persistent gecached via prijzen/get_prices(), zie
    daar) i.p.v. zelf een download te doen -- zelfde valutaset als
    _converteer_naar_eur() (die get_prices() gebruikt): alleen USD/GBP/GBp
    worden herkend, dat dekt de fondsen/aandelen die dit project tot nu toe
    tegenkomt. Geeft None terug bij een onbekende valuta of een mislukte
    lookup — de aanroeper behandelt dat dan als "geen betrouwbare
    vergelijking mogelijk", niet als een (mogelijk misleidende) rauwe
    cross-currency-vergelijking.

    `verversen` wordt ongewijzigd doorgegeven aan _fx_prijzen_serie() --
    vergelijk_prijs_op_datum() geeft hier bewust verversen=False door.
    """
    fx_pair = FX_PAAR_PER_VALUTA.get(valuta)
    if fx_pair is None:
        return None

    datum = pd.Timestamp(datum)
    einddatum = datum + pd.Timedelta(days=dagen_buffer)
    reeks = _fx_prijzen_serie(valuta, verversen=verversen)
    geldig = reeks[(reeks.index >= datum) & (reeks.index <= einddatum)].dropna()
    if geldig.empty:
        return None
    return float(geldig.iloc[0])


def vergelijk_prijs_op_datum(ticker, datum, bekende_koers):
    """
    Vergelijkt de DEGIRO-transactieprijs (bekende_koers, altijd EUR — DEGIRO
    boekt alles in EUR, ook bij een niet-EUR-genoteerde ticker zoals NFLX
    via Tradegate) met de historische Yahoo-slotkoers van 'ticker' op
    diezelfde datum, na conversie naar EUR (zie _fx_koers_op_datum) en
    gecorrigeerd voor eventuele splits sindsdien (zie
    _cumulatieve_split_factor). Een grote afwijking is een sterker signaal
    dat de ticker fout is dan beurs-string-matching alleen — een verkeerde
    ticker op de "juiste" beurs geeft alsnog een compleet andere koers.
    Zonder de valutaconversie leek een prima ticker als NFLX (Yahoo-valuta
    USD) een verkeerde match: 68,38 (EUR) vs 82,23 (USD) wijkt puur door de
    ontbrekende EUR/USD-omrekening ~17% af.

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

    Haalt ook het intraday-high/low van diezelfde handelsdag op (zie
    _haal_dagrange_op) en geeft in het resultaat "binnen_dagrange" terug:
    of bekende_koers (na dezelfde EUR/split-correctie als yahoo_koers)
    tussen dat low en high valt. None als er geen high/low beschikbaar is
    (bv. een mislukte fetch, of geen vergelijking mogelijk — zie de
    early-returns hieronder) — de aanroeper valt dan terug op de bestaande
    %-afwijkingsdrempel.
    """
    datum = pd.Timestamp(datum).date()
    cached = get_cached_prijscheck(ticker, datum)
    if cached is not None:
        yahoo_koers, valuta, high, low = cached
        dprint(f"[prijscheck] '{ticker}' op {datum}: uit cache -> yahoo_koers={yahoo_koers}")
        if yahoo_koers is not None and high is None and low is None:
            # Rij van vóór de dagrange-uitbreiding, of een eerder mislukte
            # dagrange-fetch -- alsnog proberen aan te vullen (zelfde soort
            # stale-cache-fix als bij ticker_info, zie CLAUDE.md).
            high, low = _haal_dagrange_op(ticker, datum)
            save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)
    else:
        yahoo_koers, high, low = _haal_koers_en_dagrange_op(ticker, datum)
        valuta = _ticker_details_met_cache(ticker).get("valuta")
        save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)

    if yahoo_koers is None or not bekende_koers:
        return {
            "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
            "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
            "high": high, "low": low, "binnen_dagrange": None,
        }

    valuta_conversie_toegepast = False
    yahoo_koers_eur = yahoo_koers
    high_eur, low_eur = high, low
    fx_koers = None
    if valuta not in (None, "EUR"):
        # Deze vergelijking is altijd tegen een HISTORISCHE transactiedatum
        # -- een verse FX-koers van vandaag is hier nooit relevant, dus
        # onvoorwaardelijk verversen=False (zie _fx_prijzen_serie()).
        fx_koers = _fx_koers_op_datum(valuta, datum, verversen=False)
        if fx_koers is None:
            # Geen betrouwbare EUR-vergelijking mogelijk (net zo'n signaal
            # als "geen koersdata" hierboven) -- NIET stilzwijgend de rauwe,
            # niet-vergelijkbare bedragen tegen elkaar afzetten, dat zou een
            # valse waarschuwing (of een valse "OK") kunnen opleveren.
            return {
                "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
                "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
                "high": high, "low": low, "binnen_dagrange": None,
            }
        divisor = 100 if valuta == "GBp" else 1
        yahoo_koers_eur = yahoo_koers / divisor * fx_koers
        if high_eur is not None and low_eur is not None:
            high_eur = high_eur / divisor * fx_koers
            low_eur = low_eur / divisor * fx_koers
        valuta_conversie_toegepast = True

    split_factor = _cumulatieve_split_factor(ticker, datum)
    yahoo_koers_gecorrigeerd = yahoo_koers_eur * split_factor
    if high_eur is not None and low_eur is not None:
        high_eur = high_eur * split_factor
        low_eur = low_eur * split_factor

    binnen_dagrange = (
        low_eur * (1 - DAGRANGE_TOLERANTIE) <= bekende_koers <= high_eur * (1 + DAGRANGE_TOLERANTIE)
        if (high_eur is not None and low_eur is not None) else None
    )

    afwijking_pct = abs(yahoo_koers_gecorrigeerd - bekende_koers) / bekende_koers * 100
    afwijking_fractie = afwijking_pct / 100
    if afwijking_fractie < PRIJSCHECK_DREMPEL_OK:
        niveau = "ok"
    elif afwijking_fractie < PRIJSCHECK_DREMPEL_WAARSCHUWING:
        niveau = "mild"
    else:
        niveau = "waarschuwing"

    toon_gecorrigeerd = split_factor != 1.0 or valuta_conversie_toegepast
    dprint(
        f"[prijscheck-debug] ticker={ticker} datum={datum} "
        f"yahoo_koers={yahoo_koers} valuta={valuta} "
        f"fx_koers={fx_koers} yahoo_koers_eur={yahoo_koers_eur:.4f} "
        f"split_factor={split_factor} yahoo_koers_gecorrigeerd={yahoo_koers_gecorrigeerd:.4f} "
        f"bekende_koers={bekende_koers} afwijking_pct={afwijking_pct:.2f} niveau={niveau}"
    )
    return {
        "yahoo_koers": yahoo_koers,
        "yahoo_koers_gecorrigeerd": yahoo_koers_gecorrigeerd if toon_gecorrigeerd else None,
        "split_factor": split_factor,
        "bekende_koers": bekende_koers,
        "afwijking_pct": afwijking_pct,
        "niveau": niveau,
        "match": niveau != "waarschuwing",
        "high": high_eur if toon_gecorrigeerd else high,
        "low": low_eur if toon_gecorrigeerd else low,
        "binnen_dagrange": binnen_dagrange,
    }


def _prijscheck_is_probleem(check):
    """
    Of één prijscheck als 'probleem' telt voor de samenvattende
    waarschuwingsmeldingen (verifieer_ticker_met_prijs hieronder,
    prijswaarschuwing_voor_ticker verderop): primair op basis van de
    dagrange (valt de Excel-koers buiten het intraday-high/low van die
    handelsdag), met terugval op de bestaande %-afwijkingsdrempel
    (PRIJSCHECK_DREMPEL_WAARSCHUWING, via het al berekende 'match') als er
    geen dagrange beschikbaar is — bv. een mislukte High/Low-fetch, zodat
    geen dekking verloren gaat waar de dagrange-check niet kan draaien.
    """
    binnen_dagrange = check.get("binnen_dagrange")
    if binnen_dagrange is not None:
        return not binnen_dagrange
    return check["match"] is False
