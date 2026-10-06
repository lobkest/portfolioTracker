"""Laag tussen routes en domeinmodules: basis ophalen (met korte cache) en de dashboard-respons bouwen."""
import threading
import time

import pandas as pd
try:
    # Unix-only: de [memory]-meting werkt dus alleen op Render, niet lokaal op Windows.
    import resource
except ImportError:
    resource = None


from db import (
    db_get_portfolio_naam_en_transacties, db_get_laatste_koers_update, db_get_koers_splits, TRANSACTIE_KOLOMMEN,
    db_laad_product_per_ticker, db_get_ticker_details, db_get_order_id_rijen, ORDER_ID_KOLOMMEN,
    db_get_cached_openfigi_voor_isins,
)
from diagnostiek_checks import (
    check_ontbrekende_kolommen, check_posities_zonder_ticker, check_synthetische_order_ids,
    check_corporate_action_rijen, check_isin_wissels, check_transactiekoers_vs_rekenkoers, check_waarde_vs_inleg,
    check_dagsprong, tickers_met_koersafwijking, check_koers_stilstand, _naam_per_ticker, ticker_bevindingen,
    check_valuta_consistentie,
)
from debug_utils import meet_tijd
from diagnostiek import (
    haal_meldingen, meldingen_sinds, meld_opnieuw, meld,
    CATEGORIE_LAADTIJDEN, CATEGORIE_DATA, CATEGORIE_PLAUSIBILITEIT, CATEGORIE_TICKERS, CATEGORIE_SPLITS, CATEGORIE_KOERSEN, CATEGORIE_ETF_HOLDINGS, GOED, INFO, LET_OP,
)
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl
from prijzen import get_prices
from portfolio_calc import (
    compute_split_adjusted_shares, compute_value_over_time, compute_per_ticker,
    compute_per_ticker_koers_en_aankopen, bepaal_split_boekingen, meld_split_koppeling,
)
from split_correctie import bepaal_effectieve_datums, continue_reeks, vind_wisselparen
from ticker_zekerheid import ticker_waarschuwingen_voor_transacties
from dividend import bereken_dividend_samenvatting
from statistieken import bereken_statistieken
from portfolio_verdeling import (
    compute_land_sector_verdeling, compute_valuta_verdeling, bereken_bedrijven_verdeling, bereken_etf_overlap,
    _sorteer_verdeling_groot_naar_klein, _sorteer_tickers_voor_dropdown,
    bereken_verdeling_samenvatting, BEDRIJVEN_TOP_N_MAX, bereken_land_dekking, DREMPEL_ONBEKEND_LAND_PCT,
)
from ticker_classificatie import (
    classify_tickers, _verwarm_land_sector_cache_parallel, haal_long_names, get_etf_holdings_uit_cache,
)
from naam_verkorting import kies_korte_namen
from etf_proxy import land_proxies_voor_etfs


class YahooNamenOnbeschikbaar(Exception):
    pass


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
        naam, rows = db_get_portfolio_naam_en_transacties(code)
        if naam is None:
            return None, None, None

    transacties_df = pd.DataFrame(rows, columns=TRANSACTIE_KOLOMMEN)
    transacties_df["transactiekosten"] = transacties_df["transactiekosten"].astype(float)
    transacties_df["waarde_eur"] = transacties_df["waarde_eur"].astype(float)
    with meet_tijd("basis_datakwaliteit"):
        _meld_datakwaliteit(code, transacties_df)

    with meet_tijd("basis_split_correctie"):
        transacties_df = compute_split_adjusted_shares(transacties_df)

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    with meet_tijd(f"basis_koersen_ophalen ({len(tickers)} ticker(s))"):
        price_data = get_prices(tickers, start_date, verversen=verversen, gesloten_sinds=_gesloten_sinds(transacties_df)) if tickers else pd.DataFrame()
    with meet_tijd("basis_effectieve_datums_en_koersdekking"):
        transacties_df = _pas_effectieve_datums_toe(transacties_df)
        _meld_koersdekking(transacties_df, price_data)

    with _basis_cache_lock:
        _basis_cache[code] = {
            "naam": naam, "transacties_df": transacties_df, "price_data": price_data, "op": time.time(),
            # Laadtijden niet: bij een cache-hit is die tijd niet besteed.
            "diagnostiek": [m for m in meldingen_sinds(meldingen_voor)
                            if m["categorie"] != CATEGORIE_LAADTIJDEN],
        }

    return naam, transacties_df, price_data


