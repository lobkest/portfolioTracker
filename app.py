import json
import math
import os
import re
import threading
import time
from datetime import date

from flask import (
    Flask, Response, copy_current_request_context, g, make_response, render_template, send_from_directory,
    request, jsonify, redirect, stream_with_context, url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from io import BytesIO
from werkzeug.datastructures import FileStorage
from xml.sax.saxutils import escape as xml_escape
import pandas as pd
from db import (
    db_transactie, db_init, db_delete_portfolio, db_wijzig_portfolio_code,
    db_portfolio_bestaat, db_wijzig_bijnaam, db_wijzig_bijnamen, db_herstel_echte_naam,
    db_wis_etf_proxies_voor_portfolio, db_get_order_ids, db_get_order_ids_bij_andere_portfolios,
    db_reset_verbinding_teller, db_log_verbinding_samenvatting, db_deel_verbinding, db_wijzig_ticker_voor_isins,
    db_get_dividenden, db_get_upload_meldingen, db_markeer_portfolio_gebruikt, db_verbinding_teller_stand,
    is_verbindingsfout, db_get_koers_splits,
)
from koersoverzicht import bouw_koersoverzicht, PERIODES as KOERS_PERIODES, STANDAARD_PERIODE as STANDAARD_KOERS_PERIODE
from bewaartermijn import ruim_verlopen_portfolios_op, BEWAARTERMIJN_MAANDEN
from ticker_classificatie import haal_long_names, bewaar_long_names
from prijzen import get_prices
from ticker_zekerheid import (
    verifieer_tickers_met_prijs_parallel, verifieer_ticker_met_prijs, backfill_verouderde_tickers,
    controleer_alle_transactieprijzen, bijnaam_na_tickerwissel,
)
from debug_utils import meet_tijd, maskeer_codes, print_traceback, maak_foutcode
from diagnostiek import voeg_diagnostiek_toe
from voortgang import (
    Voortgang, meld_stap, zet_voortgang, reset_voortgang, huidige_stap_tekst, STAPPEN_UPLOAD_OPSLAAN, STAPPEN_UPLOAD_NIET_OPSLAAN,
    STAPPEN_BIJWERKEN, STAPPEN_OPHALEN, STAPPEN_OPHALEN_ZONDER_HERBEPALEN,
)
from yahoo_client import (
    reset_yahoo_call_teller, log_yahoo_call_samenvatting, yahoo_teller_stand, meld_yahoo_samenvatting,
    DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, DIAGNOSTIEK_SLEUTEL_YAHOO_VERRIJKING,
)
from statistieken import bereken_benchmark_vergelijking, bereken_rendement_over_tijd, BENCHMARK_TICKERS
from dividend import (
    bereken_dividend_samenvatting, bouw_dividend_samenvatting, lees_rekeningoverzicht, order_ids_uit_rekeningoverzicht_df,
    bereken_kassaldo, bouw_rekening_regels, promotie_bedragen,
)
from dividend_verwachting import bereken_dividend_verwachting
from historisch_rendement import bereken_historisch_rendement
from portfolio_admin import (
    is_geldige_code, CODE_LENGTH, controleer_eigen_transactiebestand, controleer_eigen_rekeningoverzicht,
    FOUT_ANDERE_PORTFOLIO, FOUT_TRANSACTIES_ONTBREKEN, FOUT_GEEN_ORDER_IDS, FOUT_ONBEKENDE_TRANSACTIES,
    GeenVrijeCode, UploadHoortBijBestaandePortfolio, valideer_bijnaam, MELDING_GEEN_VRIJE_CODE,
)
from toegangslimiet import (
    Toegangslimiet, wachttijd_tekst, SLEUTEL_GLOBAAL, CATEGORIE_FOUTE_CODE, CATEGORIE_FOUTE_CODE_GLOBAAL,
    CATEGORIE_GEVOELIGE_ACTIE, CATEGORIE_UPLOAD, CATEGORIE_EXTERNE_BRON, CATEGORIE_BEREKENING,
)
from upload_verwerking import (
    lees_transacties_excel, voeg_koers_eur_toe, OngeldigExcelBestand, ticker_resolutie_niet_opslaan,
    bouw_transacties_df_niet_opslaan, vul_synthetische_order_ids_aan, vind_of_maak_portfolio,
    voeg_nieuwe_transacties_toe, bepaal_product_per_ticker, sla_dividend_bestand_op, verwerk_dividend_zonder_opslaan,
    sla_kassaldo_op, vul_bronkolommen_aan, sluit_hernoemde_synthetische_rijen_uit, sla_rekening_regels_op,
    meld_portfolio_opslaan, bewaar_upload_meldingen, upload_meldingen_voor_api, UPLOAD_SOORT_UPLOAD,
    UPLOAD_SOORT_BIJWERKEN,
)
from portfolio_orchestratie import (
    haal_portfolio_basis, wis_portfolio_basis_cache, laad_transacties_en_resultaat,
    laad_split_gecorrigeerde_transacties, pas_effectieve_datums_toe, continue_koersreeks,
    ticker_zekerheid_groepen, build_portfolio_response, bouw_transacties_lijst, analyze_transacties_verrijking, analyze_transacties,
    bouw_verdeling_over_tijd, VERDELING_OVER_TIJD_DIMENSIES,
    bepaal_korte_naam_voorstellen, YahooNamenOnbeschikbaar, meld_valuta_consistentie, ticker_per_isin_beurs_uit_basis,
    meld_order_ids_rekening, meld_order_ids_rekening_uit_bestanden, isin_ticker_product_rijen,
)
from portfolio_verdeling import bereken_etf_holdings_lijst, bereken_etf_overlap_detail
from box3 import bouw_box3_basis, bereken_box3, valideer_box3_invoer
from portfolio_calc import holdings_op_datums
from static_versie import registreer_static_versies
from yahoo_client import init_yfinance_cache

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# X-Forwarded-For is "bezoeker, Cloudflare, Render-proxy": drie hops. Elke proxy voegt achteraan toe, dus een
# bezoeker kan adressen vóóraan meesturen maar de derde positie van achteren niet vervalsen.
PROXY_AANTAL_X_FOR = 3
PROXY_AANTAL_X_PROTO = 1
ONBEKEND_IP = "onbekend"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXY_AANTAL_X_FOR, x_proto=PROXY_AANTAL_X_PROTO)
registreer_static_versies(app)
init_yfinance_cache()

