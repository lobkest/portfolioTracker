# import random
# import string
# import pandas as pd
# import yfinance as yf
# from yahooquery import search
# from db import get_db_connection, save_prices
# import time

# BEURS_MAP = {
#     "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
#     "TDG": ["GER", "MUN", "FRA"], "LSE": ["LSE"], "XLON": ["LSE"],
#     "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
#     "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
#     "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
# }

# def _is_corporate_action_row(row):
#     beurs = str(row.get("beurs", "")).strip().upper()
#     product = str(row.get("product", "")).upper()
#     return beurs == "DEG" or "NON TRADEABLE" in product


# def compute_split_adjusted_shares(transacties_df):
#     """
#     Corrigeert aandelenaantallen voor stock splits, gedetecteerd via DEGIRO's
#     NON TRADEABLE/DEG-rijen. Voegt een 'adj_aantal' kolom toe die gebruikt moet
#     worden i.p.v. 'aantal' bij alle waarde-berekeningen.
#     """
#     df = transacties_df.copy()
#     df["adj_aantal"] = df["aantal"].astype(float)
#     df["koers"] = df["koers"].fillna(0).astype(float)

#     for isin, groep in df.groupby("isin"):
#         groep = groep.sort_values("datum")
#         ca_rows = groep[groep.apply(_is_corporate_action_row, axis=1)]
#         if ca_rows.empty:
#             continue

#         real_trades = groep[~groep.apply(_is_corporate_action_row, axis=1)]
#         conversion_rows = real_trades[
#             (real_trades["koers"] == 0) & (real_trades["adj_aantal"] > 0)
#         ].sort_values("datum")

#         for _, conv in conversion_rows.iterrows():
#             conv_date = conv["datum"]
#             eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
#             shares_before = float(eerdere_trades["adj_aantal"].sum())
#             if shares_before <= 0:
#                 continue

#             last_real_date = eerdere_trades["datum"].max()
#             new_shares = float(ca_rows.loc[
#                 (ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date) & (ca_rows["adj_aantal"] > 0),
#                 "adj_aantal",
#             ].sum())
#             if new_shares <= 0:
#                 continue

#             ratio = (shares_before + new_shares) / shares_before
#             print(f"[split] {isin}: split gedetecteerd op {conv_date}, ratio {ratio:.4f}x")

#             mask = (
#                 (df["isin"] == isin)
#                 & (df["datum"] < conv_date)
#                 & (~df.apply(_is_corporate_action_row, axis=1))
#             )
#             df.loc[mask, "adj_aantal"] *= ratio

#     return df


# def generate_code(cur, length=3):
#     """Genereert een unieke portfolio-code die nog niet in gebruik is."""
#     chars = string.ascii_uppercase
#     while True:
#         code = "".join(random.choices(chars, k=length))
#         cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
#         if cur.fetchone() is None:
#             return code


# def find_ticker(product, isin, beurs):
#     """Zoekt de Yahoo Finance ticker op basis van productnaam of ISIN."""
#     if beurs == "DEG":  # corporate-action rij, geen echt aandeel/ETF
#         return None
#     targets = BEURS_MAP.get(beurs, [])

#     def best_match(quotes):
#         for exch in targets:
#             for q in quotes:
#                 if q.get("exchange") == exch:
#                     return q.get("symbol")
#         return quotes[0].get("symbol") if quotes else None

#     for query in [product, isin]:
#         try:
#             quotes = search(query).get("quotes", [])
#         except Exception:
#             quotes = []
#         symbol = best_match(quotes)
#         if symbol:
#             return symbol
#     return None

# def download_met_retry(ticker_of_pair, start_date, pogingen=3, wachttijd=5):
#     """yf.download met automatische retry bij rate limiting."""
#     for poging in range(1, pogingen + 1):
#         try:
#             return yf.download(ticker_of_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
#         except Exception as e:
#             print(f"[koersen] poging {poging}/{pogingen} mislukt voor {ticker_of_pair}: {e}")
#             if poging < pogingen:
#                 time.sleep(wachttijd)
#             else:
#                 print(f"[koersen] definitief mislukt voor {ticker_of_pair}, sla over")
#                 return pd.Series(dtype=float)


# def get_prices(tickers, start_date):
#     """Haalt koersen (in EUR) op voor een lijst tickers, met caching via de database."""
#     tickers = [t for t in tickers if t]
#     if not tickers:
#         return pd.DataFrame()

