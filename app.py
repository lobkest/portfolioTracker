from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db, delete_portfolio, wijzig_portfolio_code, get_transacties_overzicht
from prijzen import get_prices
from ticker_zekerheid import (
    verifieer_tickers_met_prijs_parallel, verifieer_ticker_met_prijs, backfill_verouderde_tickers,
)
from debug_utils import meet_tijd
from yahoo_client import reset_yahoo_call_teller, log_yahoo_call_samenvatting
from statistieken import bereken_benchmark_vergelijking, bereken_rendement_over_tijd, BENCHMARK_TICKERS
from dividend import bereken_dividend_samenvatting
from portfolio_admin import is_geldige_code, CODE_LENGTH
from upload_verwerking import (
    _lees_transacties_excel, _normaliseer_transactie_kolommen, _ticker_resolutie_niet_opslaan_pad,
    _bouw_transacties_df_niet_opslaan, _bepaal_order_ids, _vind_of_maak_portfolio_code,
    _ticker_resolutie_opslaan_pad, _insert_nieuwe_transacties, _backfill_bestaande_rijen,
    _verwerk_dividend_bestand_indien_aanwezig, _log_valuta_kolom_naast_koers,
)
from portfolio_orchestratie import (
    _haal_portfolio_basis, _wis_portfolio_basis_cache, _laad_transacties_en_resultaat,
    _ticker_zekerheid_groepen, build_portfolio_response, analyze_transacties_verrijking, analyze_transacties,
)
from portfolio_verdeling import bereken_etf_overlap_detail
import math
import time

app = Flask(__name__)
init_db()


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/dbtest")
def db_test():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return f"Database-verbinding werkt! Resultaat: {result}"
    except Exception as e:
        return f"Verbinding mislukt: {e}"

@app.route("/upload", methods=["POST"])
def upload():
    """
    Dunne wrapper om _upload_impl() die ELKE onverwachte fout (bv. een
    trage/falende Yahoo-call die uiteindelijk toch een exception geeft, of
    iets onvoorziens in de Excel-parsing) omzet in een nette JSON-
    foutrespons i.p.v. een kale 500 zonder body of een hangende request die
    de frontend nooit als 'klaar' ziet. Zie CLAUDE.md, Statistieken-incident
    2026-08-31: een 'niet opslaan'-analyse van een grotere portfolio bleef
    zo stil hangen dat er zelfs geen foutmelding verscheen.
    """
    try:
        return _upload_impl()
    except Exception as e:
        import traceback
        print(f"[upload] ONVERWACHTE FOUT: {e}")
        traceback.print_exc()
        return jsonify({
            "error": "Analyse van deze portfolio duurde te lang of is mislukt. Probeer het opnieuw, of upload "
                     "zonder 'Niet opslaan' zodat de resultaten tussentijds bewaard blijven."
        }), 500


