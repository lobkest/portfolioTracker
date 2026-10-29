"""Wat-als: de portfolio vóór en ná een aankoop. Pure functies, geen netwerk en geen database; de aanroeper levert de
invoer (koers, aantal, holdings). "Vóór" gebruikt bewust dezelfde kernen als de tabbladen, zodat beide nooit verschillen."""
import math

from backend.effect_zoeken import herken_isin
from backend.portfolio_verdeling import (
    _groepeer_holdings_per_bedrijf, _normaliseer_bedrijfsnaam, bedrijf_waarden, bereken_verdeling_samenvatting,
    etf_overlap_uit, land_sector_verdeling_uit, verdeling_is_onbekend,
)

PCT_DECIMALEN = 4
EUR_DECIMALEN = 2
MAX_AANTAL_WAT_ALS = 1_000_000
MAX_POSITIES_WAT_ALS = 300
MAX_TICKER_TEKENS = 20
MAX_NAAM_TEKENS = 200

HOLDINGS_ONBEKEND = "onbekend"
HOLDINGS_TOP10 = "top10"
HOLDINGS_VOLLEDIG = "volledig"
BRON_TOP10 = "yfinance_top10"


def _pct(deel, totaal):
    return deel / totaal * 100 if totaal > 0 else 0.0


def _schoon(waarde):
    """Geen NaN of oneindig in de uitvoer: breekt JSON."""
    if isinstance(waarde, float):
        return waarde if math.isfinite(waarde) else None
    if isinstance(waarde, dict):
        return {k: _schoon(v) for k, v in waarde.items()}
    if isinstance(waarde, list):
        return [_schoon(v) for v in waarde]
    return waarde


def _rij(naam, voor_pct, na_pct):
    return {"naam": naam, "voor_pct": round(voor_pct, PCT_DECIMALEN), "na_pct": round(na_pct, PCT_DECIMALEN),
            "verschil_pp": round(na_pct - voor_pct, PCT_DECIMALEN)}


def _verschil_rijen(voor, na):
    """{naam: EUR} vóór en ná -> rijen op het ná-%, elk % ten opzichte van het eigen totaal. Rekent op de ongegroepeerde
    bedragen: 'Overig' bevat vóór en ná andere landen, waardoor een verschil daarop niets betekent."""
    totaal_voor, totaal_na = sum(voor.values()), sum(na.values())
    namen = list(na) + [n for n in voor if n not in na]
    rijen = [_rij(n, _pct(voor.get(n, 0.0), totaal_voor), _pct(na.get(n, 0.0), totaal_na)) for n in namen]
    rijen.sort(key=lambda r: r["na_pct"], reverse=True)
    return rijen


def _na_basis(basis, aankoop):
    """De basis met de aankoop erbij. Een ticker die al in de portfolio zit krijgt alleen extra waarde: één bron per
    ticker, dus de bestaande fracties en holdings blijven gelden en die van de aankoop worden genegeerd."""
    na = {k: dict(basis[k]) for k in
          ("waarde_per_ticker", "is_etf_map", "fracties_per_ticker", "holdings_per_etf", "aandeel_namen", "bron_namen")}
    na["extra_aandelen"] = dict(basis.get("extra_aandelen") or {})
    ticker = aankoop["ticker"]
    if ticker in na["waarde_per_ticker"]:
        na["waarde_per_ticker"][ticker] += aankoop["waarde_eur"]
        return na
    na["waarde_per_ticker"][ticker] = aankoop["waarde_eur"]
    na["is_etf_map"][ticker] = aankoop["is_etf"]
    na["fracties_per_ticker"][ticker] = aankoop["fracties"]
    na["bron_namen"][ticker] = aankoop["naam"]
    if aankoop["is_etf"]:
        na["holdings_per_etf"][ticker] = aankoop["holdings"] or []
    else:
        na["aandeel_namen"][ticker] = aankoop["naam"]
    return na


def _land_sector(basis):
    return land_sector_verdeling_uit(basis["waarde_per_ticker"], basis["is_etf_map"], basis["fracties_per_ticker"],
                                     basis.get("extra_aandelen"))


def _bedrijven(basis):
    return bedrijf_waarden(basis["waarde_per_ticker"], basis["is_etf_map"], basis["holdings_per_etf"],
                           basis["aandeel_namen"])[0]


