"""Laag tussen routes en domeinmodules: basis ophalen (met korte cache) en de dashboard-respons bouwen."""
import threading
import time

import pandas as pd
try:
    # Unix-only: de [memory]-meting werkt dus alleen op Render, niet lokaal op Windows.
    import resource
except ImportError:
    resource = None


from db import get_db_connection, get_laatste_prijs_update
from debug_utils import meet_tijd
from diagnostiek import (
    haal_meldingen, meldingen_sinds, meld_opnieuw, meld,
    CATEGORIE_LAADTIJDEN, CATEGORIE_KOERSEN, CATEGORIE_ETF_HOLDINGS, GOED, INFO, LET_OP,
)
from transactie_utils import _is_corporate_action_row
from prijzen import get_prices
from portfolio_calc import (
    compute_split_adjusted_shares, compute_value_over_time, compute_per_ticker,
    compute_per_ticker_koers_en_aankopen,
)
from ticker_zekerheid import ticker_waarschuwingen_voor_transacties
from dividend import bereken_dividend_samenvatting
from statistieken import bereken_statistieken
from portfolio_verdeling import (
    compute_land_sector_verdeling, bereken_bedrijven_verdeling, bereken_etf_overlap,
    _sorteer_verdeling_groot_naar_klein, _sorteer_tickers_voor_dropdown,
    bereken_verdeling_samenvatting, BEDRIJVEN_TOP_N_MAX,
)
from ticker_classificatie import classify_tickers, _verwarm_land_sector_cache_parallel


# Alleen voor de 2-3 requests van één portfolio-bezoek; per gunicorn-worker.
_BASIS_CACHE_TTL_SECONDEN = 20

# Zelfde marge als de "cache ver genoeg terug"-check in get_prices().
MARGE_EERSTE_KOERS_DAGEN = 5

ETF_BRON_PROVIDER = "provider_csv"
_basis_cache = {}
_basis_cache_lock = threading.Lock()


