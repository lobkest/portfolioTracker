"""Verwacht jaardividend per positie (Yahoo), vergeleken met het eigen ontvangen dividend uit het rekeningoverzicht.

Netto en niet herbelegd. Uitkeringen met een ex-datum op of vóór de laatste split tellen niet mee (zie CLAUDE.md:
Data en rekenen).
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from db import db_get_cached_ticker_dividenden, db_save_ticker_dividenden, db_get_dividenden, db_get_kassaldo
from diagnostiek import meld, CATEGORIE_DIVIDEND, GOED, INFO, LET_OP
from diagnostiek_checks import _beperk
from portfolio_calc import bepaal_split_boekingen, holdings_op_datums
from portfolio_orchestratie import laad_transacties_en_resultaat
from prijzen import FX_PAAR_PER_VALUTA, _fx_prijzen_serie, _valuta_per_ticker
from split_correctie import isin_ketens, vind_wisselparen
from ticker_prijscheck import _haal_splits_op
from ticker_zekerheid import TICKER_RESOLUTIE_POOL_GROOTTE
from transactie_utils import formatteer_datum_nl, getal_nl
from yahoo_client import _met_rate_limit_retry, _tel_yahoo_call

DIVIDEND_TERUGKIJK_DAGEN = 365
EX_DATUM_MAX_DAGEN_VOOR_BETALING = 60     # DeGiro betaalt meestal 2-5 weken na de ex-datum
EX_DATUM_SCHATTING_DAGEN = 30             # zonder Yahoo-ex-datum: geschatte ex-datum = betaaldatum - dit
# Langer geen uitkering: keert niet (meer) uit. Yahoo heeft soms één los artefact (TTWO: $0,001 in 2008).
GEEN_UITKERINGEN_NA_DAGEN = 2 * DIVIDEND_TERUGKIJK_DAGEN
DIVIDEND_AFWIJKING_LET_OP_FRACTIE = 0.15  # FX en afronding geven een paar procent; meer is verdacht
YAHOO_BRONNEN_AFWIJKING_INFO_FRACTIE = 0.20
PENCE_FACTOR = 100
PENCE_FACTOR_MARGE = 0.2                  # verhouding tussen 80 en 120 -> waarschijnlijk pence vs. pond
BRONBELASTING_PER_LAND = {"NL": 0.15, "US": 0.15, "IE": 0.0}  # US: 15% via DeGiro met W-8BEN
BRONBELASTING_STANDAARD = 0.15

BRON_YAHOO_REEKS = "yahoo_reeks"
BRON_EIGEN_DATA = "eigen_data"
BRON_DIVIDEND_RATE = "dividend_rate"
BRON_TRAILING_RATE = "trailing_rate"
BRON_GEEN_UITKERINGEN = "geen_uitkeringen"
BRON_ONBEKEND = "onbekend"

BELASTING_BRON_EIGEN = "eigen"
BELASTING_BRON_LAND = "land"
BELASTING_BRON_STANDAARD = "standaard"

_NIVEAU_VOLGORDE = {LET_OP: 0, INFO: 1, GOED: 2}


def _als_getal_of_none(waarde):
    try:
        getal = float(waarde)
    except (TypeError, ValueError):
        return None
    return getal if pd.notna(getal) else None


def haal_yahoo_dividenden(ticker):
    """{dividenden: {iso_ex_datum: bedrag per aandeel}, dividend_rate, trailing_rate[, info_mislukt]}, in Yahoo-valuta.
    None als .dividends faalt (dan niet gecachet); faalt alleen .info, dan zijn de rates None."""
    cached = db_get_cached_ticker_dividenden(ticker)
    if cached is not None:
        return cached

    def _dividenden():
        _tel_yahoo_call("yf.Ticker.dividends")
        return yf.Ticker(ticker).dividends

    def _info():
        _tel_yahoo_call("yf.Ticker.info(dividend)")
        return yf.Ticker(ticker).info or {}

    reeks, fout = _met_rate_limit_retry(_dividenden)
    if fout is not None or reeks is None:
        return None
    # Eén poging: Render krijgt op .info een blijvende 401 (Invalid Crumb); retries kostten 8 + 16 s per ticker.
    info, info_fout = _met_rate_limit_retry(_info, pogingen=1)
    info = info if info_fout is None and info is not None else {}

    dividenden = {}
    for datum, bedrag in reeks.items():
        if pd.notna(bedrag):
            iso = pd.Timestamp(datum).date().isoformat()
            dividenden[iso] = dividenden.get(iso, 0.0) + float(bedrag)
    dividend_rate = _als_getal_of_none(info.get("dividendRate"))
    trailing_rate = _als_getal_of_none(info.get("trailingAnnualDividendRate"))
    db_save_ticker_dividenden(ticker, dividenden, dividend_rate, trailing_rate)
    return {"dividenden": dividenden, "dividend_rate": dividend_rate, "trailing_rate": trailing_rate,
            "info_mislukt": info_fout is not None}


def haal_yahoo_data_parallel(tickers):
    """{ticker: (dividenddata of None, splits)}. Melden doet de aanroeper: meld() is een no-op in worker-threads."""
    def _een(ticker):
        try:
            dividenden = haal_yahoo_dividenden(ticker)
        except Exception as e:
            print(f"[dividend] WARN dividenden van {ticker!a} niet opgehaald ({e!a})")
            dividenden = None
        try:
            splits = _haal_splits_op(ticker)
        except Exception as e:
            print(f"[dividend] WARN splits van {ticker!a} niet opgehaald ({e!a})")
            splits = {}
        return ticker, dividenden, splits

    if not tickers:
        return {}
    with ThreadPoolExecutor(max_workers=min(TICKER_RESOLUTIE_POOL_GROOTTE, len(tickers))) as pool:
        return {ticker: (dividenden, splits) for ticker, dividenden, splits in pool.map(_een, tickers)}


def _dag(datum):
    return pd.Timestamp(datum).normalize()


def _lege_reeks():
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def _als_reeks(dividenden):
    """{iso: bedrag} of Series -> Series op tz-loze dag, oplopend."""
    if dividenden is None or len(dividenden) == 0:
        return _lege_reeks()
    reeks = pd.Series(dividenden) if isinstance(dividenden, dict) else dividenden.copy()
    index = pd.to_datetime(reeks.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    reeks.index = index.normalize()
    return reeks.astype(float).dropna().groupby(level=0).sum().sort_index()


def _venster_begin(peildatum):
    return _dag(peildatum) - pd.Timedelta(days=DIVIDEND_TERUGKIJK_DAGEN)


def laatste_splitdatum(yahoo_splits, degiro_split_datums):
    datums = [_dag(d) for d in list((yahoo_splits or {}).keys()) + list(degiro_split_datums or [])]
    return max(datums) if datums else None


def bruikbare_dividenden(reeks, splitdatum, peildatum):
    """(dividenden met ex-datum in (peildatum - DIVIDEND_TERUGKIJK_DAGEN, peildatum] én ná de split, volledig_jaar)."""
    reeks = _als_reeks(reeks)
    peil = _dag(peildatum)
    begin = _venster_begin(peil)
    masker = (reeks.index > begin) & (reeks.index <= peil)
    if splitdatum is not None:
        masker &= reeks.index > _dag(splitdatum)
    volledig_jaar = splitdatum is None or _dag(splitdatum) <= begin
    return reeks[masker], volledig_jaar


def keert_niet_uit(reeks, peildatum):
    """Geen uitkering in de afgelopen GEEN_UITKERINGEN_NA_DAGEN (of nooit)."""
    reeks = _als_reeks(reeks)
    return reeks.empty or reeks.index.max() <= _dag(peildatum) - pd.Timedelta(days=GEEN_UITKERINGEN_NA_DAGEN)


def jaar_dividend_per_aandeel(reeks_venster, volledig_jaar, dividend_rate, trailing_rate, geen_uitkeringen,
                              eigen_per_aandeel_eur=None):
    """(bedrag per aandeel per jaar of None, bron); bij eigen_data in EUR, anders in Yahoo-valuta.
    Na een split binnen het jaar wordt niet geëxtrapoleerd: trailing_rate bevat dan uitkeringen van vóór de split.
    Keert Yahoo volgens de reeks niets uit, dan winnen de rates niet: die zijn dan ruis (TTWO: trailing 0,0)."""
    if volledig_jaar and not geen_uitkeringen:
        return float(sum(reeks_venster)), BRON_YAHOO_REEKS
    if eigen_per_aandeel_eur is not None:
        return eigen_per_aandeel_eur, BRON_EIGEN_DATA
    if geen_uitkeringen:
        return 0.0, BRON_GEEN_UITKERINGEN
    if dividend_rate is not None:
        return dividend_rate, BRON_DIVIDEND_RATE
    if trailing_rate is not None and volledig_jaar:
        return trailing_rate, BRON_TRAILING_RATE
    return None, BRON_ONBEKEND


def eigen_jaar_per_aandeel(uitkeringen, aantal_per_betaaldatum, transactie_datums, splitdatum, venster_eind):
    """(som van bruto_eur / aantal, aantal meegeteld, {reden: aantal overgeslagen}) over de betaaldatums in
    (venster_eind - DIVIDEND_TERUGKIJK_DAGEN, venster_eind]. Zonder Yahoo-ex-datum: aantal_per_betaaldatum geeft de
    stukken op de dag vóór betaaldatum - EX_DATUM_SCHATTING_DAGEN. Overgeslagen: een transactie in de
    EX_DATUM_MAX_DAGEN_VOOR_BETALING ervoor (aantal onzeker) of geen bruto bedrag."""
    eind = _dag(venster_eind)
    begin = eind - pd.Timedelta(days=DIVIDEND_TERUGKIJK_DAGEN)
    split = _dag(splitdatum) if splitdatum is not None else None
    transacties = [_dag(d) for d in transactie_datums]
    som, meegeteld = 0.0, 0
    overgeslagen = {"transactie": 0, "geen_bedrag": 0}
    for u in uitkeringen:
        betaal = _dag(u["datum"])
        geschatte_ex = betaal - pd.Timedelta(days=EX_DATUM_SCHATTING_DAGEN)
        if not begin < betaal <= eind or (split is not None and geschatte_ex <= split):
            continue
        if u.get("bruto_eur") is None:
            overgeslagen["geen_bedrag"] += 1
            continue
        if any(betaal - pd.Timedelta(days=EX_DATUM_MAX_DAGEN_VOOR_BETALING) <= d <= betaal for d in transacties):
            overgeslagen["transactie"] += 1
            continue
        aantal = aantal_per_betaaldatum.get(betaal) or 0.0
        if aantal <= 0:
            continue
        som += u["bruto_eur"] / aantal
        meegeteld += 1
    return som, meegeteld, overgeslagen


def koppel_eigen_aan_ex_datums(eigen_uitkeringen, ex_datums):
    """([(uitkering, ex_datum)], [niet-gekoppelde uitkeringen]). Elke uitkering krijgt de laatste nog vrije ex-datum op
    of vóór de betaaldatum, binnen EX_DATUM_MAX_DAGEN_VOOR_BETALING. Van laat naar vroeg: anders pakt een betaling de
    ex-datum van de volgende uitkering als die al voor haar betaaldatum lag."""
    ex = sorted(_dag(d) for d in ex_datums)
    gebruikt = set()
    gekoppeld, los = [], []
    for uitkering in sorted(eigen_uitkeringen, key=lambda u: _dag(u["datum"]), reverse=True):
        betaal = _dag(uitkering["datum"])
        kandidaat = next((d for d in reversed(ex) if d <= betaal and d not in gebruikt), None)
        if kandidaat is not None and (betaal - kandidaat).days <= EX_DATUM_MAX_DAGEN_VOOR_BETALING:
            gebruikt.add(kandidaat)
            gekoppeld.append((uitkering, kandidaat))
        else:
            los.append(uitkering)
    gekoppeld.reverse()
    los.reverse()
    return gekoppeld, los


def _naar_eur_factor(fx, valuta):
    """fx is de koers van het FX-paar (bij GBp die van GBP): Yahoo noteert GBp in pence."""
    if fx is None:
        return None
    return fx / PENCE_FACTOR if valuta == "GBp" else fx


def vergelijk_uitkeringen(gekoppeld, aantal_per_ex_datum, yahoo_bedrag_per_ex_datum, fx_per_datum, valuta):
    """[{datum, ex_datum, aantal, eigen_per_aandeel_eur, yahoo_per_aandeel_eur, afwijking_fractie}]. Zonder stukken
    op de ex-datum of zonder bruto bedrag overgeslagen; fx_per_datum per betaaldatum (koers van het FX-paar)."""
    rijen = []
    for uitkering, ex in gekoppeld:
        ex = _dag(ex)
        aantal = aantal_per_ex_datum.get(ex) or 0.0
        bruto = uitkering.get("bruto_eur")
        if aantal <= 0 or bruto is None:
            continue
        betaal = _dag(uitkering["datum"])
        factor = _naar_eur_factor(fx_per_datum.get(betaal), valuta)
        yahoo = yahoo_bedrag_per_ex_datum.get(ex)
        yahoo_eur = yahoo * factor if yahoo is not None and factor is not None else None
        eigen = bruto / aantal
        rijen.append({
            "datum": betaal, "ex_datum": ex, "aantal": aantal,
            "eigen_per_aandeel_eur": eigen, "yahoo_per_aandeel_eur": yahoo_eur,
            "afwijking_fractie": eigen / yahoo_eur - 1 if yahoo_eur else None,
        })
    return rijen


def belasting_fractie(eigen_uitkeringen, isin):
    """(fractie, bron): eigen ingehouden belasting, anders per land van de ISIN, anders de standaard."""
    paren = [(u["bruto_eur"], u["belasting_eur"]) for u in eigen_uitkeringen
             if u.get("bruto_eur") is not None and u.get("belasting_eur") is not None]
    bruto = sum(b for b, _ in paren)
    if bruto > 0:
        return abs(sum(belasting for _, belasting in paren)) / bruto, BELASTING_BRON_EIGEN
    land = (isin or "")[:2]
    if land in BRONBELASTING_PER_LAND:
        return BRONBELASTING_PER_LAND[land], BELASTING_BRON_LAND
    return BRONBELASTING_STANDAARD, BELASTING_BRON_STANDAARD


def _per_betaaldatum(uitkeringen):
    """Uitkeringen op dezelfde dag (bv. een correctierij) als één uitkering; een onbekend bedrag blijft onbekend."""
    per_dag = {}
    for u in uitkeringen:
        dag = _dag(u["datum"])
        samen = per_dag.setdefault(dag, {"datum": dag, "bruto_eur": 0.0, "belasting_eur": 0.0})
        for sleutel in ("bruto_eur", "belasting_eur"):
            if samen[sleutel] is not None:
                samen[sleutel] = None if u.get(sleutel) is None else samen[sleutel] + u[sleutel]
    return [per_dag[dag] for dag in sorted(per_dag)]


def _is_pence_verhouding(verhouding):
    if not verhouding or verhouding <= 0:
        return False
    onder, boven = PENCE_FACTOR * (1 - PENCE_FACTOR_MARGE), PENCE_FACTOR * (1 + PENCE_FACTOR_MARGE)
    return onder <= verhouding <= boven or onder <= 1 / verhouding <= boven


def positie_verwachting(isin, ticker, bijnaam, aantal, valuta, yahoo, splitdatum, peildatum, fx_actueel,
                        eigen_uitkeringen=None, aantal_per_ex_datum=None, fx_per_datum=None, dekking=None,
                        aantal_per_betaaldatum=None, transactie_datums=(), aantal_bij_eigen_begin=None):
    """Positie-dict voor de API, plus '_controle' (alleen voor dividend_bevindingen()).
    yahoo: haal_yahoo_dividenden() of None; eigen_uitkeringen None = geen rekeningoverzicht; aantal_per_ex_datum:
    stukken op de dag vóór elke ex-datum; fx_actueel en fx_per_datum: koers van het FX-paar (EUR: 1);
    dekking: (eerste, laatste datum) van het rekeningoverzicht; aantal_per_betaaldatum en transactie_datums: zie
    eigen_jaar_per_aandeel(); aantal_bij_eigen_begin: stukken aan het begin van het eigen venster."""
    peil = _dag(peildatum)
    split = _dag(splitdatum) if splitdatum is not None else None
    aantal_per_ex_datum = aantal_per_ex_datum or {}

    alle = _als_reeks(yahoo["dividenden"] if yahoo else None)
    venster, volledig_jaar = bruikbare_dividenden(alle, split, peil)
    dividend_rate = yahoo.get("dividend_rate") if yahoo else None
    trailing_rate = yahoo.get("trailing_rate") if yahoo else None
    geen_uitkeringen = yahoo is not None and keert_niet_uit(alle, peil)

    # Het eigen venster eindigt op het rekeningoverzicht, niet op vandaag: daarna kan er nog niets in staan.
    eigen_data = eigen_uitkeringen is not None
    eigen_eind = _dag(dekking[1]) if dekking is not None else peil
    eigen_begin = _venster_begin(eigen_eind)
    dekking_begin = _dag(dekking[0]) if dekking is not None else None
    dekking_onvolledig = dekking_begin is not None and dekking_begin > eigen_begin

    def _in_eigen_venster(dag):
        return eigen_begin < dag <= eigen_eind and (split is None or dag > split)

    eigen = _per_betaaldatum(eigen_uitkeringen or [])
    eigen_venster = [u for u in eigen if _in_eigen_venster(u["datum"])]
    belasting, belasting_bron = belasting_fractie(eigen_venster, isin)

    eigen_som, eigen_aantal, overgeslagen = (
        eigen_jaar_per_aandeel(eigen, aantal_per_betaaldatum or {}, transactie_datums, split, eigen_eind)
        if eigen_data else (None, None, {}))
    # Alleen een vol jaar als bron: rekeningoverzicht en bezit over het hele venster, geen split erin.
    eigen_als_bron = (eigen_data and eigen_aantal > 0 and dekking is not None and not dekking_onvolledig
                      and (split is None or split <= eigen_begin) and (aantal_bij_eigen_begin or 0) > 0)

    per_aandeel, bron = jaar_dividend_per_aandeel(
        venster, volledig_jaar and yahoo is not None, dividend_rate, trailing_rate, geen_uitkeringen,
        eigen_som if eigen_als_bron else None)
    # Eigen data is al in EUR (DeGiro rekende om op de betaaldag).
    if bron == BRON_EIGEN_DATA:
        factor, valuta = 1.0, "EUR"
    else:
        factor = _naar_eur_factor(fx_actueel, valuta)
    meegeteld = per_aandeel is not None and factor is not None

    vergelijking, los_venster, gemist = [], [], []
    if eigen_data and yahoo is not None:
        gekoppeld, los = koppel_eigen_aan_ex_datums(eigen, list(alle.index))
        gekoppeld_venster = [(u, ex) for u, ex in gekoppeld if _in_eigen_venster(ex)]
        los_venster = [u for u in los if _in_eigen_venster(u["datum"])]
        vergelijking = vergelijk_uitkeringen(
            gekoppeld_venster, aantal_per_ex_datum, alle.to_dict(), fx_per_datum or {}, valuta)
        if dekking is not None:
            gebruikt = {ex for _, ex in gekoppeld}
            max_wachten = pd.Timedelta(days=EX_DATUM_MAX_DAGEN_VOOR_BETALING)
            # Pas gemist als de betaling er al had moeten zijn: anders is hij gewoon nog onderweg.
            gemist = [ex for ex in alle.index
                      if _in_eigen_venster(ex) and ex not in gebruikt and (aantal_per_ex_datum.get(ex) or 0) > 0
                      and dekking_begin <= ex and ex + max_wachten <= eigen_eind]

    eigen_bruto = eigen_som * aantal if eigen_data else None
    # Per aandeel over dezelfde uitkeringen: een nog niet betaalde of (bij een recente aankoop) niet ontvangen
    # uitkering maakt het verschil anders groot. Gemiste uitkeringen meldt een aparte check.
    met_yahoo = [r for r in vergelijking if r["yahoo_per_aandeel_eur"]]
    afwijking = None
    if met_yahoo and volledig_jaar and not dekking_onvolledig:
        afwijking = (sum(r["eigen_per_aandeel_eur"] for r in met_yahoo)
                     / sum(r["yahoo_per_aandeel_eur"] for r in met_yahoo) - 1)

    bruto_eur = per_aandeel * aantal * factor if meegeteld else None
    yahoo_bronnen = {}
    if volledig_jaar and not alle.empty and not geen_uitkeringen:
        yahoo_bronnen[BRON_YAHOO_REEKS] = float(venster.sum())
    if dividend_rate is not None:
        yahoo_bronnen[BRON_DIVIDEND_RATE] = dividend_rate
    if trailing_rate is not None:
        yahoo_bronnen[BRON_TRAILING_RATE] = trailing_rate

    return {
        "isin": isin, "ticker": ticker, "bijnaam": bijnaam, "aantal": aantal, "valuta": valuta,
        "per_aandeel_jaar": per_aandeel, "bron": bron,
        "laatste_split": split.date().isoformat() if split is not None else None, "volledig_jaar": volledig_jaar,
        "fx": factor, "bruto_eur_jaar": bruto_eur,
        "belasting_fractie": belasting, "belasting_bron": belasting_bron,
        "netto_eur_jaar": bruto_eur * (1 - belasting) if meegeteld else None,
        "eigen_bruto_eur_jaar": eigen_bruto, "eigen_aantal_uitkeringen": eigen_aantal,
        "afwijking_fractie": afwijking,
        "meegeteld": meegeteld,
        "_controle": {
            "vergelijking": vergelijking, "los": los_venster, "gemist": gemist, "yahoo_bronnen": yahoo_bronnen,
            "split_in_venster": not volledig_jaar, "dekking_onvolledig": dekking_onvolledig,
            "geen_fx": per_aandeel is not None and factor is None,
            "overgeslagen": overgeslagen if bron == BRON_EIGEN_DATA else {},
        },
    }


def _pct(fractie):
    return f"{'+' if fractie > 0 else ''}{getal_nl(fractie * 100, 1)}%"


def _eur(bedrag):
    return f"€ {getal_nl(bedrag, 4)}" if bedrag is not None else "onbekend"


def _naam(positie):
    return f"{positie['bijnaam'] or positie['ticker'] or positie['isin']} ({positie['isin']})"


def _bevinding(niveau, tekst, positie, soort, tabel=None):
    b = {"niveau": niveau, "tekst": tekst, "sleutel": f"div_verwachting:{positie['isin']}:{soort}"}
    if tabel is not None:
        b["tabel"] = tabel
    return b


def _is_pence_positie(positie):
    c = positie["_controle"]
    verhoudingen = [r["eigen_per_aandeel_eur"] / r["yahoo_per_aandeel_eur"]
                    for r in c["vergelijking"] if r["yahoo_per_aandeel_eur"]]
    return any(_is_pence_verhouding(v) for v in verhoudingen)


OVERGESLAGEN_REDEN_TEKST = {
    "transactie": "transactie in de {dagen} dagen ervoor, aantal stukken onzeker",
    "geen_bedrag": "geen bedrag in EUR (geen valutaconversie gekoppeld)",
}


def dividend_bevindingen(posities, eigen_data=True, info_mislukt=0):
    """Bevindingen {niveau, tekst, sleutel[, tabel]}, van ernstig naar licht; per check beperkt.
    info_mislukt: aantal tickers waarvoor Yahoo's .info faalde."""
    per_check = {soort: [] for soort in (
        "niet_meegeteld", "pence", "uitkering", "totaal", "gemist", "zonder_ex", "yahoo_bronnen", "split", "dekking",
        "overgeslagen")}
    for p in posities:
        c = p["_controle"]
        if not p["meegeteld"]:
            reden = (f"valuta {p['valuta']} heeft geen wisselkoers" if c["geen_fx"]
                     else "geen bruikbaar dividendbedrag van Yahoo of uit je eigen ontvangen dividend")
            per_check["niet_meegeteld"].append(_bevinding(
                LET_OP, f"Verwacht dividend van {_naam(p)}: {reden}; niet meegeteld in de verwachting.",
                p, "niet_meegeteld"))

        if _is_pence_positie(p):
            per_check["pence"].append(_bevinding(
                LET_OP, f"Dividend van {_naam(p)} wijkt ongeveer een factor {PENCE_FACTOR} af van Yahoo: "
                        f"waarschijnlijk pence vs. pond (Yahoo-valuta GBp).", p, "pence"))
        else:
            afwijkend = [r for r in c["vergelijking"] if r["afwijking_fractie"] is not None
                         and abs(r["afwijking_fractie"]) > DIVIDEND_AFWIJKING_LET_OP_FRACTIE]
            if afwijkend:
                tabel = {
                    "kolommen": ["Datum", "Eigen €/aandeel", "Yahoo €/aandeel", "Afwijking"],
                    "rijen": [[formatteer_datum_nl(r["datum"]), _eur(r["eigen_per_aandeel_eur"]),
                               _eur(r["yahoo_per_aandeel_eur"]), _pct(r["afwijking_fractie"])] for r in afwijkend],
                }
                per_check["uitkering"].append(_bevinding(
                    LET_OP, f"Ontvangen dividend van {_naam(p)} wijkt bij {len(afwijkend)} uitkering(en) meer dan "
                            f"{getal_nl(DIVIDEND_AFWIJKING_LET_OP_FRACTIE * 100)}% af van Yahoo.",
                    p, "uitkering", tabel))
            if p["afwijking_fractie"] is not None and abs(p["afwijking_fractie"]) > DIVIDEND_AFWIJKING_LET_OP_FRACTIE:
                per_check["totaal"].append(_bevinding(
                    LET_OP, f"Eigen dividend van {_naam(p)} over het afgelopen jaar wijkt {_pct(p['afwijking_fractie'])} "
                            f"af van Yahoo (bruto, per aandeel).", p, "totaal"))

        if c["gemist"]:
            datums = ", ".join(formatteer_datum_nl(d) for d in c["gemist"])
            per_check["gemist"].append(_bevinding(
                LET_OP, f"{_naam(p)}: Yahoo meldt een uitkering (ex-datum {datums}) terwijl je stukken had, maar in "
                        f"het rekeningoverzicht staat geen dividend.", p, "gemist"))
        if c["los"]:
            datums = ", ".join(formatteer_datum_nl(u["datum"]) for u in c["los"])
            per_check["zonder_ex"].append(_bevinding(
                LET_OP, f"{_naam(p)}: ontvangen dividend op {datums} past bij geen Yahoo-ex-datum binnen "
                        f"{EX_DATUM_MAX_DAGEN_VOOR_BETALING} dagen ervoor.", p, "zonder_ex"))

        bronnen = c["yahoo_bronnen"]
        if len(bronnen) > 1:
            hoogste, laagste = max(bronnen.values()), min(bronnen.values())
            if hoogste > 0 and (hoogste - laagste) / hoogste > YAHOO_BRONNEN_AFWIJKING_INFO_FRACTIE:
                tekst = ", ".join(f"{bron} {getal_nl(w, 4)}" for bron, w in bronnen.items())
                per_check["yahoo_bronnen"].append(_bevinding(
                    INFO, f"Yahoo's dividendbedragen voor {_naam(p)} lopen uiteen ({tekst} {p['valuta']}).",
                    p, "yahoo_bronnen"))
        if c["split_in_venster"]:
            per_check["split"].append(_bevinding(
                INFO, f"{_naam(p)}: split op {formatteer_datum_nl(p['laatste_split'])}, minder dan een jaar geleden; "
                      f"alleen uitkeringen daarna tellen mee, dus de verwachting steunt op beperkte data.",
                p, "split"))
        if c["dekking_onvolledig"]:
            per_check["dekking"].append(_bevinding(
                INFO, f"{_naam(p)}: het rekeningoverzicht begint binnen het afgelopen jaar; het eigen dividend is "
                      f"een onvolledig jaar en wordt niet met Yahoo vergeleken.", p, "dekking"))
        redenen = [f"{n}x {OVERGESLAGEN_REDEN_TEKST[reden].format(dagen=EX_DATUM_MAX_DAGEN_VOOR_BETALING)}"
                   for reden, n in c["overgeslagen"].items() if n]
        if redenen:
            per_check["overgeslagen"].append(_bevinding(
                INFO, f"{_naam(p)}: verwachting uit eigen ontvangen dividend; {sum(c['overgeslagen'].values())} "
                      f"uitkering(en) niet meegeteld ({'; '.join(redenen)}).", p, "overgeslagen"))

    bevindingen = []
    for soort, lijst in per_check.items():
        niveau = lijst[0]["niveau"] if lijst else INFO
        bevindingen.extend(_beperk(lijst, niveau, f"div_verwachting:meer:{soort}"))
    if info_mislukt:
        bevindingen.append({"niveau": INFO, "sleutel": "div_verwachting:info_mislukt",
                            "tekst": f"Yahoo-info niet bereikbaar voor {info_mislukt} ticker(s); alleen de "
                                     f"uitkeringsreeks gebruikt."})
    if not eigen_data:
        bevindingen.append({"niveau": INFO, "sleutel": "div_verwachting:geen_rekeningoverzicht",
                            "tekst": "Geen rekeningoverzicht geüpload: verwacht dividend alleen uit Yahoo, met "
                                     "aangenomen bronbelasting en zonder vergelijking met eigen data."})
    if not bevindingen:
        bevindingen.append({"niveau": GOED, "sleutel": "div_verwachting:ok",
                            "tekst": "Verwacht dividend: geen afwijkingen tussen Yahoo en je eigen dividend gevonden."})
    return sorted(bevindingen, key=lambda b: _NIVEAU_VOLGORDE.get(b["niveau"], 1))


