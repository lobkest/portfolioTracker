import random
import string
import pandas as pd
import yfinance as yf
from yahooquery import search
from db import get_db_connection, save_prices

BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    "TDG": ["GER", "MUN", "FRA"], "LSE": ["LSE"], "XLON": ["LSE"],
    "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
}


def generate_code(cur, length=3):
    """Genereert een unieke portfolio-code die nog niet in gebruik is."""
    chars = string.ascii_uppercase
    while True:
        code = "".join(random.choices(chars, k=length))
        cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
        if cur.fetchone() is None:
            return code


def find_ticker(product, isin, beurs):
    """Zoekt de Yahoo Finance ticker op basis van productnaam of ISIN."""
    if beurs == "DEG":  # corporate-action rij, geen echt aandeel/ETF
        return None
    targets = BEURS_MAP.get(beurs, [])

    def best_match(quotes):
        for exch in targets:
            for q in quotes:
                if q.get("exchange") == exch:
                    return q.get("symbol")
        return quotes[0].get("symbol") if quotes else None

    for query in [product, isin]:
        try:
            quotes = search(query).get("quotes", [])
        except Exception:
            quotes = []
        symbol = best_match(quotes)
        if symbol:
            return symbol
    return None


def get_prices(tickers, start_date):
    """Haalt koersen (in EUR) op voor een lijst tickers, met caching via de database."""
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_date),
    )
    cached = pd.DataFrame(cur.fetchall(), columns=["ticker", "datum", "koers_eur"])
    cur.close()
    conn.close()

    missing = [t for t in tickers if t not in cached["ticker"].unique()]

    if missing:
        raw = yf.download(missing, start=start_date, auto_adjust=True, progress=False)["Close"]
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(name=missing[0])
        raw = raw.ffill()

        for t in missing:
            if t not in raw.columns:
                continue
            try:
                currency = yf.Ticker(t).info.get("currency")
            except Exception:
                currency = "EUR"
            if currency in ("USD", "GBP", "GBp"):
                fx_pair = "USDEUR=X" if currency == "USD" else "GBPEUR=X"
                # fx = yf.download(fx_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
                fx = yf.download(fx_pair, start=start_date, auto_adjust=True, progress=False)["Close"].squeeze()
                fx = fx.reindex(raw.index).ffill()
                divisor = 100 if currency == "GBp" else 1
                raw[t] = raw[t] / divisor * fx

        fresh_rows = []
        for t in missing:
            if t not in raw.columns:
                continue
            for datum, koers in raw[t].dropna().items():
                fresh_rows.append((t, datum.date(), float(koers)))
        save_prices(fresh_rows)

        fresh_df = pd.DataFrame(fresh_rows, columns=["ticker", "datum", "koers_eur"])
        cached = pd.concat([cached, fresh_df], ignore_index=True)

    if cached.empty:
        return pd.DataFrame()

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    return cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()


def compute_value_over_time(transacties_df, price_data):
    """Berekent per dag: portfoliowaarde, totaal geïnvesteerd en rendement."""
    transacties_df = transacties_df.dropna(subset=["ticker"]).sort_values("datum").reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    holdings = {t: 0.0 for t in tickers}
    invested = 0.0
    rows = []
    trade_i = 0

    for date in price_data.index:
        while trade_i < len(transacties_df) and pd.Timestamp(transacties_df.loc[trade_i, "datum"]) <= date:
            row = transacties_df.loc[trade_i]
            if row["ticker"] in holdings:
                holdings[row["ticker"]] += float(row["aantal"])
            invested += -float(row["totaal_eur"])
            trade_i += 1

        waarde = sum(holdings[t] * price_data.loc[date, t] for t in tickers)
        rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

    result = pd.DataFrame(rows).set_index("datum")
    result["rendement"] = result["waarde"] - result["geinvesteerd"]
    return result

def compute_per_ticker(transacties_df, price_data):
    """Per ticker: waarde en geïnvesteerd bedrag over tijd."""
    transacties_df = transacties_df.dropna(subset=["ticker"]).sort_values("datum").reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        invested = 0.0
        trade_i = 0
        rows = []

        for date in price_data.index:
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["aantal"])
                invested += -float(row["totaal_eur"])
                trade_i += 1
            waarde = holdings * price_data.loc[date, ticker]
            rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

        df_t = pd.DataFrame(rows).set_index("datum")

        nonzero_idx = df_t.index[df_t["geinvesteerd"] > 0]
        if len(nonzero_idx) > 0:
            df_t = df_t.loc[nonzero_idx[0]:nonzero_idx[-1]]
        else:
            df_t = df_t.iloc[0:0]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            "waarde": df_t["waarde"].round(2).tolist(),
            "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
        }
    return result

def get_order_id_sets(cur):
    """Geeft per portfolio-code de set van al opgeslagen Order ID's terug."""
    cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def find_matching_code(cur, new_order_ids):
    """
    Zoekt een bestaande portfolio die dezelfde persoon vertegenwoordigt:
    - bestaande data zit volledig in de nieuwe upload (update met extra transacties), of
    - de nieuwe upload zit volledig in de bestaande data (niets nieuws)
    Geeft (code, ontbrekende_order_ids) terug, of (None, None) als er geen match is.
    """
    existing = get_order_id_sets(cur)
    for code, ids in existing.items():
        if ids <= new_order_ids:
            return code, new_order_ids - ids
        if new_order_ids <= ids:
            return code, set()
    return None, None

def classify_ticker(ticker):
    """Simpele check: is dit een ETF volgens Yahoo Finance?"""
    try:
        info = yf.Ticker(ticker).info
        return info.get("quoteType") == "ETF"
    except Exception:
        return False