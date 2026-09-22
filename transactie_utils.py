"""
Kleine, gedeelde taakfuncties op ruwe transactie-rijen/DataFrames, zonder
DB/netwerk-afhankelijkheid — gebruikt door meerdere domeinmodules
(statistieken.py, portfolio_calc.py, ticker_matching.py, portfolio_orchestratie.py)
en daarom als eigen, afhankelijkheidsloze module losgetrokken i.p.v. in
een van die domeinmodules te laten zitten (dat zou een circulaire import
opleveren zodra twee van die modules elkaars functies nodig hebben).
"""
import pandas as pd


def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


def _sorteer_chronologisch(df, datum_kolom="datum", tijd_kolom="tijd"):
    """Sorteert transactierijen chronologisch op datum+tijd samen, niet
    alleen op datum. Nodig voor same-day transacties: de 'transacties'-tabel
    slaat alleen een DATE op, geen tijdstip (zie ALTER TABLE ... ADD COLUMN
    tijd in db.py) — zonder tijd kon een verkoop op dezelfde dag als de
    bijbehorende koop in de verkeerde volgorde verwerkt worden (afhankelijk
    van de willekeurige SELECT-volgorde uit de database, niet van de
    werkelijke uitvoeringstijd). Dit gaf bv. een 'onbekende' verkoopkoers
    op het Statistieken-tabblad wanneer bereken_holdings_en_gesloten() de
    verkoop verwerkte vóórdat de koop van diezelfde dag geregistreerd was.

    Rijen zonder tijd (tijd_kolom ontbreekt, of tijd IS NULL — bv. data van
    vóór de tijd-migratie die nog niet is teruggehaald via een herüpload)
    krijgen bewust 00:00:00 als fallback: dat is geen garantie voor de
    juiste volgorde, maar wel een stabiele, voorspelbare sortering die niet
    slechter is dan de oude datum-only sortering (mergesort is stable, dus
    de relatieve volgorde van rijen zonder tijd blijft ongewijzigd t.o.v.
    hoe ze zijn aangeleverd)."""
    if tijd_kolom not in df.columns:
        return df.sort_values(datum_kolom, kind="mergesort")

    def _naar_timedelta(t):
        if pd.isna(t):
            return pd.Timedelta(0)
        # pd.to_timedelta eist 'hh:mm:ss' -- een string rechtstreeks uit
        # Excel ("13:39") mist vaak de seconden, een datetime.time-object
        # heeft ze via str() altijd al ("13:39:00").
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
