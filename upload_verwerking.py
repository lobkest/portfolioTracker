"""Taakfuncties achter POST /upload; _upload_impl() in app.py roept ze in volgorde aan."""
import hashlib

import pandas as pd
import openpyxl
import psycopg2
from flask import request

from debug_utils import dprint, meet_tijd
from diagnostiek import (
    meld, CATEGORIE_WISSELKOERSEN, CATEGORIE_ORDER_IDS, CATEGORIE_OPSLAAN, CATEGORIE_DIVIDEND,
    GOED, INFO, LET_OP, FOUT,
)
from transactie_utils import _is_corporate_action_row
from ticker_zekerheid import (
    basis_ticker_zekerheid_parallel, vind_tickers_met_snelle_prijscheck_parallel,
    find_ticker_met_snelle_prijscheck,
)
from portfolio_admin import find_matching_code, generate_code
from db import save_dividenden
from dividend import verwerk_rekeningoverzicht

# Deze drie kolommen kunnen ontbreken in oudere exports (zie CLAUDE.md: Data en rekenen).
KOSTEN_KOLOM = "Transactiekosten en/of kosten van derden EUR"
# Kale waarde zonder kosten: de basis voor de GAK.
WAARDE_KOLOM = "Waarde EUR"
# DeGiro's eigen afrekenkoers; leeg bij EUR-noteringen.
WISSELKOERS_KOLOM = "Wisselkoers"

DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS = "excel_wisselkoers"
DIAGNOSTIEK_SLEUTEL_ORDER_IDS = "order_ids"
DIAGNOSTIEK_SLEUTEL_PORTFOLIO = "portfolio"
DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN = "insert_opgeslagen"
DIAGNOSTIEK_SLEUTEL_INSERT_GENEGEERD = "insert_genegeerd"
DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT = "insert_mislukt"
DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE = "excel_waarde"
DIAGNOSTIEK_SLEUTEL_EXCEL_KOSTEN = "excel_kosten"
DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS = "corporate_actions"
DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING = "dividend_samenvatting"
DIAGNOSTIEK_SLEUTEL_DIVIDEND_OVERIG = "dividend_zonder_conversie_overig"
DIAGNOSTIEK_SLEUTEL_DIVIDEND_NIET_OPSLAAN = "dividend_niet_opslaan"

# Daarboven één samenvattende melding, tegen ruis.
MAX_LOSSE_DIVIDEND_MELDINGEN = 5


def _normaliseer_tijd(waarde):
    """Excel levert een tijd als string, time of datetime; Postgres wil een TIME-string."""
    if pd.isna(waarde):
        return None
    if hasattr(waarde, "strftime"):
        return waarde.strftime("%H:%M:%S")
    return str(waarde)

def _lees_transacties_excel(bestand1):
    bestand1.seek(0)
    df = pd.read_excel(bestand1)
    df.columns = df.columns.str.strip()
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    return df


