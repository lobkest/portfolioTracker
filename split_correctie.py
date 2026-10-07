"""Pure splitlogica: ruwe koersen uit Yahoo's split-gecorrigeerde Close, DeGiro-splitboekingen herkennen en aan Yahoo-splits koppelen."""
import datetime
import math
from typing import NamedTuple

import pandas as pd

from transactie_utils import _naar_tijd_of_none

# Float32-ruis in Yahoo's gecorrigeerde koersen wegwerken bij het terugrekenen.
KOERS_DECIMALEN = 6

# Een DeGiro-splitboeking telt als dezelfde split als Yahoo's als de datums niet verder uit elkaar liggen dan dit.
SPLIT_KOPPEL_MAX_DAGEN = 5
# DeGiro boekt hele stukken (14 stuks bij 1:3 -> 4, de rest in contanten). Toegestaan: een stuk onder floor(verwacht)
# (andere afrondingsregel) tot ceil(verwacht). Absoluut en niet relatief: 5% is bij 1000 stuks al 200 stuks.
SPLIT_KOPPEL_EXTRA_STUKS_ONDER = 1
# Zulke rijen zijn geen markttransactie: ze worden tegen het slot van de dag ervoor omgeboekt.
BOEKING_TIJD = datetime.time(0, 0)


class DegiroSplitGebeurtenis(NamedTuple):
    """Eén splitboeking: `oud_aantal` stuks uitgeboekt, `nieuw_aantal` stuks ingeboekt (beide positief)."""
    datum: pd.Timestamp
    oud_aantal: float
    nieuw_aantal: float


class SplitKoppeling(NamedTuple):
    gebeurtenis: DegiroSplitGebeurtenis
    yahoo_datum: pd.Timestamp
    yahoo_ratio: float
    index: int  # positie van de gebeurtenis in de aangeleverde lijst


class SplitBoeking(NamedTuple):
    """Een DeGiro-splitboeking met de index-labels van de transactierijen die erbij horen."""
    ticker: str
    gebeurtenis: DegiroSplitGebeurtenis
    rijen: tuple
    patroon: str  # "conversierij" of "wisselpaar"
    isins: tuple = ()  # (oud, nieuw) bij een wisselpaar


class Wisselpaar(NamedTuple):
    datum: pd.Timestamp
    oud_isin: str
    nieuw_isin: str
    oud_rijen: tuple
    nieuw_rijen: tuple
    oud_aantal: float
    nieuw_aantal: float


class SplitKoppelResultaat(NamedTuple):
    gekoppeld: list          # [(SplitBoeking, SplitKoppeling)]
    zonder_yahoo: list       # [SplitBoeking] zonder Yahoo-split binnen het venster
    zonder_boeking: list     # [(ticker, datum, ratio)]: Yahoo-split terwijl je stukken hield, geen DeGiro-boeking


def _splits_als_reeks(splits):
    """{datum: ratio} (ISO-strings, date of Timestamp) -> Series met genormaliseerde, gesorteerde Timestamps."""
    if splits is None or len(splits) == 0:
        return pd.Series(dtype=float)
    reeks = pd.Series({pd.Timestamp(d).normalize(): float(r) for d, r in dict(splits).items()})
    return reeks.sort_index()


def _factor_na_datum(index, splits):
    """Per datum in `index` het product van de ratio's van splits die er ná liggen (de splitdag zelf is al post-split)."""
    factor = pd.Series(1.0, index=index)
    for splitdatum, ratio in _splits_als_reeks(splits).items():
        factor[factor.index.normalize() < splitdatum] *= ratio
    return factor


def ruwe_koers(close, splits):
    """Yahoo-Close (gecorrigeerd voor latere splits) terug naar de koers zoals hij op die dag noteerde."""
    return (close * _factor_na_datum(close.index, splits)).round(KOERS_DECIMALEN)


def continue_reeks(ruwe_close, splits):
    """Omgekeerde van ruwe_koers(): een reeks zonder sprongen op splitdagen, voor benchmark en koersgrafiek."""
    return ruwe_close / _factor_na_datum(ruwe_close.index, splits)


def _past_bij_ratio(gebeurtenis, ratio):
    verwacht = round(gebeurtenis.oud_aantal * ratio, 9)  # afronden: 5 x (1/3) x 3 mag niet 4,999... worden
    onder = math.floor(verwacht) - SPLIT_KOPPEL_EXTRA_STUKS_ONDER
    boven = math.ceil(verwacht)
    return onder <= gebeurtenis.nieuw_aantal <= boven, abs(gebeurtenis.nieuw_aantal - verwacht)


def koppel_degiro_aan_yahoo_splits(gebeurtenissen, yahoo_splits):
    """(koppelingen, niet_gekoppelde_gebeurtenissen, niet_gekoppelde_yahoo_splits).
    Beste paar eerst (kleinste afwijking in stuks, dan kleinste datumverschil); elke split hoogstens één keer."""
    yahoo = _splits_als_reeks(yahoo_splits)
    kandidaten = []
    for g_i, gebeurtenis in enumerate(gebeurtenissen):
        for splitdatum, ratio in yahoo.items():
            dagen = abs((pd.Timestamp(gebeurtenis.datum).normalize() - splitdatum).days)
            past, afwijking = _past_bij_ratio(gebeurtenis, ratio)
            if dagen <= SPLIT_KOPPEL_MAX_DAGEN and past:
                kandidaten.append((afwijking, dagen, g_i, splitdatum, ratio))
    kandidaten.sort(key=lambda k: (k[0], k[1], k[2]))

    koppelingen = {}
    gebruikte_splits = set()
    for _afwijking, _dagen, g_i, splitdatum, ratio in kandidaten:
        if g_i in koppelingen or splitdatum in gebruikte_splits:
            continue
        koppelingen[g_i] = SplitKoppeling(gebeurtenissen[g_i], splitdatum, ratio, g_i)
        gebruikte_splits.add(splitdatum)

    return (
        [koppelingen[i] for i in sorted(koppelingen)],
        [g for i, g in enumerate(gebeurtenissen) if i not in koppelingen],
        [(d, float(r)) for d, r in yahoo.items() if d not in gebruikte_splits],
    )


