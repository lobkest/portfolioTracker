"""Resultaat en dividend per aankoop (lot) voor het Transacties-tabblad: FIFO per ISIN-keten, zie CLAUDE.md: Data en rekenen."""
import pandas as pd

from split_correctie import isin_ketens, vind_wisselparen
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch

AANTAL_EPSILON = 1e-9
STATUS_OPEN = "open"
STATUS_DEELS_VERKOCHT = "deels_verkocht"
STATUS_GESLOTEN = "gesloten"
GEEN_RESULTAAT = {"resultaat_eur": None, "resultaat_pct": None, "status": None, "dividend_eur": None, "dividend_pct": None}

# Op dezelfde dag gaat een dividend vóór de transacties: alleen stukken van vóór de uitbetaaldag tellen mee.
_VOLGORDE_DIVIDEND = 0
_VOLGORDE_TRANSACTIE = 1


class _Lot:
    def __init__(self, label, adj_aantal, koers):
        self.label = label
        self.open = adj_aantal
        self.verkocht = 0.0
        self.koers = koers
        self.kostenbasis = koers * adj_aantal if koers is not None else None
        self.gerealiseerd = 0.0
        self.dividend = 0.0


def _getal(waarde):
    return None if waarde is None or pd.isna(waarde) else float(waarde)


def laatste_koersen(price_data):
    """{ticker: laatste bekende koers in EUR} uit get_prices()."""
    if price_data is None or price_data.empty:
        return {}
    koersen = {}
    for ticker in price_data.columns:
        reeks = price_data[ticker].dropna()
        if len(reeks):
            koersen[ticker] = float(reeks.iloc[-1])
    return koersen


def _aankoopkoers(rij, adj_aantal):
    waarde_eur = _getal(rij.get("waarde_eur"))
    if waarde_eur is not None:
        return abs(waarde_eur) / adj_aantal
    koers, aantal = _getal(rij.get("koers")), _getal(rij.get("aantal"))
    if not koers or aantal is None:
        return None
    return abs(koers * aantal) / adj_aantal


def _groep_sleutel(rij, ketens):
    isin = rij.get("isin")
    if isin is not None and pd.notna(isin):
        return f"isin:{ketens.get(isin, isin)}"
    ticker = rij.get("ticker")
    if ticker is not None and pd.notna(ticker):
        return f"ticker:{ticker}"
    return None


def _verkoop(loten, stukken, verkoopkoers):
    for lot in loten:
        if stukken <= AANTAL_EPSILON:
            break
        if lot.open <= AANTAL_EPSILON:
            continue
        n = min(stukken, lot.open)
        if lot.koers is not None:
            lot.gerealiseerd += n * (verkoopkoers - lot.koers)
        lot.open -= n
        lot.verkocht += n
        stukken -= n


def _keer_dividend_uit(loten, netto_eur):
    open_loten = [lot for lot in loten if lot.open > AANTAL_EPSILON]
    totaal = sum(lot.open for lot in open_loten)
    for lot in open_loten:
        lot.dividend += netto_eur * lot.open / totaal


def _verwerk_groep(groep, dividenden, wisselratio_per_rij):
    """Loopt transacties en dividenden van één ISIN-keten chronologisch af; geeft de loten."""
    gebeurtenissen = [
        (pd.Timestamp(rij["datum"]).normalize(), _VOLGORDE_TRANSACTIE, volgnummer, label, rij)
        for volgnummer, (label, rij) in enumerate(groep.iterrows())
    ]
    gebeurtenissen += [
        (dag, _VOLGORDE_DIVIDEND, volgnummer, None, netto) for volgnummer, (dag, netto) in enumerate(dividenden)
    ]
    gebeurtenissen.sort(key=lambda g: g[:3])

    loten, toegepaste_wissels = [], set()
    for _dag, soort, _volgnummer, label, inhoud in gebeurtenissen:
        if soort == _VOLGORDE_DIVIDEND:
            _keer_dividend_uit(loten, inhoud)
            continue
        if label in wisselratio_per_rij:
            # Een split met ISIN-wissel zit niet in adj_aantal: de open stukken gaan hier over naar de nieuwe ISIN.
            paar_id, ratio = wisselratio_per_rij[label]
            if paar_id not in toegepaste_wissels:
                toegepaste_wissels.add(paar_id)
                for lot in loten:
                    lot.open *= ratio
                    lot.verkocht *= ratio
                    if lot.koers is not None:
                        lot.koers /= ratio
            continue
        rij = inhoud
        if _is_corporate_action_row(rij):
            continue
        adj_aantal, totaal_eur = _getal(rij.get("adj_aantal")), _getal(rij.get("totaal_eur"))
        if not adj_aantal or not totaal_eur:
            continue
        if adj_aantal > 0:
            loten.append(_Lot(label, adj_aantal, _aankoopkoers(rij, adj_aantal)))
        else:
            _verkoop(loten, -adj_aantal, abs(totaal_eur) / -adj_aantal)
    return loten