def _gesloten_sinds(transacties_df):
    """{ticker: datum laatste transactie} voor posities zonder stukken; hun koersen hoeven niet meer ververst."""
    met_ticker = transacties_df.dropna(subset=["ticker"])
    aantal = met_ticker["aantal"].astype(float).groupby(met_ticker["ticker"]).sum()
    laatste = pd.to_datetime(met_ticker["datum"]).groupby(met_ticker["ticker"]).max()
    return {ticker: laatste[ticker] for ticker, stuks in aantal.items() if abs(stuks) <= 1e-6}


def _pas_effectieve_datums_toe(transacties_df):
    """Aantallen tellen mee vanaf Yahoo's splitdatum. Aanroepen ná get_prices(): de splits staan dan in de koersencache."""
    tickers = transacties_df["ticker"].dropna().unique().tolist()
    if not tickers:
        return transacties_df
    boekingen = bepaal_split_boekingen(transacties_df)
    splits_per_ticker = db_get_koers_splits(tickers)
    transacties_df, resultaat = bepaal_effectieve_datums(transacties_df, boekingen, splits_per_ticker)
    meld_split_koppeling(resultaat)
    # Hergebruikt door analyze_transacties_kern(): scheelt een tweede database-read.
    transacties_df.attrs["koers_splits"] = splits_per_ticker
    return transacties_df


def continue_koersreeks(ticker, ruwe_reeks):
    """Zonder sprongen op splitdagen: voor vergelijkingen die stukken kopen tegen de koers van toen en later waarderen."""
    return continue_reeks(ruwe_reeks, db_get_koers_splits([ticker]).get(ticker, {}))


def bepaal_koersstatus(tickers, tickers_met_koers, onvolledig, namen):
    """{koersen_compleet, koersen_onvolledig, koersen_ontbreken}; de lijsten als [{ticker, naam}].
    Onvolledig = nog niet opgehaald (tijdbudget, heropenen helpt); ontbreken = Yahoo gaf geen koersen."""
    def met_naam(lijst):
        return [{"ticker": t, "naam": namen.get(t, t)} for t in sorted(lijst)]
    onvolledig = set(onvolledig)
    ontbreken = {t for t in tickers if t not in tickers_met_koers and t not in onvolledig}
    return {
        "koersen_compleet": not onvolledig and not ontbreken,
        "koersen_onvolledig": met_naam(onvolledig),
        "koersen_ontbreken": met_naam(ontbreken),
    }


def splits_voor_grafiek(splits):
    """{iso_datum: ratio} -> [{datum, ratio}] op datum."""
    return [{"datum": datum, "ratio": ratio} for datum, ratio in sorted(splits.items())]


def _meld_datakwaliteit(code, transacties_df):
    """Diagnostiek mag het laden nooit breken."""
    try:
        bevindingen = (
            check_ontbrekende_kolommen(transacties_df)
            + check_posities_zonder_ticker(transacties_df)
            + check_synthetische_order_ids(pd.DataFrame(db_get_order_id_rijen(code), columns=ORDER_ID_KOLOMMEN))
            + check_corporate_action_rijen(transacties_df)
        )
        for b in bevindingen:
            meld(CATEGORIE_DATA, b["niveau"], b["tekst"], sleutel=b["sleutel"])
        for b in check_isin_wissels(transacties_df):
            meld(CATEGORIE_SPLITS, b["niveau"], b["tekst"], sleutel=b["sleutel"])
    except Exception as e:
        print(f"[diagnostiek] WARN datakwaliteit niet gecontroleerd ({e!a})")


