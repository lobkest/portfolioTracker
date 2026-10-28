"""Jaarlijkse aansluitingskosten per beurs uit het rekeningoverzicht. Een eigen cijfer: bewust niet in transactiekosten,
geïnvesteerd, rendement, XIRR, TWR of Box 3; wel in het kassaldo (zie BEHANDELING in rekening_regelsoorten.py)."""
import datetime
import re

import pandas as pd

from portfolio_verdeling import beurs_naam, gewichten_per_beurscode
from rekening_regelsoorten import GEBRUIK_AANSLUITINGSKOSTEN, behandeling
from statistieken import bereken_totale_transactiekosten
from transactie_utils import _is_corporate_action_row

# "DEGIRO Aansluitingskosten 2026 (Tradegate AG - TDG)": jaar, beursnaam, beurscode.
AANSLUITINGSKOSTEN_PATROON = re.compile(
    r"aansluitingskosten\s+(\d{4})\s*\(\s*(.+?)\s+-\s+([A-Za-z0-9]+)\s*\)\s*$", re.IGNORECASE)
BEURS_ONBEKEND = "onbekend"
POSITIE_MARGE = 1e-9
# Beurzen zonder aansluitingskosten: DEGIRO-codes, Yahoo-beurscode en naam. Volgorde = voorkeur bij het kiezen.
THUISBEURZEN = (
    {"code": "EAM", "codes": ("EAM", "XAMS"), "yahoo": "AMS", "naam": "Euronext Amsterdam"},
    {"code": "EBR", "codes": ("EBR",), "yahoo": "BRU", "naam": "Euronext Brussel"},
)


def parse_aansluitingskosten(omschrijving):
    """(jaar, beursnaam, beurscode), of None als de tekst niet in het verwachte formaat staat."""
    match = AANSLUITINGSKOSTEN_PATROON.search(str(omschrijving or ""))
    if not match:
        return None
    jaar, naam, code = match.groups()
    return int(jaar), naam, code.upper()


def _per_jaar(bedragen):
    # Jaar None (onbekend formaat) achteraan.
    return [{"jaar": jaar, "bedrag_eur": round(bedrag, 2)}
            for jaar, bedrag in sorted(bedragen.items(), key=lambda x: (x[0] is None, x[0] or 0))]


def bouw_aansluitingskosten_samenvatting(regels):
    """regels: dicts zoals bouw_rekening_regels() of db_get_rekening_regels_eur(). Bedragen als positieve kosten.
    {totaal_eur, aantal_regels, per_jaar: [{jaar, bedrag_eur}],
     per_beurs: [{code, naam, verdeling_naam, verdeling_naam_euronext, totaal_eur, aantal_regels, per_jaar}],
     onbekend_formaat: [omschrijving]}. verdeling_naam(_euronext) = sleutel in beurs_verdeling.beurs(_euronext);
    None bij een onbekende beurs."""
    per_beurs, per_jaar, onbekend = {}, {}, []
    for regel in regels:
        if (behandeling(regel["omschrijving"]).gebruik != GEBRUIK_AANSLUITINGSKOSTEN
                or regel["mutatie_valuta"] != "EUR" or regel["mutatie"] is None):
            continue
        kosten = -float(regel["mutatie"])
        geparsed = parse_aansluitingskosten(regel["omschrijving"])
        if geparsed is None:
            onbekend.append(regel["omschrijving"])
            jaar, naam, code = None, BEURS_ONBEKEND, BEURS_ONBEKEND
        else:
            jaar, naam, code = geparsed
        beurs = per_beurs.setdefault(code, {"naam": naam, "totaal": 0.0, "aantal": 0, "per_jaar": {}})
        beurs["totaal"] += kosten
        beurs["aantal"] += 1
        beurs["per_jaar"][jaar] = beurs["per_jaar"].get(jaar, 0.0) + kosten
        per_jaar[jaar] = per_jaar.get(jaar, 0.0) + kosten

    lijst = [{
        "code": code,
        "naam": b["naam"],
        "verdeling_naam": None if code == BEURS_ONBEKEND else beurs_naam(code, False),
        "verdeling_naam_euronext": None if code == BEURS_ONBEKEND else beurs_naam(code, True),
        "totaal_eur": round(b["totaal"], 2),
        "aantal_regels": b["aantal"],
        "per_jaar": _per_jaar(b["per_jaar"]),
    } for code, b in per_beurs.items()]
    lijst.sort(key=lambda b: (b["code"] == BEURS_ONBEKEND, -b["totaal_eur"], b["code"]))
    return {
        "totaal_eur": round(sum((b["totaal"] for b in per_beurs.values()), 0.0), 2),
        "aantal_regels": sum(b["aantal"] for b in per_beurs.values()),
        "per_jaar": _per_jaar(per_jaar),
        "per_beurs": lijst,
        "onbekend_formaat": onbekend,
    }


