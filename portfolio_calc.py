"""
Kern-tijdreeks-/holdings-berekeningen op transacties_df + price_data:
split-correctie en de per-dag/per-ticker waarde-/geïnvesteerd-tijdreeksen
achter Home, Per aandeel en Per aandeel aankoop.

Losgetrokken uit analysis.py; ongewijzigd overgenomen.
"""
import pandas as pd

from debug_utils import dprint
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch


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
            # Bewust een normale print (niet dprint): dit signaleert een
            # STILLE fout in de rendementsberekening (aandelenaantal klopt
            # vanaf hier niet meer) en hoort daarom net zo zichtbaar te zijn
            # als de andere ⚠️-waarschuwingen elders in het project, i.p.v.
            # alleen zichtbaar met debug-logging aan.
            pass
            # print(f"[split-detect]   ⚠️ GEEN conversion-rij gevonden (real trade met koers=0 en "
                  # f"aantal>0) ondanks {len(ca_rows)} corporate-action rij(en) voor ISIN={isin} "
                  # f"('{product_naam}') — deze split wordt NIET verwerkt! Aandelenaantal/rendement "
                  # f"voor '{product_naam}' klopt dan niet vanaf hier.")

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
            # print(f"[split] {isin} ('{product_naam}'): split gedetecteerd op {conv_date}, "
                  # f"{shares_before:.4f} -> {shares_before + new_shares:.4f} (ratio {ratio:.4f}x)")

            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            dprint(f"[split-detect]   pas ratio {ratio:.4f}x toe op {mask.sum()} eerdere rij(en)")
            df.loc[mask, "adj_aantal"] *= ratio

    return df


