import os
import traceback

from flask import Flask, render_template, request, jsonify, redirect, url_for
import pandas as pd
from db import (
    db_transactie, db_init, db_delete_portfolio, db_wijzig_portfolio_code, db_get_transacties_overzicht,
    db_portfolio_bestaat, db_wijzig_bijnaam, db_wijzig_bijnamen, db_herstel_echte_naam,
    db_wis_etf_proxies_voor_portfolio, db_get_order_ids, db_get_order_ids_bij_andere_portfolios,
    db_reset_verbinding_teller, db_log_verbinding_samenvatting, db_deel_verbinding,
)
from ticker_classificatie import haal_long_names
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
from dividend import (
    bereken_dividend_samenvatting, bouw_dividend_samenvatting, lees_rekeningoverzicht, order_ids_uit_rekeningoverzicht_df,
)
from transactie_utils import transacties_overzicht_uit_df
from portfolio_admin import (
    is_geldige_code, CODE_LENGTH, controleer_eigen_transactiebestand, controleer_eigen_rekeningoverzicht,
    FOUT_ANDERE_PORTFOLIO, FOUT_TRANSACTIES_ONTBREKEN, FOUT_GEEN_ORDER_IDS, FOUT_ONBEKENDE_TRANSACTIES,
)
from upload_verwerking import (
    lees_transacties_excel, voeg_koers_eur_toe, OngeldigExcelBestand, ticker_resolutie_niet_opslaan,
    bouw_transacties_df_niet_opslaan, vul_synthetische_order_ids_aan, vind_of_maak_portfolio,
    voeg_nieuwe_transacties_toe, bepaal_product_per_ticker, sla_dividend_bestand_op, verwerk_dividend_zonder_opslaan,
    meld_portfolio_opslaan,
)
from portfolio_orchestratie import (
    haal_portfolio_basis, wis_portfolio_basis_cache, laad_transacties_en_resultaat,
    laad_split_gecorrigeerde_transacties, pas_effectieve_datums_toe, continue_koersreeks,
    ticker_zekerheid_groepen, build_portfolio_response, analyze_transacties_verrijking, analyze_transacties,
    bepaal_korte_naam_voorstellen, YahooNamenOnbeschikbaar, meld_valuta_consistentie, ticker_per_isin_beurs_uit_basis,
)
from portfolio_verdeling import bereken_etf_overlap_detail
from portfolio_calc import holdings_op_datums

app = Flask(__name__)

# Zonder DATABASE_URL (CI/tests) overslaan, zodat 'import app' niet crasht.
if os.environ.get("DATABASE_URL"):
    db_init()


MELDING_ONGELDIGE_CODE = "ongeldige-code"

MELDING_NIET_VAN_DEZE_PORTFOLIO = "Dit bestand lijkt niet bij deze portfolio te horen."
MELDING_TRANSACTIES_ONTBREKEN = ("Dit bestand bevat niet al je opgeslagen transacties. Exporteer bij DeGiro vanaf je "
                                 "eerste transactie tot nu.")
MELDING_REKENING_ZONDER_ORDER_IDS = ("In dit rekeningoverzicht staan geen aan- of verkopen, dus kan niet worden "
                                     "gecontroleerd of het bij deze portfolio hoort. Kies een periode met minstens "
                                     "één transactie.")
MELDING_REKENING_ONBEKENDE_TRANSACTIES = ("Dit rekeningoverzicht bevat transacties die niet in deze portfolio staan. "
                                          "Upload ook het nieuwste transactiebestand.")

# Nooit de code van een andere portfolio noemen.
MELDING_PER_EIGENDOMSFOUT = {
    FOUT_ANDERE_PORTFOLIO: MELDING_NIET_VAN_DEZE_PORTFOLIO,
    FOUT_TRANSACTIES_ONTBREKEN: MELDING_TRANSACTIES_ONTBREKEN,
    FOUT_GEEN_ORDER_IDS: MELDING_REKENING_ZONDER_ORDER_IDS,
    FOUT_ONBEKENDE_TRANSACTIES: MELDING_REKENING_ONBEKENDE_TRANSACTIES,
}


@app.route("/")
def home():
    return render_template("start.html")


# Alleen de template: de pagina haalt de data zelf op, dus geen database hier.
@app.route("/p/<code>")
def portfolio_pagina(code):
    genormaliseerd = code.strip().upper()
    if not is_geldige_code(genormaliseerd):
        return redirect(url_for("home", melding=MELDING_ONGELDIGE_CODE))
    if code != genormaliseerd:
        return redirect(url_for("portfolio_pagina", code=genormaliseerd))
    return render_template("portfolio.html", code=genormaliseerd, code_lengte=CODE_LENGTH)


