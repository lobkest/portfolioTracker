"""Taakfuncties achter POST /upload en /api/portfolio/<code>/bijwerken; app.py roept ze in volgorde aan."""
import hashlib

import pandas as pd
import psycopg2

from debug_utils import meet_tijd
from diagnostiek import (
    meld, CATEGORIE_WISSELKOERSEN, CATEGORIE_ORDER_IDS, CATEGORIE_OPSLAAN, CATEGORIE_DIVIDEND, CATEGORIE_TICKERS,
    GOED, INFO, LET_OP, FOUT,
)
from split_correctie import vind_wisselparen
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl, OngeldigExcelBestand
from ticker_zekerheid import (
    basis_ticker_zekerheid_parallel, vind_tickers_met_snelle_prijscheck_parallel,
    find_ticker_met_snelle_prijscheck,
)
from portfolio_admin import find_matching_code, generate_code
from db import (
    db_save_dividenden, db_save_kassaldo, db_zet_portfolio_naam, db_maak_portfolio, db_get_bekende_tickers, db_insert_transactie,
    db_get_product_per_ticker, db_vul_bronkolommen_aan,
)
from ticker_classificatie import haal_long_names, bewaar_long_names
from dividend import verwerk_rekeningoverzicht_df, bereken_kassaldo

VERWACHTE_KOLOMMEN = [
    "Datum", "Tijd", "Product", "ISIN", "Beurs", "Uitvoeringsplaats", "Aantal", "Koers",
    "Lokale waarde", "Waarde EUR", "Wisselkoers", "AutoFX Kosten",
    "Transactiekosten en/of kosten van derden EUR", "Totaal EUR", "Order ID",
]

# kolom namen
KOSTEN_KOLOM = "Transactiekosten en/of kosten van derden EUR"
WAARDE_KOLOM = "Waarde EUR"
WISSELKOERS_KOLOM = "Wisselkoers"

DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS = "excel_wisselkoers"
DIAGNOSTIEK_SLEUTEL_ORDER_IDS = "order_ids"
DIAGNOSTIEK_SLEUTEL_PORTFOLIO = "portfolio"
DIAGNOSTIEK_SLEUTEL_INSERT_OPGESLAGEN = "insert_opgeslagen"
DIAGNOSTIEK_SLEUTEL_INSERT_GENEGEERD = "insert_genegeerd"
DIAGNOSTIEK_SLEUTEL_INSERT_MISLUKT = "insert_mislukt"
DIAGNOSTIEK_SLEUTEL_BRONKOLOMMEN_AANGEVULD = "bronkolommen_aangevuld"
DIAGNOSTIEK_SLEUTEL_EXCEL_WAARDE = "excel_waarde"
DIAGNOSTIEK_SLEUTEL_CORPORATE_ACTIONS = "corporate_actions"
DIAGNOSTIEK_SLEUTEL_DIVIDEND_SAMENVATTING = "dividend_samenvatting"
DIAGNOSTIEK_SLEUTEL_DIVIDEND_OVERIG = "dividend_zonder_conversie_overig"

# Daarboven één samenvattende melding, tegen ruis.
MAX_LOSSE_DIVIDEND_MELDINGEN = 5


def _normaliseer_tijd(waarde):
    """Excel levert een tijd als string, time of datetime; Postgres wil een TIME-string."""
    if pd.isna(waarde):
        return None
    if hasattr(waarde, "strftime"):
        return waarde.strftime("%H:%M:%S")
    return str(waarde)

def lees_transacties_excel(bestand1):
    bestand1.seek(0)
    df = pd.read_excel(bestand1)
    df.columns = df.columns.str.strip()
    ontbreekt = [kolom for kolom in VERWACHTE_KOLOMMEN if kolom not in df.columns]
    if ontbreekt:
        raise OngeldigExcelBestand(
            f"Ongeldig Excel-bestand: kolom(men) ontbreken: {', '.join(ontbreekt)}. "
            f"Upload het transactiebestand zoals DeGiro het exporteert."
        )
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["Order ID"] = _kolom_of_naamloze_buurkolom(df, "Order ID")
    df["_koers_valuta"] = naamloze_kolom_rechts(df, "Koers")
    df["_lokale_waarde_valuta"] = naamloze_kolom_rechts(df, "Lokale waarde")
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]

    _meld_order_ids(df["Order ID"]) # voor diagnostiek
    return df