#     conn = get_db_connection()
#     cur = conn.cursor()
#     cur.execute(
#         "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
#         (tickers, start_date),
#     )
#     cached = pd.DataFrame(cur.fetchall(), columns=["ticker", "datum", "koers_eur"])
#     cur.close()
#     conn.close()

#     missing = [t for t in tickers if t not in cached["ticker"].unique()]

#     if missing:
#         # raw = yf.download(missing, start=start_date, auto_adjust=True, progress=False)["Close"]
#         raw = download_met_retry(missing, start_date)
#         if isinstance(raw, pd.Series):
#             raw = raw.to_frame(name=missing[0])
#         raw = raw.ffill()

#         for t in missing:
#             if t not in raw.columns:
#                 continue
#             try:
#                 currency = yf.Ticker(t).info.get("currency")
#             except Exception:
#                 currency = "EUR"
#             if currency in ("USD", "GBP", "GBp"):
#                 fx_pair = "USDEUR=X" if currency == "USD" else "GBPEUR=X"
#                 # fx = yf.download(fx_pair, start=start_date, auto_adjust=True, progress=False)["Close"]
#                 # fx = yf.download(fx_pair, start=start_date, auto_adjust=True, progress=False)["Close"].squeeze()
#                 fx = download_met_retry(fx_pair, start_date).squeeze()
#                 fx = fx.reindex(raw.index).ffill()
#                 divisor = 100 if currency == "GBp" else 1
#                 raw[t] = raw[t] / divisor * fx

#         fresh_rows = []
#         for t in missing:
#             if t not in raw.columns:
#                 continue
#             for datum, koers in raw[t].dropna().items():
#                 fresh_rows.append((t, datum.date(), float(koers)))
#         save_prices(fresh_rows)

#         fresh_df = pd.DataFrame(fresh_rows, columns=["ticker", "datum", "koers_eur"])
#         cached = pd.concat([cached, fresh_df], ignore_index=True)

#     if cached.empty:
#         return pd.DataFrame()

#     cached["datum"] = pd.to_datetime(cached["datum"])
#     cached["koers_eur"] = cached["koers_eur"].astype(float)
#     pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

#     for t in tickers:
#         if t not in pivot.columns:
#             print(f"[koersen] GEEN data gevonden voor ticker {t}")
#             continue
#         eerste_geldige = pivot[t].first_valid_index()
#         print(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")

#     return pivot

#     # if cached.empty:
#     #     return pd.DataFrame()

#     # cached["datum"] = pd.to_datetime(cached["datum"])
#     # cached["koers_eur"] = cached["koers_eur"].astype(float)

#     # for t in tickers:
#     #     if t not in cached.columns:
#     #         print(f"[koersen] GEEN data gevonden voor ticker {t}")
#     #         continue
#     #     eerste_geldige = cached[t].first_valid_index()
#     #     print(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")
    
#     # return cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()


# def compute_value_over_time(transacties_df, price_data):
#     """Berekent per dag: portfoliowaarde, totaal geïnvesteerd en rendement."""
#     transacties_df = transacties_df.dropna(subset=["ticker"]).sort_values("datum").reset_index(drop=True)
#     tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

#     holdings = {t: 0.0 for t in tickers}
#     invested = 0.0
#     rows = []
#     trade_i = 0

#     for date in price_data.index:
#         while trade_i < len(transacties_df) and pd.Timestamp(transacties_df.loc[trade_i, "datum"]) <= date:
#             row = transacties_df.loc[trade_i]
#             if row["ticker"] in holdings:
#                 holdings[row["ticker"]] += float(row["adj_aantal"])
#             invested += -float(row["totaal_eur"])
#             trade_i += 1

#         # waarde = sum(holdings[t] * price_data.loc[date, t] for t in tickers)
#         waarde = sum(
#             holdings[t] * price_data.loc[date, t]
#             for t in tickers
#             if pd.notna(price_data.loc[date, t])
#         )
#         rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

#     result = pd.DataFrame(rows).set_index("datum")
#     result["rendement"] = result["waarde"] - result["geinvesteerd"]
#     return result

# def compute_per_ticker(transacties_df, price_data):
#     """Per ticker: waarde en geïnvesteerd bedrag over tijd."""
#     transacties_df = transacties_df.dropna(subset=["ticker"]).sort_values("datum").reset_index(drop=True)
#     tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

