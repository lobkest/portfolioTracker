"""Jaarlijkse aansluitingskosten per beurs uit het rekeningoverzicht. Een eigen cijfer: bewust niet in transactiekosten,
geïnvesteerd, rendement, XIRR, TWR of Box 3; wel in het kassaldo (zie BEHANDELING in rekening_regelsoorten.py)."""
import re

from portfolio_verdeling import beurs_naam
from rekening_regelsoorten import GEBRUIK_AANSLUITINGSKOSTEN, behandeling

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
