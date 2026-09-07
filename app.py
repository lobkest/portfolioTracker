from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db, delete_portfolio, wijzig_portfolio_code
from analysis import generate_code, is_geldige_code, CODE_LENGTH, find_ticker_detailed, get_prices, compute_value_over_time, find_matching_code, compute_per_ticker, compute_per_ticker_koers_en_aankopen, classify_tickers, compute_split_adjusted_shares, compute_land_sector_verdeling, verifieer_tickers_met_prijs_parallel, verifieer_ticker_met_prijs, verwerk_rekeningoverzicht, bereken_dividend_samenvatting, bereken_statistieken, basis_ticker_zekerheid, basis_ticker_zekerheid_parallel, find_ticker_met_snelle_prijscheck, vind_tickers_met_snelle_prijscheck_parallel, ticker_waarschuwingen_voor_transacties, _is_corporate_action_row, backfill_verouderde_tickers, bereken_bedrijven_verdeling, bereken_etf_overlap, bereken_benchmark_vergelijking, _sorteer_verdeling_groot_naar_klein, _sorteer_tickers_voor_dropdown, BENCHMARK_TICKERS, bereken_rendement_over_tijd, _verwarm_land_sector_cache_parallel, meet_tijd, reset_yahoo_call_teller, log_yahoo_call_samenvatting
from db import save_dividenden, backfill_transactiekosten, backfill_tijd, backfill_waarde_eur, get_laatste_prijs_update
import hashlib
import openpyxl
import math
import time

app = Flask(__name__)
init_db()

# Kolomnaam exact zoals DeGiro 'm in het transactiebestand zet (na
# df.columns.str.strip(), dat evt. rondom-spaties in de header wegwerkt).
# Ontbreekt in oudere DeGiro-exportformaten — daarom overal met een
# beschikbaarheids-check behandeld i.p.v. als verplichte kolom.
KOSTEN_KOLOM = "Transactiekosten en/of kosten van derden EUR"

# Kale waarde (aantal x koers, zonder AutoFX/transactiekosten) — DEGIRO's
# eigen GAK-weergave is hierop gebaseerd, in tegenstelling tot Totaal EUR
# (dat wel kosten meetelt en de GAK structureel te hoog maakt, zie
# CLAUDE.md). Zelfde beschikbaarheids-check-patroon als KOSTEN_KOLOM.
WAARDE_KOLOM = "Waarde EUR"


def _normaliseer_tijd(waarde):
    """Zet de 'Tijd'-kolom uit het transactiebestand om naar een string die
    Postgres' TIME-kolom kan opslaan. Pandas/openpyxl kan een tijdcel als
    string ("13:39"), datetime.time of datetime.datetime teruggeven,
    afhankelijk van hoe de cel in Excel geformatteerd is."""
    if pd.isna(waarde):
        return None
    if hasattr(waarde, "strftime"):
        return waarde.strftime("%H:%M:%S")
    return str(waarde)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/dbtest")
def db_test():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return f"Database-verbinding werkt! Resultaat: {result}"
    except Exception as e:
        return f"Verbinding mislukt: {e}"

@app.route("/upload", methods=["POST"])
def upload():
    """
    Dunne wrapper om _upload_impl() die ELKE onverwachte fout (bv. een
    trage/falende Yahoo-call die uiteindelijk toch een exception geeft, of
    iets onvoorziens in de Excel-parsing) omzet in een nette JSON-
    foutrespons i.p.v. een kale 500 zonder body of een hangende request die
    de frontend nooit als 'klaar' ziet. Zie CLAUDE.md, Statistieken-incident
    2026-08-31: een 'niet opslaan'-analyse van een grotere portfolio bleef
    zo stil hangen dat er zelfs geen foutmelding verscheen.
    """
    try:
        return _upload_impl()
    except Exception as e:
        print(f"[upload] ONVERWACHTE FOUT: {e}")
        return jsonify({
            "error": "Analyse van deze portfolio duurde te lang of is mislukt. Probeer het opnieuw, of upload "
                     "zonder 'Niet opslaan' zodat de resultaten tussentijds bewaard blijven."
        }), 500


