"""Verwacht koersrendement en bandbreedte uit de koershistorie van de huidige posities (Prognose > Huidige portfolio).

Koersrendement in EUR op de continue (split-gecorrigeerde) reeks, zonder dividend. De bandbreedte komt uit rollende
H-jaarsperioden van de portfolio als geheel, niet uit losse jaren (zie CLAUDE.md: Data en rekenen).
"""
import math

import numpy as np
import pandas as pd

from portfolio_calc import huidige_posities_per_keten
from portfolio_orchestratie import continue_koersreeks, laad_transacties_en_resultaat
from prijzen import get_prices
from transactie_utils import getal_nl

HISTORIE_TERUGKIJK_JAREN = 10
HISTORIE_KORT_WAARSCHUWING_JAREN = 3
HISTORIE_MIN_JAREN = 1                      # korter: geen rollend jaar mogelijk, positie telt niet mee
HISTORIE_PERCENTIEL_LAAG = 10
HISTORIE_PERCENTIEL_HOOG = 90
HISTORIE_EXTRA_JAREN_PER_HORIZON = 1        # horizon H vraagt minstens H + 1 jaar data, anders te weinig vensters
HISTORIE_MIDDEN_WAARSCHUWING_PCT = 12       # daarboven: waarschuwing over het selectie-effect
DAGEN_PER_JAAR = 365.25
# Kleinere stukken "niet compleet" zijn feestdagen rond de start, geen ontbrekende positie.
JAREN_NIET_COMPLEET_DREMPEL = 0.1

STATUS_OK = "ok"
STATUS_TE_KORT = "te_kort"
STATUS_GEEN_KOERS = "geen_koers"
STATUS_NOG_NIET_GELADEN = "nog_niet_geladen"


def _jaren_tussen(begin, eind):
    return (pd.Timestamp(eind) - pd.Timestamp(begin)).days / DAGEN_PER_JAAR


def _rond(fractie_of_none):
    return round(fractie_of_none * 100, 1) if fractie_of_none is not None else None


def venster(reeks, peildatum):
    """(reeks over de laatste HISTORIE_TERUGKIJK_JAREN t/m peildatum zonder NaN, beschikbare jaren)."""
    peil = pd.Timestamp(peildatum).normalize()
    begin = peil - pd.Timedelta(days=HISTORIE_TERUGKIJK_JAREN * DAGEN_PER_JAAR)
    reeks = reeks.dropna()
    reeks = reeks[(reeks.index >= begin) & (reeks.index <= peil)]
    if reeks.empty:
        return reeks, 0.0
    return reeks, _jaren_tussen(reeks.index[0], reeks.index[-1])


def cagr(reeks):
    """(eind/begin)^(1/jaren) - 1 als fractie; None bij minder dan HISTORIE_MIN_JAREN of een begin <= 0."""
    reeks = reeks.dropna()
    if reeks.empty:
        return None
    jaren = _jaren_tussen(reeks.index[0], reeks.index[-1])
    begin, eind = float(reeks.iloc[0]), float(reeks.iloc[-1])
    if jaren < HISTORIE_MIN_JAREN or not begin > 0 or not eind >= 0:
        return None
    return (eind / begin) ** (1 / jaren) - 1


def rollende_cagrs(reeks, horizon_jaren):
    """Per handelsdag t met t - H binnen de reeks: (koers[t] / koers op of vóór t - H)^(1/H) - 1, als fracties."""
    reeks = reeks.dropna()
    if reeks.empty:
        return []
    terug = pd.DateOffset(years=horizon_jaren)
    eind = reeks[reeks.index - terug >= reeks.index[0]]
    if eind.empty:
        return []
    begin = reeks.asof(eind.index - terug).to_numpy()
    verhouding = eind.to_numpy() / begin
    geldig = np.isfinite(verhouding) & (verhouding > 0)
    return list(verhouding[geldig] ** (1 / horizon_jaren) - 1)


def percentielen(waarden):
    """{midden, laag, hoog} (mediaan, p10, p90) of None bij een lege lijst."""
    if len(waarden) == 0:
        return None
    laag, midden, hoog = np.percentile(waarden, [HISTORIE_PERCENTIEL_LAAG, 50, HISTORIE_PERCENTIEL_HOOG])
    return {"midden": float(midden), "laag": float(laag), "hoog": float(hoog)}


