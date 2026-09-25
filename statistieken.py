"""Rendement, XIRR, TWR, GAK en jaaroverzicht. Pure functies: geen DB of netwerk."""
import pandas as pd
from pyxirr import xirr

from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch

# Accumulerende UCITS-ETF's in EUR: geen dividend-boekhouding nodig. IAEA.AS heeft pas koersen vanaf 2020-07-29.
BENCHMARK_TICKERS = {
    "S&P 500": "VUSA.AS",
    "Nasdaq 100": "CNDX.AS",
    "AEX": "IAEA.AS",
}


def bereken_positie_rendement(gak, aantal, huidige_koers):
    """Noemer is de kostenbasis van de huidige stukken (GAK x aantal), niet de historische inleg."""
    geinvesteerd = gak * aantal
    waarde = aantal * huidige_koers
    rendement_pct = ((waarde - geinvesteerd) / geinvesteerd * 100) if geinvesteerd else None
    return {"waarde": waarde, "geinvesteerd": geinvesteerd, "rendement_pct": rendement_pct}


def bereken_totaal_rendement(geinvesteerd, waarde):
    """Simpele ratio, zonder rekening te houden met wanneer er is ingelegd."""
    rendement_eur = waarde - geinvesteerd
    rendement_pct = (rendement_eur / geinvesteerd * 100) if geinvesteerd else None
    return {"rendement_eur": rendement_eur, "rendement_pct": rendement_pct}


def bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde):
    """winst_pct deelt door startwaarde + ingelegd, niet door een gemiddelde over het jaar."""
    winst_eur = eindwaarde - startwaarde - ingelegd
    noemer = startwaarde + ingelegd
    winst_pct = (winst_eur / noemer * 100) if noemer else None
    return {"winst_eur": winst_eur, "winst_pct": winst_pct}


def bereken_xirr(cashflows):
    """cashflows: (datum, bedrag), aankopen negatief. Fractie (0.10 = 10%) of None."""
    if len(cashflows) < 2:
        return None
    datums = [c[0] for c in cashflows]
    bedragen = [c[1] for c in cashflows]
    try:
        return xirr(datums, bedragen)
    except Exception:
        return None


def bereken_twr(transacties_df, resultaat):
    """Time-weighted return als fractie, of None. cf telt mee op de einddatum van een sub-periode;
    corporate-action-rijen tellen niet als cashflow."""
    if resultaat.empty or len(resultaat) < 2:
        return None

    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cf_lookup = {}
    for _, row in df.iterrows():
        cf = -float(row["totaal_eur"])
        if cf == 0:
            continue
        datum = pd.Timestamp(row["datum"]).normalize()
        cf_lookup[datum] = cf_lookup.get(datum, 0.0) + cf

    product = 1.0
    geldige_periode = False
    for i in range(1, len(resultaat)):
        waarde_start = float(resultaat["waarde"].iloc[i - 1])
        waarde_eind = float(resultaat["waarde"].iloc[i])
        cf = cf_lookup.get(pd.Timestamp(resultaat.index[i]).normalize(), 0.0)
        noemer = waarde_start + cf
        if abs(noemer) < 1e-9:
            continue  # bv. vóór de eerste aankoop
        product *= waarde_eind / noemer
        geldige_periode = True

    if not geldige_periode:
        return None
    return product - 1


