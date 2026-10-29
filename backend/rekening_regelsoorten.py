"""Soorten regels in het DeGiro-rekeningoverzicht en wat de app met elke soort doet. De tekstherkenning staat alleen
in regelsoort(); de verwerking (kassaldo, stortingen, dividend, kostenkorting, Diagnostiek) leest BEHANDELING."""
import re
from dataclasses import dataclass

import pandas as pd

REGELSOORT_KOOP_VERKOOP = "koop_verkoop"
REGELSOORT_KOSTEN = "kosten"
REGELSOORT_AANSLUITINGSKOSTEN = "aansluitingskosten"
REGELSOORT_DIVIDEND = "dividend"
REGELSOORT_DIVIDENDBELASTING = "dividendbelasting"
REGELSOORT_DIVIDEND_HERINVESTERING = "dividend_herinvestering"
REGELSOORT_VALUTA_DEBITERING = "valuta_debitering"
REGELSOORT_VALUTA_CREDITERING = "valuta_creditering"
REGELSOORT_STORTING_OPNAME = "storting_opname"
REGELSOORT_RESERVERING = "reservering"
REGELSOORT_SWEEP = "sweep"
REGELSOORT_RENTE = "rente"
REGELSOORT_CORPORATE_ACTION = "corporate_action"
REGELSOORT_VERREKENING_AANDELEN = "verrekening_aandelen"
REGELSOORT_PROMOTIE = "promotie"
REGELSOORT_CONVERSIE_GELDMARKTFONDS = "conversie_geldmarktfonds"
REGELSOORT_ONBEKEND = None

GEBRUIK_GEEN = "geen"
GEBRUIK_DIVIDEND = "dividend"
GEBRUIK_VALUTACONVERSIE = "valutaconversie"
GEBRUIK_KOSTENKORTING = "kostenkorting"
GEBRUIK_DIAGNOSTIEK = "diagnostiek"
GEBRUIK_AANSLUITINGSKOSTEN = "aansluitingskosten"

OMSCHRIJVING_VERREKENING_AANDELEN = "Verrekening van Aandelen"
OMSCHRIJVINGEN_PROMOTIE = ("DEGIRO Verrekening Promotie",)
_VASTE_TEKSTEN = {
    "Dividend": REGELSOORT_DIVIDEND,
    "Dividendbelasting": REGELSOORT_DIVIDENDBELASTING,
    "Dividend Herinvestering": REGELSOORT_DIVIDEND_HERINVESTERING,
    "Valuta Debitering": REGELSOORT_VALUTA_DEBITERING,
    "Valuta Creditering": REGELSOORT_VALUTA_CREDITERING,
    OMSCHRIJVING_VERREKENING_AANDELEN: REGELSOORT_VERREKENING_AANDELEN,
    "Flatex Interest Income": REGELSOORT_RENTE,
    "Reservation iDEAL": REGELSOORT_RESERVERING,
    **{tekst: REGELSOORT_PROMOTIE for tekst in OMSCHRIJVINGEN_PROMOTIE},
}
STORTING_TREFWOORDEN = ("ideal", "storting", "deposit", "withdrawal", "opname")
# "Koop 11 @ 57,33 EUR", en in oudere exports "Koop 20 stuks".
_KOOP_VERKOOP = re.compile(r"^(Koop|Verkoop) [\d.,]+ (@ |stuks$)")
# "CLAIMEMISSIE: Koop ..." en "PRODUCTWIJZIGING : Verkoop ...".
_CORPORATE_ACTION = re.compile(r"^[A-Z][A-Z ]*[A-Z] ?: ?(Koop|Verkoop) [\d.,]+ @ ")
_CONVERSIE_GELDMARKTFONDS = re.compile(r"^Conversie geldmarktfonds: (Koop|Verkoop) [\d.,]+ @ ")


def regelsoort(omschrijving):
    """REGELSOORT_* van een regel, REGELSOORT_ONBEKEND (None) als de tekst niet herkend wordt. Alleen teksten die in
    echte exports of de bestaande parsers voorkomen; Engels alleen Deposit/Withdrawal, Cash Sweep en Flatex-teksten."""
    tekst = str(omschrijving).strip() if pd.notna(omschrijving) else ""
    kleine_letters = tekst.lower()
    if tekst in _VASTE_TEKSTEN:
        return _VASTE_TEKSTEN[tekst]
    if _CONVERSIE_GELDMARKTFONDS.match(tekst):
        return REGELSOORT_CONVERSIE_GELDMARKTFONDS
    if _CORPORATE_ACTION.match(tekst):
        return REGELSOORT_CORPORATE_ACTION
    if _KOOP_VERKOOP.match(tekst):
        return REGELSOORT_KOOP_VERKOOP
    if tekst.startswith("DEGIRO") and "aansluitingskosten" in kleine_letters:
        return REGELSOORT_AANSLUITINGSKOSTEN
    if tekst.startswith("DEGIRO") and "kosten" in kleine_letters:
        return REGELSOORT_KOSTEN
    # zie CLAUDE.md: DeGiro-bestanden (flatex-sweeps)
    if "cash sweep" in kleine_letters or ("overboeking" in kleine_letters and "flatexdegiro bank" in kleine_letters):
        return REGELSOORT_SWEEP
    if any(woord in kleine_letters for woord in STORTING_TREFWOORDEN):
        return REGELSOORT_STORTING_OPNAME
    return REGELSOORT_ONBEKEND