def _normaliseer_transactie_kolommen(df):
    """Voegt _kosten_eur, _waarde_eur en _koers_eur (EUR per stuk) toe; NaN of ongewijzigde koers als een kolom ontbreekt."""
    if KOSTEN_KOLOM in df.columns:
        df["_kosten_eur"] = pd.to_numeric(df[KOSTEN_KOLOM], errors="coerce")
    else:
        df["_kosten_eur"] = pd.Series([None] * len(df), index=df.index, dtype="float64")

    if WAARDE_KOLOM in df.columns:
        df["_waarde_eur"] = pd.to_numeric(df[WAARDE_KOLOM], errors="coerce")
    else:
        df["_waarde_eur"] = pd.Series([None] * len(df), index=df.index, dtype="float64")

    if WISSELKOERS_KOLOM in df.columns:
        wisselkoers = pd.to_numeric(df[WISSELKOERS_KOLOM], errors="coerce")
        heeft_wisselkoers = wisselkoers.notna() & (wisselkoers != 0)
        df["_koers_eur"] = df["Koers"].astype(float)
        df.loc[heeft_wisselkoers, "_koers_eur"] = (
            df.loc[heeft_wisselkoers, "Koers"].astype(float) / wisselkoers.loc[heeft_wisselkoers]
        )
        for _, rij in df.loc[heeft_wisselkoers, ["ISIN", "Beurs", "Koers", WISSELKOERS_KOLOM, "_koers_eur"]].iterrows():
            dprint(
                f"[koers-eur] ISIN={rij['ISIN']} Beurs={rij['Beurs']}: "
                f"Koers={rij['Koers']} / Wisselkoers={rij[WISSELKOERS_KOLOM]} -> "
                f"_koers_eur={rij['_koers_eur']:.4f}"
            )
        aantal_met_wisselkoers = int(heeft_wisselkoers.sum())
        if aantal_met_wisselkoers > 0:
            meld(CATEGORIE_WISSELKOERSEN, GOED,
                 f"Wisselkoers uit Excel gebruikt voor {aantal_met_wisselkoers} van {len(df)} transacties.",
                 sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS)
        else:
            meld(CATEGORIE_WISSELKOERSEN, INFO,
                 f"Kolom '{WISSELKOERS_KOLOM}' aanwezig, maar geen enkele transactie gebruikte een wisselkoers.",
                 sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS)
    else:
        df["_koers_eur"] = df["Koers"].astype(float)
        dprint(f"[upload] WAARSCHUWING: kolom '{WISSELKOERS_KOLOM}' niet gevonden - "
               f"koers-kolom blijft ongewijzigd (aanname: al EUR)")
        meld(CATEGORIE_WISSELKOERSEN, LET_OP,
             f"Kolom '{WISSELKOERS_KOLOM}' ontbreekt in het Excel-bestand: koersen zijn ongewijzigd "
             f"overgenomen (aanname: alles in EUR).",
             sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS)
    return df


def _ticker_resolutie_niet_opslaan_pad(df):
    """Lichte ticker-check per (ISIN, Beurs). Geeft (ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw)."""
    groepen = list(df.groupby(["ISIN", "Beurs"]))
    namen = [groep["Product"].iloc[0] for (_isin, _beurs_val), groep in groepen]

    with meet_tijd(f"ticker_resolutie_niet_opslaan ({len(groepen)} positie(s))"):
        posities_voor_check = [
            (naam_positie, isin, beurs_val, [
                {"datum": row["Datum"].strftime("%Y-%m-%d"), "koers": float(row["_koers_eur"])}
                for _, row in groep.iterrows()
            ])
            for naam_positie, ((isin, beurs_val), groep) in zip(namen, groepen)
        ]
        resultaten = basis_ticker_zekerheid_parallel(posities_voor_check)

        ticker_by_isin_beurs = {}
        ticker_zekerheid = []
        ticker_posities_ruw = []
        for (naam_positie, isin, beurs_val, transacties_lijst), resultaat in zip(posities_voor_check, resultaten):
            resultaat["isin"] = isin
            resultaat["naam"] = naam_positie
            resultaat["echte_naam"] = naam_positie
            ticker_by_isin_beurs[(isin, beurs_val)] = resultaat["ticker"]
            ticker_zekerheid.append(resultaat)
            ticker_posities_ruw.append({
                "naam": naam_positie, "isin": isin, "beurs": beurs_val,
                "transacties": transacties_lijst,
            })

    return ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw


def _bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs):
    """Zelfde kolommen als de SELECT's in portfolio_orchestratie.py; samen wijzigen."""
    return pd.DataFrame({
        "datum": df["Datum"],
        "product": df["Product"],
        "isin": df["ISIN"],
        "beurs": df["Beurs"],
        "ticker": [ticker_by_isin_beurs.get((isin_val, beurs_val))
                   for isin_val, beurs_val in zip(df["ISIN"], df["Beurs"])],
        "aantal": df["Aantal"].astype(float),
        "koers": df["_koers_eur"].astype(float),
        "totaal_eur": df["Totaal EUR"].astype(float),
        "echte_naam": df["Product"],
        "transactiekosten": df["_kosten_eur"],
        "waarde_eur": df["_waarde_eur"],
        "tijd": df["Tijd"],
    })


