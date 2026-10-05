"""Yahoo-call-telling en de twee retry-patronen voor yfinance."""
import threading
import time

import pandas as pd
import yfinance as yf

from diagnostiek import meld, CATEGORIE_KOERSEN, INFO, LET_OP, FOUT
from transactie_utils import getal_nl


# Lock: de tellers worden ook vanuit worker-threads opgehoogd.
_yahoo_call_lock = threading.Lock()
_yahoo_call_teller = {}

# Globaal zodat worker-threads meetellen; melden gebeurt vanuit de hoofdthread.
_yahoo_retry_teller = {"retries": 0, "mislukt": 0, "wachttijd": 0.0}
DIAGNOSTIEK_SLEUTEL_YAHOO_KERN = "yahoo_kern"
DIAGNOSTIEK_SLEUTEL_YAHOO_VERRIJKING = "yahoo_verrijking"


def reset_yahoo_call_teller():
    with _yahoo_call_lock:
        _yahoo_call_teller.clear()
        _yahoo_retry_teller["retries"] = 0
        _yahoo_retry_teller["mislukt"] = 0
        _yahoo_retry_teller["wachttijd"] = 0.0


def _tel_yahoo_call(soort):
    with _yahoo_call_lock:
        _yahoo_call_teller[soort] = _yahoo_call_teller.get(soort, 0) + 1


def log_yahoo_call_samenvatting():
    with _yahoo_call_lock:
        samenvatting = dict(_yahoo_call_teller)
    totaal = sum(samenvatting.values())
    print(f"[timing] Yahoo-calls sinds laatste reset: {totaal} totaal -> {samenvatting}")


def _tel_yahoo_retry(soort, wachttijd=0.0):
    with _yahoo_call_lock:
        _yahoo_retry_teller[soort] += 1
        _yahoo_retry_teller["wachttijd"] += wachttijd


def yahoo_teller_stand():
    """(calls, retries, mislukt, wachttijd in seconden) sinds de laatste reset."""
    with _yahoo_call_lock:
        return (sum(_yahoo_call_teller.values()), _yahoo_retry_teller["retries"],
                _yahoo_retry_teller["mislukt"], _yahoo_retry_teller["wachttijd"])


def meld_yahoo_samenvatting(sleutel, omschrijving, vanaf=(0, 0, 0, 0.0)):
    """`vanaf` is een eerdere yahoo_teller_stand(). Alleen vanuit de hoofdthread aanroepen."""
    # max(0, ...): een reset door een andere request tussendoor geeft anders een negatief aantal.
    calls, retries, mislukt, wachttijd = (max(0, nu - toen) for nu, toen in zip(yahoo_teller_stand(), vanaf))
    niveau = FOUT if mislukt > 0 else LET_OP if retries > 0 else INFO
    meld(CATEGORIE_KOERSEN, niveau,
         f"Yahoo-calls ({omschrijving}): {calls}, retries: {retries} (wachttijd {getal_nl(wachttijd, 1)} s), "
         f"mislukt (na eventuele retries): {mislukt}.",
         sleutel=sleutel)


RATE_LIMIT_POGINGEN = 3
RATE_LIMIT_WACHTTIJD_BASIS = 8  # seconden; oplopende backoff per poging: 8s, 16s, 24s, ...


def _is_rate_limit_fout(e):
    """Ook 'Invalid Crumb' en HTTP 401: zo faalt Yahoo onder parallelle last."""
    tekst = str(e).lower()
    return (
        "rate limit" in tekst
        or "too many requests" in tekst
        or "invalid crumb" in tekst
        or "error 401" in tekst
    )


def _met_rate_limit_retry(actie, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """Retry met oplopende backoff, alleen bij rate limits. Geeft (resultaat, None) of (None, fout)."""
    for poging in range(1, pogingen + 1):
        try:
            return actie(), None
        except Exception as e:
            if _is_rate_limit_fout(e) and poging < pogingen:
                wacht = wachttijd * poging
                _tel_yahoo_retry("retries", wacht)
                time.sleep(wacht)
                continue
            _tel_yahoo_retry("mislukt")
            return None, e
    return None, None


BULK_DOWNLOAD_POGINGEN = 3
BULK_DOWNLOAD_WACHTTIJD = 5  # seconden; vast (niet oplopend)


def _als_frame(data, ticker_of_pair):
    if isinstance(data, pd.Series):
        return data.to_frame(name=ticker_of_pair if isinstance(ticker_of_pair, str) else ticker_of_pair[0])
    return data


def _splits_per_ticker(stock_splits):
    """{ticker: {iso_datum: ratio}} uit Yahoo's 'Stock Splits'-kolom (0 = geen split die dag)."""
    resultaat = {}
    for ticker in stock_splits.columns:
        kolom = stock_splits[ticker]
        dagen = kolom[kolom.notna() & (kolom != 0)]
        resultaat[ticker] = {pd.Timestamp(d).date().isoformat(): float(r) for d, r in dagen.items()}
    return resultaat


def download_koersen_met_retry(ticker_of_pair, start_date, pogingen=BULK_DOWNLOAD_POGINGEN, wachttijd=BULK_DOWNLOAD_WACHTTIJD):
    """(Close, splits): Close met auto_adjust=False is split-gecorrigeerd maar niet dividend-gecorrigeerd; de splits komen
    uit dezelfde response. Bewust anders dan _met_rate_limit_retry: retry op elke fout, vaste wachttijd, bij mislukken
    een leeg DataFrame en {}. Een ticker zonder 'Stock Splits'-kolom staat niet in splits."""
    for poging in range(1, pogingen + 1):
        try:
            _tel_yahoo_call("yf.download")
            data = yf.download(ticker_of_pair, start=start_date, auto_adjust=False, actions=True, progress=False)
            close = _als_frame(data["Close"], ticker_of_pair)
            try:
                splits = _splits_per_ticker(_als_frame(data["Stock Splits"], ticker_of_pair))
            except KeyError:
                splits = {}
            return close, splits
        except Exception:
            if poging < pogingen:
                _tel_yahoo_retry("retries", wachttijd)
                time.sleep(wachttijd)
            else:
                _tel_yahoo_retry("mislukt")
                return pd.DataFrame(dtype=float), {}