def _upload_impl():
    reset_yahoo_call_teller()
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    with meet_tijd("excel_inlezen_pandas"):
        bestand1.seek(0)
        df = pd.read_excel(bestand1)
        print(f"[upload] Excel ingelezen: {df.shape[0]} rijen, kolommen: {df.columns.tolist()}")

        df.columns = df.columns.str.strip()
        df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)

        if KOSTEN_KOLOM in df.columns:
            df["_kosten_eur"] = pd.to_numeric(df[KOSTEN_KOLOM], errors="coerce")
        else:
            df["_kosten_eur"] = pd.Series([None] * len(df), index=df.index, dtype="float64")
            print(f"[upload] WAARSCHUWING: kolom '{KOSTEN_KOLOM}' niet gevonden — transactiekosten niet beschikbaar")

        if WAARDE_KOLOM in df.columns:
            df["_waarde_eur"] = pd.to_numeric(df[WAARDE_KOLOM], errors="coerce")
        else:
            df["_waarde_eur"] = pd.Series([None] * len(df), index=df.index, dtype="float64")
            print(f"[upload] WAARSCHUWING: kolom '{WAARDE_KOLOM}' niet gevonden — "
                  f"GAK valt terug op totaal_eur (incl. kosten) voor deze upload")

    niet_opslaan = request.form.get("niet_opslaan") == "on"
    # "Ticker-informatie voor alle posities opnieuw bepalen"-vinkje (zie
    # templates/index.html): staat dit UIT (standaard), dan slaat de
    # ticker-resolutie hieronder de dure/onvoorwaardelijke yahooquery-
    # zoekopdracht over voor posities die al eerder zijn opgelost -- zie
    # CLAUDE.md/opdracht "vinkje ticker-informatie opnieuw bepalen".
    herbepaal_alle_tickers = request.form.get("herbepaal_alle_tickers") == "on"
    if niet_opslaan:
        print("[upload] 'Niet opslaan' aangevinkt — eenmalige analyse, niets wordt in de database opgeslagen")
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen: dezelfde ISIN kan
        # op meerdere beurzen genoteerd staan (bv. een fonds met een
        # Amsterdam- én een Londen-notering) en dat zijn dan ECHT
        # verschillende tickers — één ticker per ISIN voor de hele groep zou
        # de tweede notering stilzwijgend de ticker van de eerste geven.
        #
        # Bewust de GOEDKOPE find_ticker_detailed()-match + lichte, standaard
        # prijscontrole (via basis_ticker_zekerheid_parallel ->
        # find_ticker_met_snelle_prijscheck: 1 gecachete call per positie in
        # het gangbare geval, escaleert alleen bij een echte afwijking),
        # niet de volledige, dure verifieer_tickers_met_prijs_parallel() —
        # die liep bij een grotere portfolio met een koude cache ruim over
        # de gunicorn-timeout heen doordat hij hier ALTIJD synchroon voor de
        # volle portfolio draaide (zie CLAUDE.md, Statistieken-incident
        # 2026-08-31). PARALLEL over de posities (niet sequentieel): ook al
        # kost de lichte check meestal maar 1 call per positie, bij een
        # portfolio met veel unieke, nog nooit gecontroleerde tickers (koude
        # ticker_prijscheck-cache) kan die ene call per positie sequentieel
        # opgeteld alsnog richting de timeout lopen (zie CLAUDE.md, vervolg
        # op hetzelfde incident). De normale (opslaande) upload koppelt de
        # VOLLEDIGE check nog steeds lui aan de Ticker-zekerheid-pagina (zie
        # de /ticker-zekerheid-route hieronder) — dat kan hier niet op
        # dezelfde manier (geen opgeslagen code om later transacties bij op
        # te halen), dus krijgt de eenmalige analyse in plaats daarvan een
        # losse /api/ticker-zekerheid-check-aanroep vanuit de frontend, met
        # de transactiedata die hieronder als 'ticker_posities_ruw' meegaat.
        groepen = list(df.groupby(["ISIN", "Beurs"]))
        namen = [groep["Product"].iloc[0] for (_isin, _beurs_val), groep in groepen]

        with meet_tijd(f"ticker_resolutie_niet_opslaan ({len(groepen)} positie(s))"):
            posities_voor_check = [
                (naam_positie, isin, beurs_val, [
                    {"datum": row["Datum"].strftime("%Y-%m-%d"), "koers": float(row["Koers"])}
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
                print(f"[upload] ISIN {isin} (beurs={beurs_val}) -> ticker {resultaat['ticker']} "
                      f"(zekerheid={resultaat['zekerheid']}, basis)")
            print(f"[upload] {len(groepen)} positie(s) basis-ticker-resolutie (incl. snelle prijscheck, parallel)")

        transacties_df = pd.DataFrame({
            "datum": df["Datum"],
            "product": df["Product"],
            "isin": df["ISIN"],
            "beurs": df["Beurs"],
            "ticker": [ticker_by_isin_beurs.get((isin_val, beurs_val))
                       for isin_val, beurs_val in zip(df["ISIN"], df["Beurs"])],
            "aantal": df["Aantal"].astype(float),
            "koers": df["Koers"].astype(float),
            "totaal_eur": df["Totaal EUR"].astype(float),
            "echte_naam": df["Product"],
            "transactiekosten": df["_kosten_eur"],
            "waarde_eur": df["_waarde_eur"],
            "tijd": df["Tijd"],
        })
        result = analyze_transacties(transacties_df, code=None, naam=naam or None)
        result["ticker_zekerheid"] = ticker_zekerheid
        result["ticker_posities_ruw"] = ticker_posities_ruw
        log_yahoo_call_samenvatting()
        return jsonify(result)

    # Order ID-kolom kan door merged cells één kolom verschoven staan t.o.v. de header;
    # lees 'm daarom apart uit met openpyxl, die de waarden onder de merge vindt.
    with meet_tijd("excel_inlezen_orderid_openpyxl"):
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
            print("[upload] Order ID's uitgelezen via openpyxl (merged-cell fix)")
        else:
            print(f"[upload] WAARSCHUWING: rijaantal komt niet overeen ({len(order_ids_ruw)} vs {len(df)})")
            df["Order ID"] = None

    # rijen zonder echte (UUID-vormige) Order ID krijgen een synthetische, stabiele ID
    def basis_hash(row):
        basis = f"{row['Datum']}|{row['Tijd']}|{row['Product']}|{row['ISIN']}|{row['Aantal']}|{row['Totaal EUR']}"
        return "SYN-" + hashlib.md5(basis.encode()).hexdigest()[:16]

    heeft_order_id = df["Order ID"].notna()

    if (~heeft_order_id).any():
        synthetische_ids = df.loc[~heeft_order_id].apply(basis_hash, axis=1)
        volgnummer = synthetische_ids.groupby(synthetische_ids).cumcount()
        synthetische_ids = synthetische_ids + "-" + volgnummer.astype(str)
        df.loc[~heeft_order_id, "Order ID"] = synthetische_ids
        print(f"[upload] {(~heeft_order_id).sum()} rijen kregen een synthetische Order ID")

    new_order_ids = set(df["Order ID"])
    print(f"[upload] {len(new_order_ids)} unieke Order ID's in geüpload bestand")

    conn = get_db_connection()
    cur = conn.cursor()

    match_code, missing_ids = find_matching_code(cur, new_order_ids)
    print(f"[upload] match_code={match_code}, aantal missing_ids={len(missing_ids) if missing_ids is not None else 'N/A'}")

    if match_code:
        code = match_code
        if naam:
            cur.execute("UPDATE portfolios SET naam = %s WHERE code = %s", (naam, code))
        rows_to_insert = df[df["Order ID"].isin(missing_ids)] if missing_ids else df.iloc[0:0]
        # Rijen die al bestonden (order_id niet in missing_ids) maar toen
        # zonder transactiekosten zijn opgeslagen (kolom kwam er pas later
        # bij, DO NOTHING liet oude rijen dus voor altijd NULL) alsnog
        # backfillen met de waarde uit deze upload.
        rows_bestaand = df[~df["Order ID"].isin(missing_ids)]
    else:
        code = generate_code(cur)
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (code, naam or None))
        rows_to_insert = df
        rows_bestaand = df.iloc[0:0]

    print(f"[upload] code={code}, rows_to_insert={len(rows_to_insert)} rijen")

    if not rows_to_insert.empty:
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen — zie de
        # 'niet_opslaan'-tak hierboven voor de reden (een ISIN kan op
        # meerdere beurzen genoteerd staan, met een écht andere ticker).
        # find_ticker_met_snelle_prijscheck (i.p.v. de kale
        # find_ticker_detailed) doet er een lichte, standaard prijscontrole
        # bovenop — in het gangbare geval maar 1 extra, gecachete Yahoo-call
        # per groep, warmt meteen de ticker_prijscheck-cache die
        # prijswaarschuwing_voor_ticker() hieronder (in analyze_transacties)
        # bij elk bezoek hergebruikt. PARALLEL over de groepen — zie de
        # 'niet_opslaan'-tak hierboven voor de reden (koude-cache-
        # timeoutrisico bij veel unieke tickers).
        with meet_tijd("ticker_resolutie"):
            groepen = list(rows_to_insert.groupby(["ISIN", "Beurs"]))

            # Vinkje "ticker-informatie opnieuw bepalen" UIT (standaard): een
            # (ISIN, Beurs)-groep die al eerder is opgelost (staat al met een
            # ticker in transacties voor deze code) hoeft niet opnieuw door de
            # dure, onvoorwaardelijke yahooquery-zoekopdracht heen, ook al
            # bevat de groep hier een gloednieuwe transactierij. Een écht
            # nieuwe (ISIN, Beurs)-combinatie staat hier vanzelfsprekend nog
            # niet in en wordt dus altijd gewoon opgelost. Vinkje AAN:
            # bekende_tickers leeg laten -> forceert een verse zoekopdracht
            # voor elke groep (zie CLAUDE.md/opdracht "vinkje ticker-
            # informatie opnieuw bepalen").
            bekende_tickers = {}
            if not herbepaal_alle_tickers:
                cur.execute(
                    "SELECT isin, beurs, ticker FROM transacties WHERE code = %s AND ticker IS NOT NULL",
                    (code,),
                )
                bekende_tickers = {(isin_val, beurs_val): ticker for isin_val, beurs_val, ticker in cur.fetchall()}

            transacties_per_groep = {
                key: [
                    {"datum": row["Datum"].strftime("%Y-%m-%d"), "koers": float(row["Koers"])}
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
                    # Zeldzame fallback: de eerste Product-naam van de groep gaf
                    # geen match, probeer de overige rijen (zelfde gedrag als
                    # voorheen). Goedkoop: zonder ticker doet find_ticker_met_
                    # snelle_prijscheck() geen enkele prijscheck.
                    for _, row in groep.iterrows():
                        detail = find_ticker_met_snelle_prijscheck(
                            row["Product"], row["ISIN"], row["Beurs"], transacties_per_groep[key],
                            bekende_tickers.get(key),
                        )
                        if detail["ticker"]:
                            break
                ticker_by_isin_beurs[key] = detail["ticker"]
                isin, beurs_val = key
                print(f"[upload] ISIN {isin} (beurs={beurs_val}) -> ticker {detail['ticker']} "
                      f"(zekerheid={detail['zekerheid']})")
            print(f"[upload] ticker-resolutie (incl. snelle prijscheck, parallel) klaar")

        with meet_tijd(f"db_insert_transacties ({len(rows_to_insert)} rij(en))"):
            ingevoegd = 0
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
                         ticker_by_isin_beurs[(row["ISIN"], row["Beurs"])], float(row["Aantal"]), float(row["Koers"]),
                         float(row["Totaal EUR"]), row["Order ID"], row["Product"],
                         float(kosten_waarde) if pd.notna(kosten_waarde) else None,
                         float(waarde_eur_waarde) if pd.notna(waarde_eur_waarde) else None,
                         _normaliseer_tijd(row["Tijd"])),
                    )
                    ingevoegd += 1
                except Exception as e:
                    print(f"[upload] FOUT bij invoegen rij (Order ID {row['Order ID']}): {e}")

            print(f"[upload] {ingevoegd}/{len(rows_to_insert)} rijen succesvol verwerkt")

    conn.commit()
    cur.close()
    conn.close()

    if not rows_bestaand.empty:
        with meet_tijd(f"db_backfill_kosten_en_tijd ({len(rows_bestaand)} rij(en))"):
            order_id_kosten = [
                (row["Order ID"], float(row["_kosten_eur"]) if pd.notna(row["_kosten_eur"]) else None)
                for _, row in rows_bestaand.iterrows()
            ]
            gebackfilld = backfill_transactiekosten(code, order_id_kosten)
            if gebackfilld:
                print(f"[upload] {gebackfilld} bestaande rij(en) kregen een backfilled transactiekosten-bedrag")

            order_id_waarde = [
                (row["Order ID"], float(row["_waarde_eur"]) if pd.notna(row["_waarde_eur"]) else None)
                for _, row in rows_bestaand.iterrows()
            ]
            waarde_gebackfilld = backfill_waarde_eur(code, order_id_waarde)
            if waarde_gebackfilld:
                print(f"[upload] {waarde_gebackfilld} bestaande rij(en) kregen een backfilled waarde_eur-bedrag")

            order_id_tijd = [
                (row["Order ID"], _normaliseer_tijd(row["Tijd"]))
                for _, row in rows_bestaand.iterrows()
            ]
            tijd_gebackfilld = backfill_tijd(code, order_id_tijd)
            if tijd_gebackfilld:
                print(f"[upload] {tijd_gebackfilld} bestaande rij(en) kregen een backfilled tijdstip")

    if match_code:
        # Alleen zinvol bij een upload naar een BESTAANDE portfolio: een
        # verbeterde ticker-resolutielogica (bv. de G2X.MU-fix) corrigeert
        # anders alleen nieuw ingevoegde rijen, nooit wat al in de database
        # stond. Overschrijft alleen tickers die nu een prijsprobleem
        # hebben met een kandidaat die dat niet heeft (zie
        # analysis.backfill_verouderde_tickers).
        with meet_tijd("db_backfill_verouderde_tickers"):
            tickers_gecorrigeerd = backfill_verouderde_tickers(code, forceer=herbepaal_alle_tickers)
            if tickers_gecorrigeerd:
                print(f"[upload] {tickers_gecorrigeerd} bestaande (ISIN, Beurs)-groep(en) kregen een "
                      f"gecorrigeerde ticker via backfill")

    bestand2 = request.files.get("bestand2")
    if bestand2 and bestand2.filename != "":
        with meet_tijd("dividend_bestand_verwerken"):
            dividend_records = verwerk_rekeningoverzicht(bestand2)
            save_dividenden(code, dividend_records)
            print(f"[upload] rekeningoverzicht verwerkt: {len(dividend_records)} dividendrecord(s) opgeslagen voor code {code}")

    response = jsonify(build_portfolio_response(code))
    log_yahoo_call_samenvatting()
    return response


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    code = code.strip().upper()
    result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    return jsonify(result)


@app.route("/api/portfolio/<code>/verrijking")
def portfolio_verrijking(code):
    """
    Lui opgevraagde 'rest' van het dashboard (Verdeling, Land, Sector,
    Bedrijven, ETF-overlap) — bewust NIET in het hoofd-/upload-antwoord,
    want dit is het netwerk-zware deel (classificatie + holdings/sector-
    ophalen bij nog-niet-gecachete ETF's/aandelen). Zie CLAUDE.md /
    opdracht_gefaseerd_laden.md voor de achtergrond. De frontend roept dit
    meteen na het tonen van de Home-pagina aan en vult de betreffende
    tabbladen zodra dit antwoord binnenkomt.
    """
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten, waarde_eur, tijd "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten", "waarde_eur", "tijd"],
    )
    transacties_df["transactiekosten"] = transacties_df["transactiekosten"].astype(float)
    transacties_df["waarde_eur"] = transacties_df["waarde_eur"].astype(float)

    try:
        response = jsonify(analyze_transacties_verrijking(transacties_df, code))
        # Geen reset_yahoo_call_teller() hier: /verrijking wordt door de
        # frontend los van /upload aangeroepen, dus deze samenvatting toont
        # het CUMULATIEVE aantal calls sinds de laatste reset in
        # _upload_impl() (dus inclusief de kern-fase van /upload) -- zie
        # opdracht performance-meting.
        log_yahoo_call_samenvatting()
        return response
    except Exception as e:
        print(f"[verrijking] ONVERWACHTE FOUT voor code={code}: {e}")
        return jsonify({
            "error": "Verdeling/land/sector/bedrijven ophalen duurde te lang of is mislukt. Probeer het "
                     "opnieuw door de pagina te verversen."
        }), 500