def _meld_tickers(transacties_df, ticker_waarschuwingen, prijs_checks=None):
    """Alleen caches en de prijschecks van de lichte check, geen Yahoo- of OpenFIGI-call. Mag het laden nooit breken."""
    try:
        met_ticker = transacties_df.dropna(subset=["ticker"])
        tickers = met_ticker["ticker"].unique().tolist()
        isins = met_ticker["isin"].dropna().unique().tolist()
        bevindingen = ticker_bevindingen(
            transacties_df, db_get_ticker_details(tickers), db_get_cached_openfigi_voor_isins(isins), ticker_waarschuwingen,
            prijs_checks)
        for b in bevindingen:
            meld(CATEGORIE_TICKERS, b["niveau"], b["tekst"], sleutel=b["sleutel"])
    except Exception as e:
        print(f"[diagnostiek] WARN tickers niet gecontroleerd ({e!a})")


def meld_valuta_consistentie(excel_df, ticker_per_isin_beurs):
    """Alleen direct na een upload, als de Excel er nog is."""
    try:
        tickers = sorted({t for t in ticker_per_isin_beurs.values() if t})
        valuta = {t: d.get("valuta") for t, d in db_get_ticker_details(tickers).items()}
        for b in check_valuta_consistentie(excel_df, ticker_per_isin_beurs, valuta):
            meld(CATEGORIE_TICKERS, b["niveau"], b["tekst"], sleutel=b["sleutel"])
    except Exception as e:
        print(f"[diagnostiek] WARN valuta-consistentie niet gecontroleerd ({e!a})")


def ticker_per_isin_beurs_uit_basis(code):
    """{(isin, beurs): ticker} uit de (net gebouwde, dus gecachete) basis van `code`."""
    _naam, transacties_df, _prijzen = _haal_portfolio_basis(code)
    if transacties_df is None:
        return {}
    rijen = transacties_df.dropna(subset=["ticker", "isin"])
    return {(i, b): t for i, b, t in zip(rijen["isin"], rijen["beurs"], rijen["ticker"])}


def _meld_plausibiliteit(transacties_df, price_data, per_ticker):
    """Diagnostiek mag het laden nooit breken."""
    try:
        bevindingen = (
            check_transactiekoers_vs_rekenkoers(transacties_df, price_data)
            + check_waarde_vs_inleg(per_ticker, _naam_per_ticker(transacties_df))
            + check_dagsprong(transacties_df, per_ticker, tickers_met_koersafwijking(transacties_df, price_data))
        )
        for b in bevindingen:
            meld(CATEGORIE_PLAUSIBILITEIT, b["niveau"], b["tekst"], sleutel=b["sleutel"])
    except Exception as e:
        print(f"[diagnostiek] WARN plausibiliteit niet gecontroleerd ({e!a})")


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
             f"Koersen voor '{ticker}' beginnen pas op {formatteer_datum_nl(eerste_koers)}, de "
             f"eerste transactie was op {formatteer_datum_nl(eerste_transactie)}: tot de eerste koers telt deze "
             f"positie met waarde 0 mee, terwijl de inleg al meetelt.",
             sleutel=f"koers_later:{ticker}")
    try:
        for b in check_koers_stilstand(transacties_df, price_data):
            meld(CATEGORIE_KOERSEN, b["niveau"], b["tekst"], sleutel=b["sleutel"])
    except Exception as e:
        print(f"[diagnostiek] WARN koersstilstand niet gecontroleerd ({e!a})")


def _pct_nl(waarde):
    return f"{waarde:.1f}".replace(".", ",")


def _meld_etf_onbekend_land(ticker, info, naam):
    """Alleen-cache (geen Yahoo): de holdings staan er net in via compute_land_sector_verdeling()."""
    if (info.get("land") or {}).get("Unknown", 0.0) * 100 <= DREMPEL_ONBEKEND_LAND_PCT:
        return
    try:
        holdings = get_etf_holdings_uit_cache(ticker)
    except Exception as e:
        print(f"[diagnostiek] WARN holdings van '{ticker}' niet uit de cache ({e!a})")
        holdings = []
    dekking = bereken_land_dekking(holdings)
    if dekking["onbekend_pct"] <= DREMPEL_ONBEKEND_LAND_PCT:
        return
    meld(CATEGORIE_ETF_HOLDINGS, LET_OP,
         f"{naam}: {_pct_nl(dekking['onbekend_pct'])}% van het land onbekend "
         f"(bron: {info.get('land_bron')}, dekking {_pct_nl(dekking['dekking_pct'])}%)",
         sleutel=f"etf_land_onbekend:{ticker}",
         tabel={"kolommen": ["Bedrijf", "Weging", "Land", "Sector"], "rijen": dekking["rijen"]})