@app.route("/analyse")
def analyse_pagina():
    return render_template("portfolio.html", code_lengte=CODE_LENGTH)

@app.route("/upload", methods=["POST"])
def upload():
    """Zet elke onverwachte fout om in een JSON-foutrespons; anders blijft de frontend hangen."""
    db_reset_verbinding_teller()
    try:
        with meet_tijd("upload_totaal"):
            return _upload_impl()
    except Exception as e:
        print(f"[upload] ONVERWACHTE FOUT: {e}")
        traceback.print_exc()
        return jsonify({
            "error": "Analyse van deze portfolio duurde te lang of is mislukt. Probeer het opnieuw, of upload "
                     "zonder 'Niet opslaan' zodat de resultaten tussentijds bewaard blijven."
        }), 500
    finally:
        db_log_verbinding_samenvatting()


def _upload_impl():
    reset_yahoo_call_teller()
    bestand1 = _gekozen_bestand("bestand1")
    if not bestand1:
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    try:
        with meet_tijd("excel_inlezen_pandas"):
            df = voeg_koers_eur_toe(lees_transacties_excel(bestand1))
        bestand2 = _gekozen_bestand("bestand2")
        rekening_df = lees_rekeningoverzicht(bestand2) if bestand2 else None
    except OngeldigExcelBestand as e:
        return jsonify({"error": str(e)}), 400

    naam = request.form.get("naam", "").strip()
    if request.form.get("niet_opslaan") == "on":
        result = _analyseer_zonder_opslaan(df, rekening_df, naam)
    else:
        herbepaal_alle_tickers = request.form.get("herbepaal_alle_tickers") == "on"
        result = _upload_opslaan(df, rekening_df, naam, herbepaal_alle_tickers)
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
    response = jsonify(voeg_diagnostiek_toe(result))
    log_yahoo_call_samenvatting()
    return response


def _analyseer_zonder_opslaan(df, rekening_df, naam):
    # Alleen de lichte ticker-check: de volledige liep hier over de gunicorn-timeout (zie CLAUDE.md: Yahoo en tickers).
    ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw = ticker_resolutie_niet_opslaan(df)
    product_per_ticker = bepaal_product_per_ticker(df, ticker_by_isin_beurs)
    transacties_df = bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs, product_per_ticker)
    result = analyze_transacties(transacties_df, code=None, naam=naam or None)
    meld_valuta_consistentie(df, ticker_by_isin_beurs)
    result["ticker_zekerheid"] = ticker_zekerheid
    result["ticker_posities_ruw"] = ticker_posities_ruw
    result["transacties_lijst"] = transacties_overzicht_uit_df(transacties_df)
    result["dividend"] = _dividend_niet_opslaan(transacties_df, rekening_df)
    return result


def _dividend_niet_opslaan(transacties_df, rekening_df):
    dividend_records = verwerk_dividend_zonder_opslaan(rekening_df) if rekening_df is not None else None
    transactie_rows = [
        (r.isin, r.ticker, r.product)
        for r in transacties_df.itertuples(index=False) if r.ticker is not None
    ]
    samenvatting = bouw_dividend_samenvatting(dividend_records, transactie_rows)
    if samenvatting is None:
        return {"beschikbaar": False}
    return {**samenvatting, "beschikbaar": True}


def _upload_opslaan(df, rekening_df, naam, herbepaal_alle_tickers):
    df = vul_synthetische_order_ids_aan(df)
    with db_transactie() as cur:
        with meet_tijd("portfolio_zoeken_of_maken"):
            code, bestaand, rows_to_insert = vind_of_maak_portfolio(cur, df, naam)
        if bestaand and herbepaal_alle_tickers:
            _herbepaal_tickers(code)
        voeg_nieuwe_transacties_toe(cur, code, rows_to_insert, herbepaal_alle_tickers)
    if rekening_df is not None:
        sla_dividend_bestand_op(code, rekening_df)
    result = _kern_na_opslaan(code)
    meld_valuta_consistentie(df, ticker_per_isin_beurs_uit_basis(code))
    return result


def _herbepaal_tickers(code):
    """'Ticker-informatie opnieuw bepalen': opgeslagen tickers herzoeken en land-proxy's opnieuw laten zoeken."""
    with meet_tijd("db_backfill_verouderde_tickers"):
        backfill_verouderde_tickers(code)
    db_wis_etf_proxies_voor_portfolio(code)


def _kern_na_opslaan(code):
    """Pas aanroepen ná alle mutaties: wist de basis-cache en bouwt de kern opnieuw op."""
    wis_portfolio_basis_cache(code)
    with db_deel_verbinding():
        return build_portfolio_response(code)


