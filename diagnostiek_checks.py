"""Pure diagnostiek-checks: DataFrames/lijsten in, bevindingen uit. Melden doet de aanroeper (meld() is een no-op in threads).

Een bevinding is {"niveau", "tekst", "sleutel"}.
"""
import bisect

import pandas as pd

from diagnostiek import GOED, INFO, LET_OP
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl

MAX_BEVINDINGEN_PER_CHECK = 5
SYNTHETISCH_ORDER_ID_PREFIX = "SYN-"
# Alleen deze kolommen kunnen in oude rijen NULL zijn doordat er geen backfill is.
KOLOMMEN_ZONDER_BACKFILL = {
    "tijd": "de volgorde van transacties binnen een dag",
    "transactiekosten": "de getoonde kosten",
    "waarde_eur": "de GAK (die valt terug op Totaal EUR, incl. kosten, en wordt iets te hoog)",
}


# Intraday-koers en Yahoo-slot (plus DeGiro- vs. Yahoo-FX) verschillen normaal enkele procenten; een gemiste
# split geeft minstens 50%, een voor latere splits gecorrigeerde koers vaak een veelvoud.
MAX_TRANSACTIE_AFWIJKING_FRACTIE = 0.25
# Een echte 20x binnen één positie is zeldzaam; een verkeerde splitbasis geeft meestal een veel groter veelvoud.
MAX_WAARDE_INLEG_FACTOR = 20
# Een gemiste 2:1-split is -50% of +100% op één dag; zo'n dagbeweging is bij gewone aandelen zeldzaam.
MAX_DAGSPRONG_FRACTIE = 0.4
# Kleinere bedragen zijn afrondingsresten (bv. na een bijna volledige verkoop): een verhouding zegt dan niets.
MIN_BEDRAG_EUR = 1.0


def _bevinding(niveau, tekst, sleutel):
    return {"niveau": niveau, "tekst": tekst, "sleutel": sleutel}


def _beperk(bevindingen, niveau, sleutel_meer):
    """Bevindingen moeten al van ernstig naar licht gesorteerd zijn."""
    if len(bevindingen) <= MAX_BEVINDINGEN_PER_CHECK:
        return bevindingen
    rest = len(bevindingen) - MAX_BEVINDINGEN_PER_CHECK
    return bevindingen[:MAX_BEVINDINGEN_PER_CHECK] + [_bevinding(niveau, f"... en {rest} meer.", sleutel_meer)]


def _corporate_action_masker(df):
    if df.empty:
        return pd.Series(False, index=df.index)
    return df.apply(_is_corporate_action_row, axis=1).astype(bool)


def _weergavenaam(rij):
    naam = rij.get("echte_naam")
    return naam if pd.notna(naam) else rij.get("product")


def check_ontbrekende_kolommen(df):
    """Corporate-action-rijen tellen niet mee: die hebben van nature geen kosten of waarde."""
    if df.empty:
        return []
    echte = df[~_corporate_action_masker(df)]
    bevindingen = []
    for kolom, geraakt in KOLOMMEN_ZONDER_BACKFILL.items():
        rijen = echte[echte[kolom].isna()]
        if kolom == "waarde_eur":
            rijen = rijen[rijen["aantal"].astype(float) > 0]
        if rijen.empty:
            continue
        bevindingen.append(_bevinding(
            LET_OP,
            f"{len(rijen)} transacties hebben geen '{kolom}' (oudste: {formatteer_datum_nl(rijen['datum'].min())}). "
            f"Dit raakt {geraakt}. Er is geen backfill voor bestaande rijen; de oplossing is het portfolio "
            f"te verwijderen en opnieuw te uploaden.",
            f"data:ontbrekend:{kolom}",
        ))
    return bevindingen


def check_posities_zonder_ticker(df):
    if df.empty:
        return []
    kandidaten = df[~_corporate_action_masker(df) & df["ticker"].isna()]
    per_isin = []
    for isin, groep in kandidaten.groupby("isin", dropna=False):
        per_isin.append((len(groep), isin, groep))
    per_isin.sort(key=lambda x: -x[0])
    bevindingen = [
        _bevinding(
            LET_OP,
            f"{_weergavenaam(groep.iloc[0])} ({isin}) heeft {aantal} transacties zonder ticker "
            f"(eerste op {formatteer_datum_nl(groep['datum'].min())}): geen koersen, de positie telt niet mee.",
            f"data:geen_ticker:{isin}",
        )
        for aantal, isin, groep in per_isin
    ]
    return _beperk(bevindingen, LET_OP, "data:geen_ticker:meer")


def check_synthetische_order_ids(order_ids):
    aantal = sum(1 for o in order_ids if str(o).startswith(SYNTHETISCH_ORDER_ID_PREFIX))
    if not aantal:
        return []
    return [_bevinding(
        INFO,
        f"{aantal} van {len(order_ids)} transacties hebben een synthetische Order ID ({SYNTHETISCH_ORDER_ID_PREFIX}...): "
        f"bij een volgende upload kan het portfolio daardoor minder betrouwbaar herkend worden.",
        "data:synthetisch",
    )]