def compute_value_over_time(transacties_df, price_data):
    """Berekent per dag: portfoliowaarde, totaal geïnvesteerd en rendement."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    ontbrekend = [t for t in transacties_df["ticker"].unique() if t not in price_data.columns]
    if ontbrekend:
        pass
        # print(f"[waarde] ⚠️ tickers zonder koersdata, worden genegeerd in totale waarde: {ontbrekend}")

    if not price_data.empty:
        laatste_koersdatum = price_data.index.max()
        na_laatste_koers = transacties_df[pd.to_datetime(transacties_df["datum"]) > laatste_koersdatum]
        if not na_laatste_koers.empty:
            print(f"[waarde] ⚠️ {len(na_laatste_koers)} transactie(s) met datum ná de laatste "
                  f"beschikbare koersdatum ({laatste_koersdatum.date()}) — deze tellen NIET mee "
                  f"in de waarde-tijdreeks (price_data.index loopt niet ver genoeg door). "
                  f"Mogelijk is de koersencache verouderd.")

    holdings = {t: 0.0 for t in tickers}
    invested = 0.0
    rows = []
    trade_i = 0

    # Snelle dict/array-toegang i.p.v. price_data.loc[date, t] per iteratie
    # (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde loop-structuur en
    # -volgorde, alleen de koerslookup is nu een goedkope array-index.
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
    """Per ticker: waarde en geïnvesteerd bedrag over tijd."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    if not price_data.empty:
        laatste_koersdatum = price_data.index.max()
        na_laatste_koers = transacties_df[pd.to_datetime(transacties_df["datum"]) > laatste_koersdatum]
        if not na_laatste_koers.empty:
            pass
            # print(f"[per-ticker] ⚠️ {len(na_laatste_koers)} transactie(s) met datum ná de laatste "
                  # f"beschikbare koersdatum ({laatste_koersdatum.date()}) — deze tellen NIET mee "
                  # f"in de per-ticker-tijdreeks. Mogelijk is de koersencache verouderd.")

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

        # Snelle array-toegang i.p.v. price_data.loc[date, ticker] per
        # iteratie (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde
        # loop-structuur en -volgorde, alleen de koerslookup is nu een
        # goedkope array-index.
        prijzen_array = price_data[ticker].to_numpy()

        for i, date in enumerate(price_data.index):
            activiteit = False
            while trade_i < len(trades) and pd.Timestamp(trades.loc[trade_i, "datum"]) <= date:
                row = trades.loc[trade_i]
                holdings += float(row["adj_aantal"])

                # Zelfde lopende-gemiddelde-kostprijs-methode (GAK) als
                # bereken_holdings_en_gesloten(): bij een verkoop gaat alleen
                # de kostenbasis van de VERKOCHTE stukken eraf (evenredig aan
                # het gemiddelde op dat moment), niet de volledige
                # verkoopopbrengst. Zo daalt "geïnvesteerd" bij een
                # gedeeltelijke verkoop evenredig mee met het aantal
                # resterende stukken i.p.v. met de volledige cashflow.
                delta_aantal = float(row["aantal"])
                # totaal_eur incl. AutoFX/transactiekosten; alleen gebruikt
                # voor de cashflow-check (corporate-action-rijen hebben
                # totaal_eur=0) en de verkoopkant, die bewust ongewijzigd
                # blijft — zie CLAUDE.md, "GAK gebruikt verkeerde kolom".
                delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
                if delta_aantal > 0:
                    # Kostenbasis o.b.v. de kale Waarde EUR (aantal x koers,
                    # zonder kosten), niet totaal_eur — DEGIRO's eigen GAK
                    # gebruikt ook de kale waarde. Valt terug op totaal_eur (incl.
                    # AutoFX/kosten, GAK dus iets te hoog) als waarde_eur NULL is: DEGIRO
                    # leverde geen Waarde EUR of de Excel mist die kolom (wordt niet later aangevuld).
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

            rows.append({
                "datum": date, "waarde": waarde, "geinvesteerd": invested,
                "holdings": holdings, "activiteit": activiteit,
            })
            prev_waarde = waarde
            prev_invested = invested

        df_t = pd.DataFrame(rows).set_index("datum")

        # LET OP: "nog in bezit" moet op het AANDELENAANTAL bepaald worden,
        # niet op "geinvesteerd" — dat laatste is een cumulatieve netto
        # cashflow (aankopen min verkopen) die na een volledige verkoop
        # permanent > 0 blijft staan zodra er ooit winst/verlies is gemaakt
        # (het gerealiseerde resultaat), ook al is holdings dan allang 0. Met
        # geinvesteerd als signaal liep de grafiek van een verkochte positie
        # dus onterecht door tot vandaag (bug: "per-aandeel-grafiek loopt
        # door na volledige verkoop"). Epsilon i.p.v. exact 0 i.v.m.
        # float-afrondingen in de cumulatieve holdings-som.
        #
        # De crop-range (nonzero_idx) mag NIET uitsluitend op holdings != 0
        # afgaan: een koop + volledige verkoop binnen dezelfde (dagelijks
        # bemonsterde) datum eindigt ook op holdings == 0 voor die datum,
        # terwijl er wel degelijk een echte transactie was — "activiteit"
        # (er is die datum minstens 1 transactie verwerkt) vangt dat geval
        # mee, ook al is de holdings-verandering per saldo 0.
        nonzero_idx = df_t.index[(df_t["holdings"].abs() > 1e-6) | df_t["activiteit"]]
        is_still_held = abs(df_t["holdings"].iloc[-1]) > 1e-6 if len(df_t) else False
        if len(nonzero_idx) > 0:
            all_dates = list(df_t.index)
            start_pos = all_dates.index(nonzero_idx[0])
            end_pos = all_dates.index(nonzero_idx[-1])

            # 1 dag ervoor erbij, zodat de sprong vanaf 0 zichtbaar is
            start_pos = max(0, start_pos - 1)

            # 1 dag erna erbij, maar alleen als de positie niet meer
            # aangehouden wordt — anders wordt er niets zinnigs toegevoegd,
            # je bezit het nog gewoon.
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
    """
    Per ticker: de kale koers per aandeel over tijd (niet vermenigvuldigd
    met het aantal, in tegenstelling tot compute_per_ticker()'s 'waarde'),
    het aantal aangehouden aandelen over tijd, en apart de datums van
    aankopen (adj_aantal > 0) en verkopen (adj_aantal < 0) -- in
    tegenstelling tot het oude trading_degiro.py-script dat alle
    transactiedatums door elkaar als 'Aankoop' labelde. T.b.v. het 'Per
    aandeel aankoop'-tabblad.

    Gebruikt dezelfde crop-range-logica als compute_per_ticker() (rond de
    periode dat de positie daadwerkelijk aangehouden werd), zodat beide
    tabbladen consistente start-/einddatums per positie tonen.
    """
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        holdings = 0.0
        trade_i = 0
        rows = []

        # Snelle array-toegang i.p.v. price_data.loc[date, ticker] per
        # iteratie (zie CLAUDE.md, ".loc-overhead wegnemen") -- zelfde
        # loop-structuur en -volgorde, alleen de koerslookup is nu een
        # goedkope array-index.
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

        # Zelfde crop-logica als compute_per_ticker() (zie die functie voor
        # de uitgebreide toelichting) -- hier bewust NIET herschreven als
        # gedeelde helper, want dat raakt compute_per_ticker() en dat is
        # buiten scope; wel 1-op-1 hetzelfde gedrag.
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
            # LET OP: df_t["koers"] is een pandas-kolom -- als die None-
            # waarden bevat (ontbrekende koers) wordt de kolom float64 en
            # verandert None stilletjes in NaN (numpy-gedrag bij het
            # bouwen van de DataFrame uit rows-dicts). "is not None" mist
            # dat dus altijd; NaN serialiseert vervolgens als het ongeldige
            # JSON-token "NaN" (json.dumps staat dat standaard toe) en
            # breekt fetch()'s response.json() in de browser. pd.notna()
            # herkent zowel None als NaN correct.
            "koers": [round(k, 4) if pd.notna(k) else None for k in df_t["koers"]],
            "holdings": df_t["holdings"].round(6).tolist(),
            # Meegestuurd zodat de frontend niet een eigen drempel op
            # holdings[-1] hoeft toe te passen (zie werkMeerHistorieKnoppenBij).
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
    """
    Aantal aangehouden stuks (cumulatieve adj_aantal van alle trades t/m
    die datum) op elke datum in `datums`, als lijst in dezelfde volgorde.
    `trades_df` bevat de split-gecorrigeerde transacties van één ticker.

    Voor /ticker-koers-bereik: geen expliciete crop nodig, want buiten de
    crop-range van compute_per_ticker_koers_en_aankopen() is de cumulatieve
    stand per definitie al 0 (vóór de eerste trade, na een volledige
    verkoop) -- en een tussentijdse nul-periode blijft zo gewoon staan.
    """
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