def _gekozen_bestand(veld):
    bestand = request.files.get(veld)
    return bestand if bestand and bestand.filename != "" else None


@app.route("/api/portfolio/<code>/bijwerken", methods=["POST"])
def bijwerken(code):
    """Zelfde vangnet als /upload."""
    db_reset_verbinding_teller()
    try:
        with meet_tijd("bijwerken_totaal"):
            return _bijwerken_impl(code.strip().upper())
    except Exception as e:
        print(f"[bijwerken] ONVERWACHTE FOUT: {e}")
        traceback.print_exc()
        return jsonify({"error": "Bijwerken duurde te lang of is mislukt. Probeer het opnieuw."}), 500
    finally:
        db_log_verbinding_samenvatting()


def _bijwerken_impl(code):
    """Alleen opslaan als élk meegestuurd bestand bij deze portfolio hoort."""
    reset_yahoo_call_teller()
    if not db_portfolio_bestaat(code):
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    bestand1 = _gekozen_bestand("bestand1")
    bestand2 = _gekozen_bestand("bestand2")
    if not bestand1 and not bestand2:
        return jsonify({"error": "Kies minstens één bestand."}), 400

    df = None
    if bestand1:
        try:
            df = vul_synthetische_order_ids_aan(voeg_koers_eur_toe(lees_transacties_excel(bestand1)))
        except OngeldigExcelBestand as e:
            return jsonify({"error": str(e)}), 400
    nieuw = set(df["Order ID"]) if df is not None else set()

    with meet_tijd("order_ids_ophalen"), db_transactie() as cur:
        opgeslagen = db_get_order_ids(cur, code)
        bij_andere_portfolios = db_get_order_ids_bij_andere_portfolios(cur, code, nieuw)

    fout, toe_te_voegen = None, set()
    if df is not None:
        fout, toe_te_voegen = controleer_eigen_transactiebestand(opgeslagen, nieuw, bij_andere_portfolios)
    rekening_df = None
    if not fout and bestand2:
        try:
            rekening_df = lees_rekeningoverzicht(bestand2)
        except OngeldigExcelBestand as e:
            return jsonify({"error": str(e)}), 400
        fout = controleer_eigen_rekeningoverzicht(order_ids_uit_rekeningoverzicht_df(rekening_df), opgeslagen | nieuw)
    if fout:
        return jsonify({"error": MELDING_PER_EIGENDOMSFOUT[fout]}), 400

    rows_to_insert = df[df["Order ID"].isin(toe_te_voegen)] if df is not None else pd.DataFrame()
    meld_portfolio_opslaan(True, rows_to_insert)
    if not rows_to_insert.empty:
        with db_transactie() as cur:
            voeg_nieuwe_transacties_toe(cur, code, rows_to_insert, herbepaal_alle_tickers=False)
    if rekening_df is not None:
        sla_dividend_bestand_op(code, rekening_df)
    result = _kern_na_opslaan(code)
    if df is not None:
        meld_valuta_consistentie(df, ticker_per_isin_beurs_uit_basis(code))
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
    result["bijwerken"] = {
        "nieuwe_transacties": len(rows_to_insert),
        "dividend_verwerkt": rekening_df is not None,
    }
    response = jsonify(voeg_diagnostiek_toe(result))
    log_yahoo_call_samenvatting()
    return response


@app.route("/api/portfolio/<code>")
def api_portfolio(code):
    code = code.strip().upper()
    reset_yahoo_call_teller()
    db_reset_verbinding_teller()
    if request.args.get("herbepaal_alle_tickers", "").lower() == "true":
        _herbepaal_tickers(code)
        # Anders levert de _basis_cache de oude tickers.
        wis_portfolio_basis_cache(code)
    with meet_tijd("ophalen_totaal"), db_deel_verbinding():
        result = build_portfolio_response(code)
    if result is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    log_yahoo_call_samenvatting()
    db_log_verbinding_samenvatting()
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "ophalen")
    return jsonify(voeg_diagnostiek_toe(result))


@app.route("/api/portfolio/<code>/verrijking")
def portfolio_verrijking(code):
    code = code.strip().upper()
    # Stand van de (niet-gereset) Yahoo-teller bij de start: de Diagnostiek
    # meldt alleen het verschil, dus de calls van déze request.
    yahoo_voor = yahoo_teller_stand()
    with db_deel_verbinding():
        naam, transacties_df, price_data = haal_portfolio_basis(code)
    if naam is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    try:
        with db_deel_verbinding():
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

    transacties_df, resultaat = laad_transacties_en_resultaat(code)
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

    # Ruwe koersen springen op een splitdag; de vergelijking koopt en waardeert stukken, dus een continue reeks.
    vergelijk_koersen = continue_koersreeks(vergelijk_ticker, vergelijk_prices[vergelijk_ticker])
    vergelijking = bereken_benchmark_vergelijking(transacties_df, resultaat, vergelijk_koersen)
    if vergelijking is None:
        return jsonify({"error": "Vergelijking kon niet berekend worden."}), 400

    return jsonify(vergelijking)


