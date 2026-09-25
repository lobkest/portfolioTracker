"""
Portfolio-orchestratie: de laag tussen de Flask-routes (app.py) en de
taakfuncties in de domeinmodules. Haalt transacties+koersen op (met een
korte-levende in-process cache tegen dubbele fetches binnen één
portfolio-bezoek), en bouwt daaruit de dashboard-respons op (kern +
lui geladen verrijking).

Losgetrokken uit app.py.
"""
import threading
import time

import pandas as pd
try:
    # Unix-only (o.a. niet op Windows, waar dit project lokaal draait --
    # zie CLAUDE.md). Alleen gebruikt voor de [memory]-diagnostiek hieronder,
    # die dus stilzwijgend wegvalt bij lokaal draaien op Windows en gewoon
    # werkt op Render (Linux/gunicorn), waar de metingen om gaan.
    import resource
except ImportError:
    resource = None


from db import get_db_connection, get_laatste_prijs_update
from debug_utils import meet_tijd
from diagnostiek import haal_meldingen, meldingen_sinds, meld_opnieuw
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


# Korte-levende, in-process cache voor de "basis" van een portfolio-bezoek
# (transacties + split-correctie + koersen) -- zonder deze cache haalt elke
# losse frontend-aanroep binnen hetzelfde bezoek (kern via
# build_portfolio_response, verrijking via portfolio_verrijking,
# ticker-zekerheid via _ticker_zekerheid_groepen) dezelfde transacties
# opnieuw uit Postgres op en herhaalt compute_split_adjusted_shares()/
# get_prices() vanaf nul, terwijl die data een paar seconden eerder al
# berekend is (zie opdracht performance-meting/dubbele-fetches). Dit is GEEN
# vervanging van de prijzen-cache in de database -- alleen een cache tussen
# de 2-3 requests van één portfolio-bezoek. TTL kort houden zodat een nieuw
# bezoek of een nieuwe upload snel weer verse data ziet.
#
# Werkt alleen binnen één gunicorn-worker (in-process dict) -- bij meerdere
# workers kan een opeenvolgende request toevallig bij een andere worker
# terechtkomen die de cache niet heeft; dan valt dat ene request gewoon
# terug op het oude (trage) gedrag, er gaat niets stuk.
_BASIS_CACHE_TTL_SECONDEN = 20
_basis_cache = {}
_basis_cache_lock = threading.Lock()


def _haal_portfolio_basis(code, forceer_vers=False, verversen=True):
    """Haalt (naam, transacties_df, price_data) op voor `code` -- gedeeld
    door build_portfolio_response(), portfolio_verrijking() en
    _ticker_zekerheid_groepen(), zodat die binnen hetzelfde portfolio-bezoek
    niet elk apart dezelfde SELECT + split-correctie + get_prices() doen.
    transacties_df is hier AL split-gecorrigeerd. Geeft (None, None, None)
    terug als de code niet bestaat.

    `verversen` wordt doorgegeven aan get_prices() (zie daar) en wordt ook
    in de cache-entry gestopt -- een cache-hit binnen de TTL kan dus in
    theorie data teruggeven die met een ander verversen-gedrag is opgehaald
    dan de huidige aanroep vraagt. Dat is bewust geaccepteerd: de enige
    aanroepers die verversen=False gebruiken (bijnaam/code wijzigen)
    wissen de cache expliciet vóór ze build_portfolio_response() aanroepen
    (zie _wis_portfolio_basis_cache), dus in de praktijk komt deze
    situatie niet voor binnen de TTL."""
    nu = time.time()
    if not forceer_vers:
        with _basis_cache_lock:
            cached = _basis_cache.get(code)
        if cached and (nu - cached["op"]) < _BASIS_CACHE_TTL_SECONDEN:
            # De code die de Diagnostiek-meldingen maakt (get_prices()) wordt
            # bij een hit overgeslagen -- de bij de miss bewaarde meldingen
            # opnieuw doorgeven, zodat ook deze request ze meestuurt.
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

    with _basis_cache_lock:
        _basis_cache[code] = {
            "naam": naam, "transacties_df": transacties_df, "price_data": price_data, "op": nu,
            "diagnostiek": meldingen_sinds(meldingen_voor),
        }

    return naam, transacties_df, price_data


