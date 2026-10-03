"""Gedeelde helpers op transactierijen; eigen module om circulaire imports te voorkomen."""
import datetime

import pandas as pd


def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


def formatteer_datum_nl(datum):
    """Datum (date, Timestamp of ISO-string) als dd-mm-jjjj voor tekst die de gebruiker ziet."""
    return pd.Timestamp(datum).strftime("%d-%m-%Y")


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


def formatteer_transacties_overzicht(rows):
    """rows: (datum, tijd, product, aantal, koers, totaal_eur, transactiekosten); tijd en kosten mogen None zijn."""
    return [
        {
            "datum": datum.strftime("%Y-%m-%d"),
            "tijd": tijd.strftime("%H:%M") if tijd is not None else None,
            "product": product,
            "aantal": float(aantal),
            "koers": float(koers) if koers is not None else None,
            "totaal_eur": float(totaal_eur),
            "transactiekosten": float(transactiekosten) if transactiekosten is not None else None,
        }
        for datum, tijd, product, aantal, koers, totaal_eur, transactiekosten in rows
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


def transacties_overzicht_uit_df(transacties_df):
    """Zelfde lijst als db_get_transacties_overzicht(), voor 'Niet opslaan' (geen database)."""
    rows = [
        (rij.datum, _naar_tijd_of_none(rij.tijd), rij.product, rij.aantal,
         _getal_of_none(rij.koers), rij.totaal_eur, _getal_of_none(rij.transactiekosten))
        for rij in transacties_df.itertuples(index=False)
    ]
    # Zoals Postgres bij ORDER BY ... DESC: een ontbrekende tijd komt bovenaan.
    rows.sort(key=lambda r: (r[0], r[1] if r[1] is not None else datetime.time.max), reverse=True)
    return formatteer_transacties_overzicht(rows)
