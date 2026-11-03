"""Ticker wisselen: de knop, Terugzetten en de stille automatische wissel, met vastlegging in ticker_wissels."""
from backend.db import (
    db_transactie, db_wijzig_ticker_voor_isins, db_voeg_ticker_wissel_toe, db_get_ticker_wissels, db_wijzig_bijnaam,
)
from backend.debug_utils import maskeer_codes
from backend.ticker_matching import BEURS_MAP
from backend.ticker_prijscheck import _prijscheck_is_probleem
from backend.ticker_zekerheid import verifieer_ticker_met_prijs, bijnaam_na_tickerwissel, bepaal_handelsvaluta

SOORT_AUTO = "auto"
SOORT_HANDMATIG = "handmatig"

# Posities die dit proces al zonder wissel beoordeeld heeft: voorkomt een duur zoekrondje bij elk bezoek.
_zonder_wissel_beoordeeld = set()


def laatste_per_positie(wissels):
    """{(isin, beurs): laatste wissel}; wissels oudste eerst. De laatste rij is de geldende keuze."""
    return {(w["isin"], w["beurs"]): w for w in wissels}


def wissels_van_positie(wissels, isin, beurs):
    return [w for w in wissels if w["isin"] == isin and w["beurs"] == beurs]


def mag_automatisch_wisselen(wissels_van_deze_positie):
    """Alleen zonder eerdere wissel: een handmatige keuze wint altijd, en er is hoogstens één automatische wissel."""
    return not wissels_van_deze_positie


def beslis_automatische_wissel(resultaat, koers_valuta):
    """Pure beslisregel. resultaat: de volledige check (verifieer_ticker_met_prijs met doorzoek_alles).
    Wisselen alleen bij een beurs-mismatch of een datum buiten de dagrange EN precies één kandidaat die op een
    verwachte beurs staat, dezelfde valuta heeft als de transacties en op alle gecontroleerde datums (minstens 1)
    binnen de dagrange valt. Geeft {"van", "naar", "reden"} of None."""
    if not koers_valuta or not resultaat.get("ticker"):
        return None
    verwachte_beurzen = BEURS_MAP.get(resultaat.get("excel_beurs"), [])
    if not verwachte_beurzen:
        return None

    bekende_checks = [c for c in resultaat.get("prijs_checks") or [] if c["match"] is not None]
    buiten = [c for c in bekende_checks if _prijscheck_is_probleem(c)]
    beurs_mismatch = resultaat.get("beurs_klopt") is False
    if not (buiten or beurs_mismatch):
        return None

    kandidaten = [
        a for a in resultaat.get("alternatieven") or []
        if a.get("ticker") != resultaat["ticker"]
        and a.get("beurs") in verwachte_beurzen
        and a.get("valuta_gelijk") is True
        and not a.get("prijscheck_nvt") and not a.get("uitkeringsvorm_strijdig")
        and a.get("aantal_gecontroleerd", 0) >= 1 and a.get("aantal_matches") == a.get("aantal_gecontroleerd")
    ]
    if len(kandidaten) != 1:
        return None
    kandidaat = kandidaten[0]

    oorzaken = []
    if beurs_mismatch:
        oorzaken.append(f"beurs {resultaat.get('yahoo_beurs')} past niet bij {resultaat.get('excel_beurs')}")
    if buiten:
        oorzaken.append(f"{len(buiten)} van {len(bekende_checks)} datums buiten de dagrange")
    reden = (
        f"{', '.join(oorzaken)}; {kandidaat['ticker']} ({kandidaat['beurs']}, {kandidaat.get('valuta')}) "
        f"klopt op alle {kandidaat['aantal_gecontroleerd']} gecontroleerde datums"
    )
    return {"van": resultaat["ticker"], "naar": kandidaat["ticker"], "reden": reden}


def pas_ticker_wissel_toe(code, info, nieuwe_ticker, soort, reden=None):
    """De ene schrijfroute voor de knop, Terugzetten en de automatische wissel: ticker van alle rijen van de positie
    (alle ISIN's van de keten) plus de vastlegging in één transactie, daarna de bijnaam. info: een groep uit
    groepeer_posities_per_keten(). Geeft de nieuwe bijnaam of None; de aanroeper wist de basis-cache."""
    oude_ticker = info["ticker"]
    with db_transactie() as cur:
        db_wijzig_ticker_voor_isins(cur, code, info["isins"], info["beurs"], nieuwe_ticker)
        db_voeg_ticker_wissel_toe(cur, code, info["isin"], info["beurs"], oude_ticker, nieuwe_ticker, soort, reden)
    print(f"[ticker-wissel] {', '.join(info['isins'])}: ticker {oude_ticker} -> {nieuwe_ticker} ({soort})")
    try:
        bijnaam = bijnaam_na_tickerwissel(info["naam"], oude_ticker, nieuwe_ticker)
        if bijnaam:
            db_wijzig_bijnaam(code, nieuwe_ticker, bijnaam)
        return bijnaam
    except Exception as e:
        # De ticker staat er al; de bijnaam is bijzaak.
        print(f"[ticker-wissel] {info['isin']}: WARN bijnaam niet aangepast ({maskeer_codes(e)!a})")
        return None