DEELORDER_SUFFIX = re.compile(r"-\d{1,3}$")


def _order_sleutel(rij, volgnummer):
    """Deeluitvoeringen ('ID', 'ID-1', ...) delen één sleutel; een rij zonder (echte) order_id is een eigen transactie."""
    order_id = rij.get("order_id")
    if order_id is None or pd.isna(order_id) or str(order_id).startswith("SYN-"):
        return ("rij", volgnummer)
    return ("order", DEELORDER_SUFFIX.sub("", str(order_id)))


def _transacties_per_beurs(transacties_df):
    """{beurscode: {jaar: aantal}} van aan- en verkopen (een order = één transactie), zonder corporate actions."""
    orders = {}
    for volgnummer, rij in enumerate(transacties_df.to_dict("records")):
        if _is_corporate_action_row(rij):
            continue
        code = BEURS_ONBEKEND if pd.isna(rij.get("beurs")) else str(rij["beurs"]).strip().upper()
        jaar = pd.Timestamp(rij["datum"]).year
        orders.setdefault(code, {}).setdefault(jaar, set()).add(_order_sleutel(rij, volgnummer))
    return {code: {jaar: len(sleutels) for jaar, sleutels in per_jaar.items()} for code, per_jaar in orders.items()}


def _open_tickers_zonder_koers(transacties_df, statistieken):
    """Tickers met een positie > 0 die niet in `posities` staan (die slaat tickers zonder koers over)."""
    stuks = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    met_koers = {p["ticker"] for p in statistieken["posities"]}
    return {t for t, n in stuks.items() if float(n) > 1e-9 and t not in met_koers}


def _wv_per_ticker(statistieken):
    """Open (resterend) + gerealiseerd resultaat per ticker, exclusief dividend: geen dubbeltelling."""
    wv = {}
    for rij in statistieken["posities"] + statistieken["gesloten_posities"]:
        wv[rij["ticker"]] = wv.get(rij["ticker"], 0.0) + float(rij["rendement_eur"])
    return wv


def _gedekte_periode(statistieken, rekening_regels):
    """(eerste, laatste) rekeningregel als date, of None."""
    status = (statistieken.get("totalen") or {}).get("rekeningoverzicht_status")
    if status and status.get("eerste_regel_datum") and status.get("laatste_regel_datum"):
        return (datetime.date.fromisoformat(status["eerste_regel_datum"]),
                datetime.date.fromisoformat(status["laatste_regel_datum"]))
    datums = [r["datum"] for r in rekening_regels or [] if r.get("datum")]
    return (min(datums), max(datums)) if datums else None


def _jaar_volledig_gedekt(jaar, periode):
    # Het beginjaar niet: het overzicht kan midden in dat jaar beginnen.
    return periode is not None and jaar > periode[0].year and datetime.date(jaar, 12, 31) <= periode[1]


def _som_of_none(waarden):
    waarden = [w for w in waarden if w is not None]
    return round(sum(waarden), 2) if waarden else None


def _jaren_met_positie_per_beurs(transacties_df, gewichten, huidig_jaar):
    """{beurscode: {jaren}} waarin een positie openstond: stand per ticker aan het begin van het jaar > 0 (alle rijen,
    dus incl. splitboekingen), of een aankoop in dat jaar. De positie hoort bij de beurzen waar per code netto stuks
    staan (aankopen min verkopen, zonder corporate actions), anders bij elke beurs met een gewicht."""
    resultaat = {}
    df = transacties_df.dropna(subset=["ticker"])
    for ticker, groep in df.groupby("ticker"):
        per_jaar = {}
        for rij in groep.to_dict("records"):
            jaar = pd.Timestamp(rij["datum"]).year
            aantal = float(rij["aantal"])
            stand = per_jaar.setdefault(jaar, {"totaal": 0.0, "netto": {}, "gekocht": set()})
            stand["totaal"] += aantal
            if _is_corporate_action_row(rij):
                continue
            code = BEURS_ONBEKEND if pd.isna(rij.get("beurs")) else str(rij["beurs"]).strip().upper()
            stand["netto"][code] = stand["netto"].get(code, 0.0) + aantal
            if aantal > 0:
                stand["gekocht"].add(code)
        totaal, netto = 0.0, {}
        for jaar in range(min(per_jaar), max(max(per_jaar), huidig_jaar) + 1):
            codes = set(per_jaar.get(jaar, {}).get("gekocht", ()))
            if totaal > POSITIE_MARGE:
                codes |= {c for c, n in netto.items() if n > POSITIE_MARGE} or set(gewichten.get(ticker, {}))
            for code in codes:
                resultaat.setdefault(code, set()).add(jaar)
            stand = per_jaar.get(jaar)
            if stand:
                totaal += stand["totaal"]
                for code, n in stand["netto"].items():
                    netto[code] = netto.get(code, 0.0) + n
    return resultaat