def _bepaal_order_ids(bestand1, df):
    """Order ID's via openpyxl: de kop staat verschoven (zie CLAUDE.md: DeGiro-bestanden)."""
    bestand1.seek(0)
    wb = openpyxl.load_workbook(bestand1, data_only=True)
    ws = wb.active
    order_ids_ruw = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        gevonden = None
        for cell in row:
            if cell.value and isinstance(cell.value, str) and len(cell.value) == 36 and cell.value.count("-") == 4:
                gevonden = cell.value
                break
        order_ids_ruw.append(gevonden)

    if len(order_ids_ruw) == len(df):
        df["Order ID"] = order_ids_ruw
    else:
        df["Order ID"] = None
    _meld_order_ids(order_ids_ruw, len(df))

    def basis_hash(row):
        basis = f"{row['Datum']}|{row['Tijd']}|{row['Product']}|{row['ISIN']}|{row['Aantal']}|{row['Totaal EUR']}"
        return "SYN-" + hashlib.md5(basis.encode()).hexdigest()[:16]

    heeft_order_id = df["Order ID"].notna()
    if (~heeft_order_id).any():
        synthetische_ids = df.loc[~heeft_order_id].apply(basis_hash, axis=1)
        volgnummer = synthetische_ids.groupby(synthetische_ids).cumcount()
        synthetische_ids = synthetische_ids + "-" + volgnummer.astype(str)
        df.loc[~heeft_order_id, "Order ID"] = synthetische_ids

    return df


def _meld_order_ids(order_ids_ruw, aantal_rijen):
    """Bij een mismatch zijn alle ID's synthetisch, dan wordt een bestaande portfolio niet herkend."""
    if len(order_ids_ruw) != aantal_rijen:
        meld(CATEGORIE_ORDER_IDS, LET_OP,
             f"Aantal Order ID-rijen in het werkblad ({len(order_ids_ruw)}) klopt niet met het aantal "
             f"ingelezen transacties ({aantal_rijen}); alle transacties kregen een synthetische ID. Een "
             f"eerder opgeslagen portfolio met echte Order ID's kan daardoor niet herkend worden, waardoor "
             f"er een nieuwe code aangemaakt kan worden.",
             sleutel=DIAGNOSTIEK_SLEUTEL_ORDER_IDS)
        return
    aantal_echt = sum(1 for order_id in order_ids_ruw if order_id)
    if aantal_echt == aantal_rijen:
        meld(CATEGORIE_ORDER_IDS, GOED,
             f"Alle {aantal_rijen} transacties hebben een echte Order ID.",
             sleutel=DIAGNOSTIEK_SLEUTEL_ORDER_IDS)
    else:
        meld(CATEGORIE_ORDER_IDS, INFO,
             f"{aantal_rijen - aantal_echt} van {aantal_rijen} transacties zonder Order ID; die krijgen "
             f"een synthetische ID en worden bij een volgende upload herkend aan datum, tijd, product, "
             f"aantal en bedrag.",
             sleutel=DIAGNOSTIEK_SLEUTEL_ORDER_IDS)


def _vind_of_maak_portfolio_code(cur, df, naam):
    """Geeft (code, match_code, rows_to_insert)."""
    new_order_ids = set(df["Order ID"])
    match_code, missing_ids = find_matching_code(cur, new_order_ids)

    if match_code:
        code = match_code
        if naam:
            cur.execute("UPDATE portfolios SET naam = %s WHERE code = %s", (naam, code))
        rows_to_insert = df[df["Order ID"].isin(missing_ids)] if missing_ids else df.iloc[0:0]
    else:
        code = generate_code(cur)
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (code, naam or None))
        rows_to_insert = df

    if not match_code:
        tekst = f"Nieuwe portfolio aangemaakt met {len(rows_to_insert)} transacties."
    elif len(rows_to_insert):
        tekst = f"Bestaande portfolio aangevuld: {len(rows_to_insert)} nieuwe transacties."
    else:
        tekst = "Bestaande portfolio herkend: geen nieuwe transacties."
    meld(CATEGORIE_OPSLAAN, INFO, tekst, sleutel=DIAGNOSTIEK_SLEUTEL_PORTFOLIO)

    return code, match_code, rows_to_insert


