"""Split-correctie en de per-dag- en per-ticker-tijdreeksen (Home, Per aandeel, Per aandeel aankoop)."""
import numpy as np
import pandas as pd

from debug_utils import dprint
from diagnostiek import meld, CATEGORIE_SPLITS, INFO, LET_OP
from split_correctie import (
    DegiroSplitGebeurtenis, SPLIT_KOPPEL_MAX_DAGEN, SplitBoeking, vind_wisselparen,
)
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch, formatteer_datum_nl, getal_nl


def _vind_conversies(df, log=True):
    """Per ISIN met corporate-action-rijen de conversies (conversierij koers 0) met hun ratio en bijbehorende rijen."""
    resultaat = []
    for isin, groep in df.groupby("isin"):
        groep = groep.sort_values("datum")
        is_ca = groep.apply(_is_corporate_action_row, axis=1)
        ca_rows = groep[is_ca]
        if ca_rows.empty:
            continue

        product_naam = groep["product"].iloc[0] if "product" in groep.columns else "?"
        reden_geen_factor = "geen conversierij gevonden"
        if log:
            dprint(f"\n[split-detect] ISIN={isin} ('{product_naam}') heeft {len(ca_rows)} "
                   f"corporate-action rij(en), onderzoeken...")
            dprint(f"[split-detect]   alle rijen voor deze ISIN:")
            for _, r in groep.iterrows():
                dprint(f"    {r['datum']} | beurs={r.get('beurs')} | product={str(r.get('product'))[:50]} "
                       f"| aantal={r.get('aantal')} | koers={r.get('koers')} | totaal_eur={r.get('totaal_eur')}")

        real_trades = groep[~is_ca]
        conversion_rows = real_trades[
            (real_trades["koers"] == 0) & (real_trades["adj_aantal"] > 0)
        ].sort_values("datum")

        conversies = []
        # Geen conversierij: het aantal klopt vanaf hier niet meer.
        for _, conv in conversion_rows.iterrows():
            conv_date = conv["datum"]
            eerdere_trades = real_trades[(real_trades["datum"] < conv_date) & (real_trades["koers"] > 0)]
            shares_before = float(eerdere_trades["adj_aantal"].sum())
            if shares_before <= 0:
                if log:
                    dprint(f"[split-detect]   conversie op {conv_date}: shares_before={shares_before} "
                           f"(<=0) - overgeslagen, kan geen ratio berekenen")
                reden_geen_factor = "geen aandelen vóór de conversie"
                continue

            last_real_date = eerdere_trades["datum"].max()
            in_venster = ca_rows[(ca_rows["datum"] > last_real_date) & (ca_rows["datum"] <= conv_date)]
            new_shares = float(in_venster.loc[in_venster["adj_aantal"] > 0, "adj_aantal"].sum())
            if new_shares <= 0:
                if log:
                    dprint(f"[split-detect]   conversie op {conv_date}: new_shares={new_shares} (<=0) "
                           f"- overgeslagen")
                reden_geen_factor = "geen nieuwe aandelen in de corporate-action-rijen"
                continue

            conversies.append({
                "datum": conv_date, "ratio": (shares_before + new_shares) / shares_before,
                # DeGiro schrijft de nieuwe stukken op de splitdag bij (DEG) en zet ze soms weken later pas om.
                "bijschrijfdatum": in_venster.loc[in_venster["adj_aantal"] > 0, "datum"].min(),
                "shares_before": shares_before, "new_shares": new_shares,
                "rijen": [conv.name] + list(in_venster.index),
            })
        resultaat.append({"isin": isin, "product_naam": product_naam, "conversies": conversies,
                          "reden_geen_factor": reden_geen_factor})
    return resultaat


def _ticker_per_isin(df):
    if "ticker" not in df.columns:
        return {}
    return df.dropna(subset=["ticker", "isin"]).groupby("isin")["ticker"].first().to_dict()