#     result = {}
#     for ticker in tickers:
#         trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
#         holdings = 0.0
#         invested = 0.0
#         trade_i = 0
#         rows = []

#         for date in price_data.index:
#             while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
#                 row = trades.loc[trade_i]
#                 holdings += float(row["adj_aantal"])
#                 invested += -float(row["totaal_eur"])
#                 trade_i += 1
#             # waarde = holdings * price_data.loc[date, ticker]
#             prijs = price_data.loc[date, ticker]
#             waarde = holdings * prijs if pd.notna(prijs) else 0.0
#             rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

#         df_t = pd.DataFrame(rows).set_index("datum")

#         nonzero_idx = df_t.index[df_t["geinvesteerd"] > 0]
#         if len(nonzero_idx) > 0:
#             df_t = df_t.loc[nonzero_idx[0]:nonzero_idx[-1]]
#         else:
#             df_t = df_t.iloc[0:0]

#         result[ticker] = {
#             "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
#             "waarde": df_t["waarde"].round(2).tolist(),
#             "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
#         }
#     return result

# def get_order_id_sets(cur):
#     """Geeft per portfolio-code de set van al opgeslagen Order ID's terug."""
#     cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
#     sets = {}
#     for code, order_id in cur.fetchall():
#         sets.setdefault(code, set()).add(order_id)
#     return sets


# def find_matching_code(cur, new_order_ids):
#     """
#     Zoekt een bestaande portfolio die dezelfde persoon vertegenwoordigt:
#     - bestaande data zit volledig in de nieuwe upload (update met extra transacties), of
#     - de nieuwe upload zit volledig in de bestaande data (niets nieuws)
#     Geeft (code, ontbrekende_order_ids) terug, of (None, None) als er geen match is.
#     """
#     existing = get_order_id_sets(cur)
#     for code, ids in existing.items():
#         if ids <= new_order_ids:
#             return code, new_order_ids - ids
#         if new_order_ids <= ids:
#             return code, set()
#     return None, None

# def classify_ticker(ticker):
#     """Simpele check: is dit een ETF volgens Yahoo Finance?"""
#     try:
#         info = yf.Ticker(ticker).info
#         return info.get("quoteType") == "ETF"
#     except Exception:
#         return False

import random
import string
import pandas as pd
import yfinance as yf
from yahooquery import search
from db import get_db_connection, save_prices
import time

# Zet op True om overal in dit bestand debug-prints aan te zetten.
DEBUG = True

def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


BEURS_MAP = {
    "EAM": ["AMS"], "XAMS": ["AMS"], "XET": ["GER"], "FRA": ["GER"],
    "TDG": ["GER", "MUN", "FRA"], "LSE": ["LSE"], "XLON": ["LSE"],
    "NYSE": ["NYQ"], "NASDAQ": ["NMS"], "ARCA": ["PCX"], "EPA": ["PAR"],
    "EBR": ["BRU"], "BME": ["MCE"], "BIT": ["MIL"], "SWX": ["SWX"],
    "TSE": ["TOR"], "ASX": ["ASX"], "NDQ": ["NMS"],
}