def _open_tickers(transacties_df):
    stuks = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    return {t for t, n in stuks.items() if float(n) > POSITIE_MARGE}


def _posities_van_beurs(code, gewichten, wv_ticker, open_tickers, ticker_namen):
    posities = [{
        "ticker": ticker,
        "naam": ticker_namen.get(ticker, ticker),
        "nog_in_bezit": ticker in open_tickers,
        "wv_eur": round(wv_ticker.get(ticker, 0.0) * verdeling[code], 2),
        "gewicht": round(verdeling[code], 4),
    } for ticker, verdeling in gewichten.items() if verdeling.get(code, 0.0) > 0]
    posities.sort(key=lambda p: (not p["nog_in_bezit"], str(p["naam"]).casefold()))
    return posities


def _gemiddelde_kosten_per_transactie(transacties_df, codes_in_scope, codes, echte_rij):
    """Transactiekosten (zelfde definitie als bereken_totale_transactiekosten) gedeeld door het aantal transacties op
    de beurzen `codes_in_scope`; None zonder transacties of zonder kostenkolom."""
    rijen = transacties_df[codes.isin(codes_in_scope) & echte_rij]
    aantal = sum(sum(per_jaar.values()) for per_jaar in _transacties_per_beurs(rijen).values())
    kosten = bereken_totale_transactiekosten(rijen)
    if not aantal or not kosten["beschikbaar"]:
        return None
    return round(kosten["totaal"] / aantal, 2)


def _isin_per_ticker_laatste(transacties_df):
    df = transacties_df.dropna(subset=["ticker", "isin"])
    return df.drop_duplicates(subset=["ticker"], keep="last").set_index("ticker")["isin"].to_dict()


def _bouw_alternatieven_invoer(beurs, code, transacties_df, codes, echte_rij, waarde_per_ticker, isin_per_ticker):
    """Alles wat beurs_alternatieven.py nodig heeft, uit de eigen transacties: open posities (naar gewicht), gemiddelde
    transactiekosten per transactie op deze en op de thuisbeurzen, en ISIN's die al op een thuisbeurs zijn gehandeld."""
    aantallen = transacties_df.dropna(subset=["ticker"]).groupby("ticker")["aantal"].sum()
    posities = []
    for p in beurs["posities"]:
        if not p["nog_in_bezit"]:
            continue
        waarde = waarde_per_ticker.get(p["ticker"])
        posities.append({
            "ticker": p["ticker"], "naam": p["naam"], "isin": isin_per_ticker.get(p["ticker"]),
            "aantal": round(float(aantallen[p["ticker"]]) * p["gewicht"], 4),
            "waarde_eur": round(float(waarde) * p["gewicht"], 2) if waarde is not None else None,
        })
    thuis_codes = {c for thuis in THUISBEURZEN for c in thuis["codes"]}
    thuis_rijen = transacties_df[codes.isin(thuis_codes) & echte_rij]
    jaarkosten = [j["bedrag_eur"] for j in sorted(
        (j for j in beurs["per_jaar"] if j["jaar"] is not None), key=lambda j: j["jaar"])]
    return {
        "beurs": code,
        "beurs_naam": beurs["verdeling_naam"] or beurs["naam"],
        "posities": posities,
        "jaarkosten_eur": jaarkosten[-1] if jaarkosten else None,
        "betaald_dit_jaar": beurs["betaald_dit_jaar"],
        "gemiddelde_kosten_per_transactie_eur": _gemiddelde_kosten_per_transactie(
            transacties_df, {code}, codes, echte_rij),
        "thuisbeurs_kosten_per_transactie_eur": {
            thuis["code"]: _gemiddelde_kosten_per_transactie(transacties_df, set(thuis["codes"]), codes, echte_rij)
            for thuis in THUISBEURZEN},
        "isins_op_thuisbeurs": sorted({str(i) for i in thuis_rijen["isin"].dropna()}) if "isin" in thuis_rijen else [],
    }