def _kolom_of_naamloze_buurkolom(df, kolomnaam):
    """Geeft de kolom zelf, of bij een lege kolom de eerste niet-lege naamloze buurkolom (rechts, dan links)."""
    positie = df.columns.get_loc(kolomnaam)
    if df.iloc[:, positie].notna().any(): # kolom zelf heeft waarden
        return df.iloc[:, positie]
    for buur in (positie + 1, positie - 1): # check rechts, dan links
        if 0 <= buur < len(df.columns) and str(df.columns[buur]).startswith("Unnamed") and df.iloc[:, buur].notna().any():
            return df.iloc[:, buur]
    return df.iloc[:, positie]


def naamloze_kolom_rechts(df, kolomnaam):
    """Valuta staat in de naamloze kolom direct rechts van het bedrag; niet links kijken, daar staat de valuta van de vorige kolom."""
    buur = df.columns.get_loc(kolomnaam) + 1
    if buur < len(df.columns) and str(df.columns[buur]).startswith("Unnamed"):
        return df.iloc[:, buur].where(df.iloc[:, buur].notna(), None)
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def voeg_koers_eur_toe(df):
    """Voegt _koers_eur (EUR per stuk) toe."""
    wisselkoers = pd.to_numeric(df[WISSELKOERS_KOLOM], errors="coerce")
    heeft_wisselkoers = wisselkoers.notna() & (wisselkoers != 0)
    df["_koers_eur"] = df["Koers"].astype(float)
    df.loc[heeft_wisselkoers, "_koers_eur"] = (
        df.loc[heeft_wisselkoers, "Koers"].astype(float) / wisselkoers.loc[heeft_wisselkoers]
    )
    # Diagnostiek moet tonen of buitenlandse koersen naar EUR zijn omgerekend.
    aantal_met_wisselkoers = int(heeft_wisselkoers.sum())
    if aantal_met_wisselkoers > 0:
        meld(CATEGORIE_WISSELKOERSEN, GOED,
             f"Wisselkoers uit Excel gebruikt voor {aantal_met_wisselkoers} van {len(df)} transacties.",
             sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS)
    else:
        meld(CATEGORIE_WISSELKOERSEN, INFO,
             f"Kolom '{WISSELKOERS_KOLOM}' aanwezig, maar geen enkele transactie gebruikte een wisselkoers.",
             sleutel=DIAGNOSTIEK_SLEUTEL_EXCEL_WISSELKOERS)
    return df


def _wisselrij_labels(df):
    """Index-labels van de omboekingen bij een ISIN-wissel (splits): geen markttransactie, dus niet voor de prijscheck."""
    standaard = pd.DataFrame({
        "datum": df["Datum"], "tijd": df["Tijd"], "isin": df["ISIN"], "aantal": df["Aantal"],
        "koers": df["_koers_eur"], "transactiekosten": pd.to_numeric(df[KOSTEN_KOLOM], errors="coerce"),
    })
    paren, _onduidelijk = vind_wisselparen(standaard)
    return {label for paar in paren for label in paar.oud_rijen + paar.nieuw_rijen}


def _bouw_posities(df):
    """[(product, isin, beurs, transacties)] per (ISIN, Beurs); product is dat van de eerste rij."""
    wisselrijen = _wisselrij_labels(df)
    return [
        (groep["Product"].iloc[0], isin, beurs, [
            {"datum": row["Datum"].strftime("%Y-%m-%d"), "koers": float(row["_koers_eur"])}
            for label, row in groep.iterrows() if label not in wisselrijen
        ])
        for (isin, beurs), groep in df.groupby(["ISIN", "Beurs"])
    ]


