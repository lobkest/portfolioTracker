"""Yahoo-slotkoers vs. DeGiro-transactieprijs voor één (ticker, datum), na FX- en split-correctie."""
import pandas as pd
import yfinance as yf

from db import (
    db_get_cached_splits, db_save_splits, db_get_cached_prijscheck, db_save_prijscheck,
    db_get_cached_prijschecks, db_save_prijschecks,
)
from yahoo_client import RATE_LIMIT_POGINGEN, RATE_LIMIT_WACHTTIJD_BASIS, _met_rate_limit_retry, _tel_yahoo_call
from prijzen import FX_PAAR_PER_VALUTA, _fx_prijzen_serie
from ticker_classificatie import _ticker_details_met_cache

# Slotkoers vs. intraday-prijs: een kleine afwijking is normaal. "mild" telt niet als probleem.
PRIJSCHECK_DREMPEL_OK = 0.02
PRIJSCHECK_DREMPEL_WAARSCHUWING = 0.06

# Ruimte voor afronding en intraday-ruis, maar krap genoeg om een andere share class (DIS vs. ACC) te vangen.
DAGRANGE_TOLERANTIE = 0.02
# Bij goedkope aandelen is 2% maar een paar cent: dan valt afronding/intraday-ruis er al buiten.
DAGRANGE_TOLERANTIE_EUR = 0.50


def dagrange_grenzen(low_eur, high_eur):
    """(ondergrens, bovengrens): per grens de ruimste van DAGRANGE_TOLERANTIE en DAGRANGE_TOLERANTIE_EUR."""
    ondergrens = low_eur - max(low_eur * DAGRANGE_TOLERANTIE, DAGRANGE_TOLERANTIE_EUR)
    bovengrens = high_eur + max(high_eur * DAGRANGE_TOLERANTIE, DAGRANGE_TOLERANTIE_EUR)
    return ondergrens, bovengrens


def afstand_tot_dagrange_pct(koers, low_eur, high_eur):
    """Zonder marge: 0 binnen [low, high], negatief = % onder de low, positief = % boven de high."""
    if koers < low_eur:
        return (koers - low_eur) / low_eur * 100
    if koers > high_eur:
        return (koers - high_eur) / high_eur * 100
    return 0.0


def _haal_dagrange_op(ticker, datum, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN, wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """(high, low) van de eerste handelsdag op of na 'datum', in eigen valuta; (None, None) bij een fout."""
    einddatum = pd.Timestamp(datum) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(dagrange)")
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=False, progress=False)[["High", "Low"]]

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
        return yf.download(ticker, start=datum, end=einddatum, auto_adjust=False, progress=False)[["Close", "High", "Low"]]

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
    cached = db_get_cached_splits(ticker)
    if cached is not None:
        return cached
    try:
        _tel_yahoo_call("yf.Ticker.splits")
        splits = yf.Ticker(ticker).splits
    except Exception:
        return {}
    resultaat = {pd.Timestamp(datum).date().isoformat(): float(ratio) for datum, ratio in splits.items()}
    db_save_splits(ticker, resultaat)
    return resultaat


def _cumulatieve_split_factor(ticker, vanaf_datum):
    """Product van alle splitratio's na 'vanaf_datum'; nodig omdat Yahoo's Close/High/Low altijd split-gecorrigeerd zijn (zie CLAUDE.md: Yahoo en tickers)."""
    return _split_factor_uit(_haal_splits_op(ticker), vanaf_datum)


def _split_factor_uit(splits, vanaf_datum):
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
    cached = db_get_cached_prijscheck(ticker, datum)
    if cached is not None:
        yahoo_koers, valuta, high, low = cached
        if yahoo_koers is not None and high is None and low is None:
            # Rij zonder dagrange: alsnog aanvullen.
            high, low = _haal_dagrange_op(ticker, datum)
            db_save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)
    else:
        yahoo_koers, high, low = _haal_koers_en_dagrange_op(ticker, datum)
        valuta = _ticker_details_met_cache(ticker).get("valuta")
        db_save_prijscheck(ticker, datum, yahoo_koers, valuta, high, low)

    split_factor = _cumulatieve_split_factor(ticker, datum) if yahoo_koers is not None and bekende_koers else 1.0
    return _beoordeel_prijs(ticker, datum, bekende_koers, yahoo_koers, valuta, high, low, split_factor)


