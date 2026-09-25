"""Gedeelde helpers op transactierijen; eigen module om circulaire imports te voorkomen."""
import pandas as pd


def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


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