def _is_boekingstijd(tijd):
    try:
        return _naar_tijd_of_none(tijd) == BOEKING_TIJD
    except (ValueError, TypeError):
        return False


def _is_zonder_kosten(kosten):
    return pd.isna(kosten) or float(kosten) == 0


def vind_wisselparen(df):
    """Splits met ISIN-wissel boekt DeGiro als verkoop oude ISIN + aankoop nieuwe ISIN: zelfde dag, tijd 00:00,
    zonder kosten, koers > 0. Geeft (wisselparen, onduidelijke_datums); meerdere ISIN's per kant is onduidelijk.
    Vereist de kolommen datum, tijd, isin, aantal en koers; zonder tijd of kosten is er niets te herkennen."""
    if df.empty or "tijd" not in df.columns:
        return [], []
    kosten = df["transactiekosten"] if "transactiekosten" in df.columns else pd.Series(0.0, index=df.index)
    kandidaat = (
        df["tijd"].map(_is_boekingstijd)
        & kosten.map(_is_zonder_kosten)
        & (pd.to_numeric(df["koers"], errors="coerce") > 0)
        & (pd.to_numeric(df["aantal"], errors="coerce").fillna(0) != 0)
        & df["isin"].notna()
    )
    paren, onduidelijk = [], []
    dagen = df.loc[kandidaat].assign(_dag=pd.to_datetime(df.loc[kandidaat, "datum"]).dt.normalize())
    for dag, groep in dagen.groupby("_dag"):
        uit = groep[groep["aantal"].astype(float) < 0]
        in_ = groep[groep["aantal"].astype(float) > 0]
        oude, nieuwe = set(uit["isin"]), set(in_["isin"])
        if not oude or not nieuwe or oude == nieuwe:
            continue
        if len(oude) > 1 or len(nieuwe) > 1:
            onduidelijk.append(dag)
            continue
        paren.append(Wisselpaar(
            datum=dag, oud_isin=next(iter(oude)), nieuw_isin=next(iter(nieuwe)),
            oud_rijen=tuple(uit.index), nieuw_rijen=tuple(in_.index),
            oud_aantal=float(-uit["aantal"].astype(float).sum()), nieuw_aantal=float(in_["aantal"].astype(float).sum()),
        ))
    return paren, onduidelijk


def isin_ketens(paren):
    """{isin: eind_isin} voor elke ISIN in een wisselpaar, ook ketens (A->B->C: A, B en C naar C).
    Een ISIN zonder wissel staat er niet in: gebruik .get(isin, isin)."""
    volgende = {}
    for paar in sorted(paren, key=lambda p: p.datum):
        volgende[paar.oud_isin] = paar.nieuw_isin
    ketens = {}
    for isin in set(volgende) | set(volgende.values()):
        eind, gezien = isin, {isin}
        # Een cyclus (A->B->A) is niet te verwachten, maar mag niet eindeloos lopen.
        while eind in volgende and volgende[eind] not in gezien:
            eind = volgende[eind]
            gezien.add(eind)
        ketens[isin] = eind
    return ketens


def bepaal_effectieve_datums(df, boekingen, splits_per_ticker):
    """Voegt 'effectieve_datum' toe: de datum vanaf wanneer een aantalswijziging meetelt voor de waarde.
    Een gekoppelde splitboeking telt mee vanaf Yahoo's splitdatum, zodat aantal en ruwe koers op dezelfde dag van basis
    wisselen; al het andere houdt zijn eigen datum. Geeft (df, SplitKoppelResultaat)."""
    df = df.copy()
    df["effectieve_datum"] = pd.to_datetime(df["datum"])

    gekoppeld, zonder_yahoo, zonder_boeking = [], [], []
    tickers = sorted({b.ticker for b in boekingen} | set(splits_per_ticker))
    for ticker in tickers:
        eigen = [b for b in boekingen if b.ticker == ticker]
        koppelingen, niet_gekoppeld, niet_gebruikte_splits = koppel_degiro_aan_yahoo_splits(
            [b.gebeurtenis for b in eigen], splits_per_ticker.get(ticker))
        for koppeling in koppelingen:
            boeking = eigen[koppeling.index]
            df.loc[list(boeking.rijen), "effectieve_datum"] = koppeling.yahoo_datum
            gekoppeld.append((boeking, koppeling))
        zonder_yahoo.extend(b for b in eigen if b.gebeurtenis in niet_gekoppeld)

        rijen_ticker = df[df["ticker"] == ticker]
        for splitdatum, ratio in niet_gebruikte_splits:
            gehouden = rijen_ticker.loc[rijen_ticker["effectieve_datum"] < splitdatum, "aantal"].astype(float).sum()
            if gehouden > 1e-9:
                zonder_boeking.append((ticker, splitdatum, ratio))
    return df, SplitKoppelResultaat(gekoppeld, zonder_yahoo, zonder_boeking)