def _pp_nl(waarde):
    return f"{waarde:.2f}".replace(".", ",") if waarde is not None else None


def _meld_etf_land_proxy(ticker, proxy, naam):
    vergelijking = proxy.get("vergelijking") or {}
    rijen = [
        [r["bedrijf"], f"{_pp_nl(r['bron_pct'])}%",
         f"{_pp_nl(r['proxy_pct'])}%" if r["proxy_pct"] is not None else "niet gevonden",
         _pp_nl(r["verschil_pp"]) or "–"]
        for r in vergelijking.get("rijen") or []
    ]
    tabel = {"kolommen": ["Bedrijf", "Bronfonds", "Proxy", "Verschil (pp)"], "rijen": rijen} if rijen else None
    if proxy.get("proxy_isin"):
        meld(CATEGORIE_ETF_HOLDINGS, INFO,
             f"{naam}: land benaderd via {proxy['proxy_naam']} (top-10 wijkt max. "
             f"{_pp_nl(proxy['max_afwijking_pp'])} pp af).",
             sleutel=f"etf_land_proxy:{ticker}", tabel=tabel)
    elif vergelijking.get("kandidaat_naam"):
        meld(CATEGORIE_ETF_HOLDINGS, LET_OP,
             f"{naam}: geen iShares-proxy voor het land gevonden. Beste kandidaat "
             f"{vergelijking['kandidaat_naam']}: {vergelijking.get('reden')}.",
             sleutel=f"etf_land_proxy:{ticker}", tabel=tabel)
    else:
        meld(CATEGORIE_ETF_HOLDINGS, LET_OP,
             f"{naam}: geen iShares-proxy voor het land gevonden ({vergelijking.get('reden')}).",
             sleutel=f"etf_land_proxy:{ticker}")


def _bepaal_land_proxies(transacties_df, is_etf_map):
    """{ticker: etf_proxy-rij}; {} bij een fout: de proxy mag de verrijking nooit breken."""
    rijen = transacties_df.dropna(subset=["ticker", "isin"]).drop_duplicates(subset=["ticker"], keep="last")
    isin_per_etf = {t: i for t, i in zip(rijen["ticker"], rijen["isin"]) if is_etf_map.get(t, False)}
    try:
        return land_proxies_voor_etfs(isin_per_etf)
    except Exception as e:
        print(f"[etf-proxy] WARN land-proxy niet bepaald ({e!a})")
        return {}


def _meld_etf_holdings(land_sector_verdeling, ticker_namen=None, land_proxies=None):
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
        naam = (ticker_namen or {}).get(ticker, ticker)
        proxy = (land_proxies or {}).get(ticker)
        if proxy:
            _meld_etf_land_proxy(ticker, proxy, naam)
        if not (proxy and proxy.get("proxy_isin")):
            _meld_etf_onbekend_land(ticker, info, naam)


def _wis_portfolio_basis_cache(code):
    """Aanroepen ná elke wijziging aan de transacties van `code`."""
    with _basis_cache_lock:
        _basis_cache.pop(code, None)


def _laad_split_gecorrigeerde_transacties(code):
    """Zonder koersen. Split-correctie over alle transacties: corporate-action-rijen hangen aan de ISIN."""
    naam, rows = db_get_portfolio_naam_en_transacties(code)
    if naam is None:
        return None

    transacties_df = pd.DataFrame(rows, columns=TRANSACTIE_KOLOMMEN)
    return compute_split_adjusted_shares(transacties_df)