def _meld_zoekstappen(product, isin, beurs, ticker, stappen):
    """Welke Yahoo-zoekopdrachten zijn geprobeerd; nodig als de naamspelling de verkeerde notering oplevert."""
    if not stappen:
        return
    rijen = [
        [stap["query"],
         ", ".join(f"{s} ({e})" if e else str(s) for s, e in stap["resultaten"]) or "geen resultaten",
         stap["beurs_match"] or "–"]
        for stap in stappen
    ]
    meld(CATEGORIE_TICKERS, INFO,
         f"{product} ({isin}, {beurs}): {len(stappen)} zoekopdracht(en) bij Yahoo, gekozen ticker: {ticker or 'geen'}.",
         sleutel=f"zoekstappen:{isin}:{beurs}",
         tabel={"kolommen": ["Zoekopdracht", "Resultaten (beurs)", "Match op verwachte beurs"], "rijen": rijen})


def ticker_resolutie_niet_opslaan(df):
    """Lichte ticker-check per (ISIN, Beurs). Geeft (ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw)."""
    posities_voor_check = _bouw_posities(df)

    with meet_tijd(f"ticker_resolutie_niet_opslaan ({len(posities_voor_check)} positie(s))"):
        resultaten = basis_ticker_zekerheid_parallel(posities_voor_check)

        ticker_by_isin_beurs = {}
        ticker_zekerheid = []
        ticker_posities_ruw = []
        for (naam_positie, isin, beurs_val, transacties_lijst), resultaat in zip(posities_voor_check, resultaten):
            resultaat["isin"] = isin
            resultaat["naam"] = naam_positie
            resultaat["echte_naam"] = naam_positie
            _meld_zoekstappen(naam_positie, isin, beurs_val, resultaat["ticker"], resultaat.pop("zoekstappen", None))
            ticker_by_isin_beurs[(isin, beurs_val)] = resultaat["ticker"]
            ticker_zekerheid.append(resultaat)
            ticker_posities_ruw.append({
                "naam": naam_positie, "isin": isin, "beurs": beurs_val,
                "transacties": transacties_lijst,
            })

    return ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw


def bepaal_product_per_ticker(df, ticker_by_isin_beurs, bestaand=None):
    """{ticker: product}: bestaande tickers houden hun product, nieuwe krijgen Yahoo's longName (anders de DeGiro-naam)."""
    bestaand = bestaand or {}
    echte_naam_per_ticker = {}
    for isin, beurs, naam in zip(df["ISIN"], df["Beurs"], df["Product"]):
        ticker = ticker_by_isin_beurs.get((isin, beurs))
        if ticker:
            echte_naam_per_ticker.setdefault(ticker, naam)

    nieuwe_tickers = [t for t in echte_naam_per_ticker if t not in bestaand]
    long_names = haal_long_names(nieuwe_tickers)
    # Werkt alleen bestaande rijen bij; de rest vult de verrijking aan (vul_ontbrekende_long_names).
    bewaar_long_names(long_names)

    product_per_ticker = dict(bestaand)
    for ticker in nieuwe_tickers:
        product_per_ticker[ticker] = long_names.get(ticker) or echte_naam_per_ticker[ticker]
    return product_per_ticker


def bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs, product_per_ticker=None):
    """Zelfde kolommen als TRANSACTIE_KOLOMMEN in db.py; samen wijzigen."""
    product_per_ticker = product_per_ticker or {}
    tickers = [ticker_by_isin_beurs.get((isin_val, beurs_val))
               for isin_val, beurs_val in zip(df["ISIN"], df["Beurs"])]
    return pd.DataFrame({
        "datum": df["Datum"],
        "product": [product_per_ticker.get(ticker, naam) for ticker, naam in zip(tickers, df["Product"])],
        "isin": df["ISIN"],
        "beurs": df["Beurs"],
        "ticker": tickers,
        "aantal": df["Aantal"].astype(float),
        "koers": df["_koers_eur"].astype(float),
        "totaal_eur": df["Totaal EUR"].astype(float),
        "echte_naam": df["Product"],
        "transactiekosten": pd.to_numeric(df[KOSTEN_KOLOM], errors="coerce"),
        "waarde_eur": df[WAARDE_KOLOM],
        "tijd": df["Tijd"],
        "wisselkoers": pd.to_numeric(df[WISSELKOERS_KOLOM], errors="coerce"),
    })


