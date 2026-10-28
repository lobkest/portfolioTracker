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


def voeg_beurs_resultaat_toe(samenvatting, transacties_df, statistieken, huidig_jaar, rekening_regels=None):
    """Vult elk item van samenvatting["per_beurs"] aan met transacties_per_jaar, wv_eur, wv_volledig,
    wv_na_aansluitingskosten_eur, transactiekosten_eur, betaald_dit_jaar, kosten_zonder_transacties en
    transacties_zonder_kosten, en de samenvatting met totaal_wv_eur, totaal_wv_na_aansluitingskosten_eur,
    totaal_transactiekosten_eur. W/V = open + gerealiseerd per ticker (statistieken), exclusief dividend en
    transactiekosten, per beurs verdeeld naar gekochte stuks. Wijzigt `samenvatting` en geeft hem terug."""
    transacties_per_beurs = _transacties_per_beurs(transacties_df)
    gewichten = gewichten_per_beurscode(transacties_df)
    wv_ticker = _wv_per_ticker(statistieken)
    zonder_koers = _open_tickers_zonder_koers(transacties_df, statistieken)
    periode = _gedekte_periode(statistieken, rekening_regels)
    codes = transacties_df["beurs"].map(lambda c: BEURS_ONBEKEND if pd.isna(c) else str(c).strip().upper())
    echte_rij = pd.Series([not _is_corporate_action_row(r) for r in transacties_df.to_dict("records")],
                          index=transacties_df.index)

    for beurs in samenvatting["per_beurs"]:
        code = beurs["code"]
        kostenjaren = {j["jaar"]: j["bedrag_eur"] for j in beurs["per_jaar"] if j["jaar"] is not None}
        tellers = {} if code == BEURS_ONBEKEND else transacties_per_beurs.get(code, {})
        beurs["transacties_per_jaar"] = [{"jaar": j, "aantal": n} for j, n in sorted(tellers.items())]
        beurs["betaald_dit_jaar"] = kostenjaren.get(huidig_jaar, 0) > 0
        beurs["kosten_zonder_transacties"] = sorted(j for j in kostenjaren if j not in tellers)
        beurs["transacties_zonder_kosten"] = sorted(
            j for j in tellers if j != huidig_jaar and j not in kostenjaren and _jaar_volledig_gedekt(j, periode))

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