def _upload_impl():
    reset_yahoo_call_teller()
    naam = request.form.get("naam", "").strip()
    bestand1 = request.files.get("bestand1")

    if not bestand1 or bestand1.filename == "":
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    with meet_tijd("excel_inlezen_pandas"):
        df = _lees_transacties_excel(bestand1)
        df = _normaliseer_transactie_kolommen(df)

    niet_opslaan = request.form.get("niet_opslaan") == "on"
    # "Ticker-informatie voor alle posities opnieuw bepalen"-vinkje (zie
    # templates/index.html): staat dit UIT (standaard), dan slaat de
    # ticker-resolutie hieronder de dure/onvoorwaardelijke yahooquery-
    # zoekopdracht over voor posities die al eerder zijn opgelost -- zie
    # CLAUDE.md/opdracht "vinkje ticker-informatie opnieuw bepalen".
    herbepaal_alle_tickers = request.form.get("herbepaal_alle_tickers") == "on"
    if niet_opslaan:
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen: dezelfde ISIN kan
        # op meerdere beurzen genoteerd staan (bv. een fonds met een
        # Amsterdam- én een Londen-notering) en dat zijn dan ECHT
        # verschillende tickers — één ticker per ISIN voor de hele groep zou
        # de tweede notering stilzwijgend de ticker van de eerste geven.
        #
        # Bewust de GOEDKOPE find_ticker_detailed()-match + lichte, standaard
        # prijscontrole (via basis_ticker_zekerheid_parallel ->
        # find_ticker_met_snelle_prijscheck: 1 gecachete call per positie in
        # het gangbare geval, escaleert alleen bij een echte afwijking),
        # niet de volledige, dure verifieer_tickers_met_prijs_parallel() —
        # die liep bij een grotere portfolio met een koude cache ruim over
        # de gunicorn-timeout heen doordat hij hier ALTIJD synchroon voor de
        # volle portfolio draaide (zie CLAUDE.md, Statistieken-incident
        # 2026-08-31). PARALLEL over de posities (niet sequentieel): ook al
        # kost de lichte check meestal maar 1 call per positie, bij een
        # portfolio met veel unieke, nog nooit gecontroleerde tickers (koude
        # ticker_prijscheck-cache) kan die ene call per positie sequentieel
        # opgeteld alsnog richting de timeout lopen (zie CLAUDE.md, vervolg
        # op hetzelfde incident). De normale (opslaande) upload koppelt de
        # VOLLEDIGE check nog steeds lui aan de Ticker-zekerheid-pagina (zie
        # de /ticker-zekerheid-route hieronder) — dat kan hier niet op
        # dezelfde manier (geen opgeslagen code om later transacties bij op
        # te halen), dus krijgt de eenmalige analyse in plaats daarvan een
        # losse /api/ticker-zekerheid-check-aanroep vanuit de frontend, met
        # de transactiedata die hieronder als 'ticker_posities_ruw' meegaat.
        ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw = _ticker_resolutie_niet_opslaan_pad(df)
        transacties_df = _bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs)
        result = analyze_transacties(transacties_df, code=None, naam=naam or None)
        result["ticker_zekerheid"] = ticker_zekerheid
        result["ticker_posities_ruw"] = ticker_posities_ruw
        log_yahoo_call_samenvatting()
        return jsonify(result)

    with meet_tijd("excel_inlezen_orderid_openpyxl"):
        df = _bepaal_order_ids(bestand1, df)

    conn = get_db_connection()
    cur = conn.cursor()

    code, match_code, rows_to_insert, rows_bestaand = _vind_of_maak_portfolio_code(cur, df, naam)

    if not rows_to_insert.empty:
        # Per (ISIN, Beurs) resolven, niet per ISIN alleen — zie de
        # 'niet_opslaan'-tak hierboven voor de reden (een ISIN kan op
        # meerdere beurzen genoteerd staan, met een écht andere ticker).
        # find_ticker_met_snelle_prijscheck (i.p.v. de kale
        # find_ticker_detailed) doet er een lichte, standaard prijscontrole
        # bovenop — in het gangbare geval maar 1 extra, gecachete Yahoo-call
        # per groep, warmt meteen de ticker_prijscheck-cache die
        # prijswaarschuwing_voor_ticker() hieronder (in analyze_transacties)
        # bij elk bezoek hergebruikt. PARALLEL over de groepen — zie de
        # 'niet_opslaan'-tak hierboven voor de reden (koude-cache-
        # timeoutrisico bij veel unieke tickers).
        _log_valuta_kolom_naast_koers(rows_to_insert)

        with meet_tijd("ticker_resolutie"):
            ticker_by_isin_beurs = _ticker_resolutie_opslaan_pad(cur, code, rows_to_insert, herbepaal_alle_tickers)

        with meet_tijd(f"db_insert_transacties ({len(rows_to_insert)} rij(en))"):
            _insert_nieuwe_transacties(cur, code, rows_to_insert, ticker_by_isin_beurs)

    conn.commit()
    cur.close()
    conn.close()

    if not rows_bestaand.empty:
        with meet_tijd(f"db_backfill_kosten_en_tijd ({len(rows_bestaand)} rij(en))"):
            _backfill_bestaande_rijen(code, rows_bestaand)

    if match_code:
        # Alleen zinvol bij een upload naar een BESTAANDE portfolio: een
        # verbeterde ticker-resolutielogica (bv. de G2X.MU-fix) corrigeert
        # anders alleen nieuw ingevoegde rijen, nooit wat al in de database
        # stond. Overschrijft alleen tickers die nu een prijsprobleem
        # hebben met een kandidaat die dat niet heeft (zie
        # ticker_zekerheid.backfill_verouderde_tickers).
        with meet_tijd("db_backfill_verouderde_tickers"):
            backfill_verouderde_tickers(code, forceer=herbepaal_alle_tickers)

    _verwerk_dividend_bestand_indien_aanwezig(code)

    # Cache wissen ná ALLE mutaties hierboven (insert, backfills, ticker-
    # herberekening) -- een upload moet altijd verse data opleveren, nooit
    # de _basis_cache van vóór deze upload (zie opdracht dubbele-fetches).
    _wis_portfolio_basis_cache(code)
    response = jsonify(build_portfolio_response(code))
    log_yahoo_call_samenvatting()
    return response


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    code = code.strip().upper()
    # Eigen, schone Yahoo-call-telling voor dit bezoek -- zonder deze reset
    # draagt de teller het cumulatieve aantal calls mee sinds de laatste
    # upload, wat de [timing]-samenvatting hieronder misleidend zou maken
    # (zie opdracht performance-meting).
    reset_yahoo_call_teller()
    # "Ticker-informatie voor alle posities opnieuw bepalen"-vinkje bij het
    # ophalen via code (zie templates/index.html) -- zelfde forceer-vlag/
    # functie als bij de upload-flow (zie CLAUDE.md/opdracht "vinkje ticker-
    # informatie opnieuw bepalen"). Standaard (parameter afwezig/leeg/iets
    # anders dan "true") blijft het ophalen ONGEWIJZIGD: backfill_
    # verouderde_tickers() werd hier vóór deze wijziging nooit aangeroepen,
    # alleen bij /upload -- dat blijft zo zonder het vinkje.
    if request.args.get("herbepaal_alle_tickers", "").lower() == "true":
        with meet_tijd("db_backfill_verouderde_tickers_ophalen"):
            backfill_verouderde_tickers(code, forceer=True)
        # Anders krijgt build_portfolio_response() hieronder de oude tickers
        # terug uit de _basis_cache i.p.v. de net herberekende.
        _wis_portfolio_basis_cache(code)
    result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    log_yahoo_call_samenvatting()
    return jsonify(result)