def _laad_transacties_en_resultaat(code):
    """Haalt transacties op voor `code`, past split-correctie toe en
    berekent de waarde-tijdreeks (resultaat) — gedeelde basis voor de lui
    geladen endpoints die op deze twee objecten verder rekenen
    (benchmark-vergelijking, xirr-over-tijd), zodat het hoofd-dashboard-
    antwoord (build_portfolio_response) dit niet standaard hoeft mee te
    sturen. Geeft (transacties_df, resultaat) terug; resultaat is None als
    er geen koersdata is. (None, None) als de code niet bestaat."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return None, None

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten, waarde_eur, tijd "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten", "waarde_eur", "tijd"],
    )
    transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date)
    if price_data.empty:
        return transacties_df, None

    resultaat = compute_value_over_time(transacties_df, price_data)
    return transacties_df, resultaat


@app.route("/api/portfolio/<code>/benchmark-vergelijking")
def benchmark_vergelijking(code):
    """
    Losse, lui opgevraagde endpoint voor de "Vergelijk met..."-optie op het
    Rendement-tabblad — bewust niet standaard in het hoofd-dashboard-
    antwoord, want dit haalt (en cachet) koersdata op voor een extra ticker
    die niets met de eigen portfolio te maken heeft, wat de hoofdpagina
    onnodig zou vertragen voor een optie die de meeste bezoeken niet
    gebruiken. Query-param 'benchmark' is een sleutel uit BENCHMARK_TICKERS
    (bv. "S%26P%20500" voor "S&P 500"). Query-param 'eigen_ticker' is een
    alternatief: een ticker die al in de eigen portfolio zit, voor de
    "vergelijk ook met eigen aandeel"-optie — zelfde berekening
    (bereken_benchmark_vergelijking is generiek genoeg), alleen een andere
    koersbron.
    """
    code = code.strip().upper()
    benchmark_naam = request.args.get("benchmark", "")
    eigen_ticker = request.args.get("eigen_ticker", "")

    transacties_df, resultaat = _laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    if eigen_ticker:
        eigen_tickers_in_portfolio = transacties_df["ticker"].dropna().unique().tolist()
        if eigen_ticker not in eigen_tickers_in_portfolio:
            return jsonify({"error": f"Ticker '{eigen_ticker}' zit niet in deze portfolio."}), 400
        vergelijk_ticker = eigen_ticker
        vergelijk_label = eigen_ticker
    else:
        vergelijk_ticker = BENCHMARK_TICKERS.get(benchmark_naam)
        if not vergelijk_ticker:
            return jsonify({"error": f"Onbekende benchmark '{benchmark_naam}'."}), 400
        vergelijk_label = benchmark_naam

    vergelijk_prices = get_prices([vergelijk_ticker], transacties_df["datum"].min())
    if vergelijk_ticker not in vergelijk_prices.columns:
        return jsonify({"error": f"Geen koersdata gevonden voor '{vergelijk_label}'."}), 400

    vergelijking = bereken_benchmark_vergelijking(transacties_df, resultaat, vergelijk_prices[vergelijk_ticker])
    if vergelijking is None:
        return jsonify({"error": "Vergelijking kon niet berekend worden."}), 400

    return jsonify(vergelijking)


@app.route("/api/portfolio/<code>/rendement-over-tijd")
def rendement_over_tijd(code):
    """
    Losse, lui opgevraagde endpoint voor het "XIRR & rendement"-tabblad —
    zelfde reden als benchmark_vergelijking() hierboven: niet standaard in
    het hoofd-dashboard-antwoord, want dit herberekent XIRR voor elke
    maandelijkse stap (zie bereken_rendement_over_tijd), wat de hoofdpagina
    onnodig zou vertragen voor een tabblad dat niet elk bezoek bekeken wordt.

    ?stap=dag laat de gebruiker via een knop in de UI de duurdere, dagelijkse
    berekening opvragen (zie bereken_rendement_over_tijd) -- standaard blijft
    het lichtere "maand".
    """
    code = code.strip().upper()
    stap = "dag" if request.args.get("stap") == "dag" else "maand"
    transacties_df, resultaat = _laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    return jsonify(bereken_rendement_over_tijd(transacties_df, resultaat, stap=stap))


@app.route("/api/portfolio/<code>/ticker-koers-bereik")
def ticker_koers_bereik(code):
    """
    Extra koersdata voor 1 ticker buiten de standaard-crop, t.b.v. de
    "meer historie laden"-knoppen op het 'Per aandeel aankoop'-tabblad
    (per_ticker_aankoop in de hoofd-payload is gecropt tot de aanhoud-
    periode). Query-params: ticker (verplicht), vanaf (YYYY-MM-DD,
    verplicht), tot (YYYY-MM-DD, optioneel, default vandaag). Bewust een
    los, lui endpoint i.p.v. de crop-range in analyze_transacties() op te
    rekken -- zelfde reden als bij ticker-zekerheid: dit raakt alleen deze
    ene knop, niet elke portfolio-load.
    """
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    bestaat = cur.fetchone() is not None
    cur.close()
    conn.close()
    if not bestaat:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    ticker = request.args.get("ticker")
    vanaf = request.args.get("vanaf")
    tot = request.args.get("tot")
    if not ticker or not vanaf:
        return jsonify({"error": "ticker en vanaf zijn verplicht"}), 400

    price_data = get_prices([ticker], vanaf)
    if price_data.empty or ticker not in price_data.columns:
        return jsonify({"labels": [], "koers": [], "vroegste_beschikbare_datum": None})

    serie = price_data[ticker].dropna()
    if tot:
        serie = serie[serie.index <= pd.Timestamp(tot)]

    return jsonify({
        "labels": [d.strftime("%Y-%m-%d") for d in serie.index],
        "koers": [round(float(k), 4) for k in serie.values],
        # Laat de frontend weten of de gevraagde 'vanaf' daadwerkelijk
        # gehaald is, of dat de historie eerder al ophield (bv. bij een
        # positie die pas een paar maanden genoteerd staat) -- t.b.v. het
        # uitgrijzen van een knop die niks meer oplevert.
        "vroegste_beschikbare_datum": serie.index.min().strftime("%Y-%m-%d") if len(serie) else None,
    })


def _ticker_zekerheid_groepen(code):
    """
    Haalt de transacties van 'code' op en groepeert ze per (ISIN, Beurs) —
    gedeeld door de volledige route, de lichte lijst-route en de
    per-positie-route hieronder, zodat de groepeerlogica (en de corporate-
    action-rijen-filter) maar op één plek staat. Geeft None terug als de
    code niet bestaat, anders een lijst van ((isin, beurs), info)-tuples
    met info = {"naam", "echte_naam", "beurs", "isin", "transacties"}.

    Groeperen per (ISIN, Beurs), niet per ISIN alleen: dezelfde ISIN kan op
    meerdere beurzen genoteerd staan (bv. een fonds met een Amsterdam- én
    een Londen-notering) en dat zijn dan ECHT verschillende tickers met
    eigen koersen — alles onder één ISIN op een hoop gooien zou de
    steekproef van de ene notering vervuilen met transactiedatums/prijzen
    die bij de andere notering horen. echte_naam (niet product!) gaat naar
    de Yahoo-zoekopdracht: product kan een door de gebruiker aangepaste
    bijnaam zijn, en die is onbruikbaar als zoekterm.
    Corporate-action-/NON TRADEABLE-rijen (splits e.d.) horen niet als eigen
    "positie" in deze lijst -- zelfde check als elders in het project
    (analysis._is_corporate_action_row), hier vóór het groeperen toegepast
    zodat zo'n rij nooit een kansloze eigen (ISIN, Beurs)-groep vormt.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return None

    cur.execute(
        "SELECT isin, product, echte_naam, beurs, datum, koers "
        "FROM transacties WHERE code = %s ORDER BY isin, datum",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    per_isin_beurs = {}
    for isin, product, echte_naam, beurs, datum, koers in rows:
        if _is_corporate_action_row({"beurs": beurs, "product": product}):
            continue
        groep = per_isin_beurs.setdefault(
            (isin, beurs), {"naam": product, "echte_naam": echte_naam, "beurs": beurs, "isin": isin, "transacties": []}
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    return list(per_isin_beurs.items())


@app.route("/api/portfolio/<code>/ticker-zekerheid")
def ticker_zekerheid(code):
    """
    Losse, lui opgevraagde endpoint voor de Ticker-zekerheid-pagina — bewust
    NIET onderdeel van het hoofd-dashboard-antwoord, want dit doet per
    positie tot een paar extra yfinance-prijscontroles (zie
    analysis.verifieer_ticker_met_prijs), wat de hoofdpagina onnodig zou
    vertragen voor een tabblad dat maar zelden bezocht wordt.

    LET OP: bij een groter portfolio kan deze route in z'n geheel mislukken
    omdat verifieer_tickers_met_prijs_parallel() moet wachten tot ALLE
    posities klaar zijn — één trage/rate-limited positie laat dan de hele
    opvraag timen out, ook al zijn de andere posities allang klaar (zelfde
    patroon als het eerdere Statistieken-incident, zie CLAUDE.md). De
    Ticker-zekerheid-pagina gebruikt daarom sinds kort de lichte lijst-route
    + per-positie-route hieronder in plaats van deze route. Blijft bestaan
    voor eventueel ander gebruik.
    """
    code = code.strip().upper()
    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    print(f"[ticker-zekerheid] {len(groepen)} positie(s) parallel verifiëren voor code={code}")
    t0 = time.time()
    try:
        resultaten = verifieer_tickers_met_prijs_parallel(
            [(info["echte_naam"], isin, info["beurs"], info["transacties"]) for (isin, beurs), info in groepen]
        )
    except Exception as e:
        print(f"[ticker-zekerheid] FOUT bij verifiëren voor code={code}: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren duurde te lang of is mislukt. Probeer het opnieuw."
        }), 500
    print(f"[ticker-zekerheid] {len(groepen)} positie(s) geverifieerd in {time.time() - t0:.1f}s")

    posities = []
    for ((isin, beurs), info), resultaat in zip(groepen, resultaten):
        resultaat["isin"] = isin
        resultaat["naam"] = info["naam"]
        resultaat["echte_naam"] = info["echte_naam"]
        posities.append(resultaat)

    return jsonify({"posities": posities})


@app.route("/api/portfolio/<code>/ticker-zekerheid/lijst")
def ticker_zekerheid_lijst(code):
    """
    Lichte variant van de route hierboven: geeft alleen de posities terug
    (isin/beurs/naam), zonder de dure prijscontrole — vrijwel instant. De
    Ticker-zekerheid-pagina haalt hiermee meteen alle rijen op om als
    "bezig..." te tonen, en start daarna per positie een losse aanroep naar
    /ticker-zekerheid/positie hieronder (zie static/js/app.js). Zo blokkeert
    één trage/mislukte positie niet meer de andere resultaten.
    """
    code = code.strip().upper()
    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    posities = [
        {"isin": isin, "beurs": beurs, "naam": info["naam"], "echte_naam": info["echte_naam"]}
        for (isin, beurs), info in groepen
    ]
    return jsonify({"posities": posities})


@app.route("/api/portfolio/<code>/ticker-zekerheid/positie")
def ticker_zekerheid_positie(code):
    """
    Verifieert precies 1 positie (isin+beurs via de querystring) — de
    Ticker-zekerheid-pagina roept dit per positie apart aan (met een
    concurrency-limiet, zie static/js/app.js) i.p.v. te wachten tot ALLE
    posities klaar zijn. Hergebruikt verifieer_ticker_met_prijs() zoals de
    volledige route hierboven, alleen voor 1 (isin, beurs)-groep i.p.v. de
    hele portfolio — geen nieuwe backend-logica, alleen een kleinere
    aanroep-eenheid zodat één trage/rate-limited positie niet meer de hele
    opvraag laat mislukken en elke aparte aanroep ruim binnen een gunicorn-
    timeout blijft.
    """
    code = code.strip().upper()
    isin = request.args.get("isin", "")
    beurs = request.args.get("beurs", "")

    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    info = dict(groepen).get((isin, beurs))
    if info is None:
        return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

    t0 = time.time()
    try:
        resultaat = verifieer_ticker_met_prijs(info["echte_naam"], isin, info["beurs"], info["transacties"])
    except Exception as e:
        print(f"[ticker-zekerheid] FOUT bij verifiëren van {isin} ({beurs}) voor code={code}: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren voor deze positie is mislukt. Probeer het opnieuw."
        }), 500
    print(f"[ticker-zekerheid] positie {isin} ({beurs}) klaar in {time.time() - t0:.1f}s voor code={code}")

    resultaat["isin"] = isin
    resultaat["naam"] = info["naam"]
    resultaat["echte_naam"] = info["echte_naam"]
    return jsonify(resultaat)


