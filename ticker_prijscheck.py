"""Yahoo-slotkoers vs. DeGiro-transactieprijs voor één (ticker, datum), na FX- en split-correctie."""
import pandas as pd
import yfinance as yf

from db import get_cached_splits, save_splits, get_cached_prijscheck, save_prijscheck
from debug_utils import dprint
from yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from prijzen import FX_PAAR_PER_VALUTA, _fx_prijzen_serie
from ticker_classificatie import _ticker_details_met_cache

# Slotkoers vs. intraday-prijs: een kleine afwijking is normaal. "mild" telt niet als probleem.
PRIJSCHECK_DREMPEL_OK = 0.02
PRIJSCHECK_DREMPEL_WAARSCHUWING = 0.06

# Exact low <= koers <= high bleek te strak: bekend-goede tickers vielen er ~1-2% buiten.
DAGRANGE_TOLERANTIE = 0.05


def _haal_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """(high, low) van de eerste handelsdag op of na 'datum', in eigen valuta; (None, None) bij een fout."""
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
    """(slotkoers, high, low) in één call, in eigen valuta; (None, None, None) bij een fout."""
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
    """{iso_datum: ratio}; bij een fout leeg en niet gecachet."""
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
    """Product van alle splitratio's na 'vanaf_datum'; nodig door auto_adjust (zie CLAUDE.md: Yahoo en tickers)."""
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
    """FX-koers naar EUR op de eerste handelsdag op of na 'datum'; None als die er niet is."""
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
    """bekende_koers is altijd EUR. Geeft o.a. niveau ("ok"/"mild"/"waarschuwing"), match
    (False alleen bij "waarschuwing") en binnen_dagrange (None zonder high/low)."""
    datum = pd.Timestamp(datum).date()
    cached = get_cached_prijscheck(ticker, datum)
    if cached is not None:
        yahoo_koers, valuta, high, low = cached
        dprint(f"[prijscheck] '{ticker}' op {datum}: uit cache -> yahoo_koers={yahoo_koers}")
        if yahoo_koers is not None and high is None and low is None:
            # Rij zonder dagrange: alsnog aanvullen.
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
    if valuta is None:
        print(f"[prijscheck] WARN '{ticker}' op {datum}: valuta onbekend - "
              f"Yahoo-koers NIET omgerekend, aanname EUR")
    if valuta not in (None, "EUR"):
        # Historische datum: een verse FX-koers is hier nooit nodig.
        fx_koers = _fx_koers_op_datum(valuta, datum, verversen=False)
        if fx_koers is None:
            print(f"[prijscheck] WARN '{ticker}' op {datum}: geen FX-koers voor valuta "
                  f"'{valuta}' - geen prijsvergelijking mogelijk")
            # Geen ruwe bedragen in verschillende valuta vergelijken.
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
    """Primair buiten de dagrange; zonder dagrange de %-drempel. Geen koersdata telt hier niet als probleem."""
    binnen_dagrange = check.get("binnen_dagrange")
    if binnen_dagrange is not None:
        return not binnen_dagrange
    return check["match"] is False