def _posities(basis_voor, basis_na):
    totaal_voor, totaal_na = sum(basis_voor["waarde_per_ticker"].values()), sum(basis_na["waarde_per_ticker"].values())
    rijen = []
    for ticker, waarde in basis_na["waarde_per_ticker"].items():
        voor_pct = _pct(basis_voor["waarde_per_ticker"].get(ticker, 0.0), totaal_voor)
        rij = _rij(basis_na["bron_namen"].get(ticker, ticker), voor_pct, _pct(waarde, totaal_na))
        rijen.append({"ticker": ticker, "is_etf": basis_na["is_etf_map"].get(ticker, False),
                      "nieuw": ticker not in basis_voor["waarde_per_ticker"], **rij})
    rijen.sort(key=lambda r: r["na_pct"], reverse=True)
    return rijen


def _etf_aandeel(basis):
    samenvatting = bereken_verdeling_samenvatting(
        [{"waarde": w, "is_etf": basis["is_etf_map"].get(t, False)} for t, w in basis["waarde_per_ticker"].items()])
    return {"etf_pct": samenvatting["etf_pct"], "aandeel_pct": samenvatting["aandeel_pct"]}


def _etf_aandeel_voor_na(voor, na):
    """{voor, na, verschil_pp} met per categorie (etf_pct, aandeel_pct) hetzelfde verschil in pp als land en sector."""
    return {"voor": voor, "na": na, "verschil_pp": {k: round(na[k] - voor[k], PCT_DECIMALEN) for k in voor}}


def _top_bedrijven(bedrijven_voor, bedrijven_na, totaal_voor, totaal_na, top_n):
    top = sorted(bedrijven_na.items(), key=lambda kv: kv[1]["waarde"], reverse=True)[:top_n]
    return [_rij(entry["naam"], _pct(bedrijven_voor.get(key, {"waarde": 0.0})["waarde"], totaal_voor),
                 _pct(entry["waarde"], totaal_na)) for key, entry in top]


def _effectieve_holdings(basis, aankoop):
    """Bijkopen: de holdings van de bestaande positie gelden (één bron per ticker), niet die van de aankoop."""
    return basis["holdings_per_etf"].get(aankoop["ticker"], aankoop["holdings"]) or []


def _holdings_dekking(holdings):
    """(status, gedekt_pct): onbekend zonder holdings, top10 bij de yfinance-terugval, anders volledig."""
    if not holdings:
        return HOLDINGS_ONBEKEND, None
    gedekt = min(sum(h["gewicht"] for h in holdings if math.isfinite(h["gewicht"])), 1.0) * 100
    status = HOLDINGS_TOP10 if holdings[0].get("bron") == BRON_TOP10 else HOLDINGS_VOLLEDIG
    return status, round(gedekt, PCT_DECIMALEN)


def _onbekend(basis, aankoop):
    """Per onderdeel True als het voor deze ETF helemaal onbekend is; bijkopen gebruikt de bestaande positie."""
    if not aankoop["is_etf"]:
        return {"land": False, "sector": False, "bedrijven": False}
    fracties = basis["fracties_per_ticker"].get(aankoop["ticker"], aankoop["fracties"]) or {}
    return {"land": verdeling_is_onbekend(fracties.get("land_pct")), "sector": verdeling_is_onbekend(fracties.get("sector_pct")),
            "bedrijven": _holdings_dekking(_effectieve_holdings(basis, aankoop))[0] == HOLDINGS_ONBEKEND}


