"""Verwacht koersrendement en bandbreedte uit de koershistorie van de huidige posities (Prognose > Huidige portfolio).

Koersrendement in EUR op de continue (split-gecorrigeerde) reeks, zonder dividend. De bandbreedte komt uit een block
bootstrap op de maandrendementen van de nagebouwde portfolio als geheel, niet uit losse jaren per positie: een slecht jaar
van een kleine positie mag niet tellen alsof de hele portfolio dat jaar zo deed.
"""
import hashlib
import math
import threading

import numpy as np
import pandas as pd

from debug_utils import meet_tijd
from portfolio_calc import huidige_posities_per_keten
from portfolio_orchestratie import continue_koersreeks, laad_transacties_en_resultaat
from prijzen import get_prices
from transactie_utils import getal_nl

# Niet 01-01-1970: yfinance leest een start van epoch 0 als "geen start" en haalt dan maar één maand op.
HISTORIE_VROEGSTE_START = pd.Timestamp("1970-01-02")
HISTORIE_MIN_DEKKING = 0.5                  # index begint pas als posities met samen dit deel van het gewicht koers hebben
HISTORIE_MIN_MAANDEN = 36
HISTORIE_MAX_HORIZON_JAREN = 60             # gelijk aan JAREN_MAX in static/js/prognose.js
HISTORIE_PERCENTIELEN = (10, 25, 50, 75, 90)
BOOTSTRAP_PADEN = 5000
# Blokken van een jaar houden momentum en crashjaren bij elkaar; losse maanden zouden ze uitmiddelen.
BOOTSTRAP_BLOK_MAANDEN = 12
# Vast: anders verspringen de getallen bij elke klik.
BOOTSTRAP_SEED = 42
MAANDEN_PER_JAAR = 12
# In-process, per gunicorn-worker; de oudste valt eruit (5 x 721 getallen per portfolio).
BOOTSTRAP_CACHE_MAX = 64
# Afrondingsmarge bij het vergelijken van opgetelde gewichten met HISTORIE_MIN_DEKKING.
DEKKING_EPSILON = 1e-9
# Weekend plus een feestdag: de laatste handelsdag ligt hooguit zoveel dagen voor het kalendereinde van de maand.
MAAND_COMPLEET_MARGE_DAGEN = 3
HISTORIE_KORT_WAARSCHUWING_JAREN = 3
HISTORIE_MIN_JAREN = 1                      # korter: geen rollend jaar mogelijk, positie telt niet mee
HISTORIE_PERCENTIEL_LAAG = 10
HISTORIE_PERCENTIEL_HOOG = 90
HISTORIE_MIDDEN_WAARSCHUWING_PCT = 12       # daarboven: waarschuwing over het selectie-effect
DAGEN_PER_JAAR = 365.25
# Kleinere stukken "niet compleet" zijn feestdagen rond de start, geen ontbrekende positie.
JAREN_NIET_COMPLEET_DREMPEL = 0.1

STATUS_OK = "ok"
STATUS_TE_KORT = "te_kort"
STATUS_GEEN_KOERS = "geen_koers"
STATUS_NOG_NIET_GELADEN = "nog_niet_geladen"

# Sleutel: hash van de maandrendementen + alle bootstrap-instellingen; zelfde invoer geeft zo zelfde uitkomst.
_bootstrap_cache = {}
_bootstrap_cache_lock = threading.Lock()


def _jaren_tussen(begin, eind):
    return (pd.Timestamp(eind) - pd.Timestamp(begin)).days / DAGEN_PER_JAAR


def _rond(fractie_of_none):
    return round(fractie_of_none * 100, 1) if fractie_of_none is not None else None


