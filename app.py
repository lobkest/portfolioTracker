from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db
from analysis import generate_code, find_ticker, get_prices, compute_value_over_time, find_matching_code, compute_per_ticker, classify_ticker
import hashlib
import openpyxl

app = Flask(__name__)
init_db()  

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


# @app.route("/upload", methods=["POST"])
# def upload():
#     naam = request.form.get("naam", "").strip()
#     bestand1 = request.files.get("bestand1")

#     if not bestand1 or bestand1.filename == "":
#         return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

#     df = pd.read_excel(bestand1)
#     df.columns = df.columns.str.strip()
#     df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
#     df["Order ID"] = df["Order ID"].astype(str)

#     new_order_ids = set(df["Order ID"])

#     conn = get_db_connection()
#     cur = conn.cursor()

#     match_code, missing_ids = find_matching_code(cur, new_order_ids)

#     if match_code:
#         code = match_code
#         if naam:
#             cur.execute(
#                 "UPDATE portfolios SET naam = %s WHERE code = %s",
#                 (naam, code),
#             )
#         rows_to_insert = df[df["Order ID"].isin(missing_ids)] if missing_ids else df.iloc[0:0]

#     else:
#         code = generate_code(cur)
#         cur.execute(
#             "INSERT INTO portfolios (code, naam) VALUES (%s, %s)",
#             (code, naam or None),
#         )
#         rows_to_insert = df

#     if not rows_to_insert.empty:
#         combos = rows_to_insert[["Product", "ISIN", "Beurs"]].drop_duplicates()
#         ticker_map = {}
#         for _, row in combos.iterrows():
#             key = (row["Product"], row["ISIN"], row["Beurs"])
#             ticker_map[key] = find_ticker(row["Product"], row["ISIN"], row["Beurs"])

#         for _, row in rows_to_insert.iterrows():
#             key = (row["Product"], row["ISIN"], row["Beurs"])
#             # cur.execute(
#             #     """INSERT INTO transacties
#             #        (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id)
#             #        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#             #        ON CONFLICT (code, order_id) DO NOTHING""",
#             #     (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
#             #      ticker_map[key], float(row["Aantal"]), float(row["Koers"]),
#             #      float(row["Totaal EUR"]), row["Order ID"]),
#             # )
#             cur.execute(
#             """INSERT INTO transacties
#                (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam)
#                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
#                ON CONFLICT (code, order_id) DO NOTHING""",
#             (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
#              ticker_map[key], float(row["Aantal"]), float(row["Koers"]),
#              float(row["Totaal EUR"]), row["Order ID"], row["Product"]),
#         )

#     conn.commit()
#     cur.close()
#     conn.close()

#     return jsonify(build_portfolio_response(code))