def bereken_holdings_en_gesloten(transacties_df):
    """GAK-methode, zie CLAUDE.md: Data en rekenen. Alle rijen tellen voor het aantal, alleen rijen
    met totaal_eur != 0 voor kostenbasis en gemiddelde koersen.
    Geeft (open_posities {ticker: {aantal, gak, deels_verkocht?}},
           gesloten_posities {ticker: {aantal, gemiddelde_aankoopkoers, gemiddelde_verkoopkoers, gerealiseerd_eur}})."""
    open_posities = {}
    gesloten_posities = {}
    df = transacties_df.dropna(subset=["ticker"])
    for ticker, groep in df.groupby("ticker"):
        groep = _sorteer_chronologisch(groep)
        aantal_lopend = 0.0
        kostprijs_lopend = 0.0
        totaal_gekocht_aantal = 0.0
        totaal_gekocht_bedrag = 0.0
        totaal_verkocht_aantal = 0.0
        totaal_verkocht_bedrag = 0.0
        totaal_verkochte_kostenbasis = 0.0

        for _, row in groep.iterrows():
            delta_aantal = float(row["aantal"])
            # totaal_eur alleen voor de cashflow-check (splitrijen = 0) en de verkoopkant.
            delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
            if delta_aantal > 0:
                waarde_bron = row["waarde_eur"] if pd.notna(row.get("waarde_eur")) else row["totaal_eur"]
                delta_cash_aankoop = -float(waarde_bron)
                aantal_lopend += delta_aantal
                kostprijs_lopend += delta_cash_aankoop
                if delta_cash != 0:
                    totaal_gekocht_aantal += delta_aantal
                    totaal_gekocht_bedrag += delta_cash_aankoop
            elif delta_aantal < 0:
                if delta_cash != 0 and aantal_lopend > 0:
                    gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                    verkocht_nu = min(-delta_aantal, aantal_lopend)
                    kostenbasis_verkocht_nu = gak_op_dat_moment * verkocht_nu
                    kostprijs_lopend -= kostenbasis_verkocht_nu
                    totaal_verkocht_aantal += verkocht_nu
                    totaal_verkocht_bedrag += -delta_cash  # delta_cash negatief bij verkoop
                    totaal_verkochte_kostenbasis += kostenbasis_verkocht_nu
                aantal_lopend += delta_aantal

        if aantal_lopend > 1e-9:
            positie = {"aantal": aantal_lopend, "gak": kostprijs_lopend / aantal_lopend}
            if totaal_verkocht_aantal > 1e-9:
                # Gerealiseerd resultaat van een gedeeltelijke verkoop; zelfde velden als gesloten.
                positie["deels_verkocht"] = {
                    "aantal": totaal_verkocht_aantal,
                    "gemiddelde_aankoopkoers": (
                        totaal_verkochte_kostenbasis / totaal_verkocht_aantal
                        if totaal_verkocht_aantal > 1e-9 else None
                    ),
                    "gemiddelde_verkoopkoers": totaal_verkocht_bedrag / totaal_verkocht_aantal,
                    "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_verkochte_kostenbasis,
                }
            open_posities[ticker] = positie
        elif totaal_gekocht_aantal > 1e-9:
            gesloten_posities[ticker] = {
                "aantal": totaal_gekocht_aantal,
                "gemiddelde_aankoopkoers": totaal_gekocht_bedrag / totaal_gekocht_aantal,
                "gemiddelde_verkoopkoers": (
                    totaal_verkocht_bedrag / totaal_verkocht_aantal
                    if totaal_verkocht_aantal > 1e-9 else None
                ),
                "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_gekocht_bedrag,
            }

    return open_posities, gesloten_posities


def bereken_jaren_overzicht(resultaat, eerste_datum=None):
    """eerste_datum: de echte eerste transactiedatum, zodat het eerste jaar niet als vol jaar telt."""
    if resultaat.empty:
        return []

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    laatste_datum = resultaat.index.max()
    eerste_datum = pd.Timestamp(eerste_datum) if eerste_datum is not None else resultaat.index.min()
    eerste_jaar = eerste_datum.year
    laatste_jaar = laatste_datum.year

    jaren = []
    for jaar in range(eerste_jaar, laatste_jaar + 1):
        jaar_start = pd.Timestamp(year=jaar, month=1, day=1)
        jaar_eind = pd.Timestamp(year=jaar, month=12, day=31)
        dagen_in_jaar = 366 if pd.Timestamp(year=jaar, month=12, day=31).is_leap_year else 365

        periode_start = max(jaar_start, eerste_datum)
        periode_eind = min(jaar_eind, laatste_datum)
        dagen_verstreken = (periode_eind - periode_start).days + 1
        pct_van_jaar = dagen_verstreken / dagen_in_jaar * 100

        startwaarde = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "waarde")
        geinvesteerd_voor = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "geinvesteerd")
        geinvesteerd_na = waarde_op_of_voor(periode_eind, "geinvesteerd")
        ingelegd = geinvesteerd_na - geinvesteerd_voor
        eindwaarde = waarde_op_of_voor(periode_eind, "waarde")

        rendement = bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde)
        jaren.append({
            "jaar": jaar,
            "dagen_verstreken": dagen_verstreken,
            "pct_van_jaar": round(pct_van_jaar, 1),
            "startwaarde": round(startwaarde, 2),
            "ingelegd": round(ingelegd, 2),
            "eindwaarde": round(eindwaarde, 2),
            "winst_eur": round(rendement["winst_eur"], 2),
            "winst_pct": round(rendement["winst_pct"], 2) if rendement["winst_pct"] is not None else None,
        })
    return jaren