@app.route("/api/ticker-zekerheid-check", methods=["POST"])
def ticker_zekerheid_check():
    """
    Uitgebreide, prijs-geverifieerde ticker-zekerheid voor een 'niet
    opslaan'-analyse. Die heeft geen opgeslagen code om de route hierboven
    mee aan te roepen (die leest transacties uit de database) — maar
    verifieer_ticker_met_prijs() is een pure functie op aangeleverde
    transacties, dus laat de frontend die data hier los meesturen
    (huidigeData.ticker_posities_ruw, meegegeven door de niet_opslaan-tak
    van /upload). Losse, expliciet door de gebruiker aangevraagde actie
    i.p.v. synchroon in de hoofd-/upload-flow — zie de niet_opslaan-tak in
    _upload_impl() voor de reden (gunicorn-timeout-risico bij grotere/
    koude-cache-portfolio's, CLAUDE.md Statistieken-incident 2026-08-31).
    """
    data = request.get_json(silent=True) or {}
    posities = data.get("posities") or []
    if not posities:
        return jsonify({"error": "Geen posities meegestuurd."}), 400

    print(f"[ticker-zekerheid-check] {len(posities)} positie(s) parallel verifiëren (niet-opgeslagen analyse)")
    t0 = time.time()
    try:
        input_tuples = [
            (
                p.get("naam"), p.get("isin"), p.get("beurs"),
                [{"datum": t.get("datum"), "koers": t.get("koers")} for t in (p.get("transacties") or [])],
            )
            for p in posities
        ]
        resultaten = verifieer_tickers_met_prijs_parallel(input_tuples)
    except Exception as e:
        print(f"[ticker-zekerheid-check] FOUT: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren duurde te lang of is mislukt. Probeer het opnieuw, eventueel "
                     "met minder posities tegelijk."
        }), 500
    print(f"[ticker-zekerheid-check] {len(posities)} positie(s) geverifieerd in {time.time() - t0:.1f}s")

    uitkomst = []
    for p, resultaat in zip(posities, resultaten):
        resultaat["isin"] = p.get("isin")
        resultaat["naam"] = p.get("naam")
        resultaat["echte_naam"] = p.get("naam")
        uitkomst.append(resultaat)

    return jsonify({"posities": uitkomst})


