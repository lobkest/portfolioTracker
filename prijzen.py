"""Koersen ophalen en cachen (tabel prijzen) en omrekenen naar EUR."""
import threading

import pandas as pd
import yfinance as yf
from flask import g, has_app_context

from db import get_db_connection, save_prices, upsert_prices
from debug_utils import dprint, meet_tijd
from diagnostiek import meld, CATEGORIE_WISSELKOERSEN, CATEGORIE_KOERSEN, GOED, LET_OP, FOUT
from yahoo_client import download_met_retry, _tel_yahoo_call

FX_PAAR_PER_VALUTA = {"USD": "USDEUR=X", "GBP": "GBPEUR=X", "GBp": "GBPEUR=X"}

# Vast i.p.v. per aanroep, zodat een punt-in-tijd-FX-lookup altijd dezelfde cache gebruikt.
# Moet ná Yahoo's eerste FX-datum liggen (zie CLAUDE.md: Yahoo en tickers).
FX_ANKER_DATUM = pd.Timestamp("2005-01-01")

# Voorkomt dat de endpoints van één portfolio-opening Yahoo meermaals bevragen.
DREMPEL_HERGEBRUIK_KOERS = pd.Timedelta(minutes=2)

# Herkomst van koersen per request (op `g`), alleen voor de Diagnostiek.
FX_PAREN = set(FX_PAAR_PER_VALUTA.values())
FX_BRON_CACHE = "uit cache"
FX_BRON_GEDOWNLOAD = "gedownload"
FX_BRON_VERVERST = "ververst"
_G_ATTR_FX_BRON = "_fx_bron"


# Per ticker telt de "sterkste" herkomst: een latere cache-hit overschrijft geen eerdere download.
_G_ATTR_KOERS_BRON = "_koers_bron"
_KOERS_BRON_RANG = {FX_BRON_CACHE: 1, FX_BRON_VERVERST: 2, FX_BRON_GEDOWNLOAD: 3}
DIAGNOSTIEK_SLEUTEL_KOERSEN = "koersen_samenvatting"


def _noteer_koers_bron(ticker, bron):
    if ticker in FX_PAREN or not has_app_context():
        return
    bronnen = getattr(g, _G_ATTR_KOERS_BRON, None)
    if bronnen is None:
        bronnen = {}
        setattr(g, _G_ATTR_KOERS_BRON, bronnen)
    if _KOERS_BRON_RANG[bron] > _KOERS_BRON_RANG.get(bronnen.get(ticker), 0):
        bronnen[ticker] = bron


def _meld_koersen(tickers, tickers_met_koers):
    """Meldt tickers zonder koersdata plus een samenvatting over deze request."""
    if not has_app_context():
        return
    for t in tickers:
        if t not in FX_PAREN and t not in tickers_met_koers:
            meld(CATEGORIE_KOERSEN, LET_OP,
                 f"Geen koersdata van Yahoo voor '{t}': deze positie telt niet mee in de portefeuillewaarde, "
                 f"de inleg wel.",
                 sleutel=f"geen_koers:{t}")
    bronnen = getattr(g, _G_ATTR_KOERS_BRON, None) or {}
    if not bronnen:
        return
    aantal = {bron: sum(1 for b in bronnen.values() if b == bron) for bron in _KOERS_BRON_RANG}
    meld(CATEGORIE_KOERSEN, GOED,
         f"{len(bronnen)} tickers: {aantal[FX_BRON_CACHE]} uit cache, {aantal[FX_BRON_GEDOWNLOAD]} nieuw "
         f"gedownload, {aantal[FX_BRON_VERVERST]} ververst.",
         sleutel=DIAGNOSTIEK_SLEUTEL_KOERSEN)


def _noteer_fx_bron(fx_pair, bron):
    if fx_pair not in FX_PAREN or not has_app_context():
        return
    bronnen = getattr(g, _G_ATTR_FX_BRON, None)
    if bronnen is None:
        bronnen = {}
        setattr(g, _G_ATTR_FX_BRON, bronnen)
    bronnen[fx_pair] = bron


def _fx_bron(fx_pair):
    if not has_app_context():
        return None
    return (getattr(g, _G_ATTR_FX_BRON, None) or {}).get(fx_pair)


def _haal_valuta_op(t):
    """Bij een fout of geen valuta: "EUR" met een WARN. Een valuta zonder FX-paar krijgt ook een WARN."""
    try:
        _tel_yahoo_call("yf.Ticker.info(currency)")
        currency = yf.Ticker(t).info.get("currency")
    except Exception as e:
        print(f"[koersen] WARN {t}: valuta opvragen bij Yahoo mislukt ({e!a}) - "
              f"koers NIET omgerekend, aanname EUR")
        meld(CATEGORIE_WISSELKOERSEN, LET_OP,
             f"Valuta van '{t}' niet op te halen bij Yahoo; aangenomen EUR (geen omrekening).", sleutel=t)
        return "EUR"
    if not currency:
        print(f"[koersen] WARN {t}: Yahoo geeft geen valuta - koers NIET omgerekend, aanname EUR")
        meld(CATEGORIE_WISSELKOERSEN, LET_OP,
             f"Yahoo geeft geen valuta voor '{t}'; aangenomen EUR (geen omrekening).", sleutel=t)
        return "EUR"
    if currency != "EUR" and currency not in FX_PAAR_PER_VALUTA:
        print(f"[koersen] WARN {t}: valuta '{currency}' wordt niet ondersteund - "
              f"koers NIET omgerekend, telt mee alsof het EUR is")
        meld(CATEGORIE_WISSELKOERSEN, LET_OP,
             f"Valuta {currency} van '{t}' wordt niet ondersteund; niet omgerekend, telt mee alsof het EUR is.",
             sleutel=t)
    return currency