def _verwerk_wisselparen(df):
    """Markeert wisselrijen en geeft wisselrijen zonder ticker (DEG-rijen hebben er geen) die van hun ISIN-partner."""
    paren, onduidelijk = vind_wisselparen(df)
    ticker_van = _ticker_per_isin(df)
    for paar in paren:
        df.loc[list(paar.oud_rijen) + list(paar.nieuw_rijen), "is_wisselrij"] = True
        oud_ticker, nieuw_ticker = ticker_van.get(paar.oud_isin), ticker_van.get(paar.nieuw_isin)
        if "ticker" in df.columns:
            for rijen, ticker in ((paar.oud_rijen, oud_ticker or nieuw_ticker), (paar.nieuw_rijen, nieuw_ticker or oud_ticker)):
                for label in rijen:
                    if pd.isna(df.at[label, "ticker"]) and ticker:
                        df.at[label, "ticker"] = ticker
    for dag in onduidelijk:
        meld(CATEGORIE_SPLITS, LET_OP,
             f"Op {formatteer_datum_nl(dag)} staan boekingen (tijd 00:00, zonder kosten) met meer dan twee ISIN's "
             f"naast elkaar; niet als splitboeking herkend.",
             sleutel=f"wisselpaar_onduidelijk:{dag:%Y-%m-%d}")


def compute_split_adjusted_shares(transacties_df):
    """Voegt toe: 'adj_aantal' (split-gecorrigeerd, zie CLAUDE.md: Data en rekenen), 'effectieve_datum' (= datum
    tot bepaal_effectieve_datums() hem aanpast) en 'is_wisselrij' (omboeking bij een ISIN-wissel)."""
    df = transacties_df.copy()
    df["adj_aantal"] = df["aantal"].astype(float)
    df["koers"] = df["koers"].fillna(0).astype(float)
    df["effectieve_datum"] = pd.to_datetime(df["datum"])
    df["is_wisselrij"] = False
    paren, _onduidelijk = vind_wisselparen(df)
    wisselrijen = {label for paar in paren for label in paar.oud_rijen + paar.nieuw_rijen}

    for item in _vind_conversies(df):
        isin, product_naam = item["isin"], item["product_naam"]
        for conversie in item["conversies"]:
            conv_date, ratio = conversie["datum"], conversie["ratio"]
            mask = (
                (df["isin"] == isin)
                & (df["datum"] < conv_date)
                & (~df.apply(_is_corporate_action_row, axis=1))
            )
            dprint(f"[split-detect]   pas ratio {ratio:.4f}x toe op {mask.sum()} eerdere rij(en)")
            df.loc[mask, "adj_aantal"] *= ratio
            conv_datum_tekst = pd.Timestamp(conv_date).strftime("%Y-%m-%d")
            meld(CATEGORIE_SPLITS, INFO,
                 f"Split voor {product_naam} ({isin}) op {formatteer_datum_nl(conv_date)}: factor {getal_nl(ratio, 4)}.",
                 sleutel=f"split:{isin}:{conv_datum_tekst}")

        # Een herkende ISIN-wissel meldt meld_split_koppeling() al.
        ca_rijen = df.index[(df["isin"] == isin) & df.apply(_is_corporate_action_row, axis=1)]
        if not item["conversies"] and not set(ca_rijen) <= wisselrijen:
            meld(CATEGORIE_SPLITS, LET_OP,
                 f"{product_naam} ({isin}) heeft corporate-action-rijen, maar er is geen splitfactor bepaald "
                 f"({item['reden_geen_factor']}); het aantal aandelen kan vanaf dan afwijken. Bij een corporate action "
                 f"die geen split is (bv. een ISIN-wissel) kan dit terecht zijn.",
                 sleutel=f"split_onbekend:{isin}")

    _verwerk_wisselparen(df)
    return df