def _haal_portfolio_basis(code, forceer_vers=False, verversen=True):
    """(naam, split-gecorrigeerde transacties_df, price_data), of (None, None, None).
    Een cache-hit negeert `verversen`; aanroepers met verversen=False wissen de cache eerst."""
    nu = time.time()
    if not forceer_vers:
        with _basis_cache_lock:
            cached = _basis_cache.get(code)
        if cached and (nu - cached["op"]) < _BASIS_CACHE_TTL_SECONDEN:
            # get_prices() wordt overgeslagen, dus de bewaarde meldingen opnieuw doorgeven.
            meld_opnieuw(cached.get("diagnostiek"))
            return cached["naam"], cached["transacties_df"], cached["price_data"]

    meldingen_voor = haal_meldingen()
    with meet_tijd(f"basis_ophalen_db (code={code})"):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
        result = cur.fetchone()
        if result is None:
            cur.close()
            conn.close()
            return None, None, None
        naam = result[0]

        cur.execute(
            "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten, waarde_eur, tijd "
            "FROM transacties WHERE code = %s",
            (code,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten", "waarde_eur", "tijd"],
    )
    transacties_df["transactiekosten"] = transacties_df["transactiekosten"].astype(float)
    transacties_df["waarde_eur"] = transacties_df["waarde_eur"].astype(float)

    with meet_tijd("basis_split_correctie"):
        transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    with meet_tijd(f"basis_koersen_ophalen ({len(tickers)} ticker(s))"):
        price_data = get_prices(tickers, start_date, verversen=verversen) if tickers else pd.DataFrame()
    _meld_koersdekking(transacties_df, price_data)

    with _basis_cache_lock:
        _basis_cache[code] = {
            "naam": naam, "transacties_df": transacties_df, "price_data": price_data, "op": nu,
            # Laadtijden niet: bij een cache-hit is die tijd niet besteed.
            "diagnostiek": [m for m in meldingen_sinds(meldingen_voor)
                            if m["categorie"] != CATEGORIE_LAADTIJDEN],
        }

    return naam, transacties_df, price_data


def _meld_koersdekking(transacties_df, price_data):
    """Meldt tickers waarvan de koersreeks pas na de eerste transactie begint: tot dan waarde 0, inleg wel."""
    if price_data is None or price_data.empty:
        return
    echte = transacties_df.dropna(subset=["ticker"])
    echte = echte[~echte.apply(_is_corporate_action_row, axis=1)] if not echte.empty else echte
    for ticker, groep in echte.groupby("ticker"):
        if ticker not in price_data.columns:
            continue  # al gemeld in get_prices() ("geen koersdata")
        eerste_koers = price_data[ticker].first_valid_index()
        eerste_transactie = pd.Timestamp(groep["datum"].min())
        if eerste_koers is None or eerste_koers <= eerste_transactie + pd.Timedelta(days=MARGE_EERSTE_KOERS_DAGEN):
            continue
        meld(CATEGORIE_KOERSEN, LET_OP,
             f"Koersen voor '{ticker}' beginnen pas op {pd.Timestamp(eerste_koers).strftime('%Y-%m-%d')}, de "
             f"eerste transactie was op {eerste_transactie.strftime('%Y-%m-%d')}: tot de eerste koers telt deze "
             f"positie met waarde 0 mee, terwijl de inleg al meetelt.",
             sleutel=f"koers_later:{ticker}")


def _meld_etf_holdings(land_sector_verdeling):
    """Meldt per ETF de holdings-bron. Zonder holdings is land_bron toch yfinance_top10, met land 100% Unknown."""
    for ticker, info in (land_sector_verdeling or {}).get("per_etf", {}).items():
        land = info.get("land") or {}
        if set(land) <= {"Unknown"}:
            niveau, tekst = LET_OP, "geen holdings met landinformatie; land, top-bedrijven en ETF-overlap ontbreken voor deze ETF."
        elif info.get("land_bron") == ETF_BRON_PROVIDER:
            niveau, tekst = GOED, "volledige holdings van de fondsaanbieder."
        else:
            niveau, tekst = INFO, ("alleen de top-10 holdings via Yahoo; top-bedrijven en ETF-overlap zijn voor "
                                   "deze ETF onvolledig.")
        meld(CATEGORIE_ETF_HOLDINGS, niveau, f"'{ticker}': {tekst}", sleutel=f"etf:{ticker}")


def _wis_portfolio_basis_cache(code):
    """Aanroepen ná elke wijziging aan de transacties van `code`."""
    with _basis_cache_lock:
        _basis_cache.pop(code, None)


def _laad_split_gecorrigeerde_transacties(code):
    """Zonder koersen. Split-correctie over alle transacties: corporate-action-rijen hangen aan de ISIN."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return None

    cur.execute(
        "SELECT datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, echte_naam, transactiekosten, waarde_eur, tijd "
        "FROM transacties WHERE code = %s",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    transacties_df = pd.DataFrame(
        rows,
        columns=["datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur", "echte_naam", "transactiekosten", "waarde_eur", "tijd"],
    )
    return compute_split_adjusted_shares(transacties_df)


def _laad_transacties_en_resultaat(code):
    """(transacties_df, resultaat); resultaat None zonder koersdata, (None, None) als de code niet bestaat."""
    transacties_df = _laad_split_gecorrigeerde_transacties(code)
    if transacties_df is None:
        return None, None

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date)
    if price_data.empty:
        return transacties_df, None

    resultaat = compute_value_over_time(transacties_df, price_data)
    return transacties_df, resultaat


def _ticker_zekerheid_groepen(code):
    """[((isin, beurs), {naam, echte_naam, beurs, isin, transacties})] zonder corporate-action-rijen, of None.
    Zoek op echte_naam: product kan een bijnaam zijn."""
    naam_portfolio, transacties_df, _price_data = _haal_portfolio_basis(code)
    if naam_portfolio is None:
        return None

    # De basis-query sorteert niet.
    transacties_df = transacties_df.sort_values(["isin", "datum"])

    per_isin_beurs = {}
    for _, rij in transacties_df.iterrows():
        isin, product, echte_naam, beurs, datum, koers = (
            rij["isin"], rij["product"], rij["echte_naam"], rij["beurs"], rij["datum"], rij["koers"],
        )
        if _is_corporate_action_row({"beurs": beurs, "product": product}):
            continue
        groep = per_isin_beurs.setdefault(
            (isin, beurs), {"naam": product, "echte_naam": echte_naam, "beurs": beurs, "isin": isin, "transacties": []}
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    return list(per_isin_beurs.items())


def build_portfolio_response(code, verversen=True):
    naam, transacties_df, price_data = _haal_portfolio_basis(code, verversen=verversen)
    if naam is None:
        return None
    return analyze_transacties_kern(transacties_df, code, naam, verversen=verversen, prijs_data_al_klaar=price_data)


def analyze_transacties_kern(transacties_df, code, naam, verversen=True, prijs_data_al_klaar=None):
    """Met `prijs_data_al_klaar` moet transacties_df al split-gecorrigeerd zijn."""
    mem_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None

    tickers = transacties_df["ticker"].dropna().unique().tolist()

    if prijs_data_al_klaar is not None:
        price_data = prijs_data_al_klaar
    else:
        with meet_tijd("split_correctie_kern"):
            transacties_df = compute_split_adjusted_shares(transacties_df)
        start_date = transacties_df["datum"].min()
        with meet_tijd(f"koersen_ophalen_kern ({len(tickers)} ticker(s))"):
            price_data = get_prices(tickers, start_date, verversen=verversen)
        _meld_koersdekking(transacties_df, price_data)

    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None}

    laatste_koersdatum, laatst_opgehaald_op = get_laatste_prijs_update(tickers)

    resultaat = compute_value_over_time(transacties_df, price_data)
    per_ticker = compute_per_ticker(transacties_df, price_data)
    per_ticker_aankoop = compute_per_ticker_koers_en_aankopen(transacties_df, price_data)

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

    # Leest alleen de prijscheck-cache: normaal geen nieuwe Yahoo-calls.
    ticker_waarschuwingen = ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen)

    if resource:
        mem_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(
            f"[memory] portfolio {code}: RSS {mem_start/1024:.0f}MB -> {mem_end/1024:.0f}MB "
            f"(+{(mem_end-mem_start)/1024:.0f}MB), {len(tickers)} ticker(s), "
            f"{len(price_data.index) if not price_data.empty else 0} handelsdagen"
        )

    # Bij 'niet opslaan' is code None: geen dividendhistorie.
    with meet_tijd("dividend_samenvatting"):
        dividend_data = bereken_dividend_samenvatting(code) if code else None
    dividend_per_ticker = (
        {d["ticker"]: d["totaal_netto"] for d in dividend_data["per_ticker"]}
        if dividend_data else {}
    )
    statistieken = bereken_statistieken(
        transacties_df, price_data, resultaat,
        dividend_per_ticker=dividend_per_ticker, ticker_namen=ticker_namen,
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
        "per_ticker_aankoop": per_ticker_aankoop,
        "statistieken": statistieken,
        "tickers": [
            {
                "ticker": t, "naam": ticker_namen.get(t, t), "echte_naam": echte_namen.get(t, t),
                "nog_in_bezit": per_ticker[t]["nog_in_bezit"],
            }
            for t in _sorteer_tickers_voor_dropdown(per_ticker)
        ],
        "ticker_waarschuwingen": ticker_waarschuwingen,
        "laatste_koersdatum": laatste_koersdatum.strftime("%Y-%m-%d") if laatste_koersdatum else None,
        # 'Z': Neon draait in UTC (zie CLAUDE.md: Data en rekenen).
        "laatst_opgehaald_op": laatst_opgehaald_op.isoformat() + "Z" if laatst_opgehaald_op else None,
    }


def analyze_transacties_verrijking(transacties_df, code, prijs_data_al_klaar=None):
    """Met `prijs_data_al_klaar` moet transacties_df al split-gecorrigeerd zijn."""
    tickers = transacties_df["ticker"].dropna().unique().tolist()

    if prijs_data_al_klaar is not None:
        price_data = prijs_data_al_klaar
    else:
        with meet_tijd("split_correctie_verrijking"):
            transacties_df = compute_split_adjusted_shares(transacties_df)
        start_date = transacties_df["datum"].min()
        with meet_tijd(f"koersen_ophalen_verrijking ({len(tickers)} ticker(s))"):
            price_data = get_prices(tickers, start_date)

    if price_data.empty:
        return {
            "verdeling": [], "verdeling_samenvatting": bereken_verdeling_samenvatting([]),
            "land_sector_verdeling": {}, "bedrijven_verdeling": {}, "etf_overlap": {},
        }

    ticker_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"], keep="last")
        .set_index("ticker")["product"]
        .to_dict()
    )

    huidige_holdings = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    laatste_prijzen = price_data.iloc[-1]

    with meet_tijd("verrijking_totaal"):
        with meet_tijd("verrijking_classificatie_en_cache_warm"):
            is_etf_map = classify_tickers(list(huidige_holdings.index))
            _verwarm_land_sector_cache_parallel(list(huidige_holdings.index), is_etf_map)

        with meet_tijd("verrijking_land_sector"):
            land_sector_verdeling = compute_land_sector_verdeling(transacties_df, price_data, is_etf_map)

        with meet_tijd("verrijking_bedrijven"):
            # Tot het maximum meeleveren; de frontend kiest zelf hoeveel te tonen.
            bedrijven_verdeling = bereken_bedrijven_verdeling(
                transacties_df, price_data, is_etf_map, top_n=BEDRIJVEN_TOP_N_MAX,
            )

        with meet_tijd("verrijking_etf_overlap"):
            etf_overlap = bereken_etf_overlap(transacties_df, price_data, is_etf_map)
    _meld_etf_holdings(land_sector_verdeling)

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
            "is_etf": is_etf_map.get(ticker, False),
        })

    verdeling = _sorteer_verdeling_groot_naar_klein(verdeling)

    return {
        "verdeling": verdeling,
        "verdeling_samenvatting": bereken_verdeling_samenvatting(verdeling),
        "land_sector_verdeling": land_sector_verdeling,
        "bedrijven_verdeling": bedrijven_verdeling,
        "etf_overlap": etf_overlap,
    }


def analyze_transacties(transacties_df, code, naam):
    """Kern + verrijking in één keer, voor 'niet opslaan' (geen code voor een latere /verrijking)."""
    resultaat = analyze_transacties_kern(transacties_df, code, naam)
    if resultaat.get("chart_data") is None:
        return resultaat
    resultaat.update(analyze_transacties_verrijking(transacties_df, code))
    return resultaat
