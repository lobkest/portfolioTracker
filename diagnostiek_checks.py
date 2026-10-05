"""Pure diagnostiek-checks: DataFrames/lijsten in, bevindingen uit. Melden doet de aanroeper (meld() is een no-op in threads).

Een bevinding is {"niveau", "tekst", "sleutel"}.
"""
import pandas as pd

from diagnostiek import INFO, LET_OP
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl

MAX_BEVINDINGEN_PER_CHECK = 5
SYNTHETISCH_ORDER_ID_PREFIX = "SYN-"
# Alleen deze kolommen kunnen in oude rijen NULL zijn doordat er geen backfill is.
KOLOMMEN_ZONDER_BACKFILL = {
    "tijd": "de volgorde van transacties binnen een dag",
    "transactiekosten": "de getoonde kosten",
    "waarde_eur": "de GAK (die valt terug op Totaal EUR, incl. kosten, en wordt iets te hoog)",
}


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
