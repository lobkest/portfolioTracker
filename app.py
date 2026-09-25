from flask import Flask, render_template, request, jsonify
import pandas as pd
from db import get_db_connection, init_db, delete_portfolio, wijzig_portfolio_code, get_transacties_overzicht
from prijzen import get_prices
from ticker_zekerheid import (
    verifieer_tickers_met_prijs_parallel, verifieer_ticker_met_prijs, backfill_verouderde_tickers,
)
from debug_utils import meet_tijd
from diagnostiek import voeg_diagnostiek_toe
from yahoo_client import (
    reset_yahoo_call_teller, log_yahoo_call_samenvatting, yahoo_teller_stand, meld_yahoo_samenvatting,
    DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, DIAGNOSTIEK_SLEUTEL_YAHOO_VERRIJKING,
)
from statistieken import bereken_benchmark_vergelijking, bereken_rendement_over_tijd, BENCHMARK_TICKERS
from dividend import bereken_dividend_samenvatting
from portfolio_admin import is_geldige_code, CODE_LENGTH
from upload_verwerking import (
    _lees_transacties_excel, _normaliseer_transactie_kolommen, _ticker_resolutie_niet_opslaan_pad,
    _bouw_transacties_df_niet_opslaan, _bepaal_order_ids, _vind_of_maak_portfolio_code,
    _ticker_resolutie_opslaan_pad, _insert_nieuwe_transacties,
    _verwerk_dividend_bestand_indien_aanwezig, _meld_nieuwe_rijen_kwaliteit, _meld_dividend_bestand_genegeerd,
)
from portfolio_orchestratie import (
    _haal_portfolio_basis, _wis_portfolio_basis_cache, _laad_transacties_en_resultaat,
    _laad_split_gecorrigeerde_transacties,
    _ticker_zekerheid_groepen, build_portfolio_response, analyze_transacties_verrijking, analyze_transacties,
)
from portfolio_verdeling import bereken_etf_overlap_detail
from portfolio_calc import holdings_op_datums

app = Flask(__name__)
init_db()


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    """Zet elke onverwachte fout om in een JSON-foutrespons; anders blijft de frontend hangen."""
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
    herbepaal_alle_tickers = request.form.get("herbepaal_alle_tickers") == "on"
    
    if niet_opslaan:
        # Alleen de lichte ticker-check: de volledige liep hier over de
        # gunicorn-timeout (zie CLAUDE.md: Yahoo en tickers).
        ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw = _ticker_resolutie_niet_opslaan_pad(df)
        transacties_df = _bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs)
        result = analyze_transacties(transacties_df, code=None, naam=naam or None)
        result["ticker_zekerheid"] = ticker_zekerheid
        result["ticker_posities_ruw"] = ticker_posities_ruw
        _meld_dividend_bestand_genegeerd()
        log_yahoo_call_samenvatting()
        meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
        return jsonify(voeg_diagnostiek_toe(result))

    with meet_tijd("excel_inlezen_orderid_openpyxl"):
        df = _bepaal_order_ids(bestand1, df)

    conn = get_db_connection()
    cur = conn.cursor()

    code, match_code, rows_to_insert = _vind_of_maak_portfolio_code(cur, df, naam)
    _meld_nieuwe_rijen_kwaliteit(rows_to_insert)

    if not rows_to_insert.empty:
        with meet_tijd("ticker_resolutie"):
            ticker_by_isin_beurs = _ticker_resolutie_opslaan_pad(cur, code, rows_to_insert, herbepaal_alle_tickers)

        with meet_tijd(f"db_insert_transacties ({len(rows_to_insert)} rij(en))"):
            _insert_nieuwe_transacties(cur, code, rows_to_insert, ticker_by_isin_beurs)

    conn.commit()
    cur.close()
    conn.close()

    if match_code:
        # Anders profiteren al opgeslagen rijen nooit van betere ticker-logica.
        with meet_tijd("db_backfill_verouderde_tickers"):
            backfill_verouderde_tickers(code, forceer=herbepaal_alle_tickers)

    _verwerk_dividend_bestand_indien_aanwezig(code)

    # Pas ná alle mutaties hierboven wissen.
    _wis_portfolio_basis_cache(code)
    result = build_portfolio_response(code)
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
    response = jsonify(voeg_diagnostiek_toe(result))
    log_yahoo_call_samenvatting()
    return response


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    code = code.strip().upper()
    reset_yahoo_call_teller()
    if request.args.get("herbepaal_alle_tickers", "").lower() == "true":
        with meet_tijd("db_backfill_verouderde_tickers_ophalen"):
            backfill_verouderde_tickers(code, forceer=True)
        # Anders levert de _basis_cache de oude tickers.
        _wis_portfolio_basis_cache(code)
    result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    log_yahoo_call_samenvatting()
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "ophalen")
    return jsonify(voeg_diagnostiek_toe(result))


