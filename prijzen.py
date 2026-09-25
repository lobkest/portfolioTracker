"""
Koersen ophalen + cachen (via de 'prijzen'-tabel) + FX-conversie naar EUR.

Losgetrokken uit analysis.py.
"""
import threading

import pandas as pd
import yfinance as yf
from flask import g, has_app_context

from db import get_db_connection, save_prices, upsert_prices
from debug_utils import dprint, meet_tijd
from yahoo_client import download_met_retry, _tel_yahoo_call

# FX-paar per valuta, gebruikt door zowel _converteer_naar_eur() (via
# get_prices()) als _fx_koers_op_datum() (via vergelijk_prijs_op_datum) --
# één plek voor de mapping i.p.v. 'fx_pair = "USDEUR=X" if ... else
# "GBPEUR=X"' op twee plekken herhaald.
FX_PAAR_PER_VALUTA = {"USD": "USDEUR=X", "GBP": "GBPEUR=X", "GBp": "GBPEUR=X"}

# Vaste ankerdatum voor de FX-reeks-cache (zie _fx_prijzen_serie). Bewust
# een VASTE datum i.p.v. per aanroep de eigen 'vanaf'/'datum' van de
# aanroeper doorgeven: get_prices() beschouwt een cache die tot 5 dagen na
# de gevraagde startdatum begint al als "goed genoeg" (onschuldig voor een
# doorlopende koersreeks, die dan een paar dagen later begint) -- voor een
# PUNT-in-tijd FX-opzoeking zou dat net de verkeerde handelsdag kunnen
# opleveren als twee aanroepen met een net iets andere datum na elkaar
# komen. Met een vaste ankerdatum is de cache na de eerste keer altijd
# voor alle aanroepen ver genoeg terug.
#
# LET OP: deze datum moet op/na de ECHTE eerste Yahoo-datum van elk
# FX-paar liggen (leeg getest: USDEUR=X vanaf 2003-12-01, GBPEUR=X vanaf
# 2003-09-17) -- eerder dan dat zou get_prices() z'n eigen cache altijd
# als "niet ver genoeg terug" blijven zien (eerste > start_date + 5 dagen
# gaat dan NOOIT weg) en dus bij ELKE aanroep opnieuw laten downloaden,
# precies het duplicate-call-probleem dat deze fix moest oplossen.
# 2005-01-01 zit ruim ná die echte Yahoo-startdatums, en ruim VÓÓR elke
# denkbare DeGiro-transactiedatum (DeGiro bestaat pas sinds 2008).
FX_ANKER_DATUM = pd.Timestamp("2005-01-01")

# get_prices() ververst de cache-rij van "vandaag" voortaan bij ELKE
# aanroep (i.p.v. pas als de cache >4 dagen achterloopt) — deze drempel voorkomt
# dat de meerdere endpoints van ÉÉN portfolio-opening (home, verrijking,
# ticker-zekerheid) Yahoo binnen dezelfde paar seconden meermaals voor
# dezelfde ticker bevragen.
DREMPEL_HERGEBRUIK_KOERS = pd.Timedelta(minutes=2)


def _converteer_naar_eur(raw, tickers_kolommen, verversen=True):
    """Past USD/GBP/GBp -> EUR-conversie toe op raw[t] voor elke t in
    tickers_kolommen, in-place. FX-reeks komt uit _fx_prijzen_serie()
    (persistent gecached via prijzen/get_prices(), zie daar) i.p.v. bij
    elke aanroep een eigen download te doen -- vóór deze fix werd dezelfde
    FX-koers soms meermaals per upload opnieuw gedownload.

    `verversen` wordt ongewijzigd doorgegeven aan _fx_prijzen_serie(): dit
    pad hoort het gedrag van zijn aanroeper (get_prices(), voor het
    converteren van actuele aandelenkoersen) te volgen, niet een eigen vaste
    keuze te maken zoals vergelijk_prijs_op_datum() dat wel doet."""
    for t in tickers_kolommen:
        if t not in raw.columns:
            continue
        try:
            _tel_yahoo_call("yf.Ticker.info(currency)")
            currency = yf.Ticker(t).info.get("currency")
        except Exception:
            currency = "EUR"
        if currency in ("USD", "GBP", "GBp"):
            fx = _fx_prijzen_serie(currency, verversen=verversen)
            fx = fx.reindex(raw.index).ffill()
            divisor = 100 if currency == "GBp" else 1
            raw[t] = raw[t] / divisor * fx