def _wis_portfolio_basis_cache(code):
    """Cache-invalidatie -- aanroepen ná elke wijziging aan `code`'s
    transacties (nieuwe upload, bijnaam aanpassen/resetten, code wijzigen,
    portfolio verwijderen, of een geforceerde ticker-herberekening), zodat
    een volgend bezoek niet de oude data uit de cache terugkrijgt."""
    with _basis_cache_lock:
        _basis_cache.pop(code, None)


def _laad_split_gecorrigeerde_transacties(code):
    """Transacties van `code` met split-correctie, zonder koersen op te
    halen -- voor lui geladen endpoints die alleen de transacties nodig
    hebben (ticker-koers-bereik) of zelf koersen ophalen. Split-correctie
    over ALLE transacties van de code, niet per ticker gefilterd: de
    corporate-action-rijen hangen aan de ISIN, niet altijd aan de ticker.
    None als de code niet bestaat."""
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
    """Haalt transacties op voor `code`, past split-correctie toe en
    berekent de waarde-tijdreeks (resultaat) — gedeelde basis voor de lui
    geladen endpoints die op deze twee objecten verder rekenen
    (benchmark-vergelijking, rendement-over-tijd), zodat het hoofd-dashboard-
    antwoord (build_portfolio_response) dit niet standaard hoeft mee te
    sturen. Geeft (transacties_df, resultaat) terug; resultaat is None als
    er geen koersdata is. (None, None) als de code niet bestaat."""
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
    """
    Haalt de transacties van 'code' op en groepeert ze per (ISIN, Beurs) —
    gedeeld door de lijst-route en de per-positie-route van
    Ticker-zekerheid (app.py), zodat de groepeerlogica (en de corporate-
    action-rijen-filter) maar op één plek staat. Geeft None terug als de
    code niet bestaat, anders een lijst van ((isin, beurs), info)-tuples
    met info = {"naam", "echte_naam", "beurs", "isin", "transacties"}.

    Groeperen per (ISIN, Beurs), niet per ISIN alleen: dezelfde ISIN kan op
    meerdere beurzen genoteerd staan (bv. een fonds met een Amsterdam- én
    een Londen-notering) en dat zijn dan ECHT verschillende tickers met
    eigen koersen — alles onder één ISIN op een hoop gooien zou de
    steekproef van de ene notering vervuilen met transactiedatums/prijzen
    die bij de andere notering horen. echte_naam (niet product!) gaat naar
    de Yahoo-zoekopdracht: product kan een door de gebruiker aangepaste
    bijnaam zijn, en die is onbruikbaar als zoekterm.
    Corporate-action-/NON TRADEABLE-rijen (splits e.d.) horen niet als eigen
    "positie" in deze lijst -- zelfde check als elders in het project
    (transactie_utils._is_corporate_action_row), hier vóór het groeperen toegepast
    zodat zo'n rij nooit een kansloze eigen (ISIN, Beurs)-groep vormt.
    """
    naam_portfolio, transacties_df, _price_data = _haal_portfolio_basis(code)
    if naam_portfolio is None:
        return None

    # _haal_portfolio_basis() geeft transacties_df niet gegarandeerd terug in
    # (isin, datum)-volgorde (de oorspronkelijke query deed ORDER BY isin,
    # datum) -- hier alsnog sorteren zodat de volgorde binnen elke groep
    # ongewijzigd blijft.
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
    """
    Alles wat de Home-, Rendement-, Per-aandeel- en Statistieken-tabbladen
    nodig hebben — bewust ZONDER classify_tickers/land/sector/bedrijven-
    verdeling/ETF-overlap, want dat is het netwerk-zware deel dat bij een
    nieuwe, koude-cache-portfolio de meeste tijd kost. Die rest wordt lui opgehaald via
    analyze_transacties_verrijking() + de /verrijking-route.

    `prijs_data_al_klaar`: optioneel, al opgehaalde price_data -- als
    meegegeven wordt aangenomen dat transacties_df AL split-gecorrigeerd is
    (gebeurde dan al in _haal_portfolio_basis()) en worden de split-
    correctie + get_prices() hier overgeslagen. Gebruikt door
    build_portfolio_response(); de 'niet opslaan'-tak (analyze_transacties())
    laat dit weg en rekent alles zelf uit, zoals voorheen.
    """
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

    # Prijswaarschuwingen zichtbaar maken bij ELK bezoek (niet alleen direct
    # na de upload): ticker_waarschuwingen_voor_transacties() leest alleen
    # de al gecachete ticker_prijscheck-check (gevuld door find_ticker_met_
    # snelle_prijscheck bij upload), dus dit kost hier geen nieuwe Yahoo-
    # calls in het gangbare geval.
    ticker_waarschuwingen = ticker_waarschuwingen_voor_transacties(transacties_df, ticker_namen)

    if resource:
        mem_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(
            f"[memory] portfolio {code}: RSS {mem_start/1024:.0f}MB -> {mem_end/1024:.0f}MB "
            f"(+{(mem_end-mem_start)/1024:.0f}MB), {len(tickers)} ticker(s), "
            f"{len(price_data.index) if not price_data.empty else 0} handelsdagen"
        )

    # Bij de 'niet opslaan'-analyse (zie de niet_opslaan-tak in /upload) is
    # code None -- er is dan nooit dividendhistorie (die zit in de database),
    # dus gewoon leeg laten i.p.v. crashen.
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
        # 'Z'-suffix: bijgewerkt_op is een naive TIMESTAMP-kolom, maar Neon
        # draait in GMT/UTC (geverifieerd via CURRENT_SETTING('timezone')),
        # dus de opgeslagen waarde IS al UTC -- vandaar expliciet als
        # UTC-ISO-string meesturen i.p.v. de naive string kaal door te geven.
        "laatst_opgehaald_op": laatst_opgehaald_op.isoformat() + "Z" if laatst_opgehaald_op else None,
    }


