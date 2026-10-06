"""Koersen ophalen en cachen (tabel prijzen) en omrekenen naar EUR."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pandas as pd
import yfinance as yf
from flask import g, has_app_context

from db import db_get_gecachte_koersen, db_get_ticker_details, db_save_koersen
from debug_utils import dprint, meet_tijd
from diagnostiek import meld, CATEGORIE_WISSELKOERSEN, CATEGORIE_KOERSEN, GOED, LET_OP, FOUT
from split_correctie import ruwe_koers
from yahoo_client import download_koersen_met_retry, _tel_yahoo_call

FX_PAAR_PER_VALUTA = {"USD": "USDEUR=X", "GBP": "GBPEUR=X", "GBp": "GBPEUR=X"}

# Vast i.p.v. per aanroep, zodat een punt-in-tijd-FX-lookup altijd dezelfde cache gebruikt.
# Moet ná Yahoo's eerste FX-datum liggen (zie CLAUDE.md: Yahoo en tickers).
FX_ANKER_DATUM = pd.Timestamp("2005-01-01")

# Eerste opbouw van de cache: per groepje downloaden en na dit budget stoppen (gunicorn-timeout); de rest volgt bij de volgende opening.
KOERS_DOWNLOAD_GROEPJE = 10
KOERS_TIJDBUDGET_SECONDEN = 20

# Gelijktijdige Yahoo-downloads bij het verversen; bewust laag (de ticker-checks gebruiken er 6 tot 12) tegen rate limits.
KOERS_VERVERS_THREADS = 3

# Voorkomt dat de endpoints van één portfolio-opening Yahoo meermaals bevragen.
DREMPEL_HERGEBRUIK_KOERS = pd.Timedelta(minutes=2)

# Een gesloten positie heeft koersen tot haar laatste transactie nodig; daarna (marge: weekend + definitieve slotkoers) niet meer.
MARGE_GESLOTEN_POSITIE = pd.Timedelta(days=3)

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
    return _controleer_valuta(t, currency)


def _controleer_valuta(t, currency):
    """Een valuta zonder FX-paar telt mee alsof het EUR is, met een WARN."""
    if currency != "EUR" and currency not in FX_PAAR_PER_VALUTA:
        print(f"[koersen] WARN {t}: valuta '{currency}' wordt niet ondersteund - "
              f"koers NIET omgerekend, telt mee alsof het EUR is")
        meld(CATEGORIE_WISSELKOERSEN, LET_OP,
             f"Valuta {currency} van '{t}' wordt niet ondersteund; niet omgerekend, telt mee alsof het EUR is.",
             sleutel=t)
    return currency


def _valuta_per_ticker(tickers):
    """Uit de ticker_info-cache; alleen bij een gemiste cache een .info-call (één Yahoo-call per ticker, traag)."""
    # Een FX-paar noteert al in EUR per eenheid vreemde valuta.
    valuta = {t: "EUR" for t in tickers if t in FX_PAREN}
    rest = [t for t in tickers if t not in valuta]
    try:
        details = db_get_ticker_details(rest) if rest else {}
    except Exception as e:
        print(f"[koersen] WARN valuta-cache niet te lezen ({e!a}) - terugval op Yahoo")
        details = {}
    for t in rest:
        uit_cache = (details.get(t) or {}).get("valuta")
        valuta[t] = _controleer_valuta(t, uit_cache) if uit_cache else _haal_valuta_op(t)
    return valuta


def _converteer_naar_eur(raw, tickers_kolommen, verversen=True):
    """In-place; `verversen` volgt de aanroeper (get_prices())."""
    tickers = [t for t in tickers_kolommen if t in raw.columns]
    valuta = _valuta_per_ticker(tickers)
    for t in tickers:
        currency = valuta[t]
        if currency in ("USD", "GBP", "GBp"):
            fx = _fx_prijzen_serie(currency, verversen=verversen)
            fx = fx.reindex(raw.index).ffill()
            divisor = 100 if currency == "GBp" else 1
            raw[t] = raw[t] / divisor * fx


def _in_groepjes(lijst, grootte):
    return [lijst[i:i + grootte] for i in range(0, len(lijst), grootte)]


def _download_ruwe_koersen_in_eur(tickers, vanaf, verversen):
    close, splits = download_koersen_met_retry(tickers, vanaf)
    return _ruwe_koersen_in_eur(close, splits, tickers, vanaf, verversen)


@contextmanager
def _tel_tijd(tijden, sleutel):
    start = time.perf_counter()
    try:
        yield
    finally:
        tijden[sleutel] = tijden.get(sleutel, 0.0) + time.perf_counter() - start


def _ruwe_koersen_in_eur(close, splits, tickers, vanaf, verversen, tijden=None):
    """({ticker: Series met RUWE koersen in EUR}, {ticker: {iso_datum: ratio}}), alleen voor tickers waarvan
    Yahoo koersen én splits teruggaf; een ticker zonder splitlijst wordt niet opgeslagen (de koers zou onbetrouwbaar zijn)."""
    tijden = tijden if tijden is not None else {}
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0] if isinstance(tickers, list) else tickers)

    with _tel_tijd(tijden, "ruwe_koers"):
        ruw = pd.DataFrame({
            t: ruwe_koers(close[t], splits[t]) for t in close.columns if t in splits
        })
        # Pas ná het terugrekenen: een doorgetrokken koers is de laatste echte koers, nooit een ander moment.
        ruw = ruw.ffill()
    gelukt = list(ruw.columns)
    for t in gelukt:
        eerste_ruw = ruw[t].first_valid_index()
        dprint(f"[koersen] '{t}': ruwe (niet-EUR-gecorrigeerde) data vanaf {eerste_ruw}, gevraagd vanaf {vanaf}")
    with _tel_tijd(tijden, "valuta_en_fx"):
        _converteer_naar_eur(ruw, gelukt, verversen=verversen)
    return ruw, {t: splits[t] for t in gelukt}


def _rijen(ruw):
    return [
        (t, datum.date(), float(koers))
        for t in ruw.columns for datum, koers in ruw[t].dropna().items()
    ]


def get_prices(tickers, start_date, verversen=True, gesloten_sinds=None):
    """RUWE koersen in EUR (de koers zoals hij die dag noteerde, zie CLAUDE.md: Data en rekenen), index = datum,
    kolommen = tickers. verversen=False slaat alleen de incrementele verversing over; nieuwe tickers worden altijd gedownload.
    gesloten_sinds: {ticker: datum van de laatste transactie} van posities zonder stukken; die worden niet meer ververst
    zodra de cache MARGE_GESLOTEN_POSITIE voorbij die datum loopt.
    Past de opbouw niet binnen KOERS_TIJDBUDGET_SECONDEN, dan staan de rest in .attrs["koersen_onvolledig"]."""
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()

    start_date = pd.Timestamp(start_date)

    # Vroegste datum: gaat de cache ver genoeg terug? Laatste: is hij nog actueel?
    gesloten_sinds = gesloten_sinds or {}
    datums, laatst_bijgewerkt, koers_rijen = db_get_gecachte_koersen(tickers, start_date.date())
    datums_cache = {t: (pd.Timestamp(eerste), pd.Timestamp(laatste)) for t, (eerste, laatste) in datums.items()}
    cached = pd.DataFrame(koers_rijen, columns=["ticker", "datum", "koers_eur"])

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
        laatste_fetch = laatst_bijgewerkt.get(t)
        net_ververst = (
            laatste_fetch is not None
            and pd.Timestamp.now() - pd.Timestamp(laatste_fetch) < DREMPEL_HERGEBRUIK_KOERS
        )
        gesloten_en_compleet = t in gesloten_sinds and laatste > pd.Timestamp(gesloten_sinds[t]) + MARGE_GESLOTEN_POSITIE
        if not net_ververst and not gesloten_en_compleet:
            stale[t] = laatste
            dprint(f"[koersen] '{t}' wordt ververst vanaf {laatste.date()} "
                   f"(bij elke opening, tenzij <2 min geleden al ververst)")

    for t in tickers:
        _noteer_fx_bron(t, FX_BRON_GEDOWNLOAD if t in missing else FX_BRON_CACHE)
        _noteer_koers_bron(t, FX_BRON_GEDOWNLOAD if t in missing else FX_BRON_CACHE)

    onvolledig = []
    if missing:
        with meet_tijd(f"koersen_download_nieuw ({len(missing)} ticker(s))"):
            begin = time.monotonic()
            for groepje in _in_groepjes(missing, KOERS_DOWNLOAD_GROEPJE):
                if time.monotonic() - begin > KOERS_TIJDBUDGET_SECONDEN:
                    onvolledig.extend(groepje)
                    continue
                ruw, splits = _download_ruwe_koersen_in_eur(groepje, start_date, verversen)
                fresh_rows = _rijen(ruw)
                db_save_koersen(fresh_rows, splits)

                fresh_df = pd.DataFrame(fresh_rows, columns=["ticker", "datum", "koers_eur"])
                # Bij overlap de verse waarde houden; duplicaten breken pivot().
                cached = pd.concat([cached, fresh_df], ignore_index=True)
                cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if stale and verversen:
        # Per ticker: yfinance kent geen eigen startdatum per ticker in één bulk-call.
        with meet_tijd(f"koersen_download_incrementeel ({len(stale)} ticker(s))"):
            stale_rows = []
            stale_splits = {}

            def download_met_duur(t):
                start = time.perf_counter()
                resultaat = download_koersen_met_retry(t, stale[t])
                return resultaat, time.perf_counter() - start

            # Alleen de netwerkcall parallel: de verwerking gebruikt de database en meldt aan de Diagnostiek (hoofdthread).
            with meet_tijd(f"koersen_incrementeel_downloaden ({len(stale)} ticker(s), {KOERS_VERVERS_THREADS} threads)"):
                with ThreadPoolExecutor(max_workers=KOERS_VERVERS_THREADS) as executor:
                    downloads_met_duur = list(executor.map(download_met_duur, stale))
            downloads = [download for download, _ in downloads_met_duur]
            duren = [duur for _, duur in downloads_met_duur]
            print(f"[timing] koersen_incrementeel_per_call: {len(duren)} calls, gemiddeld {sum(duren) / len(duren):.2f}s, "
                  f"traagste {max(duren):.2f}s, samen {sum(duren):.2f}s")

            tijden = {}
            for (t, vanaf), (close_t, splits_download) in zip(stale.items(), downloads):
                ruw_t, splits_t = _ruwe_koersen_in_eur(close_t, splits_download, t, vanaf, verversen, tijden)
                if t not in ruw_t.columns or ruw_t[t].dropna().empty:
                    dprint(f"[koersen] '{t}': incrementele ververs-download leverde geen nieuwe "
                           f"koersen op (mogelijk geen nieuwe handelsdagen sinds {vanaf.date()})")
                    continue
                _noteer_fx_bron(t, FX_BRON_VERVERST)
                _noteer_koers_bron(t, FX_BRON_VERVERST)
                stale_rows.extend(_rijen(ruw_t))
                stale_splits.update(splits_t)

            print(f"[timing] koersen_incrementeel_verwerken: "
                  + ", ".join(f"{naam} {seconden:.2f}s" for naam, seconden in tijden.items()))
            if stale_rows:
                with meet_tijd(f"koersen_incrementeel_opslaan ({len(stale_rows)} rij(en))"):
                    db_save_koersen(stale_rows, stale_splits)
                stale_df = pd.DataFrame(stale_rows, columns=["ticker", "datum", "koers_eur"])
                cached = pd.concat([cached, stale_df], ignore_index=True)
                cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if onvolledig:
        meld(CATEGORIE_KOERSEN, LET_OP,
             f"Koersen nog niet compleet: {len(onvolledig)} van {len(tickers)} ticker(s) ({', '.join(onvolledig)}) zijn "
             f"nog niet opgehaald en tellen voorlopig niet mee in de waarde. Open het portfolio opnieuw om verder te gaan.",
             sleutel="koersen_onvolledig")

    if cached.empty:
        _meld_koersen(tickers, set(onvolledig))
        leeg = pd.DataFrame()
        leeg.attrs["koersen_onvolledig"] = onvolledig
        return leeg

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

    for t in tickers:
        if t not in pivot.columns:
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")

    _meld_koersen(tickers, set(pivot.columns) | set(onvolledig))
    pivot.attrs["koersen_onvolledig"] = onvolledig
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
