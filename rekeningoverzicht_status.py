"""Beoordeelt of het opgeslagen rekeningoverzicht (Account.xlsx) de hele periode van de transacties beslaat."""
import datetime

import pandas as pd

from rekening_regelsoorten import behandeling
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl

MARGE_BEGIN_DAGEN = 3
MARGE_GEINVESTEERD_EUR = 1.0

REDEN_BEGINT_TE_LAAT = "begint_na_eerste_transactie"
REDEN_BEGINSALDO = "beginsaldo_niet_nul"
REDEN_GESTORT_NUL = "netto_gestort_nul"
REDEN_SALDO_NEGATIEF = "saldo_negatief"
REDEN_GESTORT_TE_LAAG = "gestort_kleiner_dan_geinvesteerd"


def _eerste_transactie_datum(transacties_df):
    """Oudste transactie die geen corporate action is; None als die er niet is."""
    if transacties_df is None or transacties_df.empty:
        return None
    echte = transacties_df[[not _is_corporate_action_row(rij) for rij in transacties_df.to_dict("records")]]
    if echte.empty:
        return None
    return pd.Timestamp(echte["datum"].min()).date()


def _beginsaldo(kassaldo, rekening_regels):
    """Saldo minus de mutaties die in het kassaldo meetellen (geen kolom in de database nodig)."""
    mutaties = sum(float(r["mutatie"]) for r in rekening_regels or []
                   if r.get("mutatie") is not None and behandeling(r.get("omschrijving")).kassaldo)
    return round(float(kassaldo["saldo_eur"]) - mutaties, 2) + 0.0


def _reden_teksten(redenen, eerste_regel, eerste_transactie):
    teksten = {
        REDEN_BEGINT_TE_LAAT: (
            f"Het rekeningoverzicht begint op {formatteer_datum_nl(eerste_regel)}, "
            f"de eerste transactie is van {formatteer_datum_nl(eerste_transactie)}."
            if eerste_regel and eerste_transactie else "Het rekeningoverzicht begint na de eerste transactie."),
        REDEN_BEGINSALDO: "Het saldo vóór de eerste regel is niet nul.",
        REDEN_GESTORT_NUL: "Er is niets gestort volgens het overzicht, terwijl er wel transacties zijn.",
        REDEN_SALDO_NEGATIEF: "Het kassaldo is negatief; dat kan bij DEGIRO niet.",
        REDEN_GESTORT_TE_LAAG: "Netto gestort is lager dan het geïnvesteerde bedrag.",
    }
    return [teksten[r] for r in redenen]


def beoordeel_rekeningoverzicht(transacties_df, rekening_regels, kassaldo, geinvesteerd, waarde,
                                dividend_netto, overige_kassabewegingen):
    """None zonder rekeningoverzicht. Anders {volledig, redenen (codes), redenen_tekst, eerste_regel_datum,
    laatste_regel_datum, eerste_transactie_datum} (datums ISO of None) plus de bedragen uit de redenen
    {beginsaldo_eur, netto_gestort_eur, geinvesteerd_eur, saldo_eur}. Gebruikt de cijfers van bereken_kassaldo."""
    if not kassaldo:
        return None
    datums = [r["datum"] for r in rekening_regels or [] if r.get("datum")]
    eerste_regel = min(datums) if datums else kassaldo.get("eerste_datum")
    eerste_transactie = _eerste_transactie_datum(transacties_df)
    heeft_transacties = bool(geinvesteerd) or bool(waarde)

    redenen = []
    if eerste_regel and eerste_transactie and (eerste_regel - eerste_transactie).days > MARGE_BEGIN_DAGEN:
        redenen.append(REDEN_BEGINT_TE_LAAT)
    if not kassaldo.get("vanaf_opening"):
        redenen.append(REDEN_BEGINSALDO)
    if heeft_transacties and not kassaldo["netto_gestort_eur"]:
        redenen.append(REDEN_GESTORT_NUL)
    if kassaldo["saldo_eur"] < 0:
        redenen.append(REDEN_SALDO_NEGATIEF)
    dekking = kassaldo["netto_gestort_eur"] + (dividend_netto or 0.0) + (overige_kassabewegingen or 0.0)
    if heeft_transacties and kassaldo["netto_gestort_eur"] and dekking < (geinvesteerd or 0.0) - MARGE_GEINVESTEERD_EUR:
        redenen.append(REDEN_GESTORT_TE_LAAG)

    def iso(datum):
        return datum.isoformat() if isinstance(datum, datetime.date) else None

    return {
        "volledig": not redenen,
        "redenen": redenen,
        "redenen_tekst": _reden_teksten(redenen, eerste_regel, eerste_transactie),
        "eerste_regel_datum": iso(eerste_regel),
        "laatste_regel_datum": iso(kassaldo.get("per_datum")),
        "eerste_transactie_datum": iso(eerste_transactie),
        "beginsaldo_eur": _beginsaldo(kassaldo, rekening_regels),
        "netto_gestort_eur": round(float(kassaldo["netto_gestort_eur"]), 2),
        "geinvesteerd_eur": round(float(geinvesteerd or 0.0), 2),
        "saldo_eur": round(float(kassaldo["saldo_eur"]), 2),
    }
