"""Split-correctie en de per-dag- en per-ticker-tijdreeksen (Home, Per aandeel, Per aandeel aankoop)."""
import pandas as pd

from debug_utils import dprint
from diagnostiek import meld, CATEGORIE_SPLITS, INFO, LET_OP
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch


def compute_split_adjusted_shares(transacties_df):
    """Voegt 'adj_aantal' toe (split-gecorrigeerd); zie CLAUDE.md: Data en rekenen."""
    df = transacties_df.copy()
    df["adj_aantal"] = df["aantal"].astype(float)
    df["koers"] = df["koers"].fillna(0).astype(float)

    for isin, groep in df.groupby("isin"):
        groep = groep.sort_values("datum")
        ca_rows = groep[groep.apply(_is_corporate_action_row, axis=1)]
        if ca_rows.empty:
            continue

        product_naam = groep["product"].iloc[0] if "product" in groep.columns else "?"
        # Alleen voor de Diagnostiek-melding.
        factor_bepaald = False
        reden_geen_factor = "geen conversierij gevonden"
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

        # Geen conversierij: het aantal klopt vanaf hier niet meer.
        for _, conv in conversion_rows.iterrows():
            conv_date = conv["datum"]
            eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
            shares_before = float(eerdere_trades["adj_aantal"].sum())
            if shares_before <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: shares_before={shares_before} "
                       f"(<=0) - overgeslagen, kan geen ratio berekenen")
                reden_geen_factor = "geen aandelen vóór de conversie"
                continue

            last_real_date = eerdere_trades["datum"].max()
            new_shares = float(ca_rows.loc[
                (ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date) & (ca_rows["adj_aantal"] > 0),
                "adj_aantal",
            ].sum())
            if new_shares <= 0:
                dprint(f"[split-detect]   conversie op {conv_date}: new_shares={new_shares} (<=0) "
                       f"- overgeslagen")
                reden_geen_factor = "geen nieuwe aandelen in de corporate-action-rijen"
                continue

            ratio = (shares_before + new_shares) / shares_before

            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            dprint(f"[split-detect]   pas ratio {ratio:.4f}x toe op {mask.sum()} eerdere rij(en)")
            df.loc[mask, "adj_aantal"] *= ratio
            factor_bepaald = True
            conv_datum_tekst = pd.Timestamp(conv_date).strftime("%Y-%m-%d")
            meld(CATEGORIE_SPLITS, INFO,
                 f"Split voor {product_naam} ({isin}) op {conv_datum_tekst}: factor {ratio:.4f}.",
                 sleutel=f"split:{isin}:{conv_datum_tekst}")

        if not factor_bepaald:
            meld(CATEGORIE_SPLITS, LET_OP,
                 f"{product_naam} ({isin}) heeft corporate-action-rijen, maar er is geen splitfactor bepaald "
                 f"({reden_geen_factor}); het aantal aandelen kan vanaf dan afwijken. Bij een corporate action "
                 f"die geen split is (bv. een ISIN-wissel) kan dit terecht zijn.",
                 sleutel=f"split_onbekend:{isin}")

    return df


