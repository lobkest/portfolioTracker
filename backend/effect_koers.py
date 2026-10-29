"""Koersgrafiek van een los gezocht effect (Zoeken & wat als): koers en totaalrendement incl. bruto dividend, in EUR.

Koersen worden bewust nooit gecachet of opgeslagen; alleen de FX- en dividendcache worden gebruikt.
"""
from datetime import date

import pandas as pd

from backend.dividend_verwachting import _naar_eur_factor, haal_yahoo_dividenden
from backend.effect_profiel import GeenKoers
from backend.debug_utils import dprint
from backend.prijzen import _fx_prijzen_serie, _ruwe_koersen_in_eur, _valuta_per_ticker
from backend.split_correctie import continue_reeks
from backend.ticker_classificatie import valuta_voor_effect
from backend.yahoo_client import download_koersen_met_retry

# Volgorde = volgorde van de knoppen (WAT_ALS_KOERS_PERIODES in wat_als.js).
PERIODE_JAREN = {"1j": 1, "3j": 3, "7j": 7}
STANDAARD_PERIODE = "1j"
PER_WAARDEN = ("dag", "week")
STANDAARD_PER = "week"
BESCHIKBAAR_SINDS_MARGE_DAGEN = 7
INDEX_DECIMALEN = 2
EUR_DECIMALEN = 4
RENDEMENT_DECIMALEN = 2
VREEMDE_VALUTA_MET_FX = ("USD", "GBP", "GBp")


class ValutaOnbekend(Exception):
    pass


def periode_start(periode, vandaag):
    jaren = PERIODE_JAREN[periode]
    try:
        return vandaag.replace(year=vandaag.year - jaren)
    except ValueError:  # 29 februari
        return vandaag.replace(year=vandaag.year - jaren, day=28)


def _vanaf(koersen_eur, start):
    koersen = koersen_eur.dropna().sort_index()
    if koersen.empty:
        return koersen
    return koersen[koersen.index >= pd.Timestamp(start)]


def _dividend_reeks(dividenden_eur):
    if dividenden_eur is None or len(dividenden_eur) == 0:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return dividenden_eur.dropna().groupby(level=0).sum().sort_index()


def _cumulatief_op(datums, dividenden, na):
    """Per datum de som van de dividenden met ex-datum in (na, datum]."""
    dividenden = dividenden[dividenden.index > na]
    if dividenden.empty:
        return pd.Series(0.0, index=datums)
    alle = datums.union(dividenden.index)
    return dividenden.reindex(alle).fillna(0.0).cumsum().reindex(datums)


def dagpunten(koersen_eur, dividenden_eur, start):
    """DataFrame (index = datum; koers_eur, koers_index, totaal_index), index 100 op de eerste handelsdag >= start.
    Een dividend telt vanaf zijn ex-datum, niet als hij op de eerste handelsdag zelf valt (die koers is al ex)."""
    koersen = _vanaf(koersen_eur, start)
    if koersen.empty:
        return pd.DataFrame(columns=["koers_eur", "koers_index", "totaal_index"])
    basis = koersen.iloc[0]
    uitgekeerd = _cumulatief_op(koersen.index, _dividend_reeks(dividenden_eur), koersen.index[0])
    return pd.DataFrame({
        "koers_eur": koersen,
        "koers_index": koersen / basis * 100,
        "totaal_index": (koersen + uitgekeerd) / basis * 100,
    })


def weekpunten(koersen_eur, dividenden_eur, start):
    """Zoals dagpunten, per kalenderweek: koers_eur = gemiddelde slotkoers, datum = laatste handelsdag van de week.
    Een dividend telt pas vanaf de week na de week van zijn ex-datum."""
    koersen = _vanaf(koersen_eur, start)
    if koersen.empty:
        return pd.DataFrame(columns=["koers_eur", "koers_index", "totaal_index"])
    maandag = (koersen.index - pd.to_timedelta(koersen.index.weekday, unit="D")).normalize()
    maandagen = koersen.groupby(maandag).mean().index
    weekkoers = pd.Series(koersen.groupby(maandag).mean().values,
                          index=pd.DatetimeIndex(koersen.index.to_series().groupby(maandag).max().values))

    dividenden = _dividend_reeks(dividenden_eur)
    dividenden = dividenden[dividenden.index >= pd.Timestamp(start)]
    uitgekeerd = [dividenden[dividenden.index < m].sum() for m in maandagen]
    basis = weekkoers.iloc[0]
    return pd.DataFrame({
        "koers_eur": weekkoers,
        "koers_index": weekkoers / basis * 100,
        "totaal_index": (weekkoers + pd.Series(uitgekeerd, index=weekkoers.index)) / basis * 100,
    })


def rendementen(koersen_eur, dividenden_eur, start):
    """(koers_rendement, totaal_rendement) in procenten, altijd uit de dagkoersen (eerste en laatste handelsdag)."""
    punten = dagpunten(koersen_eur, dividenden_eur, start)
    if punten.empty:
        return None, None
    return punten["koers_index"].iloc[-1] - 100, punten["totaal_index"].iloc[-1] - 100


