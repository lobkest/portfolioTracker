from flask import Flask, render_template, request, redirect, url_for
import pandas as pd
from db import get_db_connection, init_db
from analysis import generate_code, find_ticker, get_prices, compute_value_over_time

app = Flask(__name__)
init_db()  # buiten __main__, draait ook onder gunicorn/WSGI


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
#         return "Fout: het eerste bestand (transacties) is verplicht.", 400

#     df = pd.read_excel(bestand1)
#     df.columns = df.columns.str.strip()  # "Koers " -> "Koers"
#     df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)

#     conn = get_db_connection()
#     cur = conn.cursor()
#     code = generate_code(cur)
#     cur.execute(
#         "INSERT INTO portfolios (code, naam) VALUES (%s, %s)",
#         (code, naam or None),
#     )

#     combos = df[["Product", "ISIN", "Beurs"]].drop_duplicates()
#     ticker_map = {}
#     for _, row in combos.iterrows():
#         key = (row["Product"], row["ISIN"], row["Beurs"])
#         ticker_map[key] = find_ticker(row["Product"], row["ISIN"], row["Beurs"])

#     for _, row in df.iterrows():
#         key = (row["Product"], row["ISIN"], row["Beurs"])
#         cur.execute(
#             """INSERT INTO transacties
#                (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur)
#                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
#             (code, row["Datum"].date(), row["Product"], row["ISIN"], row["Beurs"],
#              ticker_map[key], float(row["Aantal"]), float(row["Koers"]), float(row["Totaal EUR"])),
#         )

#     conn.commit()
#     cur.close()
#     conn.close()

#     return redirect(url_for("dashboard", code=code))

@app.route("/upload", methods=["POST"])
def upload():
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return "Fout: het eerste bestand (transacties) is verplicht.", 400

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
        if not missing_ids:
            # Alles zat al in de database, niets nieuws te doen
            cur.close()
            conn.close()
            return redirect(url_for("dashboard", code=code))
        rows_to_insert = df[df["Order ID"].isin(missing_ids)]
    else:
        code = generate_code(cur)
        cur.execute(
            "INSERT INTO portfolios (code, naam) VALUES (%s, %s)",
            (code, naam or None),
        )
        rows_to_insert = df

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

    return redirect(url_for("dashboard", code=code))

@app.route("/dashboard/<code>")
def dashboard(code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    result = cur.fetchone()
    if result is None:
        cur.close()
        conn.close()
        return f"Geen portfolio gevonden met code '{code}'.", 404
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
        return render_template("dashboard.html", code=code, naam=naam, chart_data=None)

    resultaat = compute_value_over_time(transacties_df, price_data)

    chart_data = {
        "labels": [d.strftime("%Y-%m-%d") for d in resultaat.index],
        "waarde": resultaat["waarde"].round(2).tolist(),
        "geinvesteerd": resultaat["geinvesteerd"].round(2).tolist(),
        "rendement": resultaat["rendement"].round(2).tolist(),
    }

    return render_template("dashboard.html", code=code, naam=naam, chart_data=chart_data)


if __name__ == "__main__":
    app.run(debug=True)