@app.route("/api/portfolio/<code>/dividend")
def dividend(code):
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    cur.close()
    conn.close()

    samenvatting = bereken_dividend_samenvatting(code)
    if samenvatting is None:
        return jsonify({"beschikbaar": False})

    samenvatting["beschikbaar"] = True
    return jsonify(samenvatting)


def build_portfolio_response(code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    result = cur.fetchone()
    if result is None:
        cur.close()
        conn.close()
        return None
    naam = result[0]

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten, waarde_eur, tijd "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten", "waarde_eur", "tijd"],
    )
    transacties_df["transactiekosten"] = transacties_df["transactiekosten"].astype(float)
    transacties_df["waarde_eur"] = transacties_df["waarde_eur"].astype(float)

    return analyze_transacties_kern(transacties_df, code, naam)


def analyze_transacties_kern(transacties_df, code, naam):
    """
    Alles wat de Home-, Rendement-, Per-aandeel- en Statistieken-tabbladen
    nodig hebben — bewust ZONDER classify_tickers/land/sector/bedrijven-
    verdeling/ETF-overlap, want dat is het netwerk-zware deel dat bij een
    nieuwe, koude-cache-portfolio de meeste tijd kost (zie CLAUDE.md /
    opdracht_gefaseerd_laden.md). Die rest wordt lui opgehaald via
    analyze_transacties_verrijking() + de /verrijking-route.
    """
    with meet_tijd("split_correctie_kern"):
        transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    with meet_tijd(f"koersen_ophalen_kern ({len(tickers)} ticker(s))"):
        price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None}

    laatste_koersdatum, laatst_opgehaald_op = get_laatste_prijs_update(tickers)

    resultaat = compute_value_over_time(transacties_df, price_data)
    per_ticker = compute_per_ticker(transacties_df, price_data)
    per_ticker_aankoop = compute_per_ticker_koers_en_aankopen(transacties_df, price_data)

    ticker_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"], keep="last")
        .set_index("ticker")["product"]
        .to_dict()
    )
    echte_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"])
        .set_index("ticker")["echte_naam"]
        .to_dict()
    )

    # Prijswaarschuwingen zichtbaar maken bij ELK bezoek (niet alleen direct
    # na de upload): ticker_waarschuwingen_voor_transacties() leest alleen
    # de al gecachete ticker_prijscheck-check (gevuld door find_ticker_met_
    # snelle_prijscheck bij upload), dus dit kost hier geen nieuwe Yahoo-
    # calls in het gangbare geval.
    ticker_waarschuwingen = ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen)

    # Bij de 'niet opslaan'-analyse (zie de niet_opslaan-tak in /upload) is
    # code None -- er is dan nooit dividendhistorie (die zit in de database),
    # dus gewoon leeg laten i.p.v. crashen.
    with meet_tijd("dividend_samenvatting"):
        dividend_data = bereken_dividend_samenvatting(code) if code else None
    dividend_per_ticker = (
        {d["ticker"]: d["totaal_netto"] for d in dividend_data["per_ticker"]}
        if dividend_data else {}
    )
    statistieken = bereken_statistieken(
        transacties_df, price_data, resultaat,
        dividend_per_ticker=dividend_per_ticker, ticker_namen=ticker_namen,
    )

    return {
        "code": code,
        "naam": naam,
        "chart_data": {
            "labels": [d.strftime("%Y-%m-%d") for d in resultaat.index],
            "waarde": resultaat["waarde"].round(2).tolist(),
            "geinvesteerd": resultaat["geinvesteerd"].round(2).tolist(),
            "rendement": resultaat["rendement"].round(2).tolist(),
        },
        "per_ticker": per_ticker,
        "per_ticker_aankoop": per_ticker_aankoop,
        "statistieken": statistieken,
        "tickers": [
            {
                "ticker": t, "naam": ticker_namen.get(t, t), "echte_naam": echte_namen.get(t, t),
                "nog_in_bezit": per_ticker[t]["nog_in_bezit"],
            }
            for t in _sorteer_tickers_voor_dropdown(per_ticker)
        ],
        "ticker_waarschuwingen": ticker_waarschuwingen,
        "laatste_koersdatum": laatste_koersdatum.strftime("%Y-%m-%d") if laatste_koersdatum else None,
        # 'Z'-suffix: bijgewerkt_op is een naive TIMESTAMP-kolom, maar Neon
        # draait in GMT/UTC (geverifieerd via CURRENT_SETTING('timezone')),
        # dus de opgeslagen waarde IS al UTC -- vandaar expliciet als
        # UTC-ISO-string meesturen i.p.v. de naive string kaal door te geven.
        "laatst_opgehaald_op": laatst_opgehaald_op.isoformat() + "Z" if laatst_opgehaald_op else None,
    }