@app.route("/api/portfolio/<code>/verrijking")
def portfolio_verrijking(code):
    """
    Lui opgevraagde 'rest' van het dashboard (Verdeling, Land, Sector,
    Bedrijven, ETF-overlap) — bewust NIET in het hoofd-/upload-antwoord,
    want dit is het netwerk-zware deel (classificatie + holdings/sector-
    ophalen bij nog-niet-gecachete ETF's/aandelen). Zie CLAUDE.md /
    opdracht_gefaseerd_laden.md voor de achtergrond. De frontend roept dit
    meteen na het tonen van de Home-pagina aan en vult de betreffende
    tabbladen zodra dit antwoord binnenkomt.
    """
    code = code.strip().upper()
    naam, transacties_df, price_data = _haal_portfolio_basis(code)
    if naam is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    try:
        response = jsonify(analyze_transacties_verrijking(transacties_df, code, prijs_data_al_klaar=price_data))
        # Geen reset_yahoo_call_teller() hier: /verrijking wordt door de
        # frontend los van /upload aangeroepen, dus deze samenvatting toont
        # het CUMULATIEVE aantal calls sinds de laatste reset in
        # _upload_impl() (dus inclusief de kern-fase van /upload) -- zie
        # opdracht performance-meting.
        log_yahoo_call_samenvatting()
        return response
    except Exception as e:
        # print(f"[verrijking] ONVERWACHTE FOUT voor code={code}: {e}")
        return jsonify({
            "error": "Verdeling/land/sector/bedrijven ophalen duurde te lang of is mislukt. Probeer het "
                     "opnieuw door de pagina te verversen."
        }), 500