def _fx_reeksen(valuta_set):
    """{valuta: FX-reeks of None}; EUR heeft geen reeks nodig. Vanuit de hoofdthread: gebruikt Flask's g."""
    reeksen = {}
    for valuta in valuta_set:
        if valuta == "EUR" or valuta not in FX_PAAR_PER_VALUTA:
            continue
        reeks = _fx_prijzen_serie(valuta).dropna()
        reeksen[valuta] = reeks if not reeks.empty else None
    return reeksen


def _fx_op(valuta, datum, reeksen):
    if valuta == "EUR":
        return 1.0
    reeks = reeksen.get(valuta)
    if reeks is None:
        return None
    waarde = reeks.asof(_dag(datum))
    return float(waarde) if pd.notna(waarde) else None


def _rond(waarde, decimalen):
    return round(float(waarde), decimalen) if waarde is not None and pd.notna(waarde) else None


def _voor_api(positie):
    p = {k: v for k, v in positie.items() if not k.startswith("_")}
    for sleutel in ("bruto_eur_jaar", "netto_eur_jaar", "eigen_bruto_eur_jaar"):
        p[sleutel] = _rond(p[sleutel], 2)
    for sleutel in ("per_aandeel_jaar", "belasting_fractie", "afwijking_fractie"):
        p[sleutel] = _rond(p[sleutel], 4)
    p["fx"] = _rond(p["fx"], 6)
    p["aantal"] = _rond(p["aantal"], 6)
    return p