def _beoordeel_prijs(ticker, datum, bekende_koers, yahoo_koers, valuta, high, low, split_factor):
    if yahoo_koers is None or not bekende_koers:
        return {
            "yahoo_koers": yahoo_koers, "yahoo_koers_gecorrigeerd": None, "split_factor": 1.0,
            "bekende_koers": bekende_koers, "afwijking_pct": None, "niveau": None, "match": None,
            "high": high, "low": low, "binnen_dagrange": None, "afstand_dagrange_pct": None,
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
                "high": high, "low": low, "binnen_dagrange": None, "afstand_dagrange_pct": None,
            }
        divisor = 100 if valuta == "GBp" else 1
        yahoo_koers_eur = yahoo_koers / divisor * fx_koers
        if high_eur is not None and low_eur is not None:
            high_eur = high_eur / divisor * fx_koers
            low_eur = low_eur / divisor * fx_koers
        valuta_conversie_toegepast = True

    yahoo_koers_gecorrigeerd = yahoo_koers_eur * split_factor
    if high_eur is not None and low_eur is not None:
        high_eur = high_eur * split_factor
        low_eur = low_eur * split_factor

    binnen_dagrange = None
    afstand_dagrange_pct = None
    if high_eur is not None and low_eur is not None:
        ondergrens, bovengrens = dagrange_grenzen(low_eur, high_eur)
        binnen_dagrange = ondergrens <= bekende_koers <= bovengrens
        afstand_dagrange_pct = afstand_tot_dagrange_pct(bekende_koers, low_eur, high_eur)

    afwijking_pct = abs(yahoo_koers_gecorrigeerd - bekende_koers) / bekende_koers * 100
    afwijking_fractie = afwijking_pct / 100
    if afwijking_fractie < PRIJSCHECK_DREMPEL_OK:
        niveau = "ok"
    elif afwijking_fractie < PRIJSCHECK_DREMPEL_WAARSCHUWING:
        niveau = "mild"
    else:
        niveau = "waarschuwing"

    toon_gecorrigeerd = split_factor != 1.0 or valuta_conversie_toegepast
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
        "afstand_dagrange_pct": afstand_dagrange_pct,
    }


def _haal_koersen_en_dagranges_op(ticker, datums, dagen_buffer=7, pogingen=RATE_LIMIT_POGINGEN,
                                  wachttijd=RATE_LIMIT_WACHTTIJD_BASIS):
    """{datum: (slotkoers, high, low)} van de eerste handelsdag op of na elke datum, in één download;
    None bij een fout. Datums zonder koers binnen de buffer krijgen (None, None, None)."""
    start = min(datums)
    einddatum = pd.Timestamp(max(datums)) + pd.Timedelta(days=dagen_buffer)

    def _actie():
        _tel_yahoo_call("yf.download(slotkoers+dagrange, reeks)")
        return yf.download(ticker, start=start, end=einddatum, auto_adjust=False, progress=False)[["Close", "High", "Low"]]

    raw, fout = _met_rate_limit_retry(_actie, pogingen, wachttijd)
    if fout is not None:
        return None

    if isinstance(raw.columns, pd.MultiIndex):
        # yf.download geeft bij 1 ticker soms toch multi-index-kolommen terug.
        raw.columns = raw.columns.get_level_values(0)

    geldig = raw.dropna()
    geldig.index = pd.to_datetime(geldig.index).tz_localize(None)
    resultaat = {}
    for datum in datums:
        begin = pd.Timestamp(datum)
        venster = geldig[(geldig.index >= begin) & (geldig.index < begin + pd.Timedelta(days=dagen_buffer))]
        if venster.empty:
            resultaat[datum] = (None, None, None)
        else:
            eerste = venster.iloc[0]
            resultaat[datum] = (float(eerste["Close"]), float(eerste["High"]), float(eerste["Low"]))
    return resultaat


def vergelijk_prijzen_op_datums(ticker, transacties):
    """vergelijk_prijs_op_datum() voor elke {datum, koers}, in dezelfde volgorde; ontbrekende datums met één
    Yahoo-download in plaats van één per datum."""
    datums = sorted({pd.Timestamp(t["datum"]).date() for t in transacties})
    cache = db_get_cached_prijschecks(ticker, datums)
    # Rij met koers maar zonder dagrange telt als ontbrekend, net als in vergelijk_prijs_op_datum().
    ontbrekend = [
        d for d in datums
        if d not in cache or (cache[d][0] is not None and cache[d][2] is None and cache[d][3] is None)
    ]
    if ontbrekend:
        valuta = _ticker_details_met_cache(ticker).get("valuta")
        gedownload = _haal_koersen_en_dagranges_op(ticker, ontbrekend)
        if gedownload is None:
            for d in ontbrekend:
                cache.setdefault(d, (None, valuta, None, None))
        else:
            rijen = []
            for d in ontbrekend:
                koers, high, low = gedownload[d]
                rijen.append((d, koers, valuta, high, low))
            db_save_prijschecks(ticker, rijen)
            cache.update({d: (koers, valuta, high, low) for d, koers, valuta, high, low in rijen})

    splits = _haal_splits_op(ticker)
    checks = []
    for t in transacties:
        datum = pd.Timestamp(t["datum"]).date()
        bekende_koers = float(t["koers"])
        yahoo_koers, valuta, high, low = cache[datum]
        split_factor = _split_factor_uit(splits, datum) if yahoo_koers is not None and bekende_koers else 1.0
        checks.append(_beoordeel_prijs(ticker, datum, bekende_koers, yahoo_koers, valuta, high, low, split_factor))
    return checks


def _prijscheck_is_probleem(check):
    """Primair buiten de dagrange; zonder dagrange de %-drempel. Geen koersdata telt hier niet als probleem."""
    binnen_dagrange = check.get("binnen_dagrange")
    if binnen_dagrange is not None:
        return not binnen_dagrange
    return check["match"] is False