def get_prices(tickers, start_date, verversen=True):
    """Haalt koersen (in EUR) op voor een lijst tickers, met caching via de database.

    verversen=False slaat de incrementele "stale"-verversing over (behandelt
    zulke tickers als cache-hit) — gebruikt door bijnaam/code wijzigen, wat
    geen koersdata raakt en dus niets aan Yahoo hoeft te vragen. Tickers die
    nog helemaal niet gecached zijn ('missing') worden altijd gedownload,
    ongeacht deze parameter.
    """
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()

    start_date = pd.Timestamp(start_date)
    vandaag = pd.Timestamp.now().normalize()

    conn = get_db_connection()
    cur = conn.cursor()

    # Vroegste én laatste gecachte datum per ticker (ongefilterd op
    # start_date!) — de vroegste om te kunnen zien of de cache al ver
    # genoeg teruggaat, de laatste om te zien of de cache nog ACTUEEL is.
    # Zonder de eerste check bleef een ticker met een eerdere, onvolledige
    # download (bv. door rate limiting) voor altijd "incompleet" gecachet,
    # met waarde=0 voor alle datums vóór de eerst gecachte datum als gevolg.
    # Zonder de tweede check werd een ticker die eenmaal ver genoeg terugging
    # nooit meer ververst, waardoor nieuwe transacties na de laatst gecachte
    # datum stilzwijgend buiten price_data.index vielen (zie compute_value_
    # over_time/compute_per_ticker, die simpelweg over price_data.index
    # itereren).
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM prijzen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums_cache = {row[0]: (pd.Timestamp(row[1]), pd.Timestamp(row[2])) for row in cur.fetchall()}

    # Wanneer is de rij van "vandaag" (indien aanwezig) voor het laatst
    # ververst — t.b.v. de hergebruik-drempel hieronder, die voorkomt dat
    # meerdere endpoints van één portfolio-opening (home, verrijking,
    # ticker-zekerheid) Yahoo binnen dezelfde paar seconden meermaals voor
    # dezelfde ticker bevragen.
    cur.execute(
        "SELECT ticker, bijgewerkt_op FROM prijzen WHERE ticker = ANY(%s) AND datum = %s",
        (tickers, vandaag.date()),
    )
    laatst_ververst_vandaag = {row[0]: row[1] for row in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_date.date()),
    )
    cached = pd.DataFrame(cur.fetchall(), columns=["ticker", "datum", "koers_eur"])
    cur.close()
    conn.close()

    missing = []
    # ticker -> datum vanaf waar incrementeel ververst moet worden
    stale = {}
    for t in tickers:
        if t not in datums_cache:
            missing.append(t)
            dprint(f"[koersen] '{t}' nog niet in cache, wordt gedownload")
            continue
        eerste, laatste = datums_cache[t]
        # kleine marge voor weekenden/feestdagen rond de gevraagde startdatum
        if eerste > start_date + pd.Timedelta(days=5):
            missing.append(t)
            continue
        # Was: alleen verversen als de cache >4 dagen achterloopt. Nu: bij
        # ELKE portfolio-opening verversen — een rij voor "vandaag" die
        # tijdens handelstijd is opgehaald (tussentijdse, niet-definitieve
        # koers) bleef anders de rest van de dag ongewijzigd staan, ook na
        # sluiting. DREMPEL_HERGEBRUIK_KOERS voorkomt dat de meerdere
        # endpoints van ÉÉN opening (home, verrijking, ticker-zekerheid)
        # Yahoo binnen dezelfde paar seconden meermaals bevragen.
        laatste_fetch_vandaag = laatst_ververst_vandaag.get(t)
        net_ververst = (
            laatste_fetch_vandaag is not None
            and pd.Timestamp.now() - pd.Timestamp(laatste_fetch_vandaag) < DREMPEL_HERGEBRUIK_KOERS
        )
        if not net_ververst:
            stale[t] = laatste
            dprint(f"[koersen] '{t}' wordt ververst vanaf {laatste.date()} "
                   f"(bij elke opening, tenzij <2 min geleden al ververst)")

    if missing:
        with meet_tijd(f"koersen_download_nieuw ({len(missing)} ticker(s))"):
            raw = download_met_retry(missing, start_date)
            if isinstance(raw, pd.Series):
                raw = raw.to_frame(name=missing[0])
            raw = raw.ffill()

            for t in missing:
                if t not in raw.columns:
                    continue
                eerste_ruw = raw[t].first_valid_index()
                dprint(f"[koersen] '{t}': ruwe (niet-EUR-gecorrigeerde) data vanaf {eerste_ruw}, "
                       f"gevraagd vanaf {start_date}")

            _converteer_naar_eur(raw, missing, verversen=verversen)

            fresh_rows = []
            for t in missing:
                if t not in raw.columns:
                    continue
                for datum, koers in raw[t].dropna().items():
                    fresh_rows.append((t, datum.date(), float(koers)))
            save_prices(fresh_rows)

            fresh_df = pd.DataFrame(fresh_rows, columns=["ticker", "datum", "koers_eur"])
            # fresh_df kan datums bevatten die al in 'cached' zaten (opnieuw
            # gedownload voor tickers die deels al gecachet waren) — bij overlap
            # de verse waarde houden, en concat kan anders duplicate
            # (ticker, datum) combinaties opleveren waar pivot() straks op stukloopt.
            cached = pd.concat([cached, fresh_df], ignore_index=True)
            cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if stale and verversen:
        # Per ticker apart gedownload (i.p.v. één bulk-call zoals bij
        # 'missing') omdat elke stale ticker een eigen 'vanaf'-datum heeft
        # (zijn eigen laatst gecachte datum + 1 dag) — een bulk-download
        # met yfinance ondersteunt geen per-ticker startdatum.
        with meet_tijd(f"koersen_download_incrementeel ({len(stale)} ticker(s))"):
            stale_rows = []
            for t, vanaf in stale.items():
                raw_t = download_met_retry(t, vanaf)
                if isinstance(raw_t, pd.Series):
                    raw_t = raw_t.to_frame(name=t)
                raw_t = raw_t.ffill()
                if t not in raw_t.columns or raw_t[t].dropna().empty:
                    dprint(f"[koersen] '{t}': incrementele ververs-download leverde geen nieuwe "
                           f"koersen op (mogelijk geen nieuwe handelsdagen sinds {vanaf.date()})")
                    continue
                _converteer_naar_eur(raw_t, [t], verversen=verversen)
                for datum, koers in raw_t[t].dropna().items():
                    stale_rows.append((t, datum.date(), float(koers)))

            if stale_rows:
                upsert_prices(stale_rows)
                stale_df = pd.DataFrame(stale_rows, columns=["ticker", "datum", "koers_eur"])
                cached = pd.concat([cached, stale_df], ignore_index=True)
                cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if cached.empty:
        return pd.DataFrame()

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

    for t in tickers:
        if t not in pivot.columns:
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")

    return pivot