def bepaal_split_boekingen(transacties_df):
    """Alle DeGiro-splitboekingen (conversierij-patroon en wisselpaar) met hun ticker, voor de koppeling aan Yahoo-splits.
    Verwacht de uitvoer van compute_split_adjusted_shares()."""
    ticker_van = _ticker_per_isin(transacties_df)
    boekingen = []
    # Terug naar de ruwe aantallen: adj_aantal is daar al met de splitratio vermenigvuldigd.
    ongecorrigeerd = transacties_df.assign(adj_aantal=transacties_df["aantal"].astype(float))
    for item in _vind_conversies(ongecorrigeerd, log=False):
        ticker = ticker_van.get(item["isin"])
        if ticker is None:
            continue
        for c in item["conversies"]:
            gebeurtenis = DegiroSplitGebeurtenis(
                pd.Timestamp(c["bijschrijfdatum"]), c["shares_before"], c["shares_before"] + c["new_shares"])
            boekingen.append(SplitBoeking(ticker, gebeurtenis, tuple(c["rijen"]), "conversierij"))

    paren, _onduidelijk = vind_wisselparen(transacties_df)
    for paar in paren:
        ticker = ticker_van.get(paar.oud_isin) or ticker_van.get(paar.nieuw_isin)
        if ticker is not None:
            gebeurtenis = DegiroSplitGebeurtenis(paar.datum, paar.oud_aantal, paar.nieuw_aantal)
            boekingen.append(SplitBoeking(
                ticker, gebeurtenis, tuple(paar.oud_rijen) + tuple(paar.nieuw_rijen), "wisselpaar",
                (paar.oud_isin, paar.nieuw_isin)))
    return boekingen


def split_tekst(ratio):
    """Yahoo-ratio 4 -> 'Split 4:1', 1/3 -> 'Reverse split 1:3' (zoals splitLabel() in koersen.js)."""
    return f"Split {getal_nl(ratio)}:1" if ratio >= 1 else f"Reverse split 1:{getal_nl(1 / ratio)}"


def _wissel_tekst(boeking, koppeling, verschil):
    g = boeking.gebeurtenis
    oud_isin, nieuw_isin = boeking.isins or ("?", "?")
    tekst = (f"{split_tekst(koppeling.yahoo_ratio)} van {boeking.ticker} op {formatteer_datum_nl(koppeling.yahoo_datum)} "
             f"met ISIN-wissel ({oud_isin} -> {nieuw_isin}): {getal_nl(g.oud_aantal)} stuks uit, "
             f"{getal_nl(g.nieuw_aantal)} stuks in")
    # DeGiro boekt hele stukken; het restant onder de 1 gaat contant.
    fractie = g.oud_aantal * koppeling.yahoo_ratio - g.nieuw_aantal
    if fractie > 1e-6:
        tekst += f", fractie {getal_nl(fractie)} stuk contant uitbetaald"
    if verschil:
        tekst += (f". DeGiro boekte op {formatteer_datum_nl(g.datum)}; het aantal telt mee vanaf Yahoo's datum")
    return tekst + "."


def meld_split_koppeling(resultaat):
    """Diagnostiek-meldingen bij het koppelen van DeGiro-boekingen aan Yahoo-splits."""
    for boeking, koppeling in resultaat.gekoppeld:
        verschil = (pd.Timestamp(boeking.gebeurtenis.datum).normalize() - koppeling.yahoo_datum).days
        if boeking.patroon == "wisselpaar":
            meld(CATEGORIE_SPLITS, INFO, _wissel_tekst(boeking, koppeling, verschil),
                 sleutel=f"split_koppeling:{boeking.ticker}:{koppeling.yahoo_datum:%Y-%m-%d}")
        elif verschil:
            meld(CATEGORIE_SPLITS, INFO,
                 f"Split van {boeking.ticker}: DeGiro boekte op {formatteer_datum_nl(boeking.gebeurtenis.datum)}, Yahoo op "
                 f"{formatteer_datum_nl(koppeling.yahoo_datum)}; het aantal telt mee vanaf Yahoo's datum.",
                 sleutel=f"split_koppeling:{boeking.ticker}:{koppeling.yahoo_datum:%Y-%m-%d}")
    for boeking in resultaat.zonder_yahoo:
        g = boeking.gebeurtenis
        meld(CATEGORIE_SPLITS, LET_OP,
             f"DeGiro-splitboeking voor {boeking.ticker} op {formatteer_datum_nl(g.datum)} ({getal_nl(g.oud_aantal)} -> "
             f"{getal_nl(g.nieuw_aantal)} stuks) past bij geen Yahoo-split binnen {SPLIT_KOPPEL_MAX_DAGEN} dagen; de waarde kan "
             f"rond die datum tijdelijk afwijken.",
             sleutel=f"split_zonder_yahoo:{boeking.ticker}:{pd.Timestamp(g.datum):%Y-%m-%d}")
    for ticker, datum, ratio in resultaat.zonder_boeking:
        meld(CATEGORIE_SPLITS, LET_OP,
             f"Yahoo meldt een split voor {ticker} op {formatteer_datum_nl(datum)} ({split_tekst(ratio).lower()}) terwijl je stukken "
             f"hield, maar er is geen DeGiro-boeking gevonden; het aantal stuks kan vanaf dan niet kloppen.",
             sleutel=f"split_zonder_boeking:{ticker}:{pd.Timestamp(datum):%Y-%m-%d}")