def compute_value_over_time(transacties_df, price_data):
    """'geinvesteerd' is hier de netto cashflow (incl. kosten), niet de GAK-kostenbasis."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    if not price_data.empty:
        laatste_koersdatum = price_data.index.max()
        na_laatste_koers = transacties_df[pd.to_datetime(transacties_df["datum"]) > laatste_koersdatum]
        if not na_laatste_koers.empty:
            print(f"[waarde] WARN {len(na_laatste_koers)} transactie(s) met datum na de laatste "
                  f"beschikbare koersdatum ({laatste_koersdatum.date()}) - deze tellen NIET mee "
                  f"in de waarde-tijdreeks (price_data.index loopt niet ver genoeg door). "
                  f"Mogelijk is de koersencache verouderd.")

    holdings = {t: 0.0 for t in tickers}
    invested = 0.0
    rows = []
    trade_i = 0

    # Arrays i.p.v. price_data.loc per iteratie (snelheid).
    prijs_per_ticker = {t: price_data[t].to_numpy() for t in tickers}

    for i, date in enumerate(price_data.index):
        while trade_i < len(transacties_df) and pd.Timestamp(transacties_df.loc[trade_i, "datum"]) <= date:
            row = transacties_df.loc[trade_i]
            if row["ticker"] in holdings:
                holdings[row["ticker"]] += float(row["adj_aantal"])
            invested += -float(row["totaal_eur"])
            trade_i += 1

        waarde = sum(
            holdings[t] * prijs_per_ticker[t][i]
            for t in tickers
            if pd.notna(prijs_per_ticker[t][i])
        )
        rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

    result = pd.DataFrame(rows).set_index("datum")
    result["rendement"] = result["waarde"] - result["geinvesteerd"]
    return result


def compute_per_ticker(transacties_df, price_data):
    """'geinvesteerd' is hier de GAK-kostenbasis van de aangehouden stukken."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        aantal_lopend = 0.0       # raw aantal, los van adj_aantal — voor kostenbasis (GAK)
        kostprijs_lopend = 0.0    # kostenbasis van de NU aangehouden stukken
        trade_i = 0
        rows = []
        prev_waarde = None
        prev_invested = None

        prijzen_array = price_data[ticker].to_numpy()

        for i, date in enumerate(price_data.index):
            activiteit = False
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])

                # GAK-methode, gelijk houden met bereken_holdings_en_gesloten() (zie CLAUDE.md: Data en rekenen).
                delta_aantal = float(row["aantal"])
                # totaal_eur alleen voor de cashflow-check (splitrijen = 0) en de verkoopkant.
                delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
                if delta_aantal > 0:
                    waarde_bron = row["waarde_eur"] if pd.notna(row.get("waarde_eur")) else row["totaal_eur"]
                    delta_cash_aankoop = -float(waarde_bron)
                    aantal_lopend += delta_aantal
                    kostprijs_lopend += delta_cash_aankoop
                elif delta_aantal < 0:
                    if delta_cash != 0 and aantal_lopend > 0:
                        gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                        verkocht_nu = min(-delta_aantal, aantal_lopend)
                        kostprijs_lopend -= gak_op_dat_moment * verkocht_nu
                    aantal_lopend += delta_aantal

                trade_i += 1
                activiteit = True
            prijs = prijzen_array[i]
            waarde = holdings * prijs if pd.notna(prijs) else 0.0
            invested = max(kostprijs_lopend, 0.0)  # epsilon-afronding kan net onder 0 uitkomen

            # Grote sprong op 1 dag: helpt ISIN-migraties en verkeerde splits opsporen.
            if prev_waarde is not None and prev_invested not in (None, 0):
                if abs(invested - prev_invested) > 0.5 * abs(prev_invested) + 50:
                    dprint(f"[per-ticker:{ticker}] grote sprong in geinvesteerd op {date.date()}: "
                           f"{prev_invested:.2f} -> {invested:.2f}")
                if pd.notna(prijs) and prev_waarde > 0 and abs(waarde - prev_waarde) > 0.5 * prev_waarde + 50 \
                        and holdings != 0:
                    dprint(f"[per-ticker:{ticker}] grote sprong in waarde op {date.date()}: "
                           f"{prev_waarde:.2f} -> {waarde:.2f} (holdings={holdings:.4f}, prijs={prijs})")

            rows.append({
                "datum": date, "waarde": waarde, "geinvesteerd": invested,
                "holdings": holdings, "activiteit": activiteit,
            })
            prev_waarde = waarde
            prev_invested = invested

        df_t = pd.DataFrame(rows).set_index("datum")

        # "Nog in bezit" op aantal stuks (zie CLAUDE.md: Data en rekenen). "activiteit"
        # vangt een koop + volledige verkoop op dezelfde dag.
        nonzero_idx = df_t.index[(df_t["holdings"].abs() > 1e-6) | df_t["activiteit"]]
        is_still_held = abs(df_t["holdings"].iloc[-1]) > 1e-6 if len(df_t) else False
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = all_dates.index(nonzero_idx[0])
            end_pos = all_dates.index(nonzero_idx[-1])

            # 1 dag ervoor erbij, zodat de sprong vanaf 0 zichtbaar is
            start_pos = max(0, start_pos - 1)

            # 1 dag erna erbij, alleen als de positie verkocht is
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)

            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            "waarde": df_t["waarde"].round(2).tolist(),
            "geinvesteerd": df_t["geinvesteerd"].round(2).tolist(),
            "nog_in_bezit": bool(is_still_held),
        }
    return result