@app.route("/api/etf-overlap-detail")
def etf_overlap_detail():
    """
    Holdings-detail voor één ETF-paar uit de overlap-matrix (klik op een
    percentage-cel op het ETF-overlap-tabblad, zie opdracht "klikbaar
    overlap-percentage"). Los van een portfolio-code: get_etf_holdings()
    is een globale, per-ticker gecachete lookup, dus dit werkt zowel voor
    een opgeslagen portfolio als de 'niet opslaan'-analyse (die geen code
    heeft).
    """
    etf_a = request.args.get("a", "").strip()
    etf_b = request.args.get("b", "").strip()
    if not etf_a or not etf_b:
        return jsonify({"error": "Query-parameters 'a' en 'b' (ETF-tickers) zijn verplicht."}), 400
    return jsonify({"holdings": bereken_etf_overlap_detail(etf_a, etf_b)})


@app.route("/api/portfolio/<code>/benchmark-vergelijking")
def benchmark_vergelijking(code):
    """
    Losse, lui opgevraagde endpoint voor de "Vergelijk met..."-optie op het
    Rendement-tabblad — bewust niet standaard in het hoofd-dashboard-
    antwoord, want dit haalt (en cachet) koersdata op voor een extra ticker
    die niets met de eigen portfolio te maken heeft, wat de hoofdpagina
    onnodig zou vertragen voor een optie die de meeste bezoeken niet
    gebruiken. Query-param 'benchmark' is een sleutel uit BENCHMARK_TICKERS
    (bv. "S%26P%20500" voor "S&P 500"). Query-param 'eigen_ticker' is een
    alternatief: een ticker die al in de eigen portfolio zit, voor de
    "vergelijk ook met eigen aandeel"-optie — zelfde berekening
    (bereken_benchmark_vergelijking is generiek genoeg), alleen een andere
    koersbron.
    """
    code = code.strip().upper()
    benchmark_naam = request.args.get("benchmark", "")
    eigen_ticker = request.args.get("eigen_ticker", "")

    transacties_df, resultaat = _laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    if eigen_ticker:
        eigen_tickers_in_portfolio = transacties_df["ticker"].dropna().unique().tolist()
        if eigen_ticker not in eigen_tickers_in_portfolio:
            return jsonify({"error": f"Ticker '{eigen_ticker}' zit niet in deze portfolio."}), 400
        vergelijk_ticker = eigen_ticker
        vergelijk_label = eigen_ticker
    else:
        vergelijk_ticker = BENCHMARK_TICKERS.get(benchmark_naam)
        if not vergelijk_ticker:
            return jsonify({"error": f"Onbekende benchmark '{benchmark_naam}'."}), 400
        vergelijk_label = benchmark_naam

    vergelijk_prices = get_prices([vergelijk_ticker], transacties_df["datum"].min())
    if vergelijk_ticker not in vergelijk_prices.columns:
        return jsonify({"error": f"Geen koersdata gevonden voor '{vergelijk_label}'."}), 400

    vergelijking = bereken_benchmark_vergelijking(transacties_df, resultaat, vergelijk_prices[vergelijk_ticker])
    if vergelijking is None:
        return jsonify({"error": "Vergelijking kon niet berekend worden."}), 400

    return jsonify(vergelijking)


@app.route("/api/portfolio/<code>/rendement-over-tijd")
def rendement_over_tijd(code):
    """
    Losse, lui opgevraagde endpoint voor het "XIRR & rendement"-tabblad —
    zelfde reden als benchmark_vergelijking() hierboven: niet standaard in
    het hoofd-dashboard-antwoord, want dit herberekent XIRR voor elke
    maandelijkse stap (zie bereken_rendement_over_tijd), wat de hoofdpagina
    onnodig zou vertragen voor een tabblad dat niet elk bezoek bekeken wordt.
    """
    code = code.strip().upper()
    transacties_df, resultaat = _laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    return jsonify(bereken_rendement_over_tijd(transacties_df, resultaat))