def _effectieve_datum_kolom(df):
    return "effectieve_datum" if "effectieve_datum" in df.columns else "datum"


def compute_value_over_time(transacties_df, price_data):
    """'geinvesteerd' is hier de netto cashflow (incl. kosten), niet de GAK-kostenbasis."""
    transacties_df = transacties_df.dropna(subset=["ticker"])
    # Aantallen tellen mee vanaf hun effectieve datum, geld vanaf de boekdatum.
    stukken = _sorteer_chronologisch(
        transacties_df, datum_kolom=_effectieve_datum_kolom(transacties_df)).reset_index(drop=True)
    transacties_df = _sorteer_chronologisch(transacties_df).reset_index(drop=True)
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
    stuk_i = 0
    stuk_kolom = _effectieve_datum_kolom(stukken)

    # Arrays en lijsten i.p.v. .loc per iteratie: .loc per dag was de helft van de laadtijd.
    prijs_per_ticker = {t: price_data[t].to_numpy() for t in tickers}
    trade_datums = [pd.Timestamp(d) for d in transacties_df["datum"]]
    trade_totalen = transacties_df["totaal_eur"].tolist()
    stuk_datums = [pd.Timestamp(d) for d in stukken[stuk_kolom]]
    stuk_tickers = stukken["ticker"].tolist()
    stuk_aantallen = stukken["aantal"].tolist()

    for i, date in enumerate(price_data.index):
        while trade_i < len(trade_datums) and trade_datums[trade_i] <= date:
            invested += -float(trade_totalen[trade_i])
            trade_i += 1
        while stuk_i < len(stuk_datums) and stuk_datums[stuk_i] <= date:
            if stuk_tickers[stuk_i] in holdings:
                holdings[stuk_tickers[stuk_i]] += float(stuk_aantallen[stuk_i])
            stuk_i += 1

        waarde = sum(
            holdings[t] * prijs_per_ticker[t][i]
            for t in tickers
            if pd.notna(prijs_per_ticker[t][i])
        )
        rows.append({"datum": date, "waarde": waarde, "geinvesteerd": invested})

    result = pd.DataFrame(rows).set_index("datum")
    result["rendement"] = result["waarde"] - result["geinvesteerd"]
    return result


def _aantal_verwerkt(datums, dagen):
    """Per dag het aantal gebeurtenissen (gesorteerd op datum) met datum <= die dag."""
    gebeurtenissen = pd.DatetimeIndex([pd.Timestamp(d) for d in datums]).to_numpy()
    return np.searchsorted(gebeurtenissen, dagen.to_numpy(), side="right")


def _holdings_per_dag(stukken, stuk_kolom, dagen):
    """(holdings per dag, aantal verwerkte stukken per dag); holdings loopt op de effectieve datum."""
    verwerkt = _aantal_verwerkt(stukken[stuk_kolom], dagen)
    cumulatief = np.concatenate(([0.0], np.cumsum([float(a) for a in stukken["aantal"].tolist()])))
    return cumulatief[verwerkt], verwerkt


def _activiteit_per_dag(*verwerkt_per_soort):
    """Dagen waarop minstens één gebeurtenis verwerkt is; vangt een koop + volledige verkoop op dezelfde dag."""
    return np.any([np.diff(v, prepend=0) > 0 for v in verwerkt_per_soort], axis=0)


