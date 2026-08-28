from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db, delete_portfolio
from analysis import generate_code, find_ticker_detailed, get_prices, compute_value_over_time, find_matching_code, compute_per_ticker, classify_tickers, compute_split_adjusted_shares, compute_land_sector_verdeling, verifieer_ticker_met_prijs, verwerk_rekeningoverzicht, bereken_dividend_samenvatting, bereken_statistieken
from db import save_dividenden
import hashlib
import openpyxl
import math

app = Flask(__name__)
init_db()

# Kolomnaam exact zoals DeGiro 'm in het transactiebestand zet (na
# df.columns.str.strip(), dat evt. rondom-spaties in de header wegwerkt).
# Ontbreekt in oudere DeGiro-exportformaten — daarom overal met een
# beschikbaarheids-check behandeld i.p.v. als verplichte kolom.
KOSTEN_KOLOM = "Transactiekosten en/of kosten van derden EUR"

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
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    bestand1.seek(0)
    df = pd.read_excel(bestand1)
    print(f"[upload] Excel ingelezen: {df.shape[0]} rijen, kolommen: {df.columns.tolist()}")

    df.columns = df.columns.str.strip()

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

    niet_opslaan = request.form.get("niet_opslaan") == "on"
    if niet_opslaan:
        print("[upload] 'Niet opslaan' aangevinkt — eenmalige analyse, niets wordt in de database opgeslagen")
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen: dezelfde ISIN kan
        # op meerdere beurzen genoteerd staan (bv. een fonds met een
        # Amsterdam- én een Londen-notering) en dat zijn dan ECHT
        # verschillende tickers — één ticker per ISIN voor de hele groep zou
        # de tweede notering stilzwijgend de ticker van de eerste geven.
        #
        # verifieer_ticker_met_prijs() heeft geen code/database nodig (het is
        # een pure functie op de aangeleverde transacties), dus kunnen we
        # hier meteen de volle, prijsgeverifieerde Ticker-zekerheid-data
        # opbouwen i.p.v. alleen de kale beurs-match — dat scheelt een aparte
        # "basis"-weergave voor een eenmalige analyse zonder code.
        ticker_by_isin_beurs = {}
        ticker_zekerheid = []
        for (isin, beurs_val), groep in df.groupby(["ISIN", "Beurs"]):
            representatieve_naam = groep["Product"].iloc[0]
            transacties_voor_verificatie = [
                {"datum": row["Datum"], "koers": row["Koers"]} for _, row in groep.iterrows()
            ]
            resultaat = verifieer_ticker_met_prijs(representatieve_naam, isin, beurs_val, transacties_voor_verificatie)
            ticker_by_isin_beurs[(isin, beurs_val)] = resultaat["ticker"]
            resultaat["isin"] = isin
            resultaat["naam"] = representatieve_naam
            resultaat["echte_naam"] = representatieve_naam
            ticker_zekerheid.append(resultaat)
            print(f"[upload] ISIN {isin} (beurs={beurs_val}) -> ticker {resultaat['ticker']} "
                  f"(zekerheid={resultaat['zekerheid']})")

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
        })
        result = analyze_transacties(transacties_df, code=None, naam=naam or None)
        result["ticker_zekerheid"] = ticker_zekerheid
        return jsonify(result)

    # Order ID-kolom kan door merged cells één kolom verschoven staan t.o.v. de header;
    # lees 'm daarom apart uit met openpyxl, die de waarden onder de merge vindt.
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
    else:
        code = generate_code(cur)
        cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (code, naam or None))
        rows_to_insert = df

    print(f"[upload] code={code}, rows_to_insert={len(rows_to_insert)} rijen")

    if not rows_to_insert.empty:
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen — zie de
        # 'niet_opslaan'-tak hierboven voor de reden (een ISIN kan op
        # meerdere beurzen genoteerd staan, met een écht andere ticker).
        ticker_by_isin_beurs = {}
        for (isin, beurs_val), groep in rows_to_insert.groupby(["ISIN", "Beurs"]):
            detail = {"ticker": None, "zekerheid": "geen_match", "alternatieven": []}
            for _, row in groep.iterrows():
                detail = find_ticker_detailed(row["Product"], row["ISIN"], row["Beurs"])
                if detail["ticker"]:
                    break
            ticker_by_isin_beurs[(isin, beurs_val)] = detail["ticker"]
            print(f"[upload] ISIN {isin} (beurs={beurs_val}) -> ticker {detail['ticker']} "
                  f"(zekerheid={detail['zekerheid']})")

        ingevoegd = 0
        for _, row in rows_to_insert.iterrows():
            try:
                kosten_waarde = row["_kosten_eur"]
                cur.execute(
                    """INSERT INTO transacties
                       (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam, transactiekosten)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (code, order_id) DO NOTHING""",
                    (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
                     ticker_by_isin_beurs[(row["ISIN"], row["Beurs"])], float(row["Aantal"]), float(row["Koers"]),
                     float(row["Totaal EUR"]), row["Order ID"], row["Product"],
                     float(kosten_waarde) if pd.notna(kosten_waarde) else None),
                )
                ingevoegd += 1
            except Exception as e:
                print(f"[upload] FOUT bij invoegen rij (Order ID {row['Order ID']}): {e}")

        print(f"[upload] {ingevoegd}/{len(rows_to_insert)} rijen succesvol verwerkt")

    conn.commit()
    cur.close()
    conn.close()

    bestand2 = request.files.get("bestand2")
    if bestand2 and bestand2.filename != "":
        dividend_records = verwerk_rekeningoverzicht(bestand2)
        save_dividenden(code, dividend_records)
        print(f"[upload] rekeningoverzicht verwerkt: {len(dividend_records)} dividendrecord(s) opgeslagen voor code {code}")

    return jsonify(build_portfolio_response(code))


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    code = code.strip().upper()
    result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    return jsonify(result)