# Zonder DATABASE_URL (CI/tests) overslaan, zodat 'import app' niet crasht.
if os.environ.get("DATABASE_URL"):
    db_init()
    ruim_verlopen_portfolios_op()


MELDING_ONGELDIGE_CODE = "ongeldige-code"
MELDING_BESTAND_TE_GROOT = f"Het bestand is te groot (maximaal {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."

GEVOELIGE_ENDPOINTS = frozenset({"verwijder_portfolio", "wijzig_code"})
# Code-loze routes die zwaar zijn (Yahoo/externe bronnen of grote invoer).
CATEGORIE_PER_ZWAAR_ENDPOINT = {
    "upload": CATEGORIE_UPLOAD,
    "ticker_zekerheid_check": CATEGORIE_EXTERNE_BRON,
    "etf_overlap_detail": CATEGORIE_EXTERNE_BRON,
    "box3_bereken": CATEGORIE_BEREKENING,
}
LIMIET_MELDING_PER_CATEGORIE = {
    CATEGORIE_FOUTE_CODE: "Te veel foute pogingen met een code.",
    CATEGORIE_FOUTE_CODE_GLOBAAL: "Ophalen met een code is tijdelijk niet mogelijk vanwege veel foute pogingen.",
    CATEGORIE_GEVOELIGE_ACTIE: "Je hebt te veel keer een code gewijzigd of een portfolio verwijderd.",
    CATEGORIE_UPLOAD: "Je hebt te veel bestanden geüpload.",
    CATEGORIE_EXTERNE_BRON: "Je hebt te veel aanvragen gedaan.",
    CATEGORIE_BEREKENING: "Je hebt te veel aanvragen gedaan.",
}

# De tellers staan in het geheugen: ze resetten bij een herstart en gelden per gunicorn-worker.
toegangslimiet = Toegangslimiet()

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


def client_ip():
    """Het IP waarop de limieten tellen; hier aanpassen als Render een andere header blijkt te gebruiken."""
    return request.remote_addr or ONBEKEND_IP


def _markeer_gebruikt(code):
    with db_transactie() as cur:
        db_markeer_portfolio_gebruikt(cur, code)


def _geen_portfolio(code):
    """404 voor een onbekende code; telt als foute poging (zie na_request)."""
    g.foute_code_poging = True
    return jsonify({"error": f"Geen portfolio gevonden met code '{code}'."}), 404


def _limiet_antwoord(categorie, wachttijd):
    response = jsonify({
        "error": f"{LIMIET_MELDING_PER_CATEGORIE[categorie]} Probeer het {wachttijd_tekst(wachttijd)} opnieuw."
    })
    response.status_code = 429
    response.headers["Retry-After"] = str(wachttijd)
    return response


def _is_code_api_route():
    return bool(request.view_args and "code" in request.view_args and request.path.startswith("/api/"))


@app.before_request
def voor_request():
    ip = client_ip()
    if request.endpoint in CATEGORIE_PER_ZWAAR_ENDPOINT:
        categorie = CATEGORIE_PER_ZWAAR_ENDPOINT[request.endpoint]
        wacht = toegangslimiet.verbruik(categorie, ip)
        if wacht:
            return _limiet_antwoord(categorie, wacht)
    if not _is_code_api_route():
        return None
    for categorie, sleutel in ((CATEGORIE_FOUTE_CODE, ip), (CATEGORIE_FOUTE_CODE_GLOBAAL, SLEUTEL_GLOBAAL)):
        wacht = toegangslimiet.wachttijd(categorie, sleutel)
        if wacht:
            return _limiet_antwoord(categorie, wacht)
    if request.endpoint in GEVOELIGE_ENDPOINTS:
        wacht = toegangslimiet.verbruik(CATEGORIE_GEVOELIGE_ACTIE, ip)
        if wacht:
            return _limiet_antwoord(CATEGORIE_GEVOELIGE_ACTIE, wacht)
    return None


def registreer_foute_code_poging():
    if g.get("foute_code_poging"):
        toegangslimiet.registreer(CATEGORIE_FOUTE_CODE, client_ip())
        toegangslimiet.registreer(CATEGORIE_FOUTE_CODE_GLOBAAL, SLEUTEL_GLOBAAL)