def _crop_en_nog_in_bezit(holdings, activiteit):
    """(slice, nog_in_bezit): van 1 dag voor de eerste activiteit/holdings tot de laatste, plus 1 dag erna als verkocht.
    "Nog in bezit" op aantal stuks (zie CLAUDE.md: Data en rekenen)."""
    aantal_dagen = len(holdings)
    nog_in_bezit = bool(abs(holdings[-1]) > 1e-6) if aantal_dagen else False
    posities = np.flatnonzero((np.abs(holdings) > 1e-6) | activiteit)
    if len(posities) == 0:
        return slice(0, 0), nog_in_bezit
    begin = max(0, int(posities[0]) - 1)
    eind = int(posities[-1])
    if not nog_in_bezit:
        eind = min(aantal_dagen - 1, eind + 1)
    return slice(begin, eind + 1), nog_in_bezit


def compute_per_ticker(transacties_df, price_data):
    """'geinvesteerd' is hier de GAK-kostenbasis van de aangehouden stukken."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]
    dagen = price_data.index

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        stukken = _sorteer_chronologisch(trades, datum_kolom=_effectieve_datum_kolom(trades)).reset_index(drop=True)
        stuk_kolom = _effectieve_datum_kolom(stukken)
        prijzen = price_data[ticker].to_numpy()

        trade_aantallen = trades["aantal"].tolist()
        trade_totalen = trades["totaal_eur"].tolist()
        trade_waarden = trades["waarde_eur"].tolist() if "waarde_eur" in trades.columns else [None] * len(trades)
        aantal_lopend = 0.0       # kostenbasis loopt op de boekdatum, holdings op de effectieve datum
        kostprijs_lopend = 0.0    # kostenbasis van de NU aangehouden stukken
        invested_na_trade = [0.0]
        for i in range(len(trades)):
            # GAK-methode, gelijk houden met bereken_holdings_en_gesloten() (zie CLAUDE.md: Data en rekenen).
            delta_aantal = float(trade_aantallen[i])
            # totaal_eur alleen voor de cashflow-check (splitrijen = 0) en de verkoopkant.
            delta_cash = -float(trade_totalen[i])  # positief = geld uitgegeven (aankoop)
            if delta_aantal > 0:
                waarde_bron = trade_waarden[i] if pd.notna(trade_waarden[i]) else trade_totalen[i]
                delta_cash_aankoop = -float(waarde_bron)
                aantal_lopend += delta_aantal
                kostprijs_lopend += delta_cash_aankoop
            elif delta_aantal < 0:
                if delta_cash != 0 and aantal_lopend > 0:
                    gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                    verkocht_nu = min(-delta_aantal, aantal_lopend)
                    kostprijs_lopend -= gak_op_dat_moment * verkocht_nu
                aantal_lopend += delta_aantal
            invested_na_trade.append(max(kostprijs_lopend, 0.0))  # epsilon-afronding kan net onder 0 uitkomen

        trades_verwerkt = _aantal_verwerkt(trades["datum"], dagen)
        holdings, stuk_verwerkt = _holdings_per_dag(stukken, stuk_kolom, dagen)
        invested = np.array(invested_na_trade)[trades_verwerkt]
        with np.errstate(invalid="ignore"):
            waarde = np.where(np.isnan(prijzen), 0.0, holdings * prijzen)

        _dprint_grote_sprongen(ticker, dagen, prijzen, waarde, invested, holdings)

        bereik, nog_in_bezit = _crop_en_nog_in_bezit(holdings, _activiteit_per_dag(trades_verwerkt, stuk_verwerkt))
        result[ticker] = {
            "labels": dagen[bereik].strftime("%Y-%m-%d").tolist(),
            "waarde": np.round(waarde[bereik], 2).tolist(),
            "geinvesteerd": np.round(invested[bereik], 2).tolist(),
            "nog_in_bezit": nog_in_bezit,
        }
    return result


def _dprint_grote_sprongen(ticker, dagen, prijzen, waarde, invested, holdings):
    """Grote sprong op 1 dag: helpt ISIN-migraties en verkeerde splits opsporen."""
    if len(dagen) < 2:
        return
    vorige_invested, vorige_waarde = invested[:-1], waarde[:-1]
    heeft_inleg = vorige_invested != 0
    sprong_invested = heeft_inleg & (np.abs(invested[1:] - vorige_invested) > 0.5 * np.abs(vorige_invested) + 50)
    with np.errstate(invalid="ignore"):
        sprong_waarde = (
            heeft_inleg & ~np.isnan(prijzen[1:]) & (vorige_waarde > 0)
            & (np.abs(waarde[1:] - vorige_waarde) > 0.5 * vorige_waarde + 50) & (holdings[1:] != 0)
        )
    for i in np.flatnonzero(sprong_invested | sprong_waarde) + 1:
        datum = dagen[i].date()
        if sprong_invested[i - 1]:
            dprint(f"[per-ticker:{ticker}] grote sprong in geinvesteerd op {datum}: "
                   f"{invested[i - 1]:.2f} -> {invested[i]:.2f}")
        if sprong_waarde[i - 1]:
            dprint(f"[per-ticker:{ticker}] grote sprong in waarde op {datum}: "
                   f"{waarde[i - 1]:.2f} -> {waarde[i]:.2f} (holdings={holdings[i]:.4f}, prijs={prijzen[i]})")


def compute_per_ticker_koers_en_aankopen(transacties_df, price_data):
    """Kale koers, aantal aangehouden en aparte aankoop-/verkoopdatums per ticker; zelfde crop als compute_per_ticker()."""
    transacties_df = _sorteer_chronologisch(transacties_df.dropna(subset=["ticker"])).reset_index(drop=True)
    tickers = [t for t in transacties_df["ticker"].unique() if t in price_data.columns]
    dagen = price_data.index

    result = {}
    for ticker in tickers:
        trades = transacties_df[transacties_df["ticker"] == ticker].reset_index(drop=True)
        stukken = _sorteer_chronologisch(trades, datum_kolom=_effectieve_datum_kolom(trades)).reset_index(drop=True)
        stuk_kolom = _effectieve_datum_kolom(stukken)
        prijzen = price_data[ticker].to_numpy()

        holdings, stuk_verwerkt = _holdings_per_dag(stukken, stuk_kolom, dagen)
        bereik, nog_in_bezit = _crop_en_nog_in_bezit(holdings, _activiteit_per_dag(stuk_verwerkt))
        dagen_t = dagen[bereik]

        # Een omboeking bij een ISIN-wissel is geen aan- of verkoop.
        echte_trades = trades[~trades["is_wisselrij"].astype(bool)] if "is_wisselrij" in trades.columns else trades
        aankopen = echte_trades[echte_trades["aantal"] > 0]
        verkopen = echte_trades[echte_trades["aantal"] < 0]
        if len(dagen_t) > 0:
            aankoop_datums_dt = pd.to_datetime(aankopen["datum"])
            aankopen = aankopen[(aankoop_datums_dt >= dagen_t[0]) & (aankoop_datums_dt <= dagen_t[-1])]
            verkoop_datums_dt = pd.to_datetime(verkopen["datum"])
            verkopen = verkopen[(verkoop_datums_dt >= dagen_t[0]) & (verkoop_datums_dt <= dagen_t[-1])]

        result[ticker] = {
            "labels": dagen_t.strftime("%Y-%m-%d").tolist(),
            # NaN breekt JSON: None.
            "koers": [None if np.isnan(k) else round(float(k), 4) for k in prijzen[bereik]],
            "holdings": np.round(holdings[bereik], 6).tolist(),
            "nog_in_bezit": nog_in_bezit,
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
    """Cumulatief ruw aantal van één ticker op elke datum in `datums` (op effectieve datum)."""
    if len(datums) == 0:
        return []
    if trades_df.empty:
        return [0.0] * len(datums)

    aantallen = pd.to_numeric(trades_df["aantal"].map(
        lambda a: float(a) if pd.notna(a) else 0.0
    ))
    per_dag = (
        pd.Series(aantallen.to_numpy(), index=pd.to_datetime(trades_df[_effectieve_datum_kolom(trades_df)]).to_numpy())
        .groupby(level=0).sum()
        .sort_index()
        .cumsum()
    )
    # Laatste stand op of vóór elke datum; datums vóór de eerste trade -> 0.
    stand = per_dag.reindex(pd.DatetimeIndex(datums), method="ffill").fillna(0.0)
    # + 0.0 maakt van -0.0 (na afronden van float-ruis) een gewone 0.0.
    return [round(float(h), 6) + 0.0 for h in stand]