# Handmatige overrides voor fondsen die yahooquery.search() niet (goed) vindt.
# Overgenomen uit class_degiro.py — vul aan als je nog meer van dit soort
# gevallen tegenkomt (print hieronder waarschuwt je als find_ticker() een
# "blinde" quotes[0]-fallback moet gebruiken, dat is meestal het signaal om
# hier iets aan toe te voegen).
MANUAL_TICKER_OVERRIDES = {
    "VANGUARD S&P 500 UCITS": "VUSA.AS",
    "VANGUARD FTSE ALL-WORLD UCITS": "VWRL.AS",
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

        product_naam = groep["product"].iloc[0] if "product" in groep.columns else "?"
        dprint(f"\n[split-detect] ISIN={isin} ('{product_naam}') heeft {len(ca_rows)} "
               f"corporate-action rij(en), onderzoeken...")
        dprint(f"[split-detect]   alle rijen voor deze ISIN:")
        for _, r in groep.iterrows():
            dprint(f"    {r['datum']} | beurs={r.get('beurs')} | product={str(r.get('product'))[:50]} "
                   f"| aantal={r.get('aantal')} | koers={r.get('koers')} | totaal_eur={r.get('totaal_eur')}")

        real_trades = groep[~groep.apply(_is_corporate_action_row, axis=1)]
        conversion_rows = real_trades[
            (real_trades["koers"] == 0) & (real_trades["adj_aantal"] > 0)
        ].sort_values("datum")

        if conversion_rows.empty:
            dprint(f"[split-detect]   ⚠️ GEEN conversion-rij gevonden (real trade met koers=0 en "
                   f"aantal>0) ondanks {len(ca_rows)} corporate-action rij(en) — deze split wordt "
                   f"NIET verwerkt! Aandelenaantal/rendement voor '{product_naam}' klopt dan niet "
                   f"vanaf hier.")

        for _, conv in conversion_rows.iterrows():
            conv_date = conv["datum"]
            eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
            shares_before = float(eerdere_trades["adj_aantal"].sum())
            if shares_before <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: shares_before={shares_before} "
                       f"(<=0) — overgeslagen, kan geen ratio berekenen")
                continue

            last_real_date = eerdere_trades["datum"].max()
            new_shares = float(ca_rows.loc[
                (ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date) & (ca_rows["adj_aantal"] > 0),
                "adj_aantal",
            ].sum())
            if new_shares <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: new_shares={new_shares} (<=0) "
                       f"— overgeslagen")
                continue

            ratio = (shares_before + new_shares) / shares_before
            print(f"[split] {isin} ('{product_naam}'): split gedetecteerd op {conv_date}, "
                  f"{shares_before:.4f} -> {shares_before + new_shares:.4f} (ratio {ratio:.4f}x)")

            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            dprint(f"[split-detect]   pas ratio {ratio:.4f}x toe op {mask.sum()} eerdere rij(en)")
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

    # Check handmatige overrides eerst (product-naam mag met iets anders eindigen)
    for key, override_ticker in MANUAL_TICKER_OVERRIDES.items():
        if product.upper().startswith(key):
            dprint(f"[ticker] '{product}' -> override '{override_ticker}'")
            return override_ticker

    targets = BEURS_MAP.get(beurs, [])

    def best_match(quotes, query):
        for exch in targets:
            for q in quotes:
                if q.get("exchange") == exch:
                    dprint(f"[ticker]   query='{query}': exact beurs-match "
                           f"{q.get('symbol')} ({exch})")
                    return q.get("symbol")
        if quotes:
            dprint(f"[ticker]   ⚠️ query='{query}': GEEN match voor beurs '{beurs}' "
                   f"(verwacht {targets}), val terug op eerste resultaat "
                   f"{quotes[0].get('symbol')} ({quotes[0].get('exchange')}) — mogelijk fout! "
                   f"Alle kandidaten: "
                   f"{[(q.get('symbol'), q.get('exchange')) for q in quotes]}")
            return quotes[0].get("symbol")
        return None

    for query in [product, isin]:
        try:
            quotes = search(query).get("quotes", [])
        except Exception as e:
            dprint(f"[ticker]   query='{query}' faalde: {e}")
            quotes = []
        symbol = best_match(quotes, query)
        if symbol:
            return symbol

    print(f"[ticker] ❌ GEEN ticker gevonden voor '{product}' (ISIN={isin}, beurs={beurs})")
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

    start_date = pd.Timestamp(start_date)

    conn = get_db_connection()
    cur = conn.cursor()

    # Vroegste gecachte datum per ticker (ongefilterd op start_date!) — nodig
    # om te kunnen zien of de cache al ver genoeg teruggaat, in plaats van
    # alleen te checken of de ticker uberhaupt in de cache voorkomt. Zonder
    # deze check bleef een ticker met een eerdere, onvolledige download
    # (bv. door rate limiting) voor altijd "incompleet" gecachet, met
    # waarde=0 voor alle datums vóór de eerst gecachte datum als gevolg.
    cur.execute(
        "SELECT ticker, MIN(datum) FROM prijzen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    eerste_datum_cache = {row[0]: pd.Timestamp(row[1]) for row in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_date.date()),
    )
    cached = pd.DataFrame(cur.fetchall(), columns=["ticker", "datum", "koers_eur"])
    cur.close()
    conn.close()

    missing = []
    for t in tickers:
        if t not in eerste_datum_cache:
            missing.append(t)
            dprint(f"[koersen] '{t}' nog niet in cache, wordt gedownload")
            continue
        eerste = eerste_datum_cache[t]
        # kleine marge voor weekenden/feestdagen rond de gevraagde startdatum
        if eerste > start_date + pd.Timedelta(days=5):
            missing.append(t)
            print(f"[koersen] ⚠️ '{t}' zit in cache maar pas vanaf {eerste.date()}, terwijl "
                  f"vanaf {start_date.date()} nodig is — cache lijkt incompleet (eerdere "
                  f"download waarschijnlijk mislukt/afgebroken), wordt opnieuw volledig "
                  f"gedownload")

    if missing:
        raw = download_met_retry(missing, start_date)
        if isinstance(raw, pd.Series):
            raw = raw.to_frame(name=missing[0])
        raw = raw.ffill()

        for t in missing:
            if t not in raw.columns:
                print(f"[koersen] ⚠️ '{t}' zit niet in yfinance-download resultaat "
                      f"(mogelijk ongeldige/onbekende ticker)")
                continue
            eerste_ruw = raw[t].first_valid_index()
            dprint(f"[koersen] '{t}': ruwe (niet-EUR-gecorrigeerde) data vanaf {eerste_ruw}, "
                   f"gevraagd vanaf {start_date}")
            try:
                currency = yf.Ticker(t).info.get("currency")
            except Exception:
                currency = "EUR"
            if currency in ("USD", "GBP", "GBp"):
                fx_pair = "USDEUR=X" if currency == "USD" else "GBPEUR=X"
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
        # fresh_df kan datums bevatten die al in 'cached' zaten (opnieuw
        # gedownload voor tickers die deels al gecachet waren) — bij overlap
        # de verse waarde houden, en concat kan anders duplicate
        # (ticker, datum) combinaties opleveren waar pivot() straks op stukloopt.
        cached = pd.concat([cached, fresh_df], ignore_index=True)
        cached = cached.drop_duplicates(subset=["ticker", "datum"], keep="last")

    if cached.empty:
        return pd.DataFrame()

    cached["datum"] = pd.to_datetime(cached["datum"])
    cached["koers_eur"] = cached["koers_eur"].astype(float)
    pivot = cached.pivot(index="datum", columns="ticker", values="koers_eur").sort_index().ffill()

    for t in tickers:
        if t not in pivot.columns:
            print(f"[koersen] ❌ GEEN data gevonden voor ticker {t} (helemaal niet in pivot)")
            continue
        eerste_geldige = pivot[t].first_valid_index()
        dprint(f"[koersen] {t}: eerste geldige koers op {eerste_geldige}, gevraagd vanaf {start_date}")
        if eerste_geldige is not None and pd.Timestamp(eerste_geldige) > pd.Timestamp(start_date) + pd.Timedelta(days=10):
            print(f"[koersen] ⚠️ {t}: eerste geldige koers ({eerste_geldige}) ligt >10 dagen na "
                  f"gevraagde startdatum ({start_date}) — 'waarde' voor deze ticker zal 0 zijn vóór "
                  f"die datum, terwijl 'geïnvesteerd' wel al kan oplopen. Vaak een teken van een "
                  f"verkeerde/onvolledige ticker.")

    return pivot