def portfolio_index(reeksen, gewichten):
    """(index die op 1 begint, jaren_niet_compleet) van de huidige verdeling, dagelijks herbalanceren.
    Dagrendement = som van gewicht x dagrendement over de tickers die die dag een koers hebben, met de gewichten per
    dag herschaald naar 1. Tickers met minder dan HISTORIE_MIN_JAREN doen niet mee (hun gewicht gaat naar de rest).
    jaren_niet_compleet: hoe lang het duurde tot alle meetellende tickers een koers hadden."""
    meetellend = {}
    for ticker, reeks in reeksen.items():
        reeks = reeks.dropna()
        if gewichten.get(ticker, 0) > 0 and not reeks.empty and \
                _jaren_tussen(reeks.index[0], reeks.index[-1]) >= HISTORIE_MIN_JAREN:
            meetellend[ticker] = reeks
    if not meetellend:
        return pd.Series(dtype=float), 0.0

    # Per ticker op zijn eigen reeks: na een eigen beursvakantie telt het rendement over beide dagen.
    rendementen = pd.DataFrame({t: r.pct_change() for t, r in meetellend.items()}).sort_index()
    gewicht = pd.Series({t: gewichten[t] for t in meetellend})
    heeft_koers = rendementen.notna()
    gewicht_per_dag = heeft_koers.mul(gewicht, axis=1).sum(axis=1)
    dag = rendementen.fillna(0).mul(gewicht, axis=1).sum(axis=1) / gewicht_per_dag.where(gewicht_per_dag > 0)
    index = (1 + dag.fillna(0)).cumprod()
    index = index / index.iloc[0]

    eerste_koersen = [r.index[0] for r in meetellend.values()]
    return index, _jaren_tussen(min(eerste_koersen), max(eerste_koersen))


def horizon_verdeling(index, beschikbare_jaren):
    """{H: {midden, laag, hoog, aantal_vensters}} in procenten (1 decimaal), H = 1 .. beschikbare jaren - 1 (min. 1)."""
    if beschikbare_jaren < HISTORIE_MIN_JAREN:
        return {}
    # round(): een venster van 10 jaar begint op de eerste handelsdag en is dus 9,99... jaar.
    max_horizon = max(1, math.floor(round(beschikbare_jaren - HISTORIE_EXTRA_JAREN_PER_HORIZON, 1)))
    verdeling = {}
    for horizon in range(1, max_horizon + 1):
        cagrs = rollende_cagrs(index, horizon)
        p = percentielen(cagrs)
        if p is not None:
            verdeling[horizon] = {**{k: _rond(v) for k, v in p.items()}, "aantal_vensters": len(cagrs)}
    return verdeling


def positie_statistiek(reeks, peildatum):
    """{beschikbare_jaren, cagr_pct, laag_1j_pct, hoog_1j_pct, te_kort, kort} over het venster."""
    reeks, jaren = venster(reeks, peildatum)
    een_jaar = percentielen(rollende_cagrs(reeks, 1)) or {}
    return {
        "beschikbare_jaren": round(jaren, 1),
        "cagr_pct": _rond(cagr(reeks)),
        "laag_1j_pct": _rond(een_jaar.get("laag")),
        "hoog_1j_pct": _rond(een_jaar.get("hoog")),
        "te_kort": jaren < HISTORIE_MIN_JAREN,
        "kort": jaren < HISTORIE_KORT_WAARSCHUWING_JAREN,
    }


def _namen_met_jaren(posities):
    return ", ".join(f"{p['bijnaam']} ({getal_nl(p['beschikbare_jaren'], 1)} jaar)" for p in posities)


def waarschuwingen(posities, midden_pct, jaren_niet_compleet, onvolledig):
    """Teksten voor onder de tabel. posities: dicts met bijnaam, beschikbare_jaren, kort, te_kort en status."""
    teksten = []
    kort = [p for p in posities if p["status"] == STATUS_OK and p["kort"]]
    if kort:
        teksten.append(f"Korte koershistorie (minder dan {HISTORIE_KORT_WAARSCHUWING_JAREN} jaar): "
                       f"{_namen_met_jaren(kort)}. Voor die posities steunt de verwachting op weinig data.")
    te_kort = [p for p in posities if p["status"] == STATUS_TE_KORT]
    if te_kort:
        teksten.append(f"Telt niet mee (minder dan {HISTORIE_MIN_JAREN} jaar koershistorie): {_namen_met_jaren(te_kort)}. "
                       f"Hun gewicht is over de andere posities verdeeld.")
    geen = [p["bijnaam"] for p in posities if p["status"] == STATUS_GEEN_KOERS]
    if geen:
        teksten.append(f"Geen koershistorie: {', '.join(geen)}; telt niet mee.")
    if midden_pct is not None and midden_pct > HISTORIE_MIDDEN_WAARSCHUWING_PCT:
        teksten.append("Dit is de historie van posities die je nu bezit. Wat je nog hebt, heeft vaak goed gelopen; "
                       "doortrekken overschat de toekomst meestal.")
    if jaren_niet_compleet >= JAREN_NIET_COMPLEET_DREMPEL:
        teksten.append(f"In de eerste {getal_nl(jaren_niet_compleet, 1)} jaar van de historie bestonden nog niet al je "
                       f"posities; daar rekent de nagebouwde portfolio met de posities die er wel waren.")
    if onvolledig:
        teksten.append("Nog niet alle koershistorie geladen — klik opnieuw.")
    return teksten