def analyze_transacties_verrijking(transacties_df, code):
    """
    Het netwerk-zware deel: Verdeling, Land/Sector, Top-bedrijven en ETF-
    overlap — lui opgevraagd via /api/portfolio/<code>/verrijking, ná de
    Home-pagina (zie analyze_transacties_kern). Doet ZELF opnieuw
    compute_split_adjusted_shares/get_prices — dat is bij het gangbare
    gebruik (kern is al opgehaald) een warme cache-hit, geen nieuwe download.
    """
    with meet_tijd("split_correctie_verrijking"):
        transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    with meet_tijd(f"koersen_ophalen_verrijking ({len(tickers)} ticker(s))"):
        price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {"verdeling": [], "land_sector_verdeling": {}, "bedrijven_verdeling": {}, "etf_overlap": {}}

    ticker_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"], keep="last")
        .set_index("ticker")["product"]
        .to_dict()
    )

    huidige_holdings = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    with meet_tijd("verrijking_totaal"):
        with meet_tijd("verrijking_classificatie_en_cache_warm"):
            is_etf_map = classify_tickers(list(huidige_holdings.index))
            _verwarm_land_sector_cache_parallel(list(huidige_holdings.index), is_etf_map)

        with meet_tijd("verrijking_land_sector"):
            land_sector_verdeling = compute_land_sector_verdeling(transacties_df, price_data, is_etf_map)

        with meet_tijd("verrijking_bedrijven"):
            bedrijven_verdeling = bereken_bedrijven_verdeling(transacties_df, price_data, is_etf_map)

        with meet_tijd("verrijking_etf_overlap"):
            etf_overlap = bereken_etf_overlap(transacties_df, price_data, is_etf_map)

    verdeling = []
    for ticker, aantal in huidige_holdings.items():
        if ticker not in price_data.columns:
            continue
        waarde = float(aantal) * float(laatste_prijzen[ticker])
        if waarde <= 0:
            continue
        verdeling.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "waarde": round(waarde, 2),
            "is_etf": is_etf_map.get(ticker, False),
        })

    verdeling = _sorteer_verdeling_groot_naar_klein(verdeling)

    return {
        "verdeling": verdeling,
        "land_sector_verdeling": land_sector_verdeling,
        "bedrijven_verdeling": bedrijven_verdeling,
        "etf_overlap": etf_overlap,
    }