# Eén lock per FX-paar (niet één globale lock): ticker-resolutie/
# prijscontrole draait deels parallel via ThreadPoolExecutor, en zonder
# deze locks kunnen meerdere threads TEGELIJK zien dat bv. 'USDEUR=X' nog
# niet gecached is en dus allemaal hun eigen download starten -- precies
# het duplicate-call-probleem dat _fx_prijzen_serie moest oplossen (de
# database-cache alleen is niet genoeg: de race zit tussen het lezen en
# het schrijven, niet in de cache zelf). Met de lock wacht een tweede
# thread voor hetzelfde paar tot de eerste klaar is en pakt daarna gewoon
# de inmiddels gevulde cache. Vooraf aangemaakt (i.p.v. lazy) omdat de set
# FX-paren vast en klein is (zie FX_PAAR_PER_VALUTA).
_fx_serie_locks = {fx_pair: threading.Lock() for fx_pair in set(FX_PAAR_PER_VALUTA.values())}


def _fx_prijzen_serie(valuta, verversen=True):
    """
    Ruwe FX-koersreeks (valuta -> EUR) vanaf FX_ANKER_DATUM, persistent
    gecached via de prijzen-tabel/get_prices() -- een FX-paar zoals
    'USDEUR=X' is voor yfinance gewoon een ticker, dus hergebruikt dit
    dezelfde cache-/download-infrastructuur als aandelenkoersen, i.p.v.
    een eigen parallelle cache te bouwen. Gedeeld door _converteer_naar_eur
    (via get_prices()) en _fx_koers_op_datum (via vergelijk_prijs_op_datum)
    -- vóór deze fix downloadde elke aanroeper z'n eigen FX-koers apart,
    ook binnen dezelfde upload voor exact dezelfde (valuta, datum).

    `verversen` wordt doorgegeven aan get_prices(): vergelijk_prijs_op_datum()
    vergelijkt altijd tegen een HISTORISCHE datum en geeft hier bewust
    verversen=False door (een verse FX-koers van vandaag is voor die
    vergelijking nooit relevant), terwijl _converteer_naar_eur() (actuele
    aandelenkoersen omrekenen) het gedrag van zijn eigen aanroeper volgt.

    Geeft een lege Series terug bij een onbekende valuta of ontbrekende
    koersdata (aanroepers behandelen dat hetzelfde als voorheen: "geen
    conversie mogelijk").

    Binnen één Flask-requestcontext wordt het resultaat per fx_pair
    gememoized op `g` -- ticker_waarschuwingen_voor_transacties() roept dit
    per unieke ticker aan, en zonder deze memo herhaalt elke aanroep dezelfde
    DB-query + pivot/ffill voor exact dezelfde (fx_pair, FX_ANKER_DATUM).
    De memo onthoudt ook MET welke verversen-waarde hij gevuld is: een
    eerdere aanroep met verversen=False heeft nooit geprobeerd te
    verversen, dus een latere aanroep binnen hetzelfde request die wél wil
    verversen (verversen=True) mag daar niet blindelings op vertrouwen --
    zonder dit onderscheid zou bv. tijdens /upload de (verversen=False)
    prijscontrole van een net-opgeloste ticker de FX-verversing voor de
    (verversen=True) aandelenkoers-conversie verderop in diezelfde request
    stilzwijgend blokkeren. Andersom (cache al met verversen=True gevuld)
    is een latere verversen=False-aanroep altijd veilig te hergebruiken.
    Geen module-level cache: dat zou tussen requests/workers heen de
    2-minuten-staleness-check van get_prices() omzeilen. Buiten een
    requestcontext (unittests, losse scripts) valt dit terug op het oude
    gedrag -- gewoon elke keer get_prices() aanroepen.
    """
    fx_pair = FX_PAAR_PER_VALUTA.get(valuta)
    if fx_pair is None:
        return pd.Series(dtype=float)

    cache = None
    if has_app_context():
        cache_attr = "_fx_serie_cache"
        if not hasattr(g, cache_attr):
            setattr(g, cache_attr, {})
        cache = getattr(g, cache_attr)
        cached_entry = cache.get(fx_pair)
        if cached_entry is not None:
            cached_reeks, cached_verversen = cached_entry
            if cached_verversen or not verversen:
                return cached_reeks

    with _fx_serie_locks[fx_pair]:
        prijzen = get_prices([fx_pair], FX_ANKER_DATUM, verversen=verversen)
    reeks = prijzen[fx_pair] if not prijzen.empty and fx_pair in prijzen.columns else pd.Series(dtype=float)

    if cache is not None:
        cache[fx_pair] = (reeks, verversen)
    return reeks