def compute_per_ticker_koers_en_aankopen(transacties_df, price_data):
    """Kale koers, aantal aangehouden en aparte aankoop-/verkoopdatums per ticker; zelfde crop als compute_per_ticker()."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        trade_i = 0
        rows = []

        prijzen_array = price_data[ticker].to_numpy()

        for i, date in enumerate(price_data.index):
            activiteit = False
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])
                trade_i += 1
                activiteit = True
            prijs = prijzen_array[i]
            rows.append({
                "datum": date,
                "koers": float(prijs) if pd.notna(prijs) else None,
                "holdings": holdings,
                "activiteit": activiteit,
            })

        df_t = pd.DataFrame(rows).set_index("datum")

        # Zelfde crop-logica als compute_per_ticker(); samen wijzigen.
        nonzero_idx = df_t.index[(df_t["holdings"].abs() > 1e-6) | df_t["activiteit"]]
        is_still_held = abs(df_t["holdings"].iloc[-1]) > 1e-6 if len(df_t) else False
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = max(0, all_dates.index(nonzero_idx[0]) - 1)
            end_pos = all_dates.index(nonzero_idx[-1])
            if not is_still_held:
                end_pos = min(len(all_dates) - 1, end_pos + 1)
            df_t = df_t.iloc[start_pos:end_pos + 1]
        else:
            df_t = df_t.iloc[0:0]

        aankopen = trades[trades["adj_aantal"] > 0]
        verkopen = trades[trades["adj_aantal"] < 0]
        if len(df_t) > 0:
            aankoop_datums_dt = pd.to_datetime(aankopen["datum"])
            aankopen = aankopen[(aankoop_datums_dt >= df_t.index[0]) & (aankoop_datums_dt <= df_t.index[-1])]
            verkoop_datums_dt = pd.to_datetime(verkopen["datum"])
            verkopen = verkopen[(verkoop_datums_dt >= df_t.index[0]) & (verkoop_datums_dt <= df_t.index[-1])]

        result[ticker] = {
            "labels": [d.strftime("%Y-%m-%d") for d in df_t.index],
            # pd.notna, niet "is not None": None wordt NaN in een float-kolom (breekt JSON).
            "koers": [round(k, 4) if pd.notna(k) else None for k in df_t["koers"]],
            "holdings": df_t["holdings"].round(6).tolist(),
            "nog_in_bezit": bool(is_still_held),
            "aankoop_datums": sorted(
                d.strftime("%Y-%m-%d")
                for d in pd.to_datetime(aankopen["datum"]).dt.normalize().unique()
            ),
            "verkoop_datums": sorted(
                d.strftime("%Y-%m-%d")
                for d in pd.to_datetime(verkopen["datum"]).dt.normalize().unique()
            ),
        }
    return result


def holdings_op_datums(trades_df, datums):
    """Cumulatieve adj_aantal van één ticker op elke datum in `datums`."""
    if len(datums) == 0:
        return []
    if trades_df.empty:
        return [0.0] * len(datums)

    aantallen = pd.to_numeric(trades_df["adj_aantal"].map(
        lambda a: float(a) if pd.notna(a) else 0.0
    ))
    per_dag = (
        pd.Series(aantallen.to_numpy(), index=pd.to_datetime(trades_df["datum"]).to_numpy())
        .groupby(level=0).sum()
        .sort_index()
        .cumsum()
    )
    # Laatste stand op of vóór elke datum; datums vóór de eerste trade -> 0.
    stand = per_dag.reindex(pd.DatetimeIndex(datums), method="ffill").fillna(0.0)
    # + 0.0 maakt van -0.0 (na afronden van float-ruis) een gewone 0.0.
    return [round(float(h), 6) + 0.0 for h in stand]

