"""Verwacht jaardividend van één gezocht effect (bruto en netto) voor Zoeken & wat als; zonder rekeningoverzicht.
Eigen module: effect_profiel kan dividend_verwachting niet bovenaan importeren (kringverwijzing via portfolio_orchestratie)."""
from datetime import date

from backend.debug_utils import dprint
from backend.dividend_verwachting import (
    BELASTING_BRON_AANNAME_ETF, BRON_GEEN_UITKERINGEN, _als_reeks, _dag, _fx_op, _fx_reeksen, _naar_eur_factor, belasting_fractie,
    bruikbare_dividenden, haal_yahoo_dividenden, jaar_dividend_per_aandeel, keert_niet_uit, laatste_splitdatum,
)
from backend.ticker_prijscheck import _haal_splits_op

STATUS_KEERT_UIT = "keert_uit"
STATUS_KEERT_NIET_UIT = "keert_niet_uit"
STATUS_ONBEKEND = "onbekend"

WAARSCHUWING_GEEN_DIVIDEND_DATA = "geen_dividend_data"
WAARSCHUWING_VALUTA_ONBEKEND = "valuta_onbekend"

EUR_DECIMALEN = 4
YIELD_DECIMALEN = 6


def dividend_profiel(yahoo, splits, valuta, isin, koers_eur, fx_actueel, peildatum, is_etf=False):
    """yahoo: haal_yahoo_dividenden() of None; splits: {iso_datum: ratio}; fx_actueel: koers van het FX-paar (EUR: 1).
    Bedragen per stuk per jaar in EUR (bij GBp via dezelfde omrekening als positie_verwachting), None als onbekend.
    Een ETF zonder ISIN rekent met de standaardbelasting als aanname (belasting_onzeker): het land van het fonds beslist."""
    peil = _dag(peildatum)
    alle = _als_reeks(yahoo["dividenden"] if yahoo else None)
    venster, volledig_jaar = bruikbare_dividenden(alle, laatste_splitdatum(splits, []), peil)
    geen_uitkeringen = yahoo is not None and keert_niet_uit(alle, peil)
    per_stuk, bron = jaar_dividend_per_aandeel(
        venster, volledig_jaar and yahoo is not None, yahoo.get("dividend_rate") if yahoo else None,
        yahoo.get("trailing_rate") if yahoo else None, geen_uitkeringen)
    factor = _naar_eur_factor(fx_actueel, valuta)
    belasting, belasting_bron = belasting_fractie([], isin)
    belasting_onzeker = is_etf and not isin
    if belasting_onzeker:
        belasting_bron = BELASTING_BRON_AANNAME_ETF
    gepasseerd = alle[alle.index <= peil]

    if bron == BRON_GEEN_UITKERINGEN or per_stuk == 0:
        status = STATUS_KEERT_NIET_UIT
    elif per_stuk is None or factor is None:
        status = STATUS_ONBEKEND
    else:
        status = STATUS_KEERT_UIT

    bruto = per_stuk * factor if status == STATUS_KEERT_UIT else 0.0 if status == STATUS_KEERT_NIET_UIT else None
    netto = bruto * (1 - belasting) if bruto is not None else None
    return {
        "per_stuk_bruto_eur": None if bruto is None else round(bruto, EUR_DECIMALEN),
        "per_stuk_netto_eur": None if netto is None else round(netto, EUR_DECIMALEN),
        "yield_bruto": round(bruto / koers_eur, YIELD_DECIMALEN) if bruto is not None and koers_eur else None,
        "yield_netto": round(netto / koers_eur, YIELD_DECIMALEN) if netto is not None and koers_eur else None,
        "belasting_fractie": belasting, "belasting_bron": belasting_bron, "belasting_onzeker": belasting_onzeker,
        "bron": bron, "uitkeringen_12m": len(venster),
        "laatste_ex_datum": gepasseerd.index.max().date().isoformat() if not gepasseerd.empty else None,
        "status": status,
    }


def haal_dividend_profiel(ticker, valuta, isin, koers_eur, waarschuwingen, is_etf=False):
    """Een ophaalfout wordt een waarschuwing en status 'onbekend', geen crash."""
    try:
        yahoo = haal_yahoo_dividenden(ticker)
    except Exception as e:
        dprint(f"[profiel] WARN dividenden niet opgehaald ({e!a})")
        yahoo = None
    if yahoo is None:
        waarschuwingen.append(WAARSCHUWING_GEEN_DIVIDEND_DATA)
    try:
        splits = _haal_splits_op(ticker) or {}
    except Exception as e:
        dprint(f"[profiel] WARN splits niet opgehaald ({e!a})")
        splits = {}
    if not valuta:
        dprint(f"[profiel] WARN valuta van '{ticker}' onbekend, dividend niet om te rekenen naar EUR")
        waarschuwingen.append(WAARSCHUWING_VALUTA_ONBEKEND)
    peildatum = date.today()
    fx_actueel = _fx_actueel(valuta, peildatum)
    return dividend_profiel(yahoo, splits, valuta, isin, koers_eur, fx_actueel, peildatum, is_etf)


def _fx_actueel(valuta, peildatum):
    if not valuta:
        return None
    try:
        return _fx_op(valuta, peildatum, _fx_reeksen({valuta}))
    except Exception as e:
        dprint(f"[profiel] WARN wisselkoers {valuta} niet opgehaald ({e!a})")
        return None


def dividend_uitleg_velden(dividend):
    """De velden voor de (i)-uitleg bij het wat-als-resultaat, overgenomen uit het profiel (niet opnieuw berekend);
    allemaal None als het dividend onbekend is."""
    if not dividend or dividend["status"] == STATUS_ONBEKEND:
        return {sleutel: None for sleutel in _UITLEG_SLEUTELS.values()}
    return {sleutel: dividend[bron] for bron, sleutel in _UITLEG_SLEUTELS.items()}


_UITLEG_SLEUTELS = {
    "per_stuk_bruto_eur": "dividend_per_stuk_bruto_eur", "per_stuk_netto_eur": "dividend_per_stuk_netto_eur",
    "bron": "dividend_bron", "uitkeringen_12m": "dividend_uitkeringen_12m",
    "belasting_fractie": "dividend_belasting_fractie", "belasting_bron": "dividend_belasting_bron",
    "belasting_onzeker": "dividend_belasting_onzeker",
}


def dividend_voor_aantal(dividend, aantal):
    """(bruto, netto) per jaar in EUR voor `aantal` stuks; (None, None) bij een onbekend bedrag."""
    if not dividend or dividend["per_stuk_bruto_eur"] is None or dividend["per_stuk_netto_eur"] is None:
        return None, None
    return (round(aantal * dividend["per_stuk_bruto_eur"], 2), round(aantal * dividend["per_stuk_netto_eur"], 2))