@app.route("/api/portfolio/<code>/ticker-zekerheid")
def ticker_zekerheid(code):
    """
    Losse, lui opgevraagde endpoint voor de Ticker-zekerheid-pagina — bewust
    NIET onderdeel van het hoofd-dashboard-antwoord, want dit doet per
    positie tot een paar extra yfinance-prijscontroles (zie
    analysis.verifieer_ticker_met_prijs), wat de hoofdpagina onnodig zou
    vertragen voor een tabblad dat maar zelden bezocht wordt.
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
        "SELECT isin, product, echte_naam, beurs, datum, koers "
        "FROM transacties WHERE code = %s ORDER BY isin, datum",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Groeperen per (ISIN, Beurs), niet per ISIN alleen: dezelfde ISIN kan op
    # meerdere beurzen genoteerd staan (bv. een fonds met een Amsterdam- én
    # een Londen-notering) en dat zijn dan ECHT verschillende tickers met
    # eigen koersen — alles onder één ISIN op een hoop gooien zou de
    # steekproef van de ene notering vervuilen met transactiedatums/prijzen
    # die bij de andere notering horen. echte_naam (niet product!) gaat naar
    # de Yahoo-zoekopdracht: product kan een door de gebruiker aangepaste
    # bijnaam zijn, en die is onbruikbaar als zoekterm.
    per_isin_beurs = {}
    for isin, product, echte_naam, beurs, datum, koers in rows:
        groep = per_isin_beurs.setdefault(
            (isin, beurs), {"naam": product, "echte_naam": echte_naam, "beurs": beurs, "isin": isin, "transacties": []}
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    posities = []
    for (isin, beurs), info in per_isin_beurs.items():
        print(f"[ticker-zekerheid] verifiëren: {isin} (beurs={beurs}, {info['naam']})")
        resultaat = verifieer_ticker_met_prijs(info["echte_naam"], isin, info["beurs"], info["transacties"])
        resultaat["isin"] = isin
        resultaat["naam"] = info["naam"]
        resultaat["echte_naam"] = info["echte_naam"]
        posities.append(resultaat)

    return jsonify({"posities": posities})


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
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten"],
    )
    transacties_df["transactiekosten"] = transacties_df["transactiekosten"].astype(float)

    return analyze_transacties(transacties_df, code, naam)


def analyze_transacties(transacties_df, code, naam):
    transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None}

    resultaat = compute_value_over_time(transacties_df, price_data)
    per_ticker = compute_per_ticker(transacties_df, price_data)

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

    is_etf_map = classify_tickers(list(per_ticker.keys()))
    land_sector_verdeling = compute_land_sector_verdeling(transacties_df, price_data)
    statistieken = bereken_statistieken(transacties_df, price_data, resultaat)

    huidige_holdings = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

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
        "verdeling": verdeling,
        "land_sector_verdeling": land_sector_verdeling,
        "statistieken": statistieken,
        "tickers": [
            {"ticker": t, "naam": ticker_namen.get(t, t), "echte_naam": echte_namen.get(t, t)}
            for t in per_ticker.keys()
        ],
    }

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

if __name__ == "__main__":
    app.run(debug=True)