def _status(ticker, reeks, statistiek, onvolledig):
    if ticker in onvolledig:
        return STATUS_NOG_NIET_GELADEN
    if reeks is None:
        return STATUS_GEEN_KOERS
    return STATUS_TE_KORT if statistiek["te_kort"] else STATUS_OK


def _horizonnen_met_waarschuwing(verdeling):
    """{"H": {midden, laag, hoog, aantal_vensters, waarschuwing}}: de selectie-waarschuwing hangt af van de horizon
    die de frontend kiest, dus per horizon."""
    return {
        str(horizon): {**v, "waarschuwing": (waarschuwingen([], v["midden"], 0.0, False) or [None])[0]}
        for horizon, v in verdeling.items()
    }


def bereken_historisch_rendement(code):
    """API-antwoord (zie de route in app.py), of None zonder koersdata of onbekende code."""
    transacties_df, resultaat = laad_transacties_en_resultaat(code)
    if transacties_df is None or resultaat is None or resultaat.empty:
        return None
    # Zelfde peildatum als chart_data, waar de Prognose begint.
    peildatum = pd.Timestamp(resultaat.index[-1]).normalize()
    posities = huidige_posities_per_keten(transacties_df, peildatum)
    tickers = sorted({p["ticker"] for p in posities if p["ticker"]})
    start = peildatum - pd.Timedelta(days=HISTORIE_TERUGKIJK_JAREN * DAGEN_PER_JAAR)
    koersen = get_prices(tickers, start) if tickers else pd.DataFrame()
    onvolledig = set(koersen.attrs.get("koersen_onvolledig", [])) & set(tickers)

    rijen, reeksen, waarde_per_ticker = [], {}, {}
    for p in posities:
        ticker = p["ticker"]
        ruw = koersen[ticker].dropna() if ticker in koersen.columns else pd.Series(dtype=float)
        # Nooit de ruwe koers: die springt op splitdagen (zie CLAUDE.md: Data en rekenen).
        reeks = continue_koersreeks(ticker, ruw) if not ruw.empty else None
        statistiek = positie_statistiek(reeks, peildatum) if reeks is not None else {
            "beschikbare_jaren": 0.0, "cagr_pct": None, "laag_1j_pct": None, "hoog_1j_pct": None,
            "te_kort": True, "kort": True}
        status = _status(ticker, reeks, statistiek, onvolledig)
        # Ruw aantal x ruwe koers: allebei op de basis van de peildatum.
        koers = ruw.asof(peildatum) if not ruw.empty else None
        waarde = p["aantal"] * float(koers) if koers is not None and pd.notna(koers) else None
        if waarde is not None:
            waarde_per_ticker[ticker] = waarde_per_ticker.get(ticker, 0.0) + waarde
        if status == STATUS_OK:
            reeksen[ticker] = venster(reeks, peildatum)[0]
        rijen.append({"isin": p["isin"], "ticker": ticker, "bijnaam": p["bijnaam"], "_waarde": waarde,
                      **statistiek, "status": status})

    totaal = sum(waarde_per_ticker.values())
    gewichten = {t: w / totaal for t, w in waarde_per_ticker.items()} if totaal > 0 else {}
    index, jaren_niet_compleet = portfolio_index(reeksen, gewichten)
    index_jaren = _jaren_tussen(index.index[0], index.index[-1]) if not index.empty else 0.0
    verdeling = horizon_verdeling(index, index_jaren) if not index.empty else {}

    for rij in rijen:
        waarde = rij.pop("_waarde")
        rij["gewicht"] = round(waarde / totaal, 4) if waarde is not None and totaal > 0 else None
    return {
        "beschikbaar": bool(verdeling),
        "onvolledig": bool(onvolledig),
        "peildatum": peildatum.date().isoformat(),
        "terugkijk_jaren": HISTORIE_TERUGKIJK_JAREN,
        "horizonnen": _horizonnen_met_waarschuwing(verdeling),
        "max_horizon": max(verdeling) if verdeling else 0,
        "posities": sorted(rijen, key=lambda r: -(r["gewicht"] or 0)),
        "waarschuwingen": waarschuwingen(rijen, None, jaren_niet_compleet, bool(onvolledig)),
        "jaren_niet_compleet": round(jaren_niet_compleet, 1),
    }