@dataclass(frozen=True)
class Behandeling:
    kassaldo: bool  # EUR-mutatie telt in bereken_kassaldo(); een andere valuta nooit
    storting: bool  # telt in netto_gestort_eur
    gebruik: str    # GEBRUIK_*: verdere verwerking buiten het kassaldo
    reden: str


BEHANDELING = {
    REGELSOORT_KOOP_VERKOOP: Behandeling(
        True, False, GEBRUIK_GEEN,
        "Kassaldo en Order-ID-check; geïnvesteerd, GAK, XIRR en TWR komen uit het transactiebestand. In vreemde valuta "
        "telt de EUR-kant via het conversiepaar."),
    REGELSOORT_KOSTEN: Behandeling(
        True, False, GEBRUIK_GEEN,
        "Kassaldo en Order-ID-check; het kostencijfer komt uit het transactiebestand."),
    REGELSOORT_AANSLUITINGSKOSTEN: Behandeling(
        True, False, GEBRUIK_AANSLUITINGSKOSTEN,
        "Kassaldo en een eigen cijfer per beurs en jaar (aansluitingskosten.py); bewust niet in transactiekosten, "
        "geïnvesteerd, rendement, XIRR, TWR of Box 3."),
    REGELSOORT_STORTING_OPNAME: Behandeling(True, True, GEBRUIK_GEEN, "Kassaldo en netto gestort."),
    REGELSOORT_RESERVERING: Behandeling(
        True, True, GEBRUIK_GEEN,
        "Zit al in het DEGIRO-saldo en valt netto weg tegen de latere iDEAL Deposit; een losse reservering telt als "
        "(nog binnenkomende) storting."),
    REGELSOORT_SWEEP: Behandeling(
        False, False, GEBRUIK_GEEN,
        "Interne overboeking flatex/DEGIRO: netto nul, met onzinnige tussensaldi."),
    REGELSOORT_RENTE: Behandeling(
        True, False, GEBRUIK_GEEN,
        "Alleen kassaldo; bewust niet in rendement, Box 3 of dividend."),
    REGELSOORT_DIVIDEND: Behandeling(
        True, False, GEBRUIK_DIVIDEND, "Dividend-samenvatting; een EUR-uitkering telt ook in het kassaldo."),
    REGELSOORT_DIVIDENDBELASTING: Behandeling(
        True, False, GEBRUIK_DIVIDEND, "Wordt per uitkering met het dividend genet."),
    REGELSOORT_DIVIDEND_HERINVESTERING: Behandeling(
        True, False, GEBRUIK_DIVIDEND, "Heft (een deel van) het dividend op; telt mee in de nettering."),
    REGELSOORT_VALUTA_DEBITERING: Behandeling(
        True, False, GEBRUIK_VALUTACONVERSIE,
        "Conversiepaar: levert het EUR-bedrag van een dividend; de EUR-kant telt in het kassaldo."),
    REGELSOORT_VALUTA_CREDITERING: Behandeling(
        True, False, GEBRUIK_VALUTACONVERSIE,
        "Conversiepaar: levert het EUR-bedrag van een dividend; de EUR-kant telt in het kassaldo."),
    REGELSOORT_PROMOTIE: Behandeling(
        True, False, GEBRUIK_KOSTENKORTING, "Korting op transactiekosten (verdeel_kostenkorting()); geen storting."),
    REGELSOORT_CORPORATE_ACTION: Behandeling(
        True, False, GEBRUIK_GEEN, "Alleen kassaldo; de stukken zelf staan in het transactiebestand."),
    REGELSOORT_VERREKENING_AANDELEN: Behandeling(
        True, False, GEBRUIK_DIAGNOSTIEK,
        "Kassaldo, en met ISIN en een bijbehorende positie ook opbrengst van die positie in rendement, Totaal W/V, "
        "XIRR en TWR (verrekening.py); aantallen, GAK en waarde blijven gelijk. Zonder ISIN of positie alleen kassaldo, "
        "en Diagnostiek meldt het."),
    REGELSOORT_CONVERSIE_GELDMARKTFONDS: Behandeling(
        False, False, GEBRUIK_GEEN, "Omzetting van fondseenheden zonder bedrag of valuta: geen kassaldo-effect."),
    REGELSOORT_ONBEKEND: Behandeling(
        True, False, GEBRUIK_DIAGNOSTIEK,
        "Terugval: EUR telt in het kassaldo, niet als storting; Diagnostiek meldt de regel."),
}


# Soorten in het kassaldo die geen aankoop, storting of dividend zijn: het deel dat de Home-opbouw "overig" noemt.
OVERIGE_KASSA_SOORTEN = frozenset({
    REGELSOORT_AANSLUITINGSKOSTEN, REGELSOORT_RENTE, REGELSOORT_VERREKENING_AANDELEN, REGELSOORT_ONBEKEND,
})


def behandeling(omschrijving):
    return BEHANDELING[regelsoort(omschrijving)]