def _teller_stand():
    aantal, seconden = db_verbinding_teller_stand()
    return aantal, seconden, yahoo_teller_stand()[0]


def _als_voortgangsstroom(werk, stappen, foutmelding, flow):
    """NDJSON-stroom: voortgangsregels en als laatste een `klaar`-regel met de status en JSON van `werk()`."""
    voortgang = Voortgang(stappen, _teller_stand, flow)

    # De kopie heeft een eigen `g`: de foute-code-poging uit `werk` moet hier geregistreerd worden, na_request ziet hem niet.
    @copy_current_request_context
    def draai():
        token = zet_voortgang(voortgang)
        status, data = 500, {"error": foutmelding}
        try:
            response = make_response(werk())
            status, data = response.status_code, response.get_json()
            registreer_foute_code_poging()
        except Exception as e:
            print(f"[voortgang] ONVERWACHTE FOUT: {maskeer_codes(e)}")
            print_traceback()
        finally:
            reset_voortgang(token)
            voortgang.klaar(status, data)

    threading.Thread(target=draai, daemon=True).start()

    def regels():
        while True:
            event = voortgang.events.get()
            yield json.dumps(event) + "\n"
            if event["type"] == "klaar":
                return

    return Response(stream_with_context(regels()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.after_request
def na_request(response):
    registreer_foute_code_poging()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.errorhandler(413)
def te_groot(_fout):
    return jsonify({"error": MELDING_BESTAND_TE_GROOT}), 413


FAVICON_BESTAND = "favicon/favicon.ico"


@app.route("/favicon.ico")
def favicon():
    """Browsers vragen het icoon op de root, los van de link in de head."""
    return send_from_directory(app.static_folder, FAVICON_BESTAND)


@app.route("/ping")
def ping():
    response = jsonify({"ok": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/sw.js")
def service_worker():
    """Op de root geserveerd, zodat de scope de hele site is."""
    response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/robots.txt")
def robots_txt():
    regels = [
        "User-agent: *",
        "Allow: /",
        "Allow: /privacy",
        "Disallow: /p/",
        "Disallow: /api/",
        "Disallow: /analyse",
        "Disallow: /upload",
        "",
        f"Sitemap: {request.url_root}sitemap.xml",
    ]
    return Response("\n".join(regels) + "\n", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    regels = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{xml_escape(request.url_root)}</loc></url>",
        f"  <url><loc>{xml_escape(request.url_root)}privacy</loc></url>",
        "</urlset>",
    ]
    return Response("\n".join(regels) + "\n", mimetype="application/xml")


@app.route("/")
def home():
    return render_template("start.html", code_lengte=CODE_LENGTH, bewaartermijn_maanden=BEWAARTERMIJN_MAANDEN)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", bewaartermijn_maanden=BEWAARTERMIJN_MAANDEN)


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
    # Hier, in de hoofdthread: een 413 hoort een gewoon antwoord te zijn en de bestanden moeten de request overleven.
    bestanden = _lees_bestanden()
    if request.args.get("voortgang") != "1":
        return _upload_uitvoeren(bestanden)
    stappen = STAPPEN_UPLOAD_NIET_OPSLAAN if request.form.get("niet_opslaan") == "on" else STAPPEN_UPLOAD_OPSLAAN
    flow = "upload niet opslaan" if stappen is STAPPEN_UPLOAD_NIET_OPSLAAN else "upload opslaan"
    return _als_voortgangsstroom(lambda: _upload_uitvoeren(bestanden), stappen, MELDING_UPLOAD_MISLUKT, flow)


MELDING_UPLOAD_MISLUKT = "Er ging iets onverwachts mis tijdens de upload. Probeer het opnieuw."

MELDING_DB_ONDERBROKEN = (
    "De verbinding met de database werd onderbroken. Klik nog een keer op Uploaden; meestal lukt het dan wel."
)


def _melding_onverwachte_fout(stap, foutcode):
    waar = f" bij de stap '{stap}'" if stap else ""
    return (f"Er ging iets onverwachts mis{waar}. Probeer het opnieuw. "
            f"Blijft het misgaan, noem dan foutcode {foutcode}.")


def _upload_uitvoeren(bestanden):
    """Zet elke onverwachte fout om in een JSON-foutrespons; anders blijft de frontend hangen."""
    db_reset_verbinding_teller()
    try:
        with meet_tijd("upload_totaal"):
            response = _upload_impl(bestanden)
        ruim_verlopen_portfolios_op()
        return response
    except GeenVrijeCode:
        return jsonify({"error": MELDING_GEEN_VRIJE_CODE}), 503
    except HTTPException:
        raise
    except Exception as e:
        foutcode = maak_foutcode()
        print(f"[upload] ONVERWACHTE FOUT (foutcode {foutcode}): {maskeer_codes(e)}")
        print_traceback()
        if is_verbindingsfout(e):
            return jsonify({"error": MELDING_DB_ONDERBROKEN}), 503
        return jsonify({"error": _melding_onverwachte_fout(huidige_stap_tekst(), foutcode)}), 500
    finally:
        db_log_verbinding_samenvatting()


def _upload_impl(bestanden):
    reset_yahoo_call_teller()
    meld_stap("bestand_inlezen")
    bestand1 = bestanden["bestand1"]
    if not bestand1:
        return jsonify({"error": "Het eerste bestand (transacties) is verplicht."}), 400

    try:
        with meet_tijd("excel_inlezen_pandas"):
            df = voeg_koers_eur_toe(lees_transacties_excel(bestand1))
        rekening_df = None
        bestand2 = bestanden["bestand2"]
        if bestand2:
            with meet_tijd("rekeningoverzicht_inlezen"):
                rekening_df = lees_rekeningoverzicht(bestand2)
    except OngeldigExcelBestand as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"[upload] FOUT in stap bestand_inlezen: {maskeer_codes(e)}")
        raise

    naam = request.form.get("naam", "").strip()
    if request.form.get("niet_opslaan") == "on":
        result = _analyseer_zonder_opslaan(df, rekening_df, naam)
    else:
        herbepaal_alle_tickers = request.form.get("herbepaal_alle_tickers") == "on"
        try:
            result = _upload_opslaan(df, rekening_df, naam, herbepaal_alle_tickers)
        except UploadHoortBijBestaandePortfolio as e:
            g.foute_code_poging = True
            return jsonify({"error": str(e)}), 400
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
    meld_stap("klaar_maken")
    response = jsonify(voeg_diagnostiek_toe(result))
    log_yahoo_call_samenvatting()
    return response


def _analyseer_zonder_opslaan(df, rekening_df, naam):
    # Alleen de lichte ticker-check: de volledige liep hier over de gunicorn-timeout (zie CLAUDE.md: Yahoo en tickers).
    meld_stap("tickers_zoeken")
    ticker_by_isin_beurs, ticker_zekerheid, ticker_posities_ruw = ticker_resolutie_niet_opslaan(df)
    product_per_ticker = bepaal_product_per_ticker(df, ticker_by_isin_beurs)
    transacties_df = bouw_transacties_df_niet_opslaan(df, ticker_by_isin_beurs, product_per_ticker)
    kassaldo = bereken_kassaldo(rekening_df) if rekening_df is not None else None
    dividend_records = verwerk_dividend_zonder_opslaan(rekening_df) if rekening_df is not None else None
    rekening_regels = bouw_rekening_regels(rekening_df) if rekening_df is not None else None
    promoties = promotie_bedragen(rekening_regels) if rekening_regels is not None else None
    meld_stap("koersen_analyseren")
    result = analyze_transacties(transacties_df, code=None, naam=naam or None, kassaldo=kassaldo, box3=True,
                                 box3_dividenden=dividend_records or None, promoties=promoties,
                                 rekening_regels=rekening_regels)
    meld_valuta_consistentie(df, ticker_by_isin_beurs)
    if rekening_df is not None:
        meld_order_ids_rekening_uit_bestanden(df, rekening_df)
    result["ticker_zekerheid"] = ticker_zekerheid
    result["ticker_posities_ruw"] = ticker_posities_ruw
    result["dividend"] = _dividend_niet_opslaan(transacties_df, dividend_records)
    return result


def _dividend_niet_opslaan(transacties_df, dividend_records):
    samenvatting = bouw_dividend_samenvatting(dividend_records, isin_ticker_product_rijen(transacties_df))
    if samenvatting is None:
        return {"beschikbaar": False}
    return {**samenvatting, "beschikbaar": True}


def _upload_opslaan(df, rekening_df, naam, herbepaal_alle_tickers):
    df = vul_synthetische_order_ids_aan(df)
    with db_transactie() as cur:
        meld_stap("portfolio_zoeken")
        with meet_tijd("portfolio_zoeken_of_maken"):
            code, bestaand, rows_to_insert = vind_of_maak_portfolio(cur, df, naam)
        db_markeer_portfolio_gebruikt(cur, code)
        if bestaand and herbepaal_alle_tickers:
            _herbepaal_tickers(code)
        voeg_nieuwe_transacties_toe(cur, code, rows_to_insert, herbepaal_alle_tickers)
        vul_bronkolommen_aan(cur, code, df)
        if rekening_df is not None:
            meld_stap("rekeningoverzicht")
            sla_dividend_bestand_op(cur, code, rekening_df)
            sla_kassaldo_op(cur, code, rekening_df)
            sla_rekening_regels_op(cur, code, rekening_df)
    meld_stap("koersen_analyseren")
    with db_deel_verbinding():
        result = _kern_na_opslaan(code)
        _meld_valuta_na_opslaan(df, code)
        meld_order_ids_rekening(code)
    bewaar_upload_meldingen(code, UPLOAD_SOORT_UPLOAD)
    return result


def _herbepaal_tickers(code):
    """'Ticker-informatie opnieuw bepalen': opgeslagen tickers herzoeken en land-proxy's opnieuw laten zoeken."""
    with meet_tijd("db_backfill_verouderde_tickers"):
        backfill_verouderde_tickers(code)
    db_wis_etf_proxies_voor_portfolio(code)


def _kern_na_opslaan(code):
    """Pas aanroepen ná alle mutaties, binnen db_deel_verbinding(): wist de basis-cache en bouwt de kern opnieuw op."""
    wis_portfolio_basis_cache(code)
    return build_portfolio_response(code)


def _meld_valuta_na_opslaan(excel_df, code):
    with meet_tijd("valuta_consistentie"):
        meld_valuta_consistentie(excel_df, ticker_per_isin_beurs_uit_basis(code))


def _lees_bestanden():
    """{veld: FileStorage of None}, volledig in het geheugen: Flask sluit de geüploade bestanden na de request, ook
    als het werk nog in de thread van een voortgangsstroom loopt. Een lege selectie (geen bestandsnaam) is None."""
    bestanden = {}
    for veld in ("bestand1", "bestand2"):
        bestand = request.files.get(veld)
        if bestand and bestand.filename != "":
            bestand = FileStorage(stream=BytesIO(bestand.read()), filename=bestand.filename,
                                  content_type=bestand.content_type)
        else:
            bestand = None
        bestanden[veld] = bestand
    return bestanden


@app.route("/api/portfolio/<code>/bijwerken", methods=["POST"])
def bijwerken(code):
    bestanden = _lees_bestanden()
    if request.args.get("voortgang") != "1":
        return _bijwerken_uitvoeren(code, bestanden)
    return _als_voortgangsstroom(
        lambda: _bijwerken_uitvoeren(code, bestanden), STAPPEN_BIJWERKEN, MELDING_BIJWERKEN_MISLUKT, "bijwerken")


MELDING_BIJWERKEN_MISLUKT = "Bijwerken duurde te lang of is mislukt. Probeer het opnieuw."


def _bijwerken_uitvoeren(code, bestanden):
    """Zelfde vangnet als /upload."""
    db_reset_verbinding_teller()
    try:
        with meet_tijd("bijwerken_totaal"):
            return _bijwerken_impl(code.strip().upper(), bestanden)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[bijwerken] ONVERWACHTE FOUT: {maskeer_codes(e)}")
        print_traceback()
        return jsonify({"error": MELDING_BIJWERKEN_MISLUKT}), 500
    finally:
        db_log_verbinding_samenvatting()


def _bijwerken_impl(code, bestanden):
    """Alleen opslaan als élk meegestuurd bestand bij deze portfolio hoort."""
    reset_yahoo_call_teller()
    meld_stap("bestand_inlezen")
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)

    bestand1 = bestanden["bestand1"]
    bestand2 = bestanden["bestand2"]
    if not bestand1 and not bestand2:
        return jsonify({"error": "Kies minstens één bestand."}), 400

    df = None
    if bestand1:
        try:
            df = vul_synthetische_order_ids_aan(voeg_koers_eur_toe(lees_transacties_excel(bestand1)))
        except OngeldigExcelBestand as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            print(f"[bijwerken] FOUT in stap bestand_inlezen: {maskeer_codes(e)}")
            raise
    nieuw = set(df["Order ID"]) if df is not None else set()

    meld_stap("bestanden_controleren")
    with meet_tijd("order_ids_ophalen"), db_transactie() as cur:
        opgeslagen = db_get_order_ids(cur, code)
        bij_andere_portfolios = db_get_order_ids_bij_andere_portfolios(cur, code, nieuw)

    fout, toe_te_voegen = None, set()
    if df is not None:
        fout, toe_te_voegen = controleer_eigen_transactiebestand(opgeslagen, nieuw, bij_andere_portfolios)
    rekening_df = None
    if not fout and bestand2:
        try:
            with meet_tijd("rekeningoverzicht_inlezen"):
                rekening_df = lees_rekeningoverzicht(bestand2)
        except OngeldigExcelBestand as e:
            return jsonify({"error": str(e)}), 400
        fout = controleer_eigen_rekeningoverzicht(order_ids_uit_rekeningoverzicht_df(rekening_df), opgeslagen | nieuw)
    if fout:
        return jsonify({"error": MELDING_PER_EIGENDOMSFOUT[fout]}), 400

    rows_to_insert = df[df["Order ID"].isin(toe_te_voegen)] if df is not None else pd.DataFrame()
    if not rows_to_insert.empty:
        with db_transactie() as cur:
            rows_to_insert = sluit_hernoemde_synthetische_rijen_uit(cur, code, rows_to_insert, nieuw)
    meld_portfolio_opslaan(True, rows_to_insert)
    if df is not None or rekening_df is not None:
        if rows_to_insert.empty:
            meld_stap("transacties_opslaan")
        with db_transactie() as cur:
            db_markeer_portfolio_gebruikt(cur, code)
            voeg_nieuwe_transacties_toe(cur, code, rows_to_insert, herbepaal_alle_tickers=False)
            if df is not None:
                vul_bronkolommen_aan(cur, code, df)
            if rekening_df is not None:
                sla_dividend_bestand_op(cur, code, rekening_df)
                sla_kassaldo_op(cur, code, rekening_df)
                sla_rekening_regels_op(cur, code, rekening_df)
    meld_stap("koersen_analyseren")
    with db_deel_verbinding():
        result = _kern_na_opslaan(code)
        if df is not None:
            _meld_valuta_na_opslaan(df, code)
        meld_order_ids_rekening(code)
    bewaar_upload_meldingen(code, UPLOAD_SOORT_BIJWERKEN)
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "upload")
    meld_stap("klaar_maken")
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
    if request.args.get("voortgang") != "1":
        return _portfolio_ophalen(code)
    herbepalen = request.args.get("herbepaal_alle_tickers", "").lower() == "true"
    stappen = STAPPEN_OPHALEN if herbepalen else STAPPEN_OPHALEN_ZONDER_HERBEPALEN
    return _als_voortgangsstroom(lambda: _portfolio_ophalen(code), stappen, MELDING_OPHALEN_MISLUKT, "ophalen")


MELDING_OPHALEN_MISLUKT = "Ophalen mislukt. Probeer het opnieuw."


def _portfolio_ophalen(code):
    reset_yahoo_call_teller()
    db_reset_verbinding_teller()
    if request.args.get("herbepaal_alle_tickers", "").lower() == "true":
        if not db_portfolio_bestaat(code):
            return _geen_portfolio(code)
        meld_stap("tickers_herbepalen")
        _herbepaal_tickers(code)
        # Anders levert de _basis_cache de oude tickers.
        wis_portfolio_basis_cache(code)
    meld_stap("koersen_analyseren")
    with meet_tijd("ophalen_totaal"), db_deel_verbinding():
        result = build_portfolio_response(code)
    if result is None:
        return _geen_portfolio(code)
    _markeer_gebruikt(code)
    log_yahoo_call_samenvatting()
    db_log_verbinding_samenvatting()
    meld_yahoo_samenvatting(DIAGNOSTIEK_SLEUTEL_YAHOO_KERN, "ophalen")
    meld_stap("klaar_maken")
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
        return _geen_portfolio(code)

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


@app.route("/api/portfolio/<code>/verdeling-over-tijd")
def verdeling_over_tijd(code):
    code = code.strip().upper()
    dimensie = request.args.get("dimensie", "")
    if dimensie not in VERDELING_OVER_TIJD_DIMENSIES:
        return jsonify({"error": f"Onbekende dimensie '{dimensie}'."}), 400
    samenvoegen = request.args.get("samenvoegen") == "1"
    azie_samenvoegen = request.args.get("azie") == "1"
    try:
        with db_deel_verbinding():
            result = bouw_verdeling_over_tijd(code, dimensie, samenvoegen, azie_samenvoegen)
    except Exception as e:
        print(f"[over-tijd] WARN {dimensie} mislukt: {type(e).__name__}: {maskeer_codes(e)!a}")
        return jsonify({
            "error": "De verdeling over tijd berekenen is mislukt of duurde te lang. Probeer het opnieuw: "
                     "wat al is opgehaald blijft bewaard, dus een tweede poging gaat sneller."
        }), 500
    if result is None:
        return _geen_portfolio(code)
    return jsonify(result)


@app.route("/api/etf-overlap-detail")
def etf_overlap_detail():
    """Zonder portfolio-code, zodat het ook bij 'niet opslaan' werkt."""
    etf_a = request.args.get("a", "").strip()
    etf_b = request.args.get("b", "").strip()
    if not etf_a or not etf_b:
        return jsonify({"error": "Query-parameters 'a' en 'b' (ETF-tickers) zijn verplicht."}), 400
    return jsonify({"holdings": bereken_etf_overlap_detail(etf_a, etf_b)})


@app.route("/api/etf-holdings")
def etf_holdings():
    """Zonder portfolio-code, zodat het ook bij 'niet opslaan' werkt. 'waarde' = huidige waarde van de positie in EUR."""
    ticker = request.args.get("ticker", "").strip()
    if not ticker:
        return jsonify({"error": "Query-parameter 'ticker' is verplicht."}), 400
    waarde = None
    if request.args.get("waarde", "").strip():
        try:
            waarde = float(request.args["waarde"])
        except ValueError:
            waarde = None
        if waarde is None or not math.isfinite(waarde):
            return jsonify({"error": "Query-parameter 'waarde' moet een getal zijn."}), 400
    return jsonify(bereken_etf_holdings_lijst(ticker, waarde))


@app.route("/api/portfolio/<code>/benchmark-vergelijking")
def benchmark_vergelijking(code):
    """Query-param 'benchmark' (sleutel uit BENCHMARK_TICKERS) of 'eigen_ticker' (ticker uit de portfolio)."""
    code = code.strip().upper()
    benchmark_naam = request.args.get("benchmark", "")
    eigen_ticker = request.args.get("eigen_ticker", "")

    transacties_df, resultaat = laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return _geen_portfolio(code)
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
        return _geen_portfolio(code)
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    return jsonify(bereken_rendement_over_tijd(transacties_df, resultaat))


@app.route("/api/portfolio/<code>/koersoverzicht")
def koersoverzicht(code):
    code = code.strip().upper()
    periode = request.args.get("periode", STANDAARD_KOERS_PERIODE)
    if periode not in KOERS_PERIODES:
        return jsonify({"error": f"Onbekende periode '{periode}'."}), 400
    _naam, transacties_df, price_data = haal_portfolio_basis(code)
    if transacties_df is None:
        return _geen_portfolio(code)
    if price_data.empty:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    splits = transacties_df.attrs.get("koers_splits")
    if splits is None:
        splits = db_get_koers_splits(transacties_df["ticker"].dropna().unique().tolist())
    return jsonify(bouw_koersoverzicht(transacties_df, price_data, splits, periode, date.today()))


@app.route("/api/portfolio/<code>/box3")
def box3_basis(code):
    code = code.strip().upper()
    transacties_df, resultaat = laad_transacties_en_resultaat(code)
    if transacties_df is None:
        return _geen_portfolio(code)
    if resultaat is None:
        return jsonify({"error": "Geen koersdata voor deze portfolio."}), 400

    # Lege lijst = nooit een rekeningoverzicht geüpload, zoals bij /dividend.
    return jsonify(bouw_box3_basis(transacties_df, resultaat, db_get_dividenden(code) or None))


@app.route("/api/box3/bereken", methods=["POST"])
def box3_bereken():
    """Stateless: de basis komt van de frontend, de invoer wordt nergens opgeslagen."""
    data = request.get_json(silent=True) or {}
    invoer, fout = valideer_box3_invoer(data.get("invoer"))
    if fout:
        return jsonify({"error": fout}), 400
    basis = data.get("basis")
    try:
        return jsonify(bereken_box3(basis, invoer))
    except (KeyError, TypeError, ValueError, AttributeError):
        return jsonify({"error": "Ongeldige portfoliogegevens voor de box 3-berekening."}), 400


@app.route("/api/portfolio/<code>/ticker-koers-bereik")
def ticker_koers_bereik(code):
    """Koersen van 1 ticker buiten de standaard-crop. Query-params: ticker, vanaf, tot (optioneel)."""
    code = code.strip().upper()
    # Niet haal_portfolio_basis(): die haalt koersen van alle tickers op.
    transacties_df = laad_split_gecorrigeerde_transacties(code)
    if transacties_df is None:
        return _geen_portfolio(code)

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
        return _geen_portfolio(code)

    posities = [
        {"isin": isin, "isins": info["isins"], "beurs": beurs, "naam": info["naam"], "echte_naam": info["echte_naam"]}
        for (isin, beurs), info in groepen
    ]
    return jsonify({"posities": posities})


@app.route("/api/portfolio/<code>/ticker-zekerheid/positie")
def ticker_zekerheid_positie(code):
    """Eén positie per aanroep, zodat elke aanroep ruim binnen de gunicorn-timeout blijft."""
    with db_deel_verbinding():
        code = code.strip().upper()
        isin = request.args.get("isin", "")
        beurs = request.args.get("beurs", "")

        groepen = ticker_zekerheid_groepen(code)
        if groepen is None:
            return _geen_portfolio(code)

        info = dict(groepen).get((isin, beurs))
        if info is None:
            return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

        start = time.time()
        try:
            resultaat = verifieer_ticker_met_prijs(
                info["echte_naam"], isin, info["beurs"], info["transacties"], opgeslagen_ticker=info["ticker"],
            )
        except Exception as e:
            print(f"[ticker-zekerheid] {isin}: FOUT na {time.time() - start:.2f}s ({maskeer_codes(e)!a})")
            print_traceback()
            return jsonify({
                "error": "Ticker-zekerheid controleren voor deze positie is mislukt. Probeer het opnieuw."
            }), 500

        print(f"[ticker-zekerheid] {isin}: klaar in {time.time() - start:.2f}s (frontend breekt af na 30s)")
        resultaat["isin"] = isin
        resultaat["isins"] = info["isins"]
        resultaat["naam"] = info["naam"]
        resultaat["echte_naam"] = info["echte_naam"]
        return jsonify(resultaat)


@app.route("/api/portfolio/<code>/ticker-zekerheid/wijzig", methods=["POST"])
def ticker_zekerheid_wijzig(code):
    """Zet de ticker van alle rijen van één positie om, alle ISIN's van een wisselketen (de knop bij een aanbevolen alternatief)."""
    code = code.strip().upper()
    data = request.get_json(silent=True) or {}
    isin, beurs = data.get("isin") or "", data.get("beurs") or ""
    ticker = (data.get("ticker") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-=^]{1,32}", ticker):
        return jsonify({"error": "Ongeldige ticker."}), 400

    groepen = ticker_zekerheid_groepen(code)
    if groepen is None:
        return _geen_portfolio(code)
    info = dict(groepen).get((isin, beurs))
    if info is None:
        return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

    with db_transactie() as cur:
        db_wijzig_ticker_voor_isins(cur, code, info["isins"], beurs, ticker)
    print(f"[ticker-zekerheid] {', '.join(info['isins'])}: ticker {info['ticker']} -> {ticker} (via de knop)")

    bijnaam = None
    try:
        bijnaam = bijnaam_na_tickerwissel(info["naam"], info["ticker"], ticker)
        if bijnaam:
            db_wijzig_bijnaam(code, ticker, bijnaam)
            print(f"[ticker-zekerheid] {isin}: bijnaam -> {bijnaam!a}")
    except Exception as e:
        # De ticker staat er al; de bijnaam is bijzaak.
        print(f"[ticker-zekerheid] {isin}: WARN bijnaam niet aangepast ({maskeer_codes(e)!a})")
        bijnaam = None
    wis_portfolio_basis_cache(code)
    return jsonify({"ticker": ticker, "oude_ticker": info["ticker"], "bijnaam": bijnaam})


@app.route("/api/portfolio/<code>/ticker-zekerheid/alle-prijzen")
def ticker_zekerheid_alle_prijzen(code):
    """Elke transactie van één positie tegen de dagrange van de opgeslagen ticker; per positie, net als /positie."""
    with db_deel_verbinding():
        code = code.strip().upper()
        isin = request.args.get("isin", "")
        beurs = request.args.get("beurs", "")

        groepen = ticker_zekerheid_groepen(code)
        if groepen is None:
            return _geen_portfolio(code)

        info = dict(groepen).get((isin, beurs))
        if info is None:
            return jsonify({"error": f"Geen positie gevonden voor ISIN '{isin}' op beurs '{beurs}'."}), 404

        start = time.time()
        try:
            resultaat = controleer_alle_transactieprijzen(info["ticker"], info["transacties"])
        except Exception as e:
            print(f"[ticker-zekerheid] {isin}: alle prijzen FOUT na {time.time() - start:.2f}s ({maskeer_codes(e)!a})")
            print_traceback()
            return jsonify({"error": "Alle prijzen controleren voor deze positie is mislukt. Probeer het opnieuw."}), 500

        print(f"[ticker-zekerheid] {isin}: alle prijzen ({len(resultaat['prijs_checks'])}) "
              f"klaar in {time.time() - start:.2f}s")
        resultaat["isin"] = isin
        resultaat["naam"] = info["naam"]
        resultaat["ticker"] = info["ticker"]
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
        return _geen_portfolio(code)

    samenvatting = bereken_dividend_samenvatting(code)
    if samenvatting is None:
        return jsonify({"beschikbaar": False})

    samenvatting["beschikbaar"] = True
    return jsonify(samenvatting)


@app.route("/api/portfolio/<code>/upload-meldingen")
def upload_meldingen(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    return jsonify({"uploads": upload_meldingen_voor_api(db_get_upload_meldingen(code))})


@app.route("/api/portfolio/<code>/dividend-verwachting")
def dividend_verwachting(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)

    try:
        with meet_tijd("dividend_verwachting"):
            resultaat = bereken_dividend_verwachting(code)
    except Exception:
        print_traceback()
        return jsonify({"error": "Verwacht dividend ophalen is mislukt. Probeer het later opnieuw."}), 500
    return jsonify(voeg_diagnostiek_toe(resultaat or {"beschikbaar": False}))


@app.route("/api/portfolio/<code>/historisch-rendement")
def historisch_rendement(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)

    try:
        with meet_tijd("historisch_rendement"):
            resultaat = bereken_historisch_rendement(code)
    except Exception:
        print_traceback()
        return jsonify({"error": "Historisch rendement berekenen is mislukt. Probeer het later opnieuw."}), 500
    return jsonify(voeg_diagnostiek_toe(resultaat or {"beschikbaar": False}))


@app.route("/api/portfolio/<code>/transacties")
def transacties_overzicht(code):
    code = code.strip().upper()
    overzicht = bouw_transacties_lijst(code)
    if overzicht is None:
        return _geen_portfolio(code)
    return jsonify(overzicht)


@app.route("/api/portfolio/<code>/bijnaam", methods=["POST"])
def set_bijnaam(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker")
    bijnaam = (data.get("bijnaam") or "").strip()
    if not ticker or not bijnaam:
        return jsonify({"error": "Ticker en bijnaam zijn verplicht."}), 400
    if fout := valideer_bijnaam(bijnaam):
        return jsonify({"error": fout}), 400

    db_wijzig_bijnaam(code, ticker, bijnaam)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>/bijnamen", methods=["POST"])
def set_bijnamen(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    namen = (request.get_json(silent=True) or {}).get("namen")
    if not isinstance(namen, dict):
        return jsonify({"error": "Namen zijn verplicht."}), 400

    schoon = {t: n.strip() for t, n in namen.items() if isinstance(n, str) and n.strip()}
    if fout := next(filter(None, map(valideer_bijnaam, schoon.values())), None):
        return jsonify({"error": fout}), 400
    if schoon:
        db_wijzig_bijnamen(code, schoon)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


@app.route("/api/portfolio/<code>/reset-bijnaam", methods=["POST"])
def reset_bijnaam(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    data = request.get_json(silent=True) or {}
    ticker = data.get("ticker")
    if not ticker:
        return jsonify({"error": "Ticker is verplicht."}), 400

    long_names = haal_long_names([ticker])
    bewaar_long_names(long_names)
    long_name = long_names.get(ticker)
    if long_name:
        db_wijzig_bijnaam(code, ticker, long_name)
    else:
        db_herstel_echte_naam(code, ticker)
    wis_portfolio_basis_cache(code)
    return jsonify(build_portfolio_response(code, verversen=False))


def _korte_namen_voorstellen_of_fout(code):
    """(voorstellen, None) of (None, (JSON-fout, status))."""
    if not db_portfolio_bestaat(code):
        return None, _geen_portfolio(code)
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
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    db_delete_portfolio(code)
    wis_portfolio_basis_cache(code)
    return jsonify({"success": True})


@app.route("/api/portfolio/<code>/wijzig-code", methods=["POST"])
def wijzig_code(code):
    code = code.strip().upper()
    if not db_portfolio_bestaat(code):
        return _geen_portfolio(code)
    data = request.get_json(silent=True) or {}
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