def voeg_beurs_resultaat_toe(samenvatting, transacties_df, statistieken, huidig_jaar, rekening_regels=None,
                             ticker_namen=None):
    """Vult elk item van samenvatting["per_beurs"] aan met transacties_per_jaar, jaren_met_positie, posities
    ({ticker, naam, nog_in_bezit, wv_eur, gewicht}), heeft_open_positie, alternatieven_invoer (voor beurs_alternatieven.py; None zonder open positie), wv_eur, wv_volledig,
    wv_na_aansluitingskosten_eur, transactiekosten_eur, betaald_dit_jaar, kosten_zonder_positie_of_transacties en
    activiteit_zonder_kosten, en de samenvatting met totaal_wv_eur, totaal_wv_na_aansluitingskosten_eur,
    totaal_transactiekosten_eur. W/V = open + gerealiseerd per ticker (statistieken), exclusief dividend en
    transactiekosten, per beurs verdeeld naar gekochte stuks. Wijzigt `samenvatting` en geeft hem terug."""
    ticker_namen = ticker_namen or {}
    transacties_per_beurs = _transacties_per_beurs(transacties_df)
    gewichten = gewichten_per_beurscode(transacties_df)
    positiejaren = _jaren_met_positie_per_beurs(transacties_df, gewichten, huidig_jaar)
    open_tickers = _open_tickers(transacties_df)
    wv_ticker = _wv_per_ticker(statistieken)
    waarde_per_ticker = {p["ticker"]: p.get("huidige_waarde") for p in statistieken["posities"]}
    isin_per_ticker = _isin_per_ticker_laatste(transacties_df) if "isin" in transacties_df.columns else {}
    zonder_koers = _open_tickers_zonder_koers(transacties_df, statistieken)
    periode = _gedekte_periode(statistieken, rekening_regels)
    codes = transacties_df["beurs"].map(lambda c: BEURS_ONBEKEND if pd.isna(c) else str(c).strip().upper())
    echte_rij = pd.Series([not _is_corporate_action_row(r) for r in transacties_df.to_dict("records")],
                          index=transacties_df.index)

    for beurs in samenvatting["per_beurs"]:
        code = beurs["code"]
        kostenjaren = {j["jaar"]: j["bedrag_eur"] for j in beurs["per_jaar"] if j["jaar"] is not None}
        onbekend = code == BEURS_ONBEKEND
        tellers = {} if onbekend else transacties_per_beurs.get(code, {})
        jaren_positie = set() if onbekend else positiejaren.get(code, set())
        beurs["transacties_per_jaar"] = [{"jaar": j, "aantal": n} for j, n in sorted(tellers.items())]
        beurs["jaren_met_positie"] = sorted(jaren_positie)
        beurs["posities"] = [] if onbekend else _posities_van_beurs(code, gewichten, wv_ticker, open_tickers, ticker_namen)
        beurs["heeft_open_positie"] = any(p["nog_in_bezit"] for p in beurs["posities"])
        beurs["betaald_dit_jaar"] = kostenjaren.get(huidig_jaar, 0) > 0
        beurs["alternatieven_invoer"] = (
            _bouw_alternatieven_invoer(beurs, code, transacties_df, codes, echte_rij, waarde_per_ticker, isin_per_ticker)
            if beurs["heeft_open_positie"] else None)
        beurs["kosten_zonder_positie_of_transacties"] = sorted(
            j for j in kostenjaren if j not in tellers and j not in jaren_positie)
        beurs["activiteit_zonder_kosten"] = sorted(
            j for j in set(tellers) | jaren_positie
            if j != huidig_jaar and j not in kostenjaren and _jaar_volledig_gedekt(j, periode))

        beurs["wv_volledig"] = True
        beurs["wv_eur"] = beurs["wv_na_aansluitingskosten_eur"] = beurs["transactiekosten_eur"] = None
        if not tellers:
            continue
        wv, heeft_ticker = 0.0, False
        for ticker, verdeling in gewichten.items():
            fractie = verdeling.get(code, 0.0)
            if fractie <= 0:
                continue
            heeft_ticker = True
            wv += wv_ticker.get(ticker, 0.0) * fractie
            if ticker in zonder_koers:
                beurs["wv_volledig"] = False
        if heeft_ticker:
            beurs["wv_eur"] = round(wv, 2)
            beurs["wv_na_aansluitingskosten_eur"] = round(wv - beurs["totaal_eur"], 2)
        kosten = bereken_totale_transactiekosten(transacties_df[(codes == code) & echte_rij])
        beurs["transactiekosten_eur"] = kosten["totaal"] if kosten["beschikbaar"] else None

    rijen = samenvatting["per_beurs"]
    samenvatting["totaal_wv_eur"] = _som_of_none(b["wv_eur"] for b in rijen)
    samenvatting["totaal_wv_na_aansluitingskosten_eur"] = _som_of_none(b["wv_na_aansluitingskosten_eur"] for b in rijen)
    samenvatting["totaal_transactiekosten_eur"] = _som_of_none(b["transactiekosten_eur"] for b in rijen)
    samenvatting["totaal_wv_volledig"] = all(b["wv_volledig"] for b in rijen)
    return samenvatting
