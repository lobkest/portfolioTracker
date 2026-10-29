# TIJDELIJK: verwijderen na het onderzoek naar de 401 op Render. Niets in de app hangt hiervan af.
"""Wat Yahoo op Render terugstuurt per stap, voor de afgeschermde route /api/diagnose/yahoo."""
import hmac
import importlib.metadata
import os
import time

import yfinance as yf
import yahooquery
from yahooquery.session_management import initialize_session

from backend.yahoo_client import _tel_yahoo_call

MAX_TICKERS = 3
STANDAARD_TICKERS = "SPY,TDIV.AS"
FOUT_MAX_TEKENS = 300
BODY_MAX_TEKENS = 200
SLEUTELS_MAX = 15
CRUMB_MAX_TEKENS = 32
FC_URL = "https://fc.yahoo.com"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"


def token_klopt(opgegeven, verwacht):
    """Zonder ingesteld token of bij een afwijkend token altijd False."""
    if not verwacht or not isinstance(opgegeven, str):
        return False
    return hmac.compare_digest(opgegeven.encode(), verwacht.encode())


def parse_tickers(tekst, max_tekens):
    """(tickers, fout); lege tekst geeft de standaard."""
    delen = [d.strip() for d in (tekst or STANDAARD_TICKERS).split(",")]
    if len(delen) > MAX_TICKERS:
        return None, f"Maximaal {MAX_TICKERS} tickers."
    if any(not d or len(d) > max_tekens for d in delen):
        return None, "Geef tickers van normale lengte."
    return delen, None


def _afkappen(tekst, n):
    return str(tekst)[:n]


def _stap(functie, soort):
    _tel_yahoo_call(f"diagnose.{soort}")
    start = time.time()
    try:
        samenvatting = functie()
        return {"ok": True, "duur_s": round(time.time() - start, 2), "type_fout": None, "fout": None,
                "samenvatting": samenvatting}
    except Exception as e:
        return {"ok": False, "duur_s": round(time.time() - start, 2), "type_fout": type(e).__name__,
                "fout": _afkappen(e, FOUT_MAX_TEKENS), "samenvatting": None}


def _info(t):
    info = yf.Ticker(t).info
    return {"aantal_sleutels": len(info), "eerste_sleutels": list(info)[:SLEUTELS_MAX],
            "currency": "currency" in info, "quoteType": "quoteType" in info}


def _top_holdings(t):
    return {"aantal_rijen": len(yf.Ticker(t).funds_data.top_holdings)}


def _history(t):
    ticker = yf.Ticker(t)
    rijen = len(ticker.history(period="5d"))
    return {"aantal_rijen": rijen, "metadata_sleutels": list(ticker.history_metadata)}


def _ruwe_tekst(antwoord, t):
    """Bij een string of een dict met een fout: de ruwe tekst, afgekapt."""
    if isinstance(antwoord, dict):
        antwoord = antwoord.get(t, antwoord)
    if isinstance(antwoord, str):
        return _afkappen(antwoord, FOUT_MAX_TEKENS)
    if isinstance(antwoord, dict) and ("error" in antwoord or "description" in antwoord):
        return _afkappen(antwoord, FOUT_MAX_TEKENS)
    return None


def _yq_price(t):
    antwoord = yahooquery.Ticker(t).price
    samenvatting = {"type": type(antwoord).__name__}
    ruw = _ruwe_tekst(antwoord, t)
    if ruw is not None:
        samenvatting["ruwe_tekst"] = ruw
    return samenvatting


def _yq_top_holdings(t):
    antwoord = yahooquery.Ticker(t).fund_top_holdings
    samenvatting = {"type": type(antwoord).__name__}
    if hasattr(antwoord, "shape"):
        samenvatting["aantal_rijen"] = len(antwoord)
    else:
        ruw = _ruwe_tekst(antwoord, t)
        if ruw is not None:
            samenvatting["ruwe_tekst"] = ruw
    return samenvatting


STAPPEN = (
    ("info", _info),
    ("funds_data", _top_holdings),
    ("history", _history),
    ("yahooquery_price", _yq_price),
    ("yahooquery_fund_top_holdings", _yq_top_holdings),
)


def _cookie_namen(sessie):
    return [c.name if hasattr(c, "name") else str(c) for c in sessie.cookies.jar]


def _cookie_waarden(sessie):
    return [c.value for c in sessie.cookies.jar if getattr(c, "value", None)]


def _veilige_body(tekst, waarden):
    """De eerste 200 tekens, zonder cookiewaarden en zonder de crumb zelf (een kort woord zonder opmaak)."""
    tekst = tekst or ""
    if tekst and "<" not in tekst and not any(ch.isspace() for ch in tekst) and len(tekst) <= CRUMB_MAX_TEKENS:
        return f"[waarde verborgen, {len(tekst)} tekens]"
    for waarde in waarden:
        tekst = tekst.replace(waarde, "***")
    return tekst[:BODY_MAX_TEKENS]


def _verzoek_samenvatting(sessie, url):
    response = sessie.get(url)
    return {"url": url, "status": response.status_code, "eindlocatie": str(response.url),
            "cookie_namen": _cookie_namen(sessie),
            "body": _veilige_body(response.text, _cookie_waarden(sessie))}


def crumb_stap():
    def werk():
        sessie = initialize_session()
        return [_verzoek_samenvatting(sessie, FC_URL), _verzoek_samenvatting(sessie, CRUMB_URL)]
    return _stap(werk, "crumb")


def _versie(pakket):
    try:
        return importlib.metadata.version(pakket)
    except importlib.metadata.PackageNotFoundError:
        return None


def omgeving():
    resultaat = {"yfinance": _versie("yfinance"), "yahooquery": _versie("yahooquery"),
                 "curl_cffi": _versie("curl_cffi")}
    if "RENDER_REGION" in os.environ:
        resultaat["RENDER_REGION"] = os.environ["RENDER_REGION"]
    return resultaat


def bouw_diagnose(tickers):
    return {
        "tijdelijk": True,
        "omgeving": omgeving(),
        "crumb": crumb_stap(),
        "tickers": {t: {naam: _stap(lambda f=f, t=t: f(t), f"{naam}") for naam, f in STAPPEN} for t in tickers},
    }