def _lot_uitkomst(lot, laatste_koers, met_dividend):
    if lot.open <= AANTAL_EPSILON:
        status = STATUS_GESLOTEN
    elif lot.verkocht > AANTAL_EPSILON:
        status = STATUS_DEELS_VERKOCHT
    else:
        status = STATUS_OPEN

    if lot.koers is None:
        resultaat = None
    elif status == STATUS_GESLOTEN:
        resultaat = lot.gerealiseerd
    elif laatste_koers is None:
        resultaat = None
    else:
        resultaat = lot.gerealiseerd + lot.open * (laatste_koers - lot.koers)

    heeft_basis = lot.kostenbasis is not None and abs(lot.kostenbasis) > AANTAL_EPSILON
    dividend = lot.dividend if met_dividend else None
    return {
        "resultaat_eur": resultaat,
        "resultaat_pct": resultaat / lot.kostenbasis * 100 if resultaat is not None and heeft_basis else None,
        "status": status,
        "dividend_eur": dividend,
        "dividend_pct": dividend / lot.kostenbasis * 100 if dividend is not None and heeft_basis else None,
    }


def bereken_transactie_resultaten(transacties_df, huidige_koersen, dividenden):
    """transacties_df: na compute_split_adjusted_shares() (adj_aantal). huidige_koersen: {ticker: EUR}.
    dividenden: records van db_get_dividenden() (datum, isin, netto_eur), of None zonder rekeningoverzicht.
    Geeft {rij-label: {resultaat_eur, resultaat_pct, status, dividend_eur, dividend_pct}} voor elke rij;
    alleen aankooprijen krijgen waarden. Dividend zit nooit in het resultaat."""
    uitkomst = {label: dict(GEEN_RESULTAAT) for label in transacties_df.index}
    if transacties_df.empty:
        return uitkomst

    paren, _onduidelijk = vind_wisselparen(transacties_df)
    ketens = isin_ketens(paren)
    wisselratio_per_rij = {
        label: (paar_id, paar.nieuw_aantal / paar.oud_aantal)
        for paar_id, paar in enumerate(paren) if paar.oud_aantal > 0
        for label in paar.oud_rijen + paar.nieuw_rijen
    }

    dividend_per_groep = {}
    for d in dividenden or []:
        netto, isin = _getal(d.get("netto_eur")), d.get("isin")
        if netto is None or not isin:
            continue
        sleutel = f"isin:{ketens.get(isin, isin)}"
        dividend_per_groep.setdefault(sleutel, []).append((pd.Timestamp(d["datum"]).normalize(), netto))

    sleutels = pd.Series([_groep_sleutel(rij, ketens) for _, rij in transacties_df.iterrows()],
                         index=transacties_df.index, dtype=object)
    for sleutel in sleutels.dropna().unique():
        groep = _sorteer_chronologisch(transacties_df[sleutels == sleutel])
        loten = _verwerk_groep(groep, dividend_per_groep.get(sleutel, []), wisselratio_per_rij)
        tickers = groep["ticker"].dropna() if "ticker" in groep.columns else pd.Series(dtype=object)
        laatste_koers = _getal(huidige_koersen.get(tickers.iloc[-1])) if len(tickers) else None
        for lot in loten:
            uitkomst[lot.label] = _lot_uitkomst(lot, laatste_koers, dividenden is not None)
    return uitkomst
