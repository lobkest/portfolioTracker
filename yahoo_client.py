"""
Gedeelde Yahoo-infrastructuur: call-telling (voor de performance-meting van
de upload-flow) en de rate-limit-/retry-logica die yfinance-aanroepers door
het hele project heen gebruiken (ticker-classificatie, prijscontrole,
koersen-download).

Losgetrokken uit analysis.py; ongewijzigd overgenomen.
"""
import threading
import time

import pandas as pd
import yfinance as yf


# Telt individuele Yahoo-calls (yfinance + yahooquery) per type, voor de
# performance-meting van de upload-flow. Lock nodig omdat ticker-resolutie/
# koersen/verrijking deels parallel draaien via ThreadPoolExecutor.
_yahoo_call_lock = threading.Lock()
_yahoo_call_teller = {}


def reset_yahoo_call_teller():
    """Zet de Yahoo-call-teller terug naar 0 -- aangeroepen aan het begin
    van _upload_impl() zodat elke upload zijn EIGEN call-aantal rapporteert,
    niet een cumulatief totaal sinds het opstarten van de server."""
    with _yahoo_call_lock:
        _yahoo_call_teller.clear()


def _tel_yahoo_call(soort):
    """Registreert één Yahoo-call van het gegeven type (bv. 'yf.download',
    'yahooquery.search', 'yf.Ticker.info')."""
    with _yahoo_call_lock:
        _yahoo_call_teller[soort] = _yahoo_call_teller.get(soort, 0) + 1


def log_yahoo_call_samenvatting():
    """Logt de Yahoo-call-tellingen sinds de laatste reset, gegroepeerd per
    type call, plus het totaal."""
    with _yahoo_call_lock:
        samenvatting = dict(_yahoo_call_teller)
    totaal = sum(samenvatting.values())
    print(f"[timing] Yahoo-calls sinds laatste reset: {totaal} totaal -> {samenvatting}")


# Gedeelde retry/backoff-instellingen voor _fetch_yf_info/_haal_slotkoers_op/
# _haal_dagrange_op (via _met_rate_limit_retry hieronder). Losstaand van
# BULK_DOWNLOAD_POGINGEN/_WACHTTIJD hieronder: dat is een functioneel ANDER
# retry-patroon (zie download_met_retry).
RATE_LIMIT_POGINGEN = 3
RATE_LIMIT_WACHTTIJD_BASIS = 8  # seconden; oplopende backoff per poging: 8s, 16s, 24s, ...


def _is_rate_limit_fout(e):
    """Herkent Yahoo's rate-limit-foutmeldingen, ongeacht exacte
    formulering/hoofdlettergebruik. Telt ook een 'Invalid Crumb'-fout of een
    HTTP 401 mee: bij een grotere gelijktijdige belasting (zie de
    28-posities-pooltest bij TICKER_RESOLUTIE_POOL_GROOTTE) faalt Yahoo soms
    hiermee i.p.v. een letterlijke rate-limit-melding, maar de remedie
    (retry met oplopende backoff via _met_rate_limit_retry) is hetzelfde --
    voorheen faalde dit in één keer definitief, zonder retry-poging."""
    tekst = str(e).lower()
    return (
        "rate limit" in tekst
        or "too many requests" in tekst
        or "invalid crumb" in tekst
        or "error 401" in tekst
    )


def _met_rate_limit_retry(actie, log_prefix, beschrijving,
                           pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """
    Voert 'actie' (een callable zonder argumenten die de eigenlijke Yahoo-
    call doet) uit met retry en oplopende backoff bij rate limiting --
    gedeeld door _fetch_yf_info, _haal_slotkoers_op en _haal_dagrange_op
    (dit patroon stond voorheen drie keer bijna-identiek uitgeschreven,
    zie CLAUDE.md).

    'log_prefix' is de []-logprefix (bv. 'yf-info', 'prijscheck'),
    'beschrijving' de tekst die in de retry-logregel na "rate limited voor"
    komt (bv. "'AAPL'" of "dagrange 'AAPL'") -- de aanroeper bepaalt de
    exacte formulering, want die verschilt per aanroeper.

    Geeft (resultaat, None) terug bij succes, of (None, fout) terug bij een
    definitieve mislukking na alle pogingen -- de aanroeper bepaalt zelf de
    juiste "leeg"-teruggave (None, (None, None), ...) en de exacte
    foutmelding, want die verschillen per aanroeper.
    """
    for poging in range(1, pogingen + 1):
        try:
            return actie(), None
        except Exception as e:
            if _is_rate_limit_fout(e) and poging < pogingen:
                wacht = wachttijd * poging
                # print(f"[{log_prefix}] rate limited voor {beschrijving} (poging {poging}/{pogingen}), "
                      # f"{wacht}s wachten...")
                time.sleep(wacht)
                continue
            return None, e
    return None, None


# BULK_DOWNLOAD_*: eigen, kleinere retry-instellingen voor download_met_retry
# hieronder -- functioneel anders dan _met_rate_limit_retry hierboven (zie
# de docstring van download_met_retry voor het verschil), dus bewust NIET
# via dezelfde helper geïmplementeerd.
BULK_DOWNLOAD_POGINGEN = 3
BULK_DOWNLOAD_WACHTTIJD = 5  # seconden; vast (niet oplopend)


def download_met_retry(ticker_of_pair, start_date, pogingen=BULK_DOWNLOAD_POGINGEN, wachttijd=BULK_DOWNLOAD_WACHTTIJD):
    """
    yf.download met automatische retry, bewust NIET via
    _met_rate_limit_retry (het gedeelde rate-limit-specifieke patroon
    hierboven): deze functie retryt op ELKE fout (niet alleen rate
    limiting), met een VASTE wachttijd (geen oplopende backoff), en geeft
    bij een definitieve mislukking een lege Series terug in plaats van
    None -- aanroepers (get_prices()) rekenen al op die lege-Series-vorm.
    Gebruikt voor bulk-downloads van (mogelijk meerdere) tickers tegelijk,
    waar een kortstondige netwerkhapering nog de moeite van een retry
    waard is zonder eerst op een rate-limit-specifieke foutmelding te
    wachten.
    """
    for poging in range(1, pogingen + 1):
        try:
            _tel_yahoo_call("yf.download")
            return yf.download(ticker_of_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
        except Exception as e:
            # print(f"[koersen] poging {poging}/{pogingen} mislukt voor {ticker_of_pair}: {e}")
            if poging < pogingen:
                time.sleep(wachttijd)
            else:
                # print(f"[koersen] definitief mislukt voor {ticker_of_pair}, sla over")
                return pd.Series(dtype=float)