def zet_automatische_wissel_terug(code, info, wissels):
    """Zet de ticker van vóór de laatste automatische wissel terug, als handmatige keuze (blokkeert een nieuwe
    automatische wissel). Geeft (oude_ticker, nieuwe_ticker, bijnaam) of None als er niets terug te zetten is."""
    laatste = (wissels_van_positie(wissels, info["isin"], info["beurs"]) or [None])[-1]
    if not laatste or laatste["soort"] != SOORT_AUTO or not laatste["oude_ticker"]:
        return None
    if info["ticker"] != laatste["nieuwe_ticker"]:
        return None
    bijnaam = pas_ticker_wissel_toe(code, info, laatste["oude_ticker"], SOORT_HANDMATIG, "Teruggezet na automatische wissel")
    return laatste["nieuwe_ticker"], laatste["oude_ticker"], bijnaam


def actieve_automatische_wissels(wissels, groepen):
    """Voor Ticker-zekerheid: de laatste wissel per positie als die automatisch was en nog geldt, met de naam van
    de positie. [{isin, beurs, naam, oude_ticker, nieuwe_ticker, reden}]."""
    resultaat = []
    for (isin, beurs), w in laatste_per_positie(wissels).items():
        info = dict(groepen).get((isin, beurs))
        if w["soort"] == SOORT_AUTO and info and info["ticker"] == w["nieuwe_ticker"]:
            resultaat.append({
                "isin": isin, "beurs": beurs, "naam": info["naam"], "oude_ticker": w["oude_ticker"],
                "nieuwe_ticker": w["nieuwe_ticker"], "reden": w["reden"],
            })
    return resultaat


def _probeer_wissel_voor_positie(code, info, koers_valuta, wissels):
    """True als de ticker automatisch is gewisseld. Een crash of netwerkfout laat de ticker staan."""
    sleutel = (code, info["isin"], info["beurs"], info["ticker"])
    if sleutel in _zonder_wissel_beoordeeld or not mag_automatisch_wisselen(
            wissels_van_positie(wissels, info["isin"], info["beurs"])):
        return False
    try:
        resultaat = verifieer_ticker_met_prijs(
            info["echte_naam"] or info["naam"], info["isin"], info["beurs"], info["transacties"],
            opgeslagen_ticker=info["ticker"], doorzoek_alles=True,
        )
        besluit = beslis_automatische_wissel(resultaat, koers_valuta)
    except Exception as e:
        print(f"[ticker-wissel] WARN {info['isin']}: beoordeling mislukt ({type(e).__name__})")
        return False
    if besluit is None:
        _zonder_wissel_beoordeeld.add(sleutel)
        return False
    pas_ticker_wissel_toe(code, info, besluit["naar"], SOORT_AUTO, besluit["reden"])
    return True


def verwerk_automatische_wissels(code, waarschuwingen, groepen):
    """Voor elke ticker met een koerswaarschuwing in dit portfolio: automatisch wisselen als de beslisregel het
    toestaat. Geeft (waarschuwingen zonder de gewisselde posities, aantal wissels); geen banner voor een gewisselde
    positie. De aanroeper wist de basis-cache bij aantal > 0."""
    if not code or not waarschuwingen:
        return waarschuwingen, 0
    koers_tickers = {w["ticker"] for w in waarschuwingen if "koers" in w.get("redenen", [])}
    if not koers_tickers:
        return waarschuwingen, 0

    wissels = db_get_ticker_wissels(code)
    gewisseld_tickers = set()
    for _sleutel, info in groepen:
        if info["ticker"] not in koers_tickers:
            continue
        valuta = bepaal_handelsvaluta(info["transacties"], BEURS_MAP.get(info["beurs"], []))
        if _probeer_wissel_voor_positie(code, info, valuta, wissels):
            gewisseld_tickers.add(info["ticker"])
    return [w for w in waarschuwingen if w["ticker"] not in gewisseld_tickers], len(gewisseld_tickers)