def _laad_transacties_en_resultaat(code):
    """(transacties_df, resultaat); resultaat None zonder koersdata, (None, None) als de code niet bestaat."""
    transacties_df = _laad_split_gecorrigeerde_transacties(code)
    if transacties_df is None:
        return None, None

    tickers = transacties_df["ticker"].dropna().unique().tolist()
    start_date = transacties_df["datum"].min()
    price_data = get_prices(tickers, start_date, gesloten_sinds=_gesloten_sinds(transacties_df))
    if price_data.empty:
        return transacties_df, None

    transacties_df = _pas_effectieve_datums_toe(transacties_df)
    resultaat = compute_value_over_time(transacties_df, price_data)
    return transacties_df, resultaat


def _ticker_zekerheid_groepen(code):
    """[((isin, beurs), {naam, echte_naam, beurs, isin, transacties})] zonder corporate-action- en wisselrijen, of None.
    Leest alleen de transacties (geen koersen, geen split-correctie). Zoek op echte_naam: product kan een bijnaam zijn."""
    naam_portfolio, rows = db_get_portfolio_naam_en_transacties(code)
    if naam_portfolio is None:
        return None

    transacties_df = pd.DataFrame(rows, columns=TRANSACTIE_KOLOMMEN)
    for kolom in ("aantal", "koers", "transactiekosten"):
        transacties_df[kolom] = transacties_df[kolom].astype(float)
    # Een omboeking bij een ISIN-wissel is geen markttransactie: koers = slot van de dag ervoor.
    paren, _onduidelijk = vind_wisselparen(transacties_df)
    wisselrijen = {label for paar in paren for label in paar.oud_rijen + paar.nieuw_rijen}

    # De basis-query sorteert niet.
    transacties_df = transacties_df.sort_values(["isin", "datum"])

    per_isin_beurs = {}
    for label, rij in transacties_df.iterrows():
        isin, product, echte_naam, beurs, datum, koers = (
            rij["isin"], rij["product"], rij["echte_naam"], rij["beurs"], rij["datum"], rij["koers"],
        )
        if _is_corporate_action_row({"beurs": beurs, "product": product}) or label in wisselrijen:
            continue
        groep = per_isin_beurs.setdefault(
            (isin, beurs), {"naam": product, "echte_naam": echte_naam, "beurs": beurs, "isin": isin, "transacties": []}
        )
        groep["transacties"].append({"datum": datum, "koers": koers})

    return list(per_isin_beurs.items())


def bepaal_korte_naam_voorstellen(code):
    """[{ticker, huidig, long_name, voorstel}]; beide None zonder Yahoo-longName. Schrijft niets weg."""
    huidig = db_laad_product_per_ticker(code)
    tickers = sorted(huidig)
    if not tickers:
        return []

    long_names = haal_long_names(tickers)
    if not any(long_names.values()):
        raise YahooNamenOnbeschikbaar()

    details = db_get_ticker_details(tickers)
    voorstellen = kies_korte_namen({
        t: {"long_name": long_names[t], "fund_family": details.get(t, {}).get("fund_family")} for t in tickers
    })
    return [{"ticker": t, "huidig": huidig[t], "long_name": long_names[t], "voorstel": voorstellen.get(t)} for t in tickers]


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
            price_data = get_prices(tickers, start_date, verversen=verversen, gesloten_sinds=_gesloten_sinds(transacties_df))
        transacties_df = _pas_effectieve_datums_toe(transacties_df)
        _meld_koersdekking(transacties_df, price_data)

    ticker_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"], keep="last")
        .set_index("ticker")["product"]
        .to_dict()
    )
    koersstatus = bepaal_koersstatus(
        tickers, set(price_data.columns), price_data.attrs.get("koersen_onvolledig", []), ticker_namen)
    if price_data.empty:
        return {"code": code, "naam": naam, "chart_data": None, **koersstatus}

    laatste_koersdatum, laatst_opgehaald_op = db_get_laatste_koers_update(tickers)

    with meet_tijd("kern_berekeningen"):
        resultaat = compute_value_over_time(transacties_df, price_data)
        per_ticker = compute_per_ticker(transacties_df, price_data)
        _meld_plausibiliteit(transacties_df, price_data, per_ticker)
        per_ticker_aankoop = compute_per_ticker_koers_en_aankopen(transacties_df, price_data)
    splits_per_ticker = transacties_df.attrs.get("koers_splits")
    if splits_per_ticker is None:
        splits_per_ticker = db_get_koers_splits(list(per_ticker_aankoop))
    for ticker, reeks in per_ticker_aankoop.items():
        reeks["splits"] = splits_voor_grafiek(splits_per_ticker.get(ticker, {}))

    echte_namen = (
        transacties_df.dropna(subset=["ticker"])
        .drop_duplicates(subset=["ticker"])
        .set_index("ticker")["echte_naam"]
        .to_dict()
    )

    # Leest alleen de prijscheck-cache: normaal geen nieuwe Yahoo-calls.
    with meet_tijd(f"kern_ticker_waarschuwingen ({len(tickers)} ticker(s))"):
        ticker_waarschuwingen, prijs_checks = ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen)
    with meet_tijd("kern_meld_tickers"):
        _meld_tickers(transacties_df, ticker_waarschuwingen, prijs_checks)

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
    with meet_tijd("kern_statistieken"):
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
        **koersstatus,
    }