def check_corporate_action_rijen(df):
    if df.empty:
        return []
    ca = df[_corporate_action_masker(df)]
    per_isin = sorted(ca.groupby("isin", dropna=False), key=lambda x: -len(x[1]))
    bevindingen = [
        _bevinding(
            INFO,
            f"{_weergavenaam(groep.iloc[0])} ({isin}): {len(groep)} corporate-action-rijen "
            f"({formatteer_datum_nl(groep['datum'].min())} t/m {formatteer_datum_nl(groep['datum'].max())}); "
            f"hier is een split of wissel geboekt.",
            f"data:corporate_action:{isin}",
        )
        for isin, groep in per_isin
    ]
    return _beperk(bevindingen, INFO, "data:corporate_action:meer")


WISSEL_TIJD = "00:00"


def _is_wissel_rij(rij):
    kosten = rij["transactiekosten"]
    return str(rij["tijd"])[:5] == WISSEL_TIJD and (pd.isna(kosten) or float(kosten) == 0)


def _wissel_op_datum(groep):
    """(datum, oude_isin, nieuwe_isin, ratio) voor de eerste dag met een negatief aantal op de ene en een positief op een andere ISIN."""
    kandidaten = groep[groep.apply(_is_wissel_rij, axis=1)]
    for datum, dag in kandidaten.groupby("datum"):
        uit = dag[dag["aantal"].astype(float) < 0]
        erin = dag[dag["aantal"].astype(float) > 0]
        for oude_isin, oud in uit.groupby("isin"):
            nieuw = erin[erin["isin"] != oude_isin]
            if nieuw.empty:
                continue
            ratio = abs(oud["aantal"].astype(float).sum()) / nieuw["aantal"].astype(float).sum()
            return datum, oude_isin, nieuw["isin"].iloc[0], ratio
    return None


def check_isin_wissels(df):
    """Alleen melden, niets corrigeren."""
    if df.empty:
        return []
    bevindingen = []
    for ticker, groep in df.dropna(subset=["ticker"]).groupby("ticker"):
        isins = sorted(groep["isin"].dropna().unique())
        if len(isins) < 2:
            continue
        namen = ", ".join(f"{_weergavenaam(groep[groep['isin'] == i].iloc[0])} ({i})" for i in isins)
        wissel = _wissel_op_datum(groep)
        if wissel:
            datum, oud, nieuw, ratio = wissel
            tekst = (f"Ticker '{ticker}' heeft meerdere ISIN's: {namen}. ISIN-wissel van {oud} naar {nieuw} op "
                     f"{formatteer_datum_nl(datum)} (oud/nieuw aantal: {ratio:.4f}).")
        else:
            tekst = f"Ticker '{ticker}' heeft meerdere ISIN's: {namen}. Geen wisselpatroon herkend."
        bevindingen.append(_bevinding(INFO, tekst, f"isin_wissel:{ticker}"))
    return _beperk(bevindingen, INFO, "isin_wissel:meer")


def _pct(fractie):
    return f"{fractie * 100:.1f}%".replace(".", ",")


def _eur(bedrag):
    return "EUR " + f"{bedrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _naam_per_ticker(df):
    if df.empty:
        return {}
    met_ticker = df.dropna(subset=["ticker"])
    return {t: _weergavenaam(groep.iloc[0]) for t, groep in met_ticker.groupby("ticker")}


def _marktransacties(df):
    """Zonder corporate-action-, wissel- en conversierijen (koers 0): die hebben geen echte marktkoers."""
    if df.empty:
        return df
    df = df.dropna(subset=["ticker"])
    masker = ~_corporate_action_masker(df) & (df["koers"].astype(float) > 0)
    if "is_wisselrij" in df.columns:
        masker &= ~df["is_wisselrij"].astype(bool)
    return df[masker]


def _mediaan(afwijkingen):
    return float(pd.Series([a[0] for a in afwijkingen]).median())