def vul_synthetische_order_ids_aan(df):
    """Vult ontbrekende Order ID's aan met synthetische ID's en maakt die van deelorders uniek."""
    def basis_hash(row):
        # Zonder Product: DeGiro hernoemt producten soms (BYD CO LTD -> BYD COMPANY LIMITED).
        basis = f"{row['Datum']}|{row['Tijd']}|{row['ISIN']}|{row['Aantal']}|{row['Totaal EUR']}"
        return "SYN-" + hashlib.md5(basis.encode()).hexdigest()[:16]

    heeft_order_id = df["Order ID"].notna()
    if (~heeft_order_id).any():
        synthetische_ids = df.loc[~heeft_order_id].apply(basis_hash, axis=1)
        volgnummer = synthetische_ids.groupby(synthetische_ids).cumcount()
        synthetische_ids = synthetische_ids + "-" + volgnummer.astype(str)
        df.loc[~heeft_order_id, "Order ID"] = synthetische_ids

    return _maak_deelorder_ids_uniek(df)


def _maak_deelorder_ids_uniek(df):
    # zie CLAUDE.md: DeGiro-bestanden
    volgnummer = df.groupby("Order ID").cumcount()
    herhaald = volgnummer > 0
    if herhaald.any():
        df.loc[herhaald, "Order ID"] = (
            df.loc[herhaald, "Order ID"].astype(str) + "-" + volgnummer[herhaald].astype(str)
        )
    return df


def _meld_order_ids(order_ids):
    aantal_rijen = len(order_ids)
    aantal_echt = int(order_ids.notna().sum())
    if aantal_echt == aantal_rijen:
        meld(CATEGORIE_ORDER_IDS, GOED,
             f"Alle {aantal_rijen} transacties hebben een echte Order ID.",
             sleutel=DIAGNOSTIEK_SLEUTEL_ORDER_IDS)
    else:
        meld(CATEGORIE_ORDER_IDS, INFO,
             f"{aantal_rijen - aantal_echt} van {aantal_rijen} transacties zonder Order ID; die krijgen "
             f"een synthetische ID en worden bij een volgende upload herkend aan datum, tijd, ISIN, "
             f"aantal en bedrag.",
             sleutel=DIAGNOSTIEK_SLEUTEL_ORDER_IDS)


def vind_of_maak_portfolio(cur, df, naam):
    """(code, bestaand, rows_to_insert): een bestaande portfolio met dezelfde Order ID's, anders een nieuwe."""
    match_code, missing_ids = find_matching_code(cur, set(df["Order ID"]))
    bestaand = match_code is not None

    if bestaand:
        code = match_code
        if naam:
            db_zet_portfolio_naam(cur, code, naam)
        rows_to_insert = df[df["Order ID"].isin(missing_ids)]
    else:
        code = generate_code(cur)
        db_maak_portfolio(cur, code, naam or None)
        rows_to_insert = df

    meld_portfolio_opslaan(bestaand, rows_to_insert)
    return code, bestaand, rows_to_insert


def meld_portfolio_opslaan(bestaand, rows_to_insert):
    _meld_nieuwe_rijen_kwaliteit(rows_to_insert)
    if not bestaand:
        tekst = f"Nieuwe portfolio aangemaakt met {len(rows_to_insert)} transacties."
    elif len(rows_to_insert):
        tekst = f"Bestaande portfolio aangevuld: {len(rows_to_insert)} nieuwe transacties."
    else:
        tekst = "Bestaande portfolio herkend: geen nieuwe transacties."
    meld(CATEGORIE_OPSLAAN, INFO, tekst, sleutel=DIAGNOSTIEK_SLEUTEL_PORTFOLIO)