def _bouw_xirr_cashflows(transacties_df, resultaat):
    """Echte transacties plus als laatste een fictieve 'verkoop vandaag' van de huidige waarde."""
    if resultaat.empty:
        return []
    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cashflows = [
        (pd.Timestamp(row["datum"]).date(), float(row["totaal_eur"]))
        for _, row in df.iterrows() if float(row["totaal_eur"]) != 0
    ]
    if not cashflows:
        return []
    laatste_datum = resultaat.index.max()
    laatste_waarde = float(resultaat["waarde"].iloc[-1])
    cashflows.append((pd.Timestamp(laatste_datum).date(), laatste_waarde))
    cashflows.sort(key=lambda c: c[0])
    return cashflows


def bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen):
    """Dezelfde cashflows (datum, bedrag) in de benchmark gestoken.
    Geeft {labels, waarde, rendement, vanaf_datum, onvolledige_dekking} of None;
    onvolledige_dekking = de benchmark heeft pas koersen na de eerste cashflow."""
    if resultaat.empty:
        return None
    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    if len(cashflows) < 2:
        return None
    cashflows = cashflows[:-1]  # laatste = fictieve 'verkoop vandaag', geen echte transactie
    if not cashflows:
        return None

    koersen = benchmark_koersen.dropna()
    if koersen.empty:
        return None

    eerst_beschikbaar = koersen.index.min()
    eerste_cashflow_datum = pd.Timestamp(min(d for d, _ in cashflows))
    labels = [d for d in resultaat.index if d >= eerst_beschikbaar]
    if not labels:
        return None

    cashflows_ts = sorted((pd.Timestamp(d), bedrag) for d, bedrag in cashflows)

    aantal = 0.0
    waarde_per_dag = []
    cf_i = 0
    for datum in labels:
        while cf_i < len(cashflows_ts) and cashflows_ts[cf_i][0] <= datum:
            cf_datum, bedrag = cashflows_ts[cf_i]
            koers_op_cf_datum = koersen.reindex([cf_datum], method="ffill").iloc[0]
            if pd.notna(koers_op_cf_datum) and koers_op_cf_datum > 0:
                aantal += -bedrag / koers_op_cf_datum
            cf_i += 1
        koers_vandaag = koersen.reindex([datum], method="ffill").iloc[0]
        waarde_per_dag.append(aantal * koers_vandaag if pd.notna(koers_vandaag) else None)

    geinvesteerd_per_label = resultaat.loc[labels, "geinvesteerd"]
    rendement_per_dag = [
        (round(w - g, 2) if w is not None else None)
        for w, g in zip(waarde_per_dag, geinvesteerd_per_label)
    ]

    return {
        "labels": [d.strftime("%Y-%m-%d") for d in labels],
        "waarde": [round(w, 2) if w is not None else None for w in waarde_per_dag],
        "rendement": rendement_per_dag,
        "vanaf_datum": labels[0].strftime("%Y-%m-%d"),
        "onvolledige_dekking": eerste_cashflow_datum < eerst_beschikbaar,
    }