def _overlap(basis, aankoop, bedrijven_voor, totaal_voor):
    """per_etf: overlap-% van de aankoop met elke huidige ETF; al_in_portfolio_pct: hoeveel van de aankoop (in %) al
    als bedrijf in de huidige portfolio zit, via ETF's of direct. Zijn de holdings onbekend, dan zijn beide None
    (geen 0: dat zou 'geen overlap' beweren)."""
    holdings_nieuw = _effectieve_holdings(basis, aankoop)
    status, gedekt_pct = _holdings_dekking(holdings_nieuw)
    gewichten_nieuw = _groepeer_holdings_per_bedrijf(holdings_nieuw)[0]
    per_etf = {}
    zonder_holdings = 0
    for ticker in basis["waarde_per_ticker"]:
        if ticker == aankoop["ticker"] or not basis["is_etf_map"].get(ticker, False):
            continue
        if not basis["holdings_per_etf"].get(ticker):
            zonder_holdings += 1
            per_etf[ticker] = None
            continue
        if status == HOLDINGS_ONBEKEND:
            per_etf[ticker] = None
            continue
        gewichten = _groepeer_holdings_per_bedrijf(basis["holdings_per_etf"][ticker])[0]
        matrix = etf_overlap_uit({aankoop["ticker"]: gewichten_nieuw, ticker: gewichten})
        per_etf[ticker] = round(matrix[aankoop["ticker"]][ticker] * 100, PCT_DECIMALEN)
    al_in_portfolio = None if status == HOLDINGS_ONBEKEND else round(sum(
        min(gewicht * 100, _pct(bedrijven_voor[key]["waarde"], totaal_voor))
        for key, gewicht in gewichten_nieuw.items() if key in bedrijven_voor), PCT_DECIMALEN)
    return {"per_etf": per_etf, "al_in_portfolio_pct": al_in_portfolio, "al_in_portfolio_onvolledig": zonder_holdings > 0,
            "etf_zonder_holdings": zonder_holdings, "holdings_status": status, "gedekt_pct": gedekt_pct}


def _al_in_bezit(basis, aankoop, bedrijven_voor):
    """Direct en via ETF's al bezeten waarde van dit bedrijf. Matcht op de genormaliseerde naam: kan missen als de
    naam bij de broker anders gespeld is."""
    naam = basis["aandeel_namen"].get(aankoop["ticker"], aankoop["naam"])
    key = _normaliseer_bedrijfsnaam(naam) or aankoop["ticker"]
    entry = bedrijven_voor.get(key)
    if entry is None:
        return {"direct_eur": 0.0, "via_etf_eur": 0.0, "per_etf": {}, "gematchte_naam": None}
    is_etf = basis["is_etf_map"]
    per_etf = {t: round(eur, EUR_DECIMALEN) for t, eur in entry["per_bron"].items() if is_etf.get(t, False)}
    direct = sum((eur for t, eur in entry["per_bron"].items() if not is_etf.get(t, False)), 0.0)
    return {"direct_eur": round(direct, EUR_DECIMALEN), "via_etf_eur": round(sum(per_etf.values(), 0.0), EUR_DECIMALEN),
            "per_etf": per_etf, "gematchte_naam": entry["naam"]}


def simuleer_aankoop(basis, aankoop, top_n):
    """De portfolio vóór en ná het bijkopen van één effect; alles in procenten (0-100) van het eigen totaal, het
    verschil in procentpunten.
    basis: {waarde_per_ticker, is_etf_map, fracties_per_ticker, holdings_per_etf, aandeel_namen, bron_namen,
            extra_aandelen} zoals bouw_verdeling_invoer() ze levert.
    aankoop: {ticker, naam, is_etf, waarde_eur (aantal x koers), fracties (land_pct/sector_pct), holdings of None}.
    Sleutels: aankoop (met onbekend {land, sector, bedrijven}: voor die ETF helemaal onbekend), totaal {voor, na}, posities, etf_aandeel {voor, na, verschil_pp}, land, sector, bedrijven (top_n op het
    ná-%), overlap (alleen bij een ETF), al_in_bezit (alleen bij een aandeel)."""
    na = _na_basis(basis, aankoop)
    land_sector_voor, land_sector_na = _land_sector(basis), _land_sector(na)
    bedrijven_voor, bedrijven_na = _bedrijven(basis), _bedrijven(na)
    totaal_voor = sum(basis["waarde_per_ticker"].values())
    totaal_na = sum(na["waarde_per_ticker"].values())

    return _schoon({
        "aankoop": {"ticker": aankoop["ticker"], "naam": aankoop["naam"], "is_etf": aankoop["is_etf"],
                    "waarde_eur": round(aankoop["waarde_eur"], EUR_DECIMALEN),
                    "bijkopen": aankoop["ticker"] in basis["waarde_per_ticker"],
                    "bedrijven_onvolledig": aankoop["is_etf"]
                    and _holdings_dekking(_effectieve_holdings(basis, aankoop))[0] == HOLDINGS_ONBEKEND,
                    "onbekend": _onbekend(basis, aankoop)},
        "totaal": {"voor": round(totaal_voor, EUR_DECIMALEN), "na": round(totaal_na, EUR_DECIMALEN)},
        "posities": _posities(basis, na),
        "etf_aandeel": _etf_aandeel_voor_na(_etf_aandeel(basis), _etf_aandeel(na)),
        "land": _verschil_rijen(land_sector_voor["land_totaal"], land_sector_na["land_totaal"]),
        "sector": _verschil_rijen(land_sector_voor["sector_totaal"], land_sector_na["sector_totaal"]),
        "bedrijven": _top_bedrijven(bedrijven_voor, bedrijven_na, totaal_voor, totaal_na, top_n),
        "overlap": _overlap(basis, aankoop, bedrijven_voor, totaal_voor) if aankoop["is_etf"] else None,
        "al_in_bezit": None if aankoop["is_etf"] else _al_in_bezit(basis, aankoop, bedrijven_voor),
    })