def compute_value_over_time(transacties_df, price_data):
    """Berekent per dag: portfoliowaarde, totaal geïnvesteerd en rendement."""
    transacties_df = transacties_df.dropna(subset=["ticker"]).sort_values("datum").reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    ontbrekend = [t for t in transacties_df["ticker"].unique() if t not in price_data.columns]
    if ontbrekend:
        print(f"[waarde] ⚠️ tickers zonder koersdata, worden genegeerd in totale waarde: {ontbrekend}")

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
        prev_waarde = None
        prev_invested = None

        for date in price_data.index:
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])
                invested += -float(row["totaal_eur"])
                trade_i += 1
            prijs = price_data.loc[date, ticker]
            waarde = holdings * prijs if pd.notna(prijs) else 0.0

            # Spike-detector: grote sprong in waarde of geinvesteerd op 1 dag zonder
            # duidelijke oorzaak (helpt ISIN-migraties / verkeerde splits opsporen)
            if prev_waarde is not None and prev_invested not in (None, 0):
                if abs(invested - prev_invested) > 0.5 * abs(prev_invested) + 50:
                    dprint(f"[per-ticker:{ticker}] grote sprong in geïnvesteerd op {date.date()}: "
                           f"{prev_invested:.2f} -> {invested:.2f}")
                if pd.notna(prijs) and prev_waarde > 0 and abs(waarde - prev_waarde) > 0.5 * prev_waarde + 50 \
                        and holdings != 0:
                    dprint(f"[per-ticker:{ticker}] grote sprong in waarde op {date.date()}: "
                           f"{prev_waarde:.2f} -> {waarde:.2f} (holdings={holdings:.4f}, prijs={prijs})")

            rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})
            prev_waarde = waarde
            prev_invested = invested

        df_t = pd.DataFrame(rows).set_index("datum")

        nonzero_idx = df_t.index[df_t["geinvesteerd"] > 0]
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = all_dates.index(nonzero_idx[0])
            end_pos = all_dates.index(nonzero_idx[-1])

            # 1 dag ervoor erbij, zodat de sprong vanaf 0 zichtbaar is
            start_pos = max(0, start_pos - 1)

            # 1 dag erna erbij, maar alleen als de laatste investeringsdag niet
            # de laatste (= meest recente/vandaag) datum in de dataset is —
            # anders wordt er niets zinnigs toegevoegd, je bezit het nog gewoon.
            is_still_held = nonzero_idx[-1] == all_dates[-1]
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)

            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            "waarde": df_t["waarde"].round(2).tolist(),
            "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
        }
    return result