def _meld_nieuwe_rijen_kwaliteit(rows_to_insert):
    """Geeft waarschuwingen bij ontbrekende Waarde EUR of corporate-action-rijen."""
    if rows_to_insert.empty:
        return

    is_corporate_action = rows_to_insert.apply(
        lambda rij: _is_corporate_action_row({"beurs": rij["Beurs"], "product": rij["Product"]}), axis=1,
    ).astype(bool)
    aankopen = rows_to_insert[(rows_to_insert["Aantal"].astype(float) > 0) & ~is_corporate_action]
    zonder_waarde = int(aankopen[WAARDE_KOLOM].isna().sum())
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


def _probeer_andere_productnamen(groep, transacties, bekende_ticker):
    """De eerste productnaam gaf niets: probeer de namen van de andere rijen."""
    detail = {"ticker": None}
    stappen = []
    for _, row in groep.iterrows():
        detail = find_ticker_met_snelle_prijscheck(
            row["Product"], row["ISIN"], row["Beurs"], transacties, bekende_ticker,
        )
        stappen += detail.get("zoekstappen") or []
        if detail["ticker"]:
            break
    return {**detail, "zoekstappen": stappen}


def _ticker_resolutie_opslaan(cur, code, rows_to_insert, herbepaal_alle_tickers):
    """Lichte check per (ISIN, Beurs); bekende tickers worden hergebruikt, tenzij het vinkje 'opnieuw bepalen' aan staat."""
    groepen = dict(list(rows_to_insert.groupby(["ISIN", "Beurs"])))
    posities = _bouw_posities(rows_to_insert)

    bekende_tickers = {}
    if not herbepaal_alle_tickers:
        bekende_tickers = db_get_bekende_tickers(cur, code)

    resultaten = vind_tickers_met_snelle_prijscheck_parallel(posities, bekende_tickers=bekende_tickers)

    ticker_by_isin_beurs = {}
    for (naam, isin, beurs, transacties), detail in zip(posities, resultaten):
        key = (isin, beurs)
        stappen = list(detail.get("zoekstappen") or [])
        if not detail["ticker"]:
            detail = _probeer_andere_productnamen(groepen[key], transacties, bekende_tickers.get(key))
            stappen += detail["zoekstappen"]
        _meld_zoekstappen(naam, isin, beurs, detail["ticker"], stappen)
        ticker_by_isin_beurs[key] = detail["ticker"]

    return ticker_by_isin_beurs


def _product_per_ticker_opslaan(cur, code, rows_to_insert, ticker_by_isin_beurs):
    return bepaal_product_per_ticker(
        rows_to_insert, ticker_by_isin_beurs, bestaand=db_get_product_per_ticker(cur, code),
    )


def voeg_nieuwe_transacties_toe(cur, code, rows_to_insert, herbepaal_alle_tickers):
    """Ticker en product per nieuwe rij bepalen en invoegen, in de transactie van de aanroeper."""
    if rows_to_insert.empty:
        return
    with meet_tijd("ticker_resolutie"):
        ticker_by_isin_beurs = _ticker_resolutie_opslaan(cur, code, rows_to_insert, herbepaal_alle_tickers)
    with meet_tijd("long_names_ophalen"):
        product_per_ticker = _product_per_ticker_opslaan(cur, code, rows_to_insert, ticker_by_isin_beurs)
    with meet_tijd(f"db_insert_transacties ({len(rows_to_insert)} rij(en))"):
        _insert_nieuwe_transacties(cur, code, rows_to_insert, ticker_by_isin_beurs, product_per_ticker)


def _getal_of_none(waarde):
    getal = pd.to_numeric(waarde, errors="coerce")
    return float(getal) if pd.notna(getal) else None


def _tekst_of_none(waarde):
    return str(waarde).strip() if pd.notna(waarde) else None


def _bronwaarden(row):
    """In de volgorde van db.BRONKOLOMMEN; alleen om het Excel na te bouwen, de app rekent er niet mee."""
    return (
        _tekst_of_none(row.get("Uitvoeringsplaats")),
        _getal_of_none(row.get("Koers")),
        _tekst_of_none(row.get("_koers_valuta")),
        _getal_of_none(row.get("Lokale waarde")),
        _tekst_of_none(row.get("_lokale_waarde_valuta")),
        _getal_of_none(row.get("AutoFX Kosten")),
    )