def analyze_transacties(transacties_df, code, naam):
    """Combineert kern + verrijking in één keer — voor de 'niet opslaan'-
    tak (geen opgeslagen code om later apart de verrijking op te halen) en
    voor eventuele andere plekken die de volledige, ongefaseerde data in
    één keer nodig hebben. De normale opslaande upload-flow en het
    bezoeken van een bestaande code gebruiken i.p.v. deze wrapper de kern-
    en verrijkingsfunctie apart (zie build_portfolio_response en de
    /verrijking-route)."""
    resultaat = analyze_transacties_kern(transacties_df, code, naam)
    if resultaat.get("chart_data") is None:
        return resultaat
    resultaat.update(analyze_transacties_verrijking(transacties_df, code))
    return resultaat

@app.route("/api/portfolio/<code>/bijnaam", methods=["POST"])
def set_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    bijnaam = (data.get("bijnaam") or "").strip()
    if not ticker or not bijnaam:
        return jsonify({"error": "Ticker en bijnaam zijn verplicht."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = %s WHERE code = %s AND ticker = %s",
        (bijnaam, code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(build_portfolio_response(code))


@app.route("/api/portfolio/<code>/reset-bijnaam", methods=["POST"])
def reset_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    if not ticker:
        return jsonify({"error": "Ticker is verplicht."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = echte_naam WHERE code = %s AND ticker = %s",
        (code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify(build_portfolio_response(code))


@app.route("/api/portfolio/<code>", methods=["DELETE"])
def verwijder_portfolio(code):
    code = code.strip().upper()
    delete_portfolio(code)
    return jsonify({"success": True})


@app.route("/api/portfolio/<code>/wijzig-code", methods=["POST"])
def wijzig_code(code):
    code = code.strip().upper()
    data = request.get_json()
    nieuwe_code = (data.get("nieuwe_code") or "").strip().upper()

    if not is_geldige_code(nieuwe_code):
        return jsonify({"error": f"Ongeldige code. Gebruik precies {CODE_LENGTH} hoofdletters (A-Z)."}), 400
    if nieuwe_code == code:
        return jsonify({"error": "De nieuwe code is gelijk aan de huidige code."}), 400

    success, foutmelding = wijzig_portfolio_code(code, nieuwe_code)
    if not success:
        return jsonify({"error": foutmelding}), 400

    return jsonify(build_portfolio_response(nieuwe_code))

if __name__ == "__main__":
    app.run(debug=True)