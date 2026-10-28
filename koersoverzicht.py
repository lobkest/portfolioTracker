import calendar
from datetime import date

import pandas as pd

from portfolio_calc import holdings_op_datums
from split_correctie import continue_reeks

# Volgorde = volgorde van de knoppen (KOERS_PERIODES in koersoverzicht.js).
PERIODES = ("maand", "ytd", "1m", "1j", "alles")
STANDAARD_PERIODE = "ytd"
EPSILON_STUKS = 1e-6
PCT_DECIMALEN = 2
EUR_DECIMALEN = 4


def periode_grens(periode, vandaag):
    """Startdatum van de periode, of None bij 'alles'."""
    if periode == "maand":
        return vandaag.replace(day=1)
    if periode == "ytd":
        return date(vandaag.year, 1, 1)
    if periode in ("1m", "1j"):
        terug = 1 if periode == "1m" else 12
        totaal = vandaag.year * 12 + (vandaag.month - 1) - terug
        jaar, maand = divmod(totaal, 12)
        maand += 1
        # Clamp zoals grensDatum() in periode.js: 31 maart min 1 maand = 28/29 februari.
        return date(jaar, maand, min(vandaag.day, calendar.monthrange(jaar, maand)[1]))
    if periode == "alles":
        return None
    raise ValueError(f"Onbekende periode: {periode}")


def _afgerond_of_none(waarde, decimalen):
    return None if pd.isna(waarde) else round(float(waarde), decimalen)


def bouw_koersoverzicht(transacties_df, price_data, splits_per_ticker, periode, vandaag):
    """{periode, vanaf, labels, reeksen: [{ticker, naam, pct, in_bezit, pct_periode, koers_begin, koers_nu}], ontbreken: [{ticker, naam}]}
    voor de posities met stukken op de laatste koersdag; reeksen aflopend op pct_periode."""
    grens = periode_grens(periode, vandaag)
    prijzen = price_data.copy()
    prijzen.index = pd.to_datetime(prijzen.index)
    prijzen = prijzen.sort_index()
    if grens is not None:
        prijzen = prijzen[prijzen.index >= pd.Timestamp(grens)]
    labels = list(prijzen.index)

    df = transacties_df.dropna(subset=["ticker"])
    namen = df.drop_duplicates(subset=["ticker"], keep="last").set_index("ticker")["product"].to_dict()
    laatste_dag = price_data.index.max() if len(price_data.index) else pd.Timestamp(vandaag)
    laatste_dag = pd.Timestamp(laatste_dag)

    reeksen, ontbreken = [], []
    for ticker, rijen in df.groupby("ticker"):
        if holdings_op_datums(rijen, [laatste_dag])[0] <= EPSILON_STUKS:
            continue
        naam = namen.get(ticker, ticker)
        if ticker not in price_data.columns or price_data[ticker].dropna().empty:
            ontbreken.append({"ticker": ticker, "naam": naam})
            continue

        # Splitcorrectie op de hele reeks, pas daarna afsnijden: de factor hangt van latere splits af.
        koers = continue_reeks(price_data[ticker].set_axis(pd.to_datetime(price_data.index)), splits_per_ticker.get(ticker, {}))
        koers = koers.sort_index().reindex(prijzen.index)
        geldig = koers.dropna()
        start = geldig.iloc[0] if not geldig.empty else None
        pct = [None if start is None or pd.isna(k) else _afgerond_of_none((k / start - 1) * 100, PCT_DECIMALEN) for k in koers]
        in_bezit = [h > EPSILON_STUKS for h in holdings_op_datums(rijen, labels)]
        niet_leeg = [p for p in pct if p is not None]
        reeksen.append({
            "ticker": ticker, "naam": naam, "pct": pct, "in_bezit": in_bezit,
            "pct_periode": niet_leeg[-1] if niet_leeg else None,
            "koers_begin": None if start is None else _afgerond_of_none(start, EUR_DECIMALEN),
            "koers_nu": None if geldig.empty else _afgerond_of_none(geldig.iloc[-1], EUR_DECIMALEN),
        })

    reeksen.sort(key=lambda r: (r["pct_periode"] is None, -(r["pct_periode"] or 0)))
    return {
        "periode": periode,
        "vanaf": labels[0].strftime("%Y-%m-%d") if labels else None,
        "labels": [d.strftime("%Y-%m-%d") for d in labels],
        "reeksen": reeksen,
        "ontbreken": ontbreken,
    }