@app.route("/upload", methods=["POST"])
def upload():
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    # df = pd.read_excel(bestand1)
    # print(f"[upload] Excel ingelezen: {df.shape[0]} rijen, kolommen: {df.columns.tolist()}")
    # df.columns = df.columns.str.strip()
    bestand1.seek(0)
    df = pd.read_excel(bestand1)
    print(f"[upload] Excel ingelezen: {df.shape[0]} rijen, kolommen: {df.columns.tolist()}")

    df.columns = df.columns.str.strip()

    # Order ID-kolom kan door merged cells één kolom verschoven staan t.o.v. de header;
    # lees 'm daarom apart uit met openpyxl, die effectief de waarden onder de merge vindt.
    # bestand1.seek(0)
    # wb = openpyxl.load_workbook(bestand1, data_only=True)
    # ws = wb.active
    # order_ids_ruw = []
    # for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
    #     gevonden = None
    #     for cell in row:
    #         if cell.value and isinstance(cell.value, str) and len(cell.value) == 36 and cell.value.count("-") == 4:
    #             gevonden = cell.value
    #             break
    #     order_ids_ruw.append(gevonden)

    # if len(order_ids_ruw) == len(df):
    #     df["Order ID"] = order_ids_ruw
    #     print(f"[upload] Order ID's uitgelezen via openpyxl (merged-cell fix)")
    # else:
    #     print(f"[upload] WAARSCHUWING: rijaantal komt niet overeen ({len(order_ids_ruw)} vs {len(df)}), Order ID's mogelijk onbetrouwbaar")

    
    # df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    # # df["Order ID"] = df["Order ID"].astype(str)

    # df["Order ID"] = df["Order ID"].astype(str)

    # def maak_order_id(row):
    #     if row["Order ID"] and row["Order ID"].lower() != "nan":
    #         return row["Order ID"]
    #     # synthetische, stabiele ID voor rijen zonder eigen Order ID
    #     # (bv. corporate actions of niet-verhandelbare boekingen)
    #     basis = f"{row['Datum']}|{row['Tijd']}|{row['Product']}|{row['ISIN']}|{row['Aantal']}|{row['Totaal EUR']}"
    #     return "SYN-" + hashlib.md5(basis.encode()).hexdigest()[:16]

    # df["Order ID"] = df.apply(maak_order_id, axis=1)
    # print(f"[upload] {(df['Order ID'].str.startswith('SYN-')).sum()} rijen kregen een synthetische Order ID")

    # new_order_ids = set(df["Order ID"])
    # print(f"[upload] {len(new_order_ids)} unieke Order ID's in geüpload bestand")

    bestand1.seek(0)
    df = pd.read_excel(bestand1)
    print(f"[upload] Excel ingelezen: {df.shape[0]} rijen, kolommen: {df.columns.tolist()}")

    df.columns = df.columns.str.strip()
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)

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
        ticker_by_isin = {}
        for isin, groep in rows_to_insert.groupby("ISIN"):
            ticker = None
            for _, row in groep.iterrows():
                ticker = find_ticker(row["Product"], row["ISIN"], row["Beurs"])
                if ticker:
                    break
            ticker_by_isin[isin] = ticker
            print(f"[upload] ISIN {isin} -> ticker {ticker}")

        ingevoegd = 0
        for _, row in rows_to_insert.iterrows():
            try:
                cur.execute(
                    """INSERT INTO transacties
                       (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (code, order_id) DO NOTHING""",
                    (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
                     ticker_by_isin[row["ISIN"]], float(row["Aantal"]), float(row["Koers"]),
                     float(row["Totaal EUR"]), row["Order ID"], row["Product"]),
                )
                ingevoegd += 1
            except Exception as e:
                print(f"[upload] FOUT bij invoegen rij (Order ID {row['Order ID']}): {e}")

        print(f"[upload] {ingevoegd}/{len(rows_to_insert)} rijen succesvol verwerkt")

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(build_portfolio_response(code))


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    return jsonify(result)


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

    # cur.execute(
    #     "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur "
    #     "FROM transacties WHERE code = %s",
    #     (code,),
    # )
    # rows = cur.fetchall()
    # cur.close()
    # conn.close()

    # transacties_df = pd.DataFrame(
    #     rows, columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur"]
    # )

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows, columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam"]
    )

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None}

    resultaat = compute_value_over_time(transacties_df, price_data)
    per_ticker = compute_per_ticker(transacties_df, price_data)

    # ticker_namen = (
    #     transacties_df.dropna(subset=["ticker"])
    #     .drop_duplicates(subset=["ticker"])
    #     .set_index("ticker")["product"]
    #     .to_dict()
    # )
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
            "is_etf": classify_ticker(ticker),
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
        # "tickers": [{"ticker": t, "naam": ticker_namen.get(t, t)} for t in per_ticker.keys()],
        "tickers": [
            {"ticker": t, "naam": ticker_namen.get(t, t), "echte_naam": echte_namen.get(t, t)}
            for t in per_ticker.keys()
        ],
    }

@app.route("/api/portfolio/<code>/bijnaam", methods=["POST"])
def set_bijnaam(code):
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

if __name__ == "__main__":
    app.run(debug=True)