def _meld_nieuwe_rijen_kwaliteit(rows_to_insert):
    if rows_to_insert.empty:
        return

    if KOSTEN_KOLOM not in rows_to_insert.columns:
        meld(CATEGORIE_OPSLAAN, LET_OP,
             f"Kolom '{KOSTEN_KOLOM}' ontbreekt in het Excel-bestand: totale transactiekosten zijn niet "
             f"beschikbaar.",
             sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_KOSTEN)

    is_corporate_action = rows_to_insert.apply(
        lambda rij: _is_corporate_action_row({"beurs": rij["Beurs"], "product": rij["Product"]}), axis=1,
    ).astype(bool)
    aankopen = rows_to_insert[(rows_to_insert["Aantal"].astype(float) > 0) & ~is_corporate_action]
    if WAARDE_KOLOM not in rows_to_insert.columns:
        meld(CATEGORIE_OPSLAAN, LET_OP,
             f"Kolom '{WAARDE_KOLOM}' ontbreekt in het Excel-bestand: de GAK gebruikt voor aankopen "
             f"Totaal EUR (incl. kosten) en valt daardoor iets te hoog uit.",
             sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE)
    else:
        zonder_waarde = int(aankopen["_waarde_eur"].isna().sum())
        if zonder_waarde > 0:
            meld(CATEGORIE_OPSLAAN, LET_OP,
                 f"{zonder_waarde} van de {len(aankopen)} nieuwe aankopen hebben geen '{WAARDE_KOLOM}': de "
                 f"GAK gebruikt daarvoor Totaal EUR (incl. kosten) en valt iets te hoog uit.",
                 sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE)

    aantal_corporate_actions = int(is_corporate_action.sum())
    if aantal_corporate_actions > 0:
        meld(CATEGORIE_OPSLAAN, INFO,
             f"{aantal_corporate_actions} corporate-action-rijen (splits e.d.), zonder ticker.",
             sleutel=DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS)


def _ticker_resolutie_opslaan_pad(cur, code, rows_to_insert, herbepaal_alle_tickers):
    """Lichte check per (ISIN, Beurs); bekende tickers worden hergebruikt, tenzij het vinkje 'opnieuw bepalen' aan staat."""
    groepen = list(rows_to_insert.groupby(["ISIN", "Beurs"]))

    bekende_tickers = {}
    if not herbepaal_alle_tickers:
        cur.execute(
            "SELECT isin, beurs, ticker FROM transacties WHERE code = %s AND ticker IS NOT NULL",
            (code,),
        )
        bekende_tickers = {(isin_val, beurs_val): ticker for isin_val, beurs_val, ticker in cur.fetchall()}

    transacties_per_groep = {
        key: [
            {"datum": row["Datum"].strftime("%Y-%m-%d"), "koers": float(row["_koers_eur"])}
            for _, row in groep.iterrows()
        ]
        for key, groep in groepen
    }
    eerste_poging = [
        (groep["Product"].iloc[0], key[0], key[1], transacties_per_groep[key])
        for key, groep in groepen
    ]
    resultaten = vind_tickers_met_snelle_prijscheck_parallel(eerste_poging, bekende_tickers=bekende_tickers)

    ticker_by_isin_beurs = {}
    for (key, groep), detail in zip(groepen, resultaten):
        if not detail["ticker"]:
            # De eerste productnaam gaf niets: probeer de namen van de andere rijen.
            for _, row in groep.iterrows():
                detail = find_ticker_met_snelle_prijscheck(
                    row["Product"], row["ISIN"], row["Beurs"], transacties_per_groep[key],
                    bekende_tickers.get(key),
                )
                if detail["ticker"]:
                    break
        ticker_by_isin_beurs[key] = detail["ticker"]

    return ticker_by_isin_beurs


def _insert_nieuwe_transacties(cur, code, rows_to_insert, ticker_by_isin_beurs):
    """Geeft het aantal INSERT's zonder exception, inclusief rijen die ON CONFLICT negeerde."""
    ingevoegd = 0
    # Alleen voor de Diagnostiek.
    opgeslagen = genegeerd = mislukt = 0
    eerste_fout = None
    for _, row in rows_to_insert.iterrows():
        try:
            kosten_waarde = row["_kosten_eur"]
            waarde_eur_waarde = row["_waarde_eur"]
            cur.execute(
                """INSERT INTO transacties
                   (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam, transactiekosten, waarde_eur, tijd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, order_id) DO NOTHING""",
                (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
                 ticker_by_isin_beurs[(row["ISIN"], row["Beurs"])], float(row["Aantal"]), float(row["_koers_eur"]),
                 float(row["Totaal EUR"]), row["Order ID"], row["Product"],
                 float(kosten_waarde) if pd.notna(kosten_waarde) else None,
                 float(waarde_eur_waarde) if pd.notna(waarde_eur_waarde) else None,
                 _normaliseer_tijd(row["Tijd"])),
            )
            ingevoegd += 1
            if cur.rowcount == 0:
                genegeerd += 1
            else:
                opgeslagen += 1
        except Exception as e:
            mislukt += 1
            if eerste_fout is None:
                eerste_fout = e
    _meld_insert_resultaat(opgeslagen, genegeerd, mislukt, eerste_fout)
    return ingevoegd


