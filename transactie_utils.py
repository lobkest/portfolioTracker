"""Gedeelde helpers op transactierijen; eigen module om circulaire imports te voorkomen."""
import datetime

import pandas as pd


KOSTENKORTING_KOLOM = "kostenkorting_eur"
SYNTHETISCH_ORDER_ID_PREFIX = "SYN-"


class OngeldigExcelBestand(Exception):
    pass


def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


def formatteer_datum_nl(datum):
    """Datum (date, Timestamp of ISO-string) als dd-mm-jjjj voor tekst die de gebruiker ziet."""
    return pd.Timestamp(datum).strftime("%d-%m-%Y")


def getal_nl(x, decimalen=2):
    """3.5 -> '3,5', 4.0 -> '4': zonder overbodige nullen, met komma."""
    tekst = f"{float(x):.{decimalen}f}"
    if "." in tekst:
        tekst = tekst.rstrip("0").rstrip(".")
    return "0" if tekst == "-0" else tekst.replace(".", ",")


def _sorteer_chronologisch(df, datum_kolom="datum", tijd_kolom="tijd"):
    """Ontbrekende tijd telt als 00:00; mergesort houdt de volgorde daarbinnen stabiel."""
    if tijd_kolom not in df.columns:
        return df.sort_values(datum_kolom, kind="mergesort")

    def _naar_timedelta(t):
        if pd.isna(t):
            return pd.Timedelta(0)
        # pd.to_timedelta eist hh:mm:ss; Excel levert vaak "13:39".
        tekst = str(t)
        if tekst.count(":") == 1:
            tekst += ":00"
        return pd.to_timedelta(tekst)

    tijd_offset = df[tijd_kolom].apply(_naar_timedelta)
    chronologisch = pd.to_datetime(df[datum_kolom]) + tijd_offset
    return (
        df.assign(_chronologisch=chronologisch)
        .sort_values("_chronologisch", kind="mergesort")
        .drop(columns="_chronologisch")
    )


def verdeel_kostenkorting(transacties, promoties):
    """promoties: [(datum, bedrag_eur)]. Geeft (korting per rij-label, positief; [(datum, niet toegewezen bedrag)]).
    transactiekosten is negatief (zoals in het DeGiro-bestand); een rij krijgt hooguit zijn eigen kosten."""
    # Afspraak, geen feit uit het bestand: DeGiro zegt niet welke orders gekort zijn, dus de vroegste kosten eerst.
    korting = pd.Series(0.0, index=transacties.index)
    if transacties.empty or not promoties:
        return korting, []
    chronologisch = _sorteer_chronologisch(transacties)
    betaald = (-pd.to_numeric(chronologisch["transactiekosten"], errors="coerce")).fillna(0.0).clip(lower=0.0)
    datums = pd.to_datetime(chronologisch["datum"])
    restanten = []
    for promo_datum, bedrag in sorted(promoties, key=lambda p: pd.Timestamp(p[0])):
        over = round(float(bedrag), 2)
        for label in chronologisch.index:
            if over <= 0 or datums[label] > pd.Timestamp(promo_datum):
                break
            deel = round(min(betaald[label] - korting[label], over), 2)
            if deel > 0:
                korting[label] = round(korting[label] + deel, 2)
                over = round(over - deel, 2)
        if over > 0:
            restanten.append((promo_datum, over))
    return korting, restanten


def pas_kostenkorting_toe(transacties, korting):
    """Kopie met de korting op transactiekosten en totaal_eur; waarde_eur (basis van de GAK) blijft ongewijzigd.
    Bewuste vereenvoudiging: het geld telt op de transactiedatum, niet op de boekdatum van de promotie."""
    df = transacties.copy()
    df[KOSTENKORTING_KOLOM] = korting.astype(float)
    if (korting != 0).any():
        for kolom in ("transactiekosten", "totaal_eur"):
            df[kolom] = pd.to_numeric(df[kolom], errors="coerce").astype(float) + df[KOSTENKORTING_KOLOM]
    return df


def formatteer_transacties_overzicht(rows):
    """rows: (datum, tijd, product, aantal, koers, totaal_eur, transactiekosten, isin, beurs, wisselkoers); tijd en kosten mogen None zijn."""
    return [
        {
            "datum": datum.strftime("%Y-%m-%d"),
            "tijd": tijd.strftime("%H:%M") if tijd is not None else None,
            "product": product,
            "isin": isin,
            "beurs": beurs,
            "wisselkoers": float(wisselkoers) if wisselkoers is not None else None,
            "aantal": float(aantal),
            "koers": float(koers) if koers is not None else None,
            "totaal_eur": float(totaal_eur),
            "transactiekosten": float(transactiekosten) if transactiekosten is not None else None,
        }
        for datum, tijd, product, aantal, koers, totaal_eur, transactiekosten, isin, beurs, wisselkoers in rows
    ]


def _naar_tijd_of_none(waarde):
    if pd.isna(waarde):
        return None
    if isinstance(waarde, datetime.datetime):
        return waarde.time()
    if isinstance(waarde, datetime.time):
        return waarde
    return pd.Timestamp(f"2000-01-01 {waarde}").time()


def _getal_of_none(waarde):
    return None if pd.isna(waarde) else float(waarde)


def transacties_overzicht_uit_df(transacties_df, resultaten=None):
    """Lijst voor het Transacties-tabblad, meest recent eerst. resultaten: {rij-label: velden} uit
    bereken_transactie_resultaten(), per rij toegevoegd. totaal_eur en transactiekosten zoals betaald (DeGiro-bestand),
    de promotiekorting apart in kostenkorting_eur."""
    korting = (transacties_df[KOSTENKORTING_KOLOM] if KOSTENKORTING_KOLOM in transacties_df.columns
               else pd.Series(0.0, index=transacties_df.index))
    rows = [
        (label, (rij.datum, _naar_tijd_of_none(rij.tijd), rij.echte_naam if pd.notna(rij.echte_naam) else rij.product,
                 rij.aantal, _getal_of_none(rij.koers), float(rij.totaal_eur) - k,
                 None if pd.isna(rij.transactiekosten) else float(rij.transactiekosten) - k,
                 rij.isin if pd.notna(rij.isin) else None, rij.beurs if pd.notna(rij.beurs) else None,
                 _getal_of_none(rij.wisselkoers)))
        for label, rij, k in zip(transacties_df.index, transacties_df.itertuples(index=False), korting.astype(float))
    ]
    korting_per_label = dict(zip(transacties_df.index, korting.astype(float)))
    # Zoals Postgres bij ORDER BY ... DESC: een ontbrekende tijd komt bovenaan.
    rows.sort(key=lambda lr: (lr[1][0], lr[1][1] if lr[1][1] is not None else datetime.time.max), reverse=True)
    lijst = formatteer_transacties_overzicht([r for _, r in rows])
    for (label, _), item in zip(rows, lijst):
        item[KOSTENKORTING_KOLOM] = round(korting_per_label[label], 2)
    if resultaten is not None:
        for (label, _), item in zip(rows, lijst):
            item.update(resultaten[label])
    return lijst