def analyze_transacties_verrijking(transacties_df, code, prijs_data_al_klaar=None, gebruik_proxy=True):
    """Met `prijs_data_al_klaar` moet transacties_df al split-gecorrigeerd zijn. Zonder `gebruik_proxy` geen
    land-proxy (geen screener/CSV's, geen etf_proxy-cache): land dan uit de eigen top-10."""
    tickers = transacties_df["ticker"].dropna().unique().tolist()

    if prijs_data_al_klaar is not None:
        price_data = prijs_data_al_klaar
    else:
        with meet_tijd("split_correctie_verrijking"):
            transacties_df = compute_split_adjusted_shares(transacties_df)
        start_date = transacties_df["datum"].min()
        with meet_tijd(f"koersen_ophalen_verrijking ({len(tickers)} ticker(s))"):
            price_data = get_prices(tickers, start_date, gesloten_sinds=_gesloten_sinds(transacties_df))

    if price_data.empty:
        return {
            "verdeling": [], "verdeling_samenvatting": bereken_verdeling_samenvatting([]),
            "land_sector_verdeling": {}, "valuta_verdeling": {}, "bedrijven_verdeling": {}, "etf_overlap": {},
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

        with meet_tijd("verrijking_land_proxy"):
            land_proxies = _bepaal_land_proxies(transacties_df, is_etf_map) if gebruik_proxy else {}

        with meet_tijd("verrijking_land_sector"):
            land_sector_verdeling = compute_land_sector_verdeling(
                transacties_df, price_data, is_etf_map, land_proxies=land_proxies)

        with meet_tijd("verrijking_valuta"):
            valuta_verdeling = compute_valuta_verdeling(transacties_df, price_data)

        with meet_tijd("verrijking_bedrijven"):
            # Tot het maximum meeleveren; de frontend kiest zelf hoeveel te tonen.
            bedrijven_verdeling = bereken_bedrijven_verdeling(
                transacties_df, price_data, is_etf_map, top_n=BEDRIJVEN_TOP_N_MAX,
            )

        with meet_tijd("verrijking_etf_overlap"):
            etf_overlap = bereken_etf_overlap(transacties_df, price_data, is_etf_map)
    _meld_etf_holdings(land_sector_verdeling, ticker_namen, land_proxies)

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
        "valuta_verdeling": valuta_verdeling,
        "bedrijven_verdeling": bedrijven_verdeling,
        "etf_overlap": etf_overlap,
    }


def analyze_transacties(transacties_df, code, naam):
    """Kern + verrijking in één keer, voor 'niet opslaan' (geen code voor een latere /verrijking).
    Zonder land-proxy: die zoektocht kost bij een koude cache ~9 s binnen /upload (gunicorn-timeout)."""
    resultaat = analyze_transacties_kern(transacties_df, code, naam)
    if resultaat.get("chart_data") is None:
        return resultaat
    resultaat.update(analyze_transacties_verrijking(transacties_df, code, gebruik_proxy=False))
    return resultaat