@app.route("/api/portfolio/<code>/verrijking")
def portfolio_verrijking(code):
    code = code.strip().upper()
    # Stand van de (niet-gereset) Yahoo-teller bij de start: de Diagnostiek
    # meldt alleen het verschil, dus de calls van déze request.
    yahoo_voor = yahoo_teller_stand()
    naam, transacties_df, price_data = _haal_portfolio_basis(code)
    if naam is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    try:
        result = analyze_transacties_verrijking(transacties_df, code, prijs_data_al_klaar=price_data)
        meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_VERRIJKING, "verrijking", vanaf=yahoo_voor)
        response = jsonify(voeg_diagnostiek_toe(result))
        # Bewust geen reset: de log toont het totaal inclusief de kern-fase.
        log_yahoo_call_samenvatting()
        return response
    except Exception:
        return jsonify({
            "error": "Verdeling/land/sector/bedrijven ophalen duurde te lang of is mislukt. Probeer het "
                     "opnieuw door de pagina te verversen."
        }), 500


@app.route("/api/etf-overlap-detail")
def etf_overlap_detail():
    """Zonder portfolio-code, zodat het ook bij 'niet opslaan' werkt."""
    etf_a = request.args.get("a", "").strip()
    etf_b = request.args.get("b", "").strip()
    if not etf_a or not etf_b:
        return jsonify({"error": "Query-parameters 'a' en 'b' (ETF-tickers) zijn verplicht."}), 400
    return jsonify({"holdings": bereken_etf_overlap_detail(etf_a, etf_b)})


@app.route("/api/portfolio/<code>/benchmark-vergelijking")
def benchmark_vergelijking(code):
    """Query-param 'benchmark' (sleutel uit BENCHMARK_TICKERS) of 'eigen_ticker' (ticker uit de portfolio)."""
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
    code = code.strip().upper()
    transacties_df, resultaat = _laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    return jsonify(bereken_rendement_over_tijd(transacties_df, resultaat))


@app.route("/api/portfolio/<code>/ticker-koers-bereik")
def ticker_koers_bereik(code):
    """Koersen van 1 ticker buiten de standaard-crop. Query-params: ticker, vanaf, tot (optioneel)."""
    code = code.strip().upper()
    # Niet _haal_portfolio_basis(): die haalt koersen van alle tickers op.
    transacties_df = _laad_split_gecorrigeerde_transacties(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    ticker = request.args.get("ticker")
    vanaf = request.args.get("vanaf")
    tot = request.args.get("tot")
    if not ticker or not vanaf:
        return jsonify({"error": "ticker en vanaf zijn verplicht"}), 400

    price_data = get_prices([ticker], vanaf)
    if price_data.empty or ticker not in price_data.columns:
        return jsonify({"labels": [], "koers": [], "holdings": [], "vroegste_beschikbare_datum": None})

    serie = price_data[ticker].dropna()
    if tot:
        serie = serie[serie.index <= pd.Timestamp(tot)]

    return jsonify({
        "labels": [d.strftime("%Y-%m-%d") for d in serie.index],
        "koers": [round(float(k), 4) for k in serie.values],
        "holdings": holdings_op_datums(transacties_df[transacties_df["ticker"] == ticker], serie.index),
        # Laat de frontend zien of de historie eerder ophield dan 'vanaf'.
        "vroegste_beschikbare_datum": serie.index.min().strftime("%Y-%m-%d") if len(serie) else None,
    })


@app.route("/api/portfolio/<code>/ticker-zekerheid/lijst")
def ticker_zekerheid_lijst(code):
    """Alleen de posities, zonder prijscontrole; die volgt per positie via /positie."""
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
    """Eén positie per aanroep, zodat elke aanroep ruim binnen de gunicorn-timeout blijft."""
    code = code.strip().upper()
    isin = request.args.get("isin", "")
    beurs = request.args.get("beurs", "")

    groepen = _ticker_zekerheid_groepen(code)
    if groepen is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    info = dict(groepen).get((isin, beurs))
    if info is None:
        return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

    try:
        resultaat = verifieer_ticker_met_prijs(info["echte_naam"], isin, info["beurs"], info["transacties"])
    except Exception:
        return jsonify({
            "error": "Ticker-zekerheid controleren voor deze positie is mislukt. Probeer het opnieuw."
        }), 500

    resultaat["isin"] = isin
    resultaat["naam"] = info["naam"]
    resultaat["echte_naam"] = info["echte_naam"]
    return jsonify(resultaat)


@app.route("/api/ticker-zekerheid-check", methods=["POST"])
def ticker_zekerheid_check():
    """Volledige ticker-check voor 'niet opslaan': de frontend stuurt de transacties zelf mee."""
    data = request.get_json(silent=True) or {}
    posities = data.get("posities") or []
    if not posities:
        return jsonify({"error": "Geen posities meegestuurd."}), 400

    try:
        input_tuples = [
            (
                p.get("naam"), p.get("isin"), p.get("beurs"),
                [{"datum": t.get("datum"), "koers": t.get("koers")} for t in (p.get("transacties") or [])],
            )
            for p in posities
        ]
        resultaten = verifieer_tickers_met_prijs_parallel(input_tuples)
    except Exception:
        return jsonify({
            "error": "Ticker-zekerheid controleren duurde te lang of is mislukt. Probeer het opnieuw, eventueel "
                     "met minder posities tegelijk."
        }), 500

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

    _wis_portfolio_basis_cache(code)
    _wis_portfolio_basis_cache(nieuwe_code)
    return jsonify(build_portfolio_response(nieuwe_code, verversen=False))

if __name__ == "__main__":
    app.run(debug=True)