def _meld_insert_resultaat(opgeslagen, genegeerd, mislukt, eerste_fout):
    """Na een psycopg2-fout faalt de rest van de transactie; dan alleen de fout melden."""
    db_fout = isinstance(eerste_fout, psycopg2.Error)
    if mislukt:
        fout_type = type(eerste_fout).__name__
        print(f"[upload] WARN {mislukt} transactie(s) niet opgeslagen (eerste fout: {fout_type})")
        tekst = f"{mislukt} transacties niet opgeslagen (eerste fout: {fout_type})."
        if db_fout:
            tekst += (" Na een databasefout kan de database de hele upload hebben teruggedraaid, "
                      "ook de rijen die als opgeslagen geteld zijn.")
        meld(CATEGORIE_OPSLAAN, FOUT, tekst, sleutel=DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT)
    if db_fout:
        return
    if opgeslagen:
        meld(CATEGORIE_OPSLAAN, GOED, f"{opgeslagen} transacties opgeslagen.",
             sleutel=DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN)
    if genegeerd:
        meld(CATEGORIE_OPSLAAN, INFO, f"{genegeerd} transacties stonden al in de database (genegeerd).",
             sleutel=DIAGNOSTIEK_SLEUTEL_INSERT_GENEGEERD)


def _verwerk_dividend_bestand_indien_aanwezig(code):
    bestand2 = request.files.get("bestand2")
    if bestand2 and bestand2.filename != "":
        with meet_tijd("dividend_bestand_verwerken"):
            dividend_records = verwerk_rekeningoverzicht(bestand2)
            save_dividenden(code, dividend_records)
        _meld_dividend_records(dividend_records)


def _meld_dividend_records(records):
    if not records:
        meld(CATEGORIE_DIVIDEND, INFO, "Geen dividenduitkeringen gevonden in het rekeningoverzicht.",
             sleutel=DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING)
        return
    in_eur = sum(1 for r in records if r["valuta"] == "EUR")
    zonder_conversie = [r for r in records if r["valuta"] != "EUR" and r["netto_eur"] is None]
    gekoppeld = len(records) - in_eur - len(zonder_conversie)
    herinvesteerd = sum(1 for r in records if r.get("herinvesteerd"))
    tekst = (f"{len(records)} uitkeringen verwerkt: {in_eur} in EUR, {gekoppeld} in vreemde valuta "
             f"gekoppeld aan een valutaconversie, {herinvesteerd} herinvesteerd.")
    if zonder_conversie:
        tekst += f" {len(zonder_conversie)} zonder valutaconversie (bedrag onbekend)."
    meld(CATEGORIE_DIVIDEND, INFO if zonder_conversie else GOED, tekst,
         sleutel=DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING)

    for r in zonder_conversie[:MAX_LOSSE_DIVIDEND_MELDINGEN]:
        meld(CATEGORIE_DIVIDEND, LET_OP,
             f"Dividend {r['product']} op {r['datum']} ({r['valuta']}): geen valutaconversie gevonden, "
             f"bedrag onbekend.",
             sleutel=f"dividend:{r['isin']}:{r['datum']}")
    rest = len(zonder_conversie) - MAX_LOSSE_DIVIDEND_MELDINGEN
    if rest > 0:
        meld(CATEGORIE_DIVIDEND, LET_OP, f"Nog {rest} uitkeringen zonder valutaconversie.",
             sleutel=DIAGNOSTIEK_SLEUTEL_DIVIDEND_OVERIG)


def _meld_dividend_bestand_genegeerd():
    bestand2 = request.files.get("bestand2")
    if bestand2 and bestand2.filename != "":
        meld(CATEGORIE_DIVIDEND, INFO,
             "Rekeningoverzicht meegestuurd, maar niet verwerkt bij 'Niet opslaan' (dividend wordt alleen "
             "bij opslaan bewaard).",
             sleutel=DIAGNOSTIEK_SLEUTEL_DIVIDEND_NIET_OPSLAAN)