def venster(reeks, peildatum):
    """(reeks vanaf HISTORIE_VROEGSTE_START t/m peildatum zonder NaN, beschikbare jaren)."""
    peil = pd.Timestamp(peildatum).normalize()
    reeks = reeks.dropna()
    reeks = reeks[(reeks.index >= HISTORIE_VROEGSTE_START) & (reeks.index <= peil)]
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
    """(index die op 1 begint, jaren_niet_compleet, dekking_bij_start) van de huidige verdeling, dagelijks herbalanceren.
    Dagrendement = som van gewicht x dagrendement over de tickers die die dag een koers hebben, met de gewichten per
    dag herschaald naar 1. Tickers met minder dan HISTORIE_MIN_JAREN doen niet mee (hun gewicht gaat naar de rest).
    De index begint op de eerste dag waarop de meetellende tickers samen HISTORIE_MIN_DEKKING van het gewicht hebben;
    lege index als dat nooit lukt. jaren_niet_compleet: vanaf die start tot alle meetellende tickers een koers hadden."""
    meetellend = {}
    for ticker, reeks in reeksen.items():
        reeks = reeks.dropna()
        if gewichten.get(ticker, 0) > 0 and not reeks.empty and \
                _jaren_tussen(reeks.index[0], reeks.index[-1]) >= HISTORIE_MIN_JAREN:
            meetellend[ticker] = reeks
    if not meetellend:
        return pd.Series(dtype=float), 0.0, 0.0

    eerste_koers = pd.Series({t: r.index[0] for t, r in meetellend.items()})
    gewicht = pd.Series({t: gewichten[t] for t in meetellend})
    dekking = gewicht.groupby(eerste_koers).sum().sort_index().cumsum()
    voldoende = dekking[dekking >= HISTORIE_MIN_DEKKING - DEKKING_EPSILON]
    if voldoende.empty:
        return pd.Series(dtype=float), 0.0, 0.0
    start = voldoende.index[0]

    # Per ticker op zijn eigen reeks: na een eigen beursvakantie telt het rendement over beide dagen.
    rendementen = pd.DataFrame({t: r.pct_change() for t, r in meetellend.items()}).sort_index()
    rendementen = rendementen[rendementen.index >= start]
    heeft_koers = rendementen.notna()
    gewicht_per_dag = heeft_koers.mul(gewicht, axis=1).sum(axis=1)
    dag = rendementen.fillna(0).mul(gewicht, axis=1).sum(axis=1) / gewicht_per_dag.where(gewicht_per_dag > 0)
    dag.iloc[0] = 0.0
    index = (1 + dag.fillna(0)).cumprod()

    jaren_niet_compleet = max(0.0, _jaren_tussen(start, eerste_koers.max()))
    return index, jaren_niet_compleet, float(voldoende.iloc[0])


def maandrendementen(index):
    """Rendementen van maandeinde tot maandeinde als numpy-array. De eerste maand valt weg, de laatste ook als de index
    niet tot zijn laatste handelsdag loopt: een paar dagen groei zou anders als een hele maand meetellen."""
    if index.empty:
        return np.array([], dtype=float)
    maandeinden = index.resample("ME").last()
    laatste = index.index[-1]
    laatste_handelsdag = laatste.normalize() + pd.offsets.MonthEnd(0) - pd.Timedelta(days=MAAND_COMPLEET_MARGE_DAGEN)
    if laatste < laatste_handelsdag:
        maandeinden = maandeinden.iloc[:-1]
    return maandeinden.pct_change().dropna().to_numpy(dtype=float)


def bootstrap_percentielpaden(maandrendementen, maanden, n_paden=BOOTSTRAP_PADEN, blok_maanden=BOOTSTRAP_BLOK_MAANDEN,
                              seed=BOOTSTRAP_SEED):
    """{"p10": array, ...} per HISTORIE_PERCENTIELEN: groeifactor per maand 0 .. maanden (maand 0 = 1,0).
    Circulaire block bootstrap: elk pad plakt blokken van blok_maanden aaneengesloten historische maanden aan elkaar,
    vanaf willekeurige startmaanden; een blok dat over het einde loopt, gaat verder bij het begin."""
    rendementen = np.asarray(maandrendementen, dtype=float)
    sleutel = (hashlib.sha256(rendementen.tobytes()).hexdigest(), maanden, n_paden, blok_maanden, seed,
               HISTORIE_PERCENTIELEN)
    with _bootstrap_cache_lock:
        bewaard = _bootstrap_cache.get(sleutel)
    if bewaard is not None:
        return {k: v.copy() for k, v in bewaard.items()}

    aantal_blokken = math.ceil(maanden / blok_maanden)
    with meet_tijd("bootstrap_startindices"):
        rng = np.random.default_rng(seed)
        starts = rng.integers(0, len(rendementen), size=(n_paden, aantal_blokken))
        posities = (starts[:, :, None] + np.arange(blok_maanden)) % len(rendementen)
        posities = posities.reshape(n_paden, aantal_blokken * blok_maanden)[:, :maanden].T
    with meet_tijd("bootstrap_rendementen"):
        factoren = 1 + rendementen[posities]
    # Maand x pad (getransponeerd): cumprod en percentielen lopen dan over aaneengesloten geheugen; zelfde uitkomst.
    with meet_tijd("bootstrap_cumprod"):
        groei = np.empty((maanden + 1, n_paden))
        groei[0] = 1.0
        np.cumprod(factoren, axis=0, out=groei[1:])
    with meet_tijd("bootstrap_percentielen"):
        waarden = np.percentile(groei, HISTORIE_PERCENTIELEN, axis=1, overwrite_input=True)
    paden = {f"p{p}": rij for p, rij in zip(HISTORIE_PERCENTIELEN, waarden)}

    with _bootstrap_cache_lock:
        if len(_bootstrap_cache) >= BOOTSTRAP_CACHE_MAX:
            _bootstrap_cache.pop(next(iter(_bootstrap_cache)))
        _bootstrap_cache[sleutel] = paden
    return {k: v.copy() for k, v in paden.items()}