def debug_position(transacties_df, price_data, ticker=None, product_contains=None):
    """
    Handmatige diagnose-helper voor 1 positie. Roep aan met bv.:
        debug_position(transacties_df, price_data, product_contains="S&P 500")
    of
        debug_position(transacties_df, price_data, ticker="VUSA.AS")

    Print: alle ruwe transactierijen, split-adjustment resultaat, en of/vanaf
    wanneer er koersdata is.
    """
    print("\n" + "=" * 70)
    print("DEBUG POSITION")
    print("=" * 70)

    df = transacties_df.copy()
    if product_contains:
        mask = df["product"].astype(str).str.upper().str.contains(product_contains.upper())
        df = df[mask]
    if ticker:
        df = df[df["ticker"] == ticker]

    if df.empty:
        print("Geen transacties gevonden voor dit filter.")
        return

    print(f"\n{len(df)} transactie(s) gevonden. Unieke ISIN's: {df['isin'].unique().tolist()}")
    print(f"Unieke tickers: {df['ticker'].unique().tolist() if 'ticker' in df.columns else '(nog niet toegekend)'}")
    print(f"Unieke product-namen: {df['product'].unique().tolist()}")

    print("\nAlle rijen (gesorteerd op datum):")
    cols_to_show = [c for c in ["datum", "isin", "product", "beurs", "ticker", "aantal",
                                 "adj_aantal", "koers", "totaal_eur"] if c in df.columns]
    for _, r in df.sort_values("datum").iterrows():
        print("  " + " | ".join(f"{c}={r[c]}" for c in cols_to_show))

    if ticker and ticker in price_data.columns:
        serie = price_data[ticker]
        eerste = serie.first_valid_index()
        laatste = serie.last_valid_index()
        n_nan = serie.isna().sum()
        print(f"\nKoersdata voor '{ticker}': eerste geldige waarde op {eerste}, laatste op {laatste}, "
              f"{n_nan} NaN-waarden van de {len(serie)} dagen in price_data.")
    elif ticker:
        print(f"\n⚠️ '{ticker}' zit niet (of nog niet) in price_data.columns: "
              f"{list(price_data.columns)[:20]}...")

    print("=" * 70 + "\n")


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
    """
    Is dit een ETF volgens Yahoo Finance? quoteType is soms leeg/onbetrouwbaar
    voor UCITS-ETF's, dus val terug op een heuristiek (zoals class_degiro.py deed)
    in plaats van blind False terug te geven.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        print(f"[classify] ❌ kon info niet ophalen voor '{ticker}': {e}")
        return False

    quote_type = info.get("quoteType", "")
    if quote_type:
        is_etf = quote_type == "ETF"
        dprint(f"[classify] '{ticker}': quoteType='{quote_type}' -> ETF={is_etf}")
        return is_etf

    # quoteType onbekend/leeg -> heuristiek
    country = info.get("country")
    sector = info.get("sector")
    total_assets = info.get("totalAssets")
    fund_family = info.get("fundFamily")
    category = info.get("category")

    signals = [
        country is None,
        sector is None,
        total_assets is not None,
        fund_family is not None,
        category is not None,
    ]
    guess = sum(signals) >= 2
    print(f"[classify] ⚠️ quoteType onbekend voor '{ticker}', gok ETF={guess} "
          f"(country={country}, sector={sector}, totalAssets={total_assets}, "
          f"fundFamily={fund_family}, category={category})")
    return guess