@app.route("/api/portfolio/<code>/ticker-koers-bereik")
def ticker_koers_bereik(code):
    """
    Extra koersdata voor 1 ticker buiten de standaard-crop, t.b.v. de
    "meer historie laden"-knoppen op het 'Per aandeel aankoop'-tabblad
    (per_ticker_aankoop in de hoofd-payload is gecropt tot de aanhoud-
    periode). Query-params: ticker (verplicht), vanaf (YYYY-MM-DD,
    verplicht), tot (YYYY-MM-DD, optioneel, default vandaag). Bewust een
    los, lui endpoint i.p.v. de crop-range in analyze_transacties() op te
    rekken -- zelfde reden als bij ticker-zekerheid: dit raakt alleen deze
    ene knop, niet elke portfolio-load.
    """
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    bestaat = cur.fetchone() is not None
    cur.close()
    conn.close()
    if not bestaat:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    ticker = request.args.get("ticker")
    vanaf = request.args.get("vanaf")
    tot = request.args.get("tot")
    if not ticker or not vanaf:
        return jsonify({"error": "ticker en vanaf zijn verplicht"}), 400

    price_data = get_prices([ticker], vanaf)
    if price_data.empty or ticker not in price_data.columns:
        return jsonify({"labels": [], "koers": [], "vroegste_beschikbare_datum": None})

    serie = price_data[ticker].dropna()
    if tot:
        serie = serie[serie.index <= pd.Timestamp(tot)]

    return jsonify({
        "labels": [d.strftime("%Y-%m-%d") for d in serie.index],
        "koers": [round(float(k), 4) for k in serie.values],
        # Laat de frontend weten of de gevraagde 'vanaf' daadwerkelijk
        # gehaald is, of dat de historie eerder al ophield (bv. bij een
        # positie die pas een paar maanden genoteerd staat) -- t.b.v. het
        # uitgrijzen van een knop die niks meer oplevert.
        "vroegste_beschikbare_datum": serie.index.min().strftime("%Y-%m-%d") if len(serie) else None,
    })


@app.route("/api/portfolio/<code>/ticker-zekerheid")
def ticker_zekerheid(code):
    """
    Losse, lui opgevraagde endpoint voor de Ticker-zekerheid-pagina — bewust
    NIET onderdeel van het hoofd-dashboard-antwoord, want dit doet per
    positie tot een paar extra yfinance-prijscontroles (zie
    ticker_zekerheid.verifieer_ticker_met_prijs), wat de hoofdpagina
    onnodig zou vertragen voor een tabblad dat maar zelden bezocht wordt.

    LET OP: bij een groter portfolio kan deze route in z'n geheel mislukken
    omdat verifieer_tickers_met_prijs_parallel() moet wachten tot ALLE
    posities klaar zijn — één trage/rate-limited positie laat dan de hele
    opvraag timen out, ook al zijn de andere posities allang klaar (zelfde
    patroon als het eerdere Statistieken-incident, zie CLAUDE.md). De
    Ticker-zekerheid-pagina gebruikt daarom sinds kort de lichte lijst-route
    + per-positie-route hieronder in plaats van deze route. Blijft bestaan
    voor eventueel ander gebruik.
    """
    code = code.strip().upper()
    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    # print(f"[ticker-zekerheid] {len(groepen)} positie(s) parallel verifiëren voor code={code}")
    t0 = time.time()
    try:
        resultaten = verifieer_tickers_met_prijs_parallel(
            [(info["echte_naam"], isin, info["beurs"], info["transacties"]) for (isin, beurs), info in groepen]
        )
    except Exception as e:
        # print(f"[ticker-zekerheid] FOUT bij verifiëren voor code={code}: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren duurde te lang of is mislukt. Probeer het opnieuw."
        }), 500
    # print(f"[ticker-zekerheid] {len(groepen)} positie(s) geverifieerd in {time.time() - t0:.1f}s")

    posities = []
    for ((isin, beurs), info), resultaat in zip(groepen, resultaten):
        resultaat["isin"] = isin
        resultaat["naam"] = info["naam"]
        resultaat["echte_naam"] = info["echte_naam"]
        posities.append(resultaat)

    return jsonify({"posities": posities})