def check_transactiekoers_vs_rekenkoers(transacties_df, price_data):
    """DeGiro-koers (EUR) per transactie tegen de koers waarmee het dashboard op die dag rekent."""
    if transacties_df.empty or price_data is None or price_data.empty:
        return []
    namen = _naam_per_ticker(transacties_df)
    per_positie = []
    for ticker, groep in _marktransacties(transacties_df).groupby("ticker"):
        if ticker not in price_data.columns:
            continue
        reeks = price_data[ticker]
        afwijkingen = []
        for _, rij in groep.iterrows():
            rekenkoers = reeks.asof(pd.Timestamp(rij["datum"]))
            if pd.isna(rekenkoers) or rekenkoers <= 0:
                continue
            koers = float(rij["koers"])
            afwijkingen.append((abs(koers / float(rekenkoers) - 1), rij["datum"], koers, float(rekenkoers)))
        if afwijkingen:
            per_positie.append((ticker, afwijkingen))

    bevindingen = []
    for ticker, afwijkingen in per_positie:
        grootste, datum, koers, rekenkoers = max(afwijkingen, key=lambda a: a[0])
        if grootste <= MAX_TRANSACTIE_AFWIJKING_FRACTIE:
            continue
        boven = sum(1 for a in afwijkingen if a[0] > MAX_TRANSACTIE_AFWIJKING_FRACTIE)
        bevindingen.append((grootste, _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): {boven} van {len(afwijkingen)} transactiekoersen wijken meer dan "
            f"{_pct(MAX_TRANSACTIE_AFWIJKING_FRACTIE)} af van de koers waarmee het dashboard rekent (mediaan "
            f"{_pct(_mediaan(afwijkingen))}, max. {_pct(grootste)} op {formatteer_datum_nl(datum)}: DeGiro "
            f"{_eur(koers)}, dashboard {_eur(rekenkoers)}). Mogelijk een verkeerde ticker of split.",
            f"plausibel:koers:{ticker}",
        )))
    if bevindingen:
        bevindingen.sort(key=lambda b: -b[0])
        return _beperk([b for _, b in bevindingen], LET_OP, "plausibel:koers:meer")
    if not per_positie:
        return []
    aantal = sum(len(a) for _, a in per_positie)
    return [_bevinding(
        GOED,
        f"Transactiekoersen van {len(per_positie)} posities ({aantal} transacties) liggen binnen "
        f"{_pct(MAX_TRANSACTIE_AFWIJKING_FRACTIE)} van de rekenkoers (hoogste mediane afwijking per positie: "
        f"{_pct(max(_mediaan(a) for _, a in per_positie))}).",
        "plausibel:koers",
    )]


def check_waarde_vs_inleg(per_ticker, namen):
    """per_ticker zoals compute_per_ticker(); 'geinvesteerd' is daar de GAK-kostenbasis."""
    bevindingen = []
    for ticker, reeks in per_ticker.items():
        slechtste = None
        for label, waarde, inleg in zip(reeks["labels"], reeks["waarde"], reeks["geinvesteerd"]):
            if inleg < MIN_BEDRAG_EUR or waarde <= MAX_WAARDE_INLEG_FACTOR * inleg:
                continue
            if slechtste is None or waarde / inleg > slechtste[0]:
                slechtste = (waarde / inleg, label, waarde, inleg)
        if slechtste:
            factor, label, waarde, inleg = slechtste
            bevindingen.append((factor, _bevinding(
                LET_OP,
                f"{namen.get(ticker, ticker)} ({ticker}): de waarde ({_eur(waarde)}) is op {formatteer_datum_nl(label)} "
                f"{factor:.0f}x de inleg ({_eur(inleg)}); de grens is {MAX_WAARDE_INLEG_FACTOR}x. Mogelijk een "
                f"verkeerde ticker of split.",
                f"plausibel:waarde_inleg:{ticker}",
            )))
    bevindingen.sort(key=lambda b: -b[0])
    return _beperk([b for _, b in bevindingen], LET_OP, "plausibel:waarde_inleg:meer")


def _transactiedagen(groep, labels):
    """Eerste koersdag op of na elke boek- en effectieve datum (een weekendboeking telt op maandag)."""
    datums = list(groep["datum"])
    if "effectieve_datum" in groep.columns:
        datums += list(groep["effectieve_datum"])
    dagen = set()
    for datum in datums:
        i = bisect.bisect_left(labels, pd.Timestamp(datum).strftime("%Y-%m-%d"))
        if i < len(labels):
            dagen.add(labels[i])
    return dagen


def check_dagsprong(transacties_df, per_ticker):
    """Een gekoppelde split zet de effectieve datum op Yahoo's splitdag: die dag telt dus als transactiedag."""
    if transacties_df.empty:
        return []
    namen = _naam_per_ticker(transacties_df)
    rijen_per_ticker = dict(tuple(transacties_df.dropna(subset=["ticker"]).groupby("ticker")))
    bevindingen = []
    for ticker, reeks in per_ticker.items():
        labels, waarden = reeks["labels"], reeks["waarde"]
        transactiedagen = _transactiedagen(rijen_per_ticker.get(ticker, pd.DataFrame(columns=["datum"])), labels)
        sprongen = []
        for i in range(1, len(labels)):
            vorige, huidige = waarden[i - 1], waarden[i]
            if vorige < MIN_BEDRAG_EUR or labels[i] in transactiedagen:
                continue
            fractie = abs(huidige / vorige - 1)
            if fractie > MAX_DAGSPRONG_FRACTIE:
                sprongen.append((fractie, labels[i], vorige, huidige))
        if not sprongen:
            continue
        fractie, label, vorige, huidige = max(sprongen)
        bevindingen.append((fractie, _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): de waarde springt op {formatteer_datum_nl(label)} van "
            f"{_eur(vorige)} naar {_eur(huidige)} ({'+' if huidige > vorige else '-'}{_pct(fractie)}) zonder "
            f"transactie of gekoppelde split ({len(sprongen)} dag(en) boven {_pct(MAX_DAGSPRONG_FRACTIE)}).",
            f"plausibel:dagsprong:{ticker}",
        )))
    bevindingen.sort(key=lambda b: -b[0])
    return _beperk([b for _, b in bevindingen], LET_OP, "plausibel:dagsprong:meer")