def vul_bronkolommen_aan(cur, code, df):
    """Vult lege bronkolommen van al opgeslagen rijen aan bij een herupload; een insert raakt bestaande Order ID's niet."""
    rijen = [(row["Order ID"], *_bronwaarden(row)) for _, row in df.iterrows()]
    aangevuld = db_vul_bronkolommen_aan(cur, code, rijen)
    if aangevuld:
        meld(CATEGORIE_OPSLAAN, INFO, f"Ontbrekende Excel-kolommen aangevuld bij {aangevuld} bestaande transacties.",
             sleutel=DIAGNOSTIEK_SLEUTEL_BRONKOLOMMEN_AANGEVULD)
    return aangevuld


def _insert_nieuwe_transacties(cur, code, rows_to_insert, ticker_by_isin_beurs, product_per_ticker=None):
    """Geeft het aantal INSERT's zonder exception, inclusief rijen die ON CONFLICT negeerde."""
    product_per_ticker = product_per_ticker or {}
    ingevoegd = 0
    # Alleen voor de Diagnostiek.
    opgeslagen = genegeerd = mislukt = 0
    eerste_fout = None
    for _, row in rows_to_insert.iterrows():
        try:
            kosten_waarde = pd.to_numeric(row[KOSTEN_KOLOM], errors="coerce")
            waarde_eur_waarde = row[WAARDE_KOLOM]
            wisselkoers_waarde = pd.to_numeric(row[WISSELKOERS_KOLOM], errors="coerce")
            ticker = ticker_by_isin_beurs[(row["ISIN"], row["Beurs"])]
            nieuw = db_insert_transactie(
                cur, code, row["Datum"].date(), product_per_ticker.get(ticker, row["Product"]), row["ISIN"], row["Beurs"],
                ticker, float(row["Aantal"]), float(row["_koers_eur"]),
                float(row["Totaal EUR"]), row["Order ID"], row["Product"],
                float(kosten_waarde) if pd.notna(kosten_waarde) else None,
                float(waarde_eur_waarde) if pd.notna(waarde_eur_waarde) else None,
                _normaliseer_tijd(row["Tijd"]),
                float(wisselkoers_waarde) if pd.notna(wisselkoers_waarde) else None,
                *_bronwaarden(row),
            )
            ingevoegd += 1
            if nieuw:
                opgeslagen += 1
            else:
                genegeerd += 1
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


def sla_dividend_bestand_op(cur, code, rekening_df):
    """In de transactie van de aanroeper: samen met de transacties opgeslagen of geen van beide."""
    with meet_tijd("dividend_bestand_verwerken"):
        dividend_records = verwerk_rekeningoverzicht_df(rekening_df)
        db_save_dividenden(cur, code, dividend_records)
    _meld_dividend_records(dividend_records)


def sla_kassaldo_op(cur, code, rekening_df):
    kassaldo = bereken_kassaldo(rekening_df)
    if kassaldo is not None:
        db_save_kassaldo(cur, code, kassaldo)


def verwerk_dividend_zonder_opslaan(rekening_df):
    with meet_tijd("dividend_bestand_verwerken"):
        dividend_records = verwerk_rekeningoverzicht_df(rekening_df)
    _meld_dividend_records(dividend_records)
    return dividend_records


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
             f"Dividend {r['product']} op {formatteer_datum_nl(r['datum'])} ({r['valuta']}): geen valutaconversie gevonden, "
             f"bedrag onbekend.",
             sleutel=f"dividend:{r['isin']}:{r['datum']}")
    rest = len(zonder_conversie) - MAX_LOSSE_DIVIDEND_MELDINGEN
    if rest > 0:
        meld(CATEGORIE_DIVIDEND, LET_OP, f"Nog {rest} uitkeringen zonder valutaconversie.",
             sleutel=DIAGNOSTIEK_SLEUTEL_DIVIDEND_OVERIG)