@app.route("/api/portfolio/<code>/ticker-zekerheid/lijst")
def ticker_zekerheid_lijst(code):
    """
    Lichte variant van de route hierboven: geeft alleen de posities terug
    (isin/beurs/naam), zonder de dure prijscontrole — vrijwel instant. De
    Ticker-zekerheid-pagina haalt hiermee meteen alle rijen op om als
    "bezig..." te tonen, en start daarna per positie een losse aanroep naar
    /ticker-zekerheid/positie hieronder (zie static/js/app.js). Zo blokkeert
    één trage/mislukte positie niet meer de andere resultaten.
    """
    code = code.strip().upper()
    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    posities = [
        {"isin": isin, "beurs": beurs, "naam": info["naam"], "echte_naam": info["echte_naam"]}
        for (isin, beurs), info in groepen
    ]
    return jsonify({"posities": posities})


@app.route("/api/portfolio/<code>/ticker-zekerheid/positie")
def ticker_zekerheid_positie(code):
    """
    Verifieert precies 1 positie (isin+beurs via de querystring) — de
    Ticker-zekerheid-pagina roept dit per positie apart aan (met een
    concurrency-limiet, zie static/js/app.js) i.p.v. te wachten tot ALLE
    posities klaar zijn. Hergebruikt verifieer_ticker_met_prijs() zoals de
    volledige route hierboven, alleen voor 1 (isin, beurs)-groep i.p.v. de
    hele portfolio — geen nieuwe backend-logica, alleen een kleinere
    aanroep-eenheid zodat één trage/rate-limited positie niet meer de hele
    opvraag laat mislukken en elke aparte aanroep ruim binnen een gunicorn-
    timeout blijft.
    """
    code = code.strip().upper()
    isin = request.args.get("isin", "")
    beurs = request.args.get("beurs", "")

    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    info = dict(groepen).get((isin, beurs))
    if info is None:
        return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

    t0 = time.time()
    try:
        resultaat = verifieer_ticker_met_prijs(info["echte_naam"], isin, info["beurs"], info["transacties"])
    except Exception as e:
        # print(f"[ticker-zekerheid] FOUT bij verifiëren van {isin} ({beurs}) voor code={code}: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren voor deze positie is mislukt. Probeer het opnieuw."
        }), 500
    # print(f"[ticker-zekerheid] positie {isin} ({beurs}) klaar in {time.time() - t0:.1f}s voor code={code}")

    resultaat["isin"] = isin
    resultaat["naam"] = info["naam"]
    resultaat["echte_naam"] = info["echte_naam"]
    return jsonify(resultaat)