def uitleg_uit_dagkoersen(koersen_eur, dividenden_eur, start):
    """{begin_datum, eind_datum, koers_begin_eur, koers_eind_eur, dividend_som_eur} uit dezelfde dagkoersen als
    rendementen(), ook bij de weekweergave; None zonder koersen. De dividendsom telt de ex-datums in (begin, eind]."""
    koersen = _vanaf(koersen_eur, start)
    if koersen.empty:
        return None
    dividenden = _dividend_reeks(dividenden_eur)
    som = dividenden[(dividenden.index > koersen.index[0]) & (dividenden.index <= koersen.index[-1])].sum()
    return {
        "begin_datum": koersen.index[0].strftime("%Y-%m-%d"), "eind_datum": koersen.index[-1].strftime("%Y-%m-%d"),
        "koers_begin_eur": _afgerond(koersen.iloc[0], EUR_DECIMALEN), "koers_eind_eur": _afgerond(koersen.iloc[-1], EUR_DECIMALEN),
        "dividend_som_eur": _afgerond(som, EUR_DECIMALEN),
    }


def beschikbaar_sinds(koersen_eur, start):
    koersen = _vanaf(koersen_eur, start)
    if koersen.empty:
        return None
    eerste = koersen.index[0]
    if eerste > pd.Timestamp(start) + pd.Timedelta(days=BESCHIKBAAR_SINDS_MARGE_DAGEN):
        return eerste.date()
    return None


def heeft_dividend(koersen_eur, dividenden_eur, start):
    koersen = _vanaf(koersen_eur, start)
    if koersen.empty:
        return False
    dividenden = _dividend_reeks(dividenden_eur)
    return bool(((dividenden.index > koersen.index[0]) & (dividenden.index <= koersen.index[-1])).any())


def dividenden_in_eur(dividenden, valuta, fx_serie):
    """Series (ex-datum -> EUR per aandeel). {iso_datum: bedrag in Yahoo-valuta}; FX-koers op de ex-datum (laatste bekende).
    Zonder FX-koers voor een datum wordt het dividend weggelaten, niet geschat."""
    if not dividenden:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    reeks = pd.Series({pd.Timestamp(d).normalize(): float(b) for d, b in dividenden.items()}).sort_index()
    if valuta == "EUR":
        return reeks
    if valuta not in VREEMDE_VALUTA_MET_FX or fx_serie is None or fx_serie.dropna().empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    fx = fx_serie.dropna().sort_index()
    fx_op_datum = fx.reindex(fx.index.union(reeks.index)).ffill().reindex(reeks.index)
    factor = fx_op_datum.map(lambda f: _naar_eur_factor(f, valuta))
    return (reeks * factor).dropna()


def haal_koersen_en_dividenden(ticker, start, valuta):
    """(continue koersreeks in EUR vanaf start, dividend-Series in EUR). Geen koerscache; lege reeks als Yahoo niets geeft."""
    close, splits = download_koersen_met_retry(ticker, start)
    ruw, splits_ok = _ruwe_koersen_in_eur(close, splits, [ticker], True)
    if ruw.empty or ticker not in ruw.columns or ruw[ticker].dropna().empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    koersen = continue_reeks(ruw[ticker].dropna(), splits_ok[ticker])
    koersen = _vanaf(koersen, start)

    try:
        data = haal_yahoo_dividenden(ticker)
    except Exception as e:
        print(f"[effect_koers] WARN dividenden van {ticker!a} niet opgehaald ({e!a})")
        data = None
    dividenden = (data or {}).get("dividenden") or {}
    prijzen_valuta = _valuta_per_ticker([ticker])[ticker]
    if prijzen_valuta != valuta:
        dprint(f"[effect_koers] WARN valuta '{valuta}' van {ticker!a} wijkt af van de koersvaluta '{prijzen_valuta}'")
    fx = _fx_prijzen_serie(valuta) if valuta in VREEMDE_VALUTA_MET_FX and dividenden else None
    return koersen, dividenden_in_eur(dividenden, valuta, fx)


def _afgerond(waarde, decimalen):
    return None if waarde is None or pd.isna(waarde) else round(float(waarde), decimalen)


def bouw_effect_koers(ticker, periode, per, vandaag=None):
    """{periode, per, punten: [{datum, koers_eur, koers_index, totaal_index}], koers_rendement, totaal_rendement,
    beschikbaar_sinds, heeft_dividend, uitleg}. Bij periode 1j altijd per dag.
    Raises ValutaOnbekend (vóór de download) en GeenKoers."""
    vandaag = vandaag or date.today()
    start = periode_start(periode, vandaag)
    valuta = valuta_voor_effect(ticker)
    if valuta is None:
        raise ValutaOnbekend(ticker)
    koersen, dividenden = haal_koersen_en_dividenden(ticker, start, valuta)
    if _vanaf(koersen, start).empty:
        raise GeenKoers(ticker)

    per = "dag" if periode == "1j" else per
    punten = (dagpunten if per == "dag" else weekpunten)(koersen, dividenden, start)
    koers_rendement, totaal_rendement = rendementen(koersen, dividenden, start)
    sinds = beschikbaar_sinds(koersen, start)
    return {
        "periode": periode,
        "per": per,
        "punten": [
            {
                "datum": datum.strftime("%Y-%m-%d"),
                "koers_eur": _afgerond(rij.koers_eur, EUR_DECIMALEN),
                "koers_index": _afgerond(rij.koers_index, INDEX_DECIMALEN),
                "totaal_index": _afgerond(rij.totaal_index, INDEX_DECIMALEN),
            }
            for datum, rij in punten.iterrows()
        ],
        "koers_rendement": _afgerond(koers_rendement, RENDEMENT_DECIMALEN),
        "totaal_rendement": _afgerond(totaal_rendement, RENDEMENT_DECIMALEN),
        "beschikbaar_sinds": sinds.isoformat() if sinds else None,
        "heeft_dividend": heeft_dividend(koersen, dividenden, start),
        "uitleg": uitleg_uit_dagkoersen(koersen, dividenden, start),
    }