def _converteer_naar_eur(raw, tickers_kolommen, verversen=True):
    """In-place; `verversen` volgt de aanroeper (get_prices())."""
    for t in tickers_kolommen:
        if t not in raw.columns:
            continue
        currency = _haal_valuta_op(t)
        if currency in ("USD", "GBP", "GBp"):
            fx = _fx_prijzen_serie(currency, verversen=verversen)
            fx = fx.reindex(raw.index).ffill()
            divisor = 100 if currency == "GBp" else 1
            raw[t] = raw[t] / divisor * fx


def get_prices(tickers, start_date, verversen=True):
    """Koersen in EUR, index = datum, kolommen = tickers.
    verversen=False slaat alleen de incrementele verversing over; nieuwe tickers worden altijd gedownload."""
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()

    start_date = pd.Timestamp(start_date)
    vandaag = pd.Timestamp.now().normalize()

    conn = get_db_connection()
    cur = conn.cursor()

    # Vroegste datum: gaat de cache ver genoeg terug? Laatste: is hij nog actueel?
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM prijzen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums_cache = {row[0]: (pd.Timestamp(row[1]), pd.Timestamp(row[2])) for row in cur.fetchall()}

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
        # Elke opening verversen: een koers van vandaag kan tussentijds zijn.
        laatste_fetch_vandaag = laatst_ververst_vandaag.get(t)
        net_ververst = (
            laatste_fetch_vandaag is not None
            and pd.Timestamp.now() - pd.Timestamp(laatste_fetch_vandaag) < DREMPEL_HERGEBRUIK_KOERS
        )
        if not net_ververst:
            stale[t] = laatste
            dprint(f"[koersen] '{t}' wordt ververst vanaf {laatste.date()} "
                   f"(bij elke opening, tenzij <2 min geleden al ververst)")

    for t in tickers:
        _noteer_fx_bron(t, FX_BRON_GEDOWNLOAD if t in missing else FX_BRON_CACHE)
        _noteer_koers_bron(t, FX_BRON_GEDOWNLOAD if t in missing else FX_BRON_CACHE)

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
            # Bij overlap de verse waarde houden; duplicaten breken pivot().
            cached = pd.concat([cached, fresh_df], ignore_index=True)
            cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if stale and verversen:
        # Per ticker: yfinance kent geen eigen startdatum per ticker in één bulk-call.
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
                _noteer_fx_bron(t, FX_BRON_VERVERST)
                _noteer_koers_bron(t, FX_BRON_VERVERST)
                for datum, koers in raw_t[t].dropna().items():
                    stale_rows.append((t, datum.date(), float(koers)))

            if stale_rows:
                upsert_prices(stale_rows)
                stale_df = pd.DataFrame(stale_rows, columns=["ticker", "datum", "koers_eur"])
                cached = pd.concat([cached, stale_df], ignore_index=True)
                cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if cached.empty:
        _meld_koersen(tickers, set())
        return pd.DataFrame()

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

    for t in tickers:
        if t not in pivot.columns:
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")

    _meld_koersen(tickers, set(pivot.columns))
    return pivot


# Eén lock per FX-paar: anders downloaden parallelle threads hetzelfde paar tegelijk.
_fx_serie_locks = {fx_pair: threading.Lock() for fx_pair in set(FX_PAAR_PER_VALUTA.values())}


def _fx_prijzen_serie(valuta, verversen=True):
    """FX-reeks valuta -> EUR via dezelfde prijzen-cache als aandelen; lege Series als onbekend.
    Per request gememoized op `g`, samen met de verversen-waarde: een memo met
    verversen=False mag een latere aanroep met verversen=True niet blokkeren."""
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
    _meld_fx_reeks(valuta, fx_pair, reeks)
    return reeks


def _meld_fx_reeks(valuta, fx_pair, reeks):
    geldig = reeks.dropna()
    if geldig.empty:
        meld(CATEGORIE_WISSELKOERSEN, FOUT,
             f"{valuta} -> EUR: geen koersdata van Yahoo ({fx_pair}); posities in {valuta} "
             f"kunnen verkeerd gewaardeerd zijn.",
             sleutel=fx_pair)
        return
    vanaf = pd.Timestamp(geldig.index.min()).strftime("%Y-%m-%d")
    bron = _fx_bron(fx_pair)
    tekst = f"{valuta} -> EUR via Yahoo ({fx_pair}): {len(geldig)} koersen, vanaf {vanaf}"
    tekst += f"; {bron}." if bron else "."
    meld(CATEGORIE_WISSELKOERSEN, GOED, tekst, sleutel=fx_pair)