def wis_bootstrap_cache():
    with _bootstrap_cache_lock:
        _bootstrap_cache.clear()


def horizonnen_uit_paden(paden, max_horizon_jaren):
    """{H: {p10, p25, ...}}: geannualiseerd rendement in procenten (1 decimaal) per heel jaar H."""
    return {
        horizon: {
            sleutel: round((float(pad[horizon * MAANDEN_PER_JAAR]) ** (1 / horizon) - 1) * 100, 1)
            for sleutel, pad in paden.items()
        }
        for horizon in range(1, max_horizon_jaren + 1)
    }


def positie_statistiek(reeks, peildatum):
    """{beschikbare_jaren, historie_vanaf (jaar), cagr_pct, laag_1j_pct, hoog_1j_pct, te_kort, kort} over het venster."""
    reeks, jaren = venster(reeks, peildatum)
    een_jaar = percentielen(rollende_cagrs(reeks, 1)) or {}
    return {
        "beschikbare_jaren": round(jaren, 1),
        "historie_vanaf": int(reeks.index[0].year) if not reeks.empty else None,
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


def _horizonnen_met_waarschuwing(horizonnen):
    """{"H": {p10, p25, p50, p75, p90, waarschuwing}}: de selectie-waarschuwing hangt af van de horizon die de
    frontend kiest, dus per horizon."""
    return {
        str(horizon): {**v, "waarschuwing": (waarschuwingen([], v["p50"], 0.0, False) or [None])[0]}
        for horizon, v in horizonnen.items()
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
    koersen = get_prices(tickers, HISTORIE_VROEGSTE_START) if tickers else pd.DataFrame()
    onvolledig = set(koersen.attrs.get("koersen_onvolledig", [])) & set(tickers)

    rijen, reeksen, waarde_per_ticker = [], {}, {}
    for p in posities:
        ticker = p["ticker"]
        ruw = koersen[ticker].dropna() if ticker in koersen.columns else pd.Series(dtype=float)
        # Nooit de ruwe koers: die springt op splitdagen (zie CLAUDE.md: Data en rekenen).
        reeks = continue_koersreeks(ticker, ruw) if not ruw.empty else None
        statistiek = positie_statistiek(reeks, peildatum) if reeks is not None else {
            "beschikbare_jaren": 0.0, "historie_vanaf": None, "cagr_pct": None, "laag_1j_pct": None, "hoog_1j_pct": None,
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
    index, jaren_niet_compleet, dekking_bij_start = portfolio_index(reeksen, gewichten)
    rendementen = maandrendementen(index)
    beschikbaar = len(rendementen) >= HISTORIE_MIN_MAANDEN

    paden, horizonnen = None, {}
    if beschikbaar:
        with meet_tijd("historisch_rendement_bootstrap"):
            paden = bootstrap_percentielpaden(rendementen, HISTORIE_MAX_HORIZON_JAREN * MAANDEN_PER_JAAR)
        horizonnen = _horizonnen_met_waarschuwing(horizonnen_uit_paden(paden, HISTORIE_MAX_HORIZON_JAREN))

    for rij in rijen:
        waarde = rij.pop("_waarde")
        rij["gewicht"] = round(waarde / totaal, 4) if waarde is not None and totaal > 0 else None
    return {
        "beschikbaar": beschikbaar,
        "melding": None if beschikbaar else (
            f"Te weinig koershistorie: minstens {HISTORIE_MIN_MAANDEN} maanden nodig van posities die samen minstens "
            f"{round(HISTORIE_MIN_DEKKING * 100)}% van je portfolio vormen."),
        "onvolledig": bool(onvolledig),
        "peildatum": peildatum.date().isoformat(),
        "paden": {k: [round(float(v), 4) for v in pad] for k, pad in paden.items()} if paden else None,
        "horizonnen": horizonnen,
        "historie_start": index.index[0].date().isoformat() if not index.empty else None,
        "historie_jaren": round(_jaren_tussen(index.index[0], index.index[-1]), 1) if not index.empty else 0.0,
        "dekking_bij_start": round(dekking_bij_start, 4),
        "aantal_maanden": len(rendementen),
        "posities": sorted(rijen, key=lambda r: -(r["gewicht"] or 0)),
        "waarschuwingen": waarschuwingen(rijen, None, jaren_niet_compleet, bool(onvolledig)),
        "jaren_niet_compleet": round(jaren_niet_compleet, 1),
    }
