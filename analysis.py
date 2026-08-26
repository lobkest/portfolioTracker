import random
import string
import pandas as pd
import yfinance as yf
from yahooquery import search
from db import get_db_connection, save_prices
import time

BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    "TDG": ["GER", "MUN", "FRA"], "LSE": ["LSE"], "XLON": ["LSE"],
    "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
}

def _is_corporate_action_row(row):
    beurs = str(row.get("beurs", "")).strip().upper()
    product = str(row.get("product", "")).upper()
    return beurs == "DEG" or "NON TRADEABLE" in product


def compute_split_adjusted_shares(transacties_df):
    """
    Corrigeert aandelenaantallen voor stock splits, gedetecteerd via DEGIRO's
    NON TRADEABLE/DEG-rijen. Voegt een 'adj_aantal' kolom toe die gebruikt moet
    worden i.p.v. 'aantal' bij alle waarde-berekeningen.
    """
    df = transacties_df.copy()
    df["adj_aantal"] = df["aantal"].astype(float)
    df["koers"] = df["koers"].fillna(0).astype(float)

    for isin, groep in df.groupby("isin"):
        groep = groep.sort_values("datum")
        ca_rows = groep[groep.apply(_is_corporate_action_row, axis=1)]
        if ca_rows.empty:
            continue

        real_trades = groep[~groep.apply(_is_corporate_action_row, axis=1)]
        conversion_rows = real_trades[
            (real_trades["koers"] == 0) & (real_trades["aantal"] > 0)
        ].sort_values("datum")

        for _, conv in conversion_rows.iterrows():
            conv_date = conv["datum"]
            eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
            shares_before = eerdere_trades["aantal"].sum()
            if shares_before <= 0:
                continue

            last_real_date = eerdere_trades["datum"].max()
            new_shares = ca_rows.loc[
                (ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date) & (ca_rows["aantal"] > 0),
                "aantal",
            ].sum()
            if new_shares <= 0:
                continue

            ratio = (shares_before + new_shares) / shares_before
            print(f"[split] {isin}: split gedetecteerd op {conv_date}, ratio {ratio:.4f}x")

            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            df.loc[mask, "adj_aantal"] *= ratio

    return df


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

def download_met_retry(ticker_of_pair, start_date, pogingen=3, wachttijd=5):
    """yf.download met automatische retry bij rate limiting."""
    for poging in range(1, pogingen + 1):
        try:
            return yf.download(ticker_of_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
        except Exception as e:
            print(f"[koersen] poging {poging}/{pogingen} mislukt voor {ticker_of_pair}: {e}")
            if poging < pogingen:
                time.sleep(wachttijd)
            else:
                print(f"[koersen] definitief mislukt voor {ticker_of_pair}, sla over")
                return pd.Series(dtype=float)


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
        # raw = yf.download(missing, start=start_date, auto_adjust=True, progress=False)["Close"]
        raw = download_met_retry(missing, start_date)
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
                # fx = yf.download(fx_pair, start=start_date, auto_adjust=True, progress=False)["Close"].squeeze()
                fx = download_met_retry(fx_pair, start_date).squeeze()
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
                holdings[row["ticker"]] += float(row["adj_aantal"])
            invested += -float(row["totaal_eur"])
            trade_i += 1

        # waarde = sum(holdings[t] * price_data.loc[date, t] for t in tickers)
        waarde = sum(
            holdings[t] * price_data.loc[date, t]
            for t in tickers
            if pd.notna(price_data.loc[date, t])
        )
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
                holdings += float(row["adj_aantal"])
                invested += -float(row["totaal_eur"])
                trade_i += 1
            # waarde = holdings * price_data.loc[date, ticker]
            prijs = price_data.loc[date, ticker]
            waarde = holdings * prijs if pd.notna(prijs) else 0.0
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