def bereken_rendement_over_tijd(transacties_df, resultaat):
    """Rendement%, XIRR% en TWR% per maandeinde plus de laatste datum (percentages, niet fracties).
    Dat XIRR en TWR vlak na een storting uiteenlopen, is verwacht."""
    if resultaat.empty:
        return {"labels": [], "rendement_pct": [], "xirr_pct": [], "twr_pct": []}

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    eerste_datum = resultaat.index.min()
    laatste_datum = resultaat.index.max()
    stap_datums = list(pd.date_range(eerste_datum, laatste_datum, freq="ME"))
    if not stap_datums or stap_datums[-1] < laatste_datum:
        stap_datums.append(laatste_datum)

    alle_cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    # Zonder de fictieve eind-cashflow: elke stap krijgt hieronder een eigen (op d).
    echte_cashflows = alle_cashflows[:-1] if alle_cashflows else []

    labels, rendement_pct_lijst, xirr_pct_lijst, twr_pct_lijst = [], [], [], []
    for d in stap_datums:
        waarde = waarde_op_of_voor(d, "waarde")
        geinvesteerd = waarde_op_of_voor(d, "geinvesteerd")

        rendement = bereken_totaal_rendement(geinvesteerd, waarde)

        cashflows_tot_d = [(dat, bedrag) for dat, bedrag in echte_cashflows if pd.Timestamp(dat) <= d]
        xirr = None
        if cashflows_tot_d:
            xirr = bereken_xirr(cashflows_tot_d + [(d.date(), waarde)])

        twr = bereken_twr(transacties_df, resultaat.loc[:d])

        labels.append(d.strftime("%Y-%m-%d"))
        rendement_pct_lijst.append(
            round(rendement["rendement_pct"], 2) if rendement["rendement_pct"] is not None else None
        )
        xirr_pct_lijst.append(round(xirr * 100, 2) if xirr is not None else None)
        twr_pct_lijst.append(round(twr * 100, 2) if twr is not None else None)

    return {
        "labels": labels,
        "rendement_pct": rendement_pct_lijst,
        "xirr_pct": xirr_pct_lijst,
        "twr_pct": twr_pct_lijst,
    }


def bereken_totale_transactiekosten(transacties_df):
    """Positief totaal; beschikbaar=False als de kolom ontbreekt of leeg is (nooit een verzonnen €0,00)."""
    if "transactiekosten" not in transacties_df.columns:
        return {"totaal": None, "beschikbaar": False}
    kosten = pd.to_numeric(transacties_df["transactiekosten"], errors="coerce").dropna()
    if kosten.empty:
        return {"totaal": None, "beschikbaar": False}
    return {"totaal": round(abs(float(kosten.sum())), 2), "beschikbaar": True}