@app.route("/api/portfolio/<code>/rendement-over-tijd")
def rendement_over_tijd(code):
    code = code.strip().upper()
    transacties_df, resultaat = laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    return jsonify(bereken_rendement_over_tijd(transacties_df, resultaat))


@app.route("/api/portfolio/<code>/ticker-koers-bereik")
def ticker_koers_bereik(code):
    """Koersen van 1 ticker buiten de standaard-crop. Query-params: ticker, vanaf, tot (optioneel)."""
    code = code.strip().upper()
    # Niet haal_portfolio_basis(): die haalt koersen van alle tickers op.
    transacties_df = laad_split_gecorrigeerde_transacties(code)
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
    transacties_df = pas_effectieve_datums_toe(transacties_df)

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
    groepen = ticker_zekerheid_groepen(code)
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

    groepen = ticker_zekerheid_groepen(code)
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
    if not db_portfolio_bestaat(code):
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    samenvatting = bereken_dividend_samenvatting(code)
    if samenvatting is None:
        return jsonify({"beschikbaar": False})

    samenvatting["beschikbaar"] = True
    return jsonify(samenvatting)


@app.route("/api/portfolio/<code>/transacties")
def transacties_overzicht(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404

    return jsonify({"lijst": db_get_transacties_overzicht(code)})


@app.route("/api/portfolio/<code>/bijnaam", methods=["POST"])
def set_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    bijnaam = (data.get("bijnaam") or "").strip()
    if not ticker or not bijnaam:
        return jsonify({"error": "Ticker en bijnaam zijn verplicht."}), 400

    db_wijzig_bijnaam(code, ticker, bijnaam)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>/bijnamen", methods=["POST"])
def set_bijnamen(code):
    code = code.strip().upper()
    namen = (request.get_json() or {}).get("namen")
    if not isinstance(namen, dict):
        return jsonify({"error": "Namen zijn verplicht."}), 400

    schoon = {t: n.strip() for t, n in namen.items() if isinstance(n, str) and n.strip()}
    if schoon:
        db_wijzig_bijnamen(code, schoon)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>/reset-bijnaam", methods=["POST"])
def reset_bijnaam(code):
    code = code.strip().upper()
    data = request.get_json()
    ticker = data.get("ticker")
    if not ticker:
        return jsonify({"error": "Ticker is verplicht."}), 400

    long_name = haal_long_names([ticker]).get(ticker)
    if long_name:
        db_wijzig_bijnaam(code, ticker, long_name)
    else:
        db_herstel_echte_naam(code, ticker)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


def _korte_namen_voorstellen_of_fout(code):
    """(voorstellen, None) of (None, (JSON-fout, status))."""
    if not db_portfolio_bestaat(code):
        return None, (jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404)
    try:
        return bepaal_korte_naam_voorstellen(code), None
    except YahooNamenOnbeschikbaar:
        return None, (jsonify({"error": "Yahoo gaf geen namen terug. Probeer het later opnieuw."}), 502)


@app.route("/api/portfolio/<code>/korte-namen")
def get_korte_namen(code):
    code = code.strip().upper()
    voorstellen, fout = _korte_namen_voorstellen_of_fout(code)
    if fout:
        return fout
    return jsonify({"namen": voorstellen})


@app.route("/api/portfolio/<code>/korte-namen", methods=["POST"])
def pas_korte_namen_toe(code):
    code = code.strip().upper()
    voorstellen, fout = _korte_namen_voorstellen_of_fout(code)
    if fout:
        return fout

    nieuwe_namen = {v["ticker"]: v["voorstel"] for v in voorstellen if v["voorstel"]}
    if nieuwe_namen:
        db_wijzig_bijnamen(code, nieuwe_namen)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>", methods=["DELETE"])
def verwijder_portfolio(code):
    code = code.strip().upper()
    db_delete_portfolio(code)
    wis_portfolio_basis_cache(code)
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

    success, foutmelding = db_wijzig_portfolio_code(code, nieuwe_code)
    if not success:
        return jsonify({"error": foutmelding}), 400

    wis_portfolio_basis_cache(code)
    wis_portfolio_basis_cache(nieuwe_code)
    return jsonify(build_portfolio_response(nieuwe_code, verversen=False))

if __name__ == "__main__":
    app.run(debug=True)