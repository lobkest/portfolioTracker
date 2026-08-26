from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db
from analysis import generate_code, find_ticker, get_prices, compute_value_over_time, find_matching_code, compute_per_ticker

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


@app.route("/upload", methods=["POST"])
def upload():
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    df = pd.read_excel(bestand1)
    df.columns = df.columns.str.strip()
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["Order ID"] = df["Order ID"].astype(str)

    new_order_ids = set(df["Order ID"])

    conn = get_db_connection()
    cur = conn.cursor()

    match_code, missing_ids = find_matching_code(cur, new_order_ids)

    if match_code:
        code = match_code
        if naam:
            cur.execute(
                "UPDATE portfolios SET naam = %s WHERE code = %s",
                (naam, code),
            )
        rows_to_insert = df[df["Order ID"].isin(missing_ids)] if missing_ids else df.iloc[0:0]

    else:
        code = generate_code(cur)
        cur.execute(
            "INSERT INTO portfolios (code, naam) VALUES (%s, %s)",
            (code, naam or None),
        )
        rows_to_insert = df

    if not rows_to_insert.empty:
        combos = rows_to_insert[["Product", "ISIN", "Beurs"]].drop_duplicates()
        ticker_map = {}
        for _, row in combos.iterrows():
            key = (row["Product"], row["ISIN"], row["Beurs"])
            ticker_map[key] = find_ticker(row["Product"], row["ISIN"], row["Beurs"])

        for _, row in rows_to_insert.iterrows():
            key = (row["Product"], row["ISIN"], row["Beurs"])
            cur.execute(
                """INSERT INTO transacties
                   (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, order_id) DO NOTHING""",
                (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
                 ticker_map[key], float(row["Aantal"]), float(row["Koers"]),
                 float(row["Totaal EUR"]), row["Order ID"]),
            )

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

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows, columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur"]
    )

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None}

    resultaat = compute_value_over_time(transacties_df, price_data)
    per_ticker = compute_per_ticker(transacties_df, price_data)

    ticker_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"])
        .set_index("ticker")["product"]
        .to_dict()
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
        "tickers": [{"ticker": t, "naam": ticker_namen.get(t, t)} for t in per_ticker.keys()],
    }

if __name__ == "__main__":
    app.run(debug=True)