def bereken_statistieken(transacties_df, price_data, resultaat, dividend_per_ticker=None, ticker_namen=None):
    """Alles voor het Statistieken-tabblad, zonder extra Yahoo-calls.
    Huidige aantallen uit de ruwe 'aantal'-kolom (zie CLAUDE.md: Data en rekenen)."""
    dividend_per_ticker = dividend_per_ticker or {}
    ticker_namen = ticker_namen or {}
    laatste_prijzen = price_data.iloc[-1] if not price_data.empty else pd.Series(dtype=float)
    holdings, gesloten_posities = bereken_holdings_en_gesloten(transacties_df)

    posities = []
    for ticker, info in holdings.items():
        if ticker not in price_data.columns or pd.isna(laatste_prijzen.get(ticker)):
            continue
        huidige_koers = float(laatste_prijzen[ticker])
        r = bereken_positie_rendement(info["gak"], info["aantal"], huidige_koers)
        posities.append({
            "ticker": ticker,
            "aantal": round(info["aantal"], 4),
            "gak": round(info["gak"], 4),
            "huidige_koers": round(huidige_koers, 4),
            "huidige_waarde": round(r["waarde"], 2),
            "geinvesteerd": round(r["geinvesteerd"], 2),
            "rendement_eur": round(r["waarde"] - r["geinvesteerd"], 2),
            "rendement_pct": round(r["rendement_pct"], 2) if r["rendement_pct"] is not None else None,
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    posities.sort(key=lambda p: p["huidige_waarde"], reverse=True)

    gesloten_posities_output = []
    for ticker, info in gesloten_posities.items():
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(info["aantal"], 4),
            "resterend_aantal": 0.0,
            "nog_in_bezit": False,
            "gemiddelde_aankoopkoers": round(info["gemiddelde_aankoopkoers"], 4),
            "gemiddelde_verkoopkoers": (
                round(info["gemiddelde_verkoopkoers"], 4)
                if info["gemiddelde_verkoopkoers"] is not None else None
            ),
            # Exclusief dividend; dat staat bewust als apart veld ernaast.
            "rendement_eur": round(info["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(info["gerealiseerd_eur"] / (info["gemiddelde_aankoopkoers"] * info["aantal"]) * 100, 2)
                if info["gemiddelde_aankoopkoers"] else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })

    for ticker, info in holdings.items():
        deels = info.get("deels_verkocht")
        if not deels:
            continue
        kostenbasis_verkocht = (deels["gemiddelde_aankoopkoers"] or 0) * deels["aantal"]
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(deels["aantal"], 4),
            "resterend_aantal": round(info["aantal"], 4),
            "nog_in_bezit": True,
            "gemiddelde_aankoopkoers": (
                round(deels["gemiddelde_aankoopkoers"], 4)
                if deels["gemiddelde_aankoopkoers"] is not None else None
            ),
            "gemiddelde_verkoopkoers": round(deels["gemiddelde_verkoopkoers"], 4),
            "rendement_eur": round(deels["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(deels["gerealiseerd_eur"] / kostenbasis_verkocht * 100, 2)
                if kostenbasis_verkocht else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    gesloten_posities_output.sort(key=lambda p: p["rendement_pct"] or 0, reverse=True)

    totaal_geinvesteerd = float(resultaat["geinvesteerd"].iloc[-1]) if not resultaat.empty else 0.0
    totaal_waarde = float(resultaat["waarde"].iloc[-1]) if not resultaat.empty else 0.0
    totaal = bereken_totaal_rendement(totaal_geinvesteerd, totaal_waarde)

    # Hoogste rendement, niet hoogste waarde (zie CLAUDE.md: Data en rekenen).
    all_time_high = {"waarde": None, "datum": None}
    if not resultaat.empty:
        ath_idx = resultaat["rendement"].idxmax()
        all_time_high = {
            "waarde": round(float(resultaat["rendement"].max()), 2),
            "datum": ath_idx.strftime("%Y-%m-%d"),
        }

    eerste_datum = None
    if not resultaat.empty:
        eerste_datum = transacties_df.dropna(subset=["ticker"])["datum"].min()

    jaren = bereken_jaren_overzicht(resultaat, eerste_datum=eerste_datum)
    geldige_pcts = [j["winst_pct"] for j in jaren if j["winst_pct"] is not None]
    gemiddeld_jaarrendement = round(sum(geldige_pcts) / len(geldige_pcts), 2) if geldige_pcts else None

    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    xirr_fractie = bereken_xirr(cashflows) if cashflows else None
    twr_fractie = bereken_twr(transacties_df, resultaat)

    aantal_jaren = None
    if not resultaat.empty:
        aantal_jaren = round((resultaat.index.max() - pd.Timestamp(eerste_datum)).days / 365.25, 2)

    kosten_info = bereken_totale_transactiekosten(transacties_df)

    return {
        "posities": posities,
        "gesloten_posities": gesloten_posities_output,
        "totalen": {
            "geinvesteerd": round(totaal_geinvesteerd, 2),
            "waarde": round(totaal_waarde, 2),
            "rendement_eur": round(totaal["rendement_eur"], 2),
            "rendement_pct": round(totaal["rendement_pct"], 2) if totaal["rendement_pct"] is not None else None,
            "all_time_high": all_time_high,
            "totale_transactiekosten": kosten_info["totaal"],
            "transactiekosten_beschikbaar": kosten_info["beschikbaar"],
        },
        "jaren": jaren,
        "geavanceerd": {
            "gemiddeld_jaarrendement_pct": gemiddeld_jaarrendement,
            "xirr_pct": round(xirr_fractie * 100, 2) if xirr_fractie is not None else None,
            "twr_pct": round(twr_fractie * 100, 2) if twr_fractie is not None else None,
            "aantal_jaren": aantal_jaren,
        },
    }