def bereken_dividend_verwachting(code):
    """API-antwoord (zie de route in app.py), of None zonder koersdata of onbekende code."""
    transacties_df, resultaat = laad_transacties_en_resultaat(code)
    if transacties_df is None or resultaat is None or resultaat.empty:
        return None
    # Zelfde reeks als chart_data, waar de Prognose begint: yield en lijn rekenen met hetzelfde getal.
    peildatum = _dag(resultaat.index[-1])
    huidige_waarde = _als_getal_of_none(resultaat["waarde"].iloc[-1])

    ketens = isin_ketens(vind_wisselparen(transacties_df)[0])
    df = transacties_df[transacties_df["isin"].notna()].copy()
    df["_eind_isin"] = df["isin"].map(lambda i: ketens.get(i, i))
    boekingen = bepaal_split_boekingen(transacties_df)

    groepen = []
    for eind_isin, groep in df.groupby("_eind_isin"):
        aantal = holdings_op_datums(groep, [peildatum])[0]
        if aantal <= 1e-6:
            continue
        # Een ISIN kan op twee beurzen staan; dividend is per ISIN.
        per_ticker = {t: holdings_op_datums(g, [peildatum])[0] for t, g in groep.dropna(subset=["ticker"]).groupby("ticker")}
        ticker = max(per_ticker, key=per_ticker.get) if per_ticker else None
        rijen_naam = groep[groep["ticker"] == ticker] if ticker else groep
        bijnaam = rijen_naam.sort_values("datum")["product"].iloc[-1]
        rijen = set(groep.index)
        degiro_splits = [b.gebeurtenis.datum for b in boekingen if rijen & set(b.rijen)]
        groepen.append((eind_isin, groep, aantal, ticker, bijnaam, degiro_splits))

    tickers = sorted({g[3] for g in groepen if g[3]})
    yahoo_data = haal_yahoo_data_parallel(tickers)
    valuta_per_ticker = _valuta_per_ticker(tickers) if tickers else {}
    fx_reeksen = _fx_reeksen(set(valuta_per_ticker.values()))

    kassaldo = db_get_kassaldo(code)
    eigen_data = kassaldo is not None
    eigen_per_isin = {}
    if eigen_data:
        for u in db_get_dividenden(code):
            if u.get("isin"):
                eigen_per_isin.setdefault(ketens.get(u["isin"], u["isin"]), []).append(u)
    dekking = (kassaldo["eerste_datum"], kassaldo["per_datum"]) if eigen_data else None

    posities = []
    for eind_isin, groep, aantal, ticker, bijnaam, degiro_splits in groepen:
        yahoo, yahoo_splits = yahoo_data.get(ticker, (None, {})) if ticker else (None, {})
        valuta = valuta_per_ticker.get(ticker)
        ex_datums = list(_als_reeks(yahoo["dividenden"]).index) if yahoo else []
        # De dag vóór de ex-datum: wie op de ex-datum koopt, krijgt het dividend niet.
        aantallen = holdings_op_datums(groep, [ex - pd.Timedelta(days=1) for ex in ex_datums])
        eigen = eigen_per_isin.get(eind_isin, []) if eigen_data else None
        betaaldatums = sorted({_dag(u["datum"]) for u in (eigen or [])})
        schatting = pd.Timedelta(days=EX_DATUM_SCHATTING_DAGEN + 1)
        aantal_per_betaaldatum = dict(zip(betaaldatums, holdings_op_datums(groep, [d - schatting for d in betaaldatums])))
        aantal_bij_eigen_begin = (holdings_op_datums(groep, [_venster_begin(dekking[1])])[0] if eigen_data else None)
        posities.append(positie_verwachting(
            isin=eind_isin, ticker=ticker, bijnaam=bijnaam, aantal=aantal, valuta=valuta, yahoo=yahoo,
            splitdatum=laatste_splitdatum(yahoo_splits, degiro_splits), peildatum=peildatum,
            fx_actueel=_fx_op(valuta, peildatum, fx_reeksen) if valuta else None,
            eigen_uitkeringen=eigen,
            aantal_per_ex_datum=dict(zip(ex_datums, aantallen)),
            fx_per_datum={_dag(u["datum"]): _fx_op(valuta, u["datum"], fx_reeksen) for u in (eigen or [])} if valuta else {},
            dekking=dekking,
            aantal_per_betaaldatum=aantal_per_betaaldatum,
            transactie_datums=list(groep["datum"]),
            aantal_bij_eigen_begin=aantal_bij_eigen_begin,
        ))

    info_mislukt = sum(1 for dividenden, _ in yahoo_data.values() if dividenden and dividenden.get("info_mislukt"))
    for b in dividend_bevindingen(posities, eigen_data=eigen_data, info_mislukt=info_mislukt):
        meld(CATEGORIE_DIVIDEND, b["niveau"], b["tekst"], sleutel=b["sleutel"], tabel=b.get("tabel"))

    meegeteld = [p for p in posities if p["meegeteld"]]
    totaal_bruto = sum(p["bruto_eur_jaar"] for p in meegeteld)
    totaal_netto = sum(p["netto_eur_jaar"] for p in meegeteld)
    return {
        "beschikbaar": True,
        "peildatum": peildatum.date().isoformat(),
        "huidige_waarde_eur": _rond(huidige_waarde, 2),
        "totaal_bruto_eur_jaar": _rond(totaal_bruto, 2),
        "totaal_netto_eur_jaar": _rond(totaal_netto, 2),
        "yield_netto": _rond(totaal_netto / huidige_waarde, 6) if huidige_waarde else None,
        "eigen_data": eigen_data,
        "eigen_per_datum": _dag(dekking[1]).date().isoformat() if eigen_data else None,
        "posities": [_voor_api(p) for p in sorted(posities, key=lambda p: -(p["netto_eur_jaar"] or 0))],
    }