@app.route("/api/ticker-zekerheid-check", methods=["POST"])
def ticker_zekerheid_check():
    """
    Uitgebreide, prijs-geverifieerde ticker-zekerheid voor een 'niet
    opslaan'-analyse. Die heeft geen opgeslagen code om de route hierboven
    mee aan te roepen (die leest transacties uit de database) — maar
    verifieer_ticker_met_prijs() is een pure functie op aangeleverde
    transacties, dus laat de frontend die data hier los meesturen
    (huidigeData.ticker_posities_ruw, meegegeven door de niet_opslaan-tak
    van /upload). Losse, expliciet door de gebruiker aangevraagde actie
    i.p.v. synchroon in de hoofd-/upload-flow — zie de niet_opslaan-tak in
    _upload_impl() voor de reden (gunicorn-timeout-risico bij grotere/
    koude-cache-portfolio's, CLAUDE.md Statistieken-incident 2026-08-31).
    """
    data = request.get_json(silent=True) or {}
    posities = data.get("posities") or []
    if not posities:
        return jsonify({"error": "Geen posities meegestuurd."}), 400

    # print(f"[ticker-zekerheid-check] {len(posities)} positie(s) parallel verifiëren (niet-opgeslagen analyse)")
    t0 = time.time()
    try:
        input_tuples = [
            (
                p.get("naam"), p.get("isin"), p.get("beurs"),
                [{"datum": t.get("datum"), "koers": t.get("koers")} for t in (p.get("transacties") or [])],
            )
            for p in posities
        ]
        resultaten = verifieer_tickers_met_prijs_parallel(input_tuples)
    except Exception as e:
        # print(f"[ticker-zekerheid-check] FOUT: {e}")
        return jsonify({
            "error": "Ticker-zekerheid controleren duurde te lang of is mislukt. Probeer het opnieuw, eventueel "
                     "met minder posities tegelijk."
        }), 500
    # print(f"[ticker-zekerheid-check] {len(posities)} positie(s) geverifieerd in {time.time() - t0:.1f}s")

    uitkomst = []
    for p, resultaat in zip(posities, resultaten):
        resultaat["isin"] = p.get("isin")
        resultaat["naam"] = p.get("naam")
        resultaat["echte_naam"] = p.get("naam")
        uitkomst.append(resultaat)

    return jsonify({"posities": uitkomst})


@app.route("/api/portfolio/<code>/dividend")
def dividend(code):
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    cur.close()
    conn.close()

    samenvatting = bereken_dividend_samenvatting(code)
    if samenvatting is None:
        return jsonify({"beschikbaar": False})

    samenvatting["beschikbaar"] = True
    return jsonify(samenvatting)


@app.route("/api/portfolio/<code>/transacties")
def transacties_overzicht(code):
    code = code.strip().upper()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    cur.close()
    conn.close()

    return jsonify({"lijst": get_transacties_overzicht(code)})


@app.route("/api/portfolio/<code>/bijnaam", methods=["POST"])
def set_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    bijnaam = (data.get("bijnaam") or "").strip()
    if not ticker or not bijnaam:
        return jsonify({"error": "Ticker en bijnaam zijn verplicht."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = %s WHERE code = %s AND ticker = %s",
        (bijnaam, code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()
    _wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>/reset-bijnaam", methods=["POST"])
def reset_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    if not ticker:
        return jsonify({"error": "Ticker is verplicht."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = echte_naam WHERE code = %s AND ticker = %s",
        (code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()
    _wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>", methods=["DELETE"])
def verwijder_portfolio(code):
    code = code.strip().upper()
    delete_portfolio(code)
    _wis_portfolio_basis_cache(code)
    return jsonify({"success": True})


@app.route("/api/portfolio/<code>/wijzig-code", methods=["POST"])
def wijzig_code(code):
    code = code.strip().upper()
    data = request.get_json()
    nieuwe_code = (data.get("nieuwe_code") or "").strip().upper()

    if not is_geldige_code(nieuwe_code):
        return jsonify({"error": f"Ongeldige code. Gebruik precies {CODE_LENGTH} hoofdletters (A-Z)."}), 400
    if nieuwe_code == code:
        return jsonify({"error": "De nieuwe code is gelijk aan de huidige code."}), 400

    success, foutmelding = wijzig_portfolio_code(code, nieuwe_code)
    if not success:
        return jsonify({"error": foutmelding}), 400

    # Oude code bestaat na de rename niet meer, en de nieuwe code is nog
    # nooit via _haal_portfolio_basis() opgehaald onder die naam -- beide
    # cache-entries wissen voorkomt dat een eventuele stale entry (bv. de
    # oude code kort hiervoor bezocht) blijft rondhangen.
    _wis_portfolio_basis_cache(code)
    _wis_portfolio_basis_cache(nieuwe_code)
    return jsonify(build_portfolio_response(nieuwe_code, verversen=False))

if __name__ == "__main__":
    app.run(debug=True)