def analyze_transacties_verrijking(transacties_df, code, prijs_data_al_klaar=None):
    """
    Het netwerk-zware deel: Verdeling (+ verdeling_samenvatting),
    Land/Sector, Top-bedrijven en ETF-overlap — lui opgevraagd via
    /api/portfolio/<code>/verrijking, ná de Home-pagina (zie
    analyze_transacties_kern).

    `prijs_data_al_klaar`: optioneel, al opgehaalde price_data -- als
    meegegeven wordt aangenomen dat transacties_df AL split-gecorrigeerd is
    (gebeurde dan al in _haal_portfolio_basis()) en worden de split-
    correctie + get_prices() hier overgeslagen. Gebruikt door
    portfolio_verrijking(); analyze_transacties() (de 'niet opslaan'-tak)
    laat dit weg en rekent alles zelf uit, zoals voorheen.
    """
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
    """Combineert kern + verrijking in één keer — voor de 'niet opslaan'-
    tak (geen opgeslagen code om later apart de verrijking op te halen) en
    voor eventuele andere plekken die de volledige, ongefaseerde data in
    één keer nodig hebben. De normale opslaande upload-flow en het
    bezoeken van een bestaande code gebruiken i.p.v. deze wrapper de kern-
    en verrijkingsfunctie apart (zie build_portfolio_response en de
    /verrijking-route)."""
    resultaat = analyze_transacties_kern(transacties_df, code, naam)
    if resultaat.get("chart_data") is None:
        return resultaat
    resultaat.update(analyze_transacties_verrijking(transacties_df, code))
    return resultaat