def zelfde_fonds_als(isin_per_ticker, open_tickers, ticker, isin):
    """Open posities met dezelfde ISIN als de aankoop maar een andere ticker (zelfde fonds, andere notering); [] zonder
    ISIN. De berekening blijft de aankoop als nieuwe positie zien: dit is alleen een signaal."""
    if not isin:
        return []
    return sorted(t for t in open_tickers if t != ticker and isin_per_ticker.get(t) == isin)


def _is_getal(waarde):
    return isinstance(waarde, (int, float)) and not isinstance(waarde, bool) and math.isfinite(waarde)


def _valideer_ticker(ticker):
    return isinstance(ticker, str) and 0 < len(ticker.strip()) <= MAX_TICKER_TEKENS


def valideer_aankoop_invoer(data):
    """(ticker, isin, aantal, fout) uit een JSON-body {ticker, isin?, aantal}; fout is een tekst of None."""
    if not isinstance(data, dict):
        return None, None, None, "Stuur een JSON-body met ticker en aantal."
    if not _valideer_ticker(data.get("ticker")):
        return None, None, None, f"Geef een ticker van 1 tot {MAX_TICKER_TEKENS} tekens."
    aantal = data.get("aantal")
    # Alleen een geheel getal: een float of string wijzen we af in plaats van af te ronden.
    if not isinstance(aantal, int) or isinstance(aantal, bool) or not 1 <= aantal <= MAX_AANTAL_WAT_ALS:
        return None, None, None, f"Het aantal moet een geheel getal van 1 tot {MAX_AANTAL_WAT_ALS} zijn."
    isin_invoer = data.get("isin")
    isin = None
    if isin_invoer not in (None, ""):
        isin = herken_isin(isin_invoer) if isinstance(isin_invoer, str) else None
        if not isin:
            return None, None, None, "Dit is geen geldige ISIN."
    return data["ticker"].strip(), isin, aantal, None


def valideer_posities(posities):
    """([{ticker, naam, waarde}], fout) voor 'niet opslaan': de verdeling-lijst uit het verrijkingsantwoord."""
    if not isinstance(posities, list) or not 1 <= len(posities) <= MAX_POSITIES_WAT_ALS:
        return None, f"Stuur 1 tot {MAX_POSITIES_WAT_ALS} posities mee."
    schoon, gezien = [], set()
    for positie in posities:
        if not isinstance(positie, dict) or not _valideer_ticker(positie.get("ticker")):
            return None, f"Elke positie heeft een ticker van 1 tot {MAX_TICKER_TEKENS} tekens."
        ticker = positie["ticker"].strip()
        waarde = positie.get("waarde")
        if not _is_getal(waarde) or waarde <= 0:
            return None, "Elke positie heeft een waarde groter dan 0."
        naam = positie.get("naam")
        if naam is not None and (not isinstance(naam, str) or len(naam) > MAX_NAAM_TEKENS):
            return None, f"Een naam is maximaal {MAX_NAAM_TEKENS} tekens."
        if ticker in gezien:
            return None, f"De ticker '{ticker}' staat dubbel in de posities."
        gezien.add(ticker)
        schoon.append({"ticker": ticker, "naam": naam or ticker, "waarde": float(waarde)})
    return schoon, None
