"""Box 3 onder drie stelsels (huidig, vermogensaanwas, vermogenswinst). Pure functies: geen DB of netwerk."""
import math
from datetime import date

import pandas as pd

from box3_parameters import (
    PARAMETERS_STAND, WWR_TARIEF, WWR_HEFFINGSVRIJ_RESULTAAT, WWR_VERLIESDREMPEL, FORFAITAIR, FORFAITAIR_EERSTE_JAAR,
)
from debug_utils import dprint
from statistieken import waarde_op_of_voor
from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch

INVOER_BEDRAGEN = ("banktegoeden", "overige_bezittingen", "schulden", "rendement_ander_vermogen", "spaarrente_pct")
SPAARRENTE_MAX_PCT = 20


def _getal(waarde):
    """float, of NaN bij None/tekst/Decimal('NaN'); Postgres levert Decimal."""
    try:
        return float(waarde)
    except (TypeError, ValueError):
        return math.nan


def _som(waarden):
    return sum(w for w in waarden if not math.isnan(w))


def _box3_transacties(transacties_df, laatste_datum):
    """Zelfde rijen als 'geinvesteerd' in compute_value_over_time(): met ticker, tot de laatste koersdatum."""
    df = transacties_df.dropna(subset=["ticker"]).copy()
    df["datum"] = pd.to_datetime(df["datum"])
    df = df[df["datum"] <= laatste_datum]
    for kolom in ("aantal", "totaal_eur", "transactiekosten", "autofx_kosten"):
        df[kolom] = df[kolom].map(_getal) if kolom in df.columns else math.nan
    return df


def _verkopen_en_open_kostenbasis(df):
    """Gemiddelde kostprijs per ticker op totaal_eur (dus incl. aankoopkosten), los van de GAK op waarde_eur.
    Zelfde opbouw als bereken_holdings_en_gesloten(): alle rijen voor het aantal, totaal_eur 0 niet voor de kosten."""
    verkopen = []
    open_kostenbasis = 0.0
    for ticker, groep in df.groupby("ticker"):
        aantal = 0.0
        kosten = 0.0
        for row in _sorteer_chronologisch(groep).itertuples(index=False):
            delta = 0.0 if math.isnan(row.aantal) else row.aantal
            cash = 0.0 if math.isnan(row.totaal_eur) else row.totaal_eur
            if delta > 0:
                aantal += delta
                if cash != 0:
                    kosten += -cash
            elif delta < 0:
                if cash != 0 and aantal > 1e-9:
                    verkocht = min(-delta, aantal)
                    kostenbasis = kosten / aantal * verkocht
                    kosten -= kostenbasis
                    verkopen.append({
                        "datum": row.datum.strftime("%Y-%m-%d"),
                        "ticker": ticker,
                        "bijnaam": row.product,
                        "aantal": round(verkocht, 4),
                        "opbrengst": round(cash, 2),
                        "kostenbasis": round(kostenbasis, 2),
                        "winst": cash - kostenbasis,
                    })
                aantal += delta
        if aantal > 1e-9:
            open_kostenbasis += kosten
    verkopen.sort(key=lambda v: v["datum"])
    return verkopen, open_kostenbasis


def _kosten_en_onvolledig(rijen):
    """Kosten van echte aan- en verkopen; splits, wissels en corporate actions hebben normaal geen kosten."""
    handel = rijen[rijen["totaal_eur"].fillna(0) != 0]
    if "is_wisselrij" in handel.columns:
        handel = handel[~handel["is_wisselrij"].fillna(False).astype(bool)]
    if not handel.empty:
        handel = handel[~handel.apply(_is_corporate_action_row, axis=1)]
    kosten = _som(abs(k) for k in handel["transactiekosten"]) + _som(abs(k) for k in handel["autofx_kosten"])
    return kosten, bool(handel["transactiekosten"].isna().any())


def _dividend_per_jaar(dividenden):
    """{jaar: (bruto, dividendbelasting als positief bedrag)}; uitkeringen zonder EUR-bedrag tellen niet mee."""
    per_jaar = {}
    for d in dividenden:
        jaar = pd.Timestamp(d["datum"]).year
        bruto, belasting = per_jaar.get(jaar, (0.0, 0.0))
        if d.get("bruto_eur") is not None:
            bruto += float(d["bruto_eur"])
        if d.get("belasting_eur") is not None:
            belasting += -float(d["belasting_eur"])
        per_jaar[jaar] = (bruto, belasting)
    return per_jaar


def bouw_box3_basis(transacties_df, resultaat, dividenden, vandaag=None):
    """Portfoliocijfers per kalenderjaar, nog zonder belastingregels. dividenden None = geen rekeningoverzicht."""
    vandaag = pd.Timestamp(vandaag or date.today()).normalize()
    leeg = {"jaren": [], "verkopen": [], "latente_winst": 0.0, "dividend_beschikbaar": dividenden is not None}
    if resultaat is None or resultaat.empty:
        return leeg

    laatste_datum = resultaat.index.max()
    df = _box3_transacties(transacties_df, laatste_datum)
    eerste_datum = df["datum"].min() if not df.empty else resultaat.index.min()
    verkopen, open_kostenbasis = _verkopen_en_open_kostenbasis(df)
    dividend = _dividend_per_jaar(dividenden) if dividenden is not None else None

    jaren = []
    for jaar in range(eerste_datum.year, max(laatste_datum.year, vandaag.year) + 1):
        peil_begin = pd.Timestamp(year=jaar, month=1, day=1) - pd.Timedelta(days=1)
        eind = min(pd.Timestamp(year=jaar, month=12, day=31), laatste_datum)
        rijen = df[df["datum"].dt.year == jaar]
        kosten, kosten_onvolledig = _kosten_en_onvolledig(rijen)
        bruto, belasting = dividend.get(jaar, (0.0, 0.0)) if dividend is not None else (None, None)
        jaren.append({
            "jaar": jaar,
            "lopend": jaar == vandaag.year,
            "waarde_begin": round(waarde_op_of_voor(resultaat, peil_begin, "waarde"), 2),
            "waarde_eind": round(waarde_op_of_voor(resultaat, eind, "waarde"), 2),
            "netto_inleg": round(-_som(rijen["totaal_eur"]), 2),
            "kosten": round(kosten, 2),
            "kosten_onvolledig": kosten_onvolledig,
            "dividend_bruto": round(bruto, 2) if bruto is not None else None,
            "dividendbelasting": round(belasting, 2) if belasting is not None else None,
            "gerealiseerd": round(sum(v["winst"] for v in verkopen if v["datum"].startswith(str(jaar))), 2),
        })

    for v in verkopen:
        v["winst"] = round(v["winst"], 2)
    latente_winst = float(resultaat["waarde"].iloc[-1]) - open_kostenbasis
    dprint(f"[box3] basis: {len(jaren)} jaar, {len(verkopen)} verkopen, latente winst {latente_winst:.2f}")
    return {
        "jaren": jaren,
        "verkopen": verkopen,
        "latente_winst": round(latente_winst, 2),
        "dividend_beschikbaar": dividend is not None,
    }


def valideer_box3_invoer(invoer):
    """(schone invoer, None) of (None, Nederlandse foutmelding). Ontbrekend = 0 / False."""
    if invoer is None:
        invoer = {}
    if not isinstance(invoer, dict):
        return None, "Ongeldige invoer."
    schoon = {}
    for sleutel in INVOER_BEDRAGEN:
        waarde = invoer.get(sleutel)
        if waarde is None or waarde == "":
            waarde = 0
        # bool is in Python ook een int; dat is hier geen bedrag.
        if isinstance(waarde, bool) or not isinstance(waarde, (int, float)) or not math.isfinite(waarde):
            return None, f"'{sleutel}' moet een getal zijn."
        if waarde < 0:
            return None, f"'{sleutel}' mag niet negatief zijn."
        schoon[sleutel] = float(waarde)
    if schoon["spaarrente_pct"] > SPAARRENTE_MAX_PCT:
        return None, f"De spaarrente moet tussen 0 en {SPAARRENTE_MAX_PCT}% liggen."
    partner = invoer.get("fiscale_partner", False)
    if not isinstance(partner, bool):
        return None, "'fiscale_partner' moet waar of onwaar zijn."
    schoon["fiscale_partner"] = partner
    return schoon, None


def _forfaitaire_parameters(jaar):
    """(parameters, geschat) of (None, False) vóór de Overbruggingswet."""
    if jaar < FORFAITAIR_EERSTE_JAAR:
        return None, False
    if jaar in FORFAITAIR:
        return FORFAITAIR[jaar], False
    return FORFAITAIR[max(FORFAITAIR)], True


def _huidig_stelsel(j, invoer, koersresultaat, dividend, ander):
    params, geschat = _forfaitaire_parameters(j["jaar"])
    if params is None:
        return {"berekend": False}
    factor = 2 if invoer["fiscale_partner"] else 1
    bank = invoer["banktegoeden"]
    overig = j["waarde_begin"] + invoer["overige_bezittingen"]
    schuldendrempel = params["schuldendrempel"] * factor
    schulden = max(0.0, invoer["schulden"] - schuldendrempel)
    forfaitair_rendement = bank * params["bank"] + overig * params["overig"] - schulden * params["schuld"]
    grondslag = bank + overig - schulden
    heffingsvrij = params["heffingsvrij"] * factor
    na_vrijstelling = max(0.0, grondslag - heffingsvrij)
    belasting_forfaitair = (
        max(0.0, params["tarief"] * forfaitair_rendement / grondslag * na_vrijstelling) if grondslag > 0 else 0.0
    )
    # Kosten zijn in het huidige stelsel niet aftrekbaar: terugtellen. Spaarrente alleen hier, niet in het forfaitaire deel.
    werkelijk = koersresultaat + j["kosten"] + dividend + ander["totaal"]
    belasting_tegenbewijs = params["tarief"] * max(0.0, werkelijk)
    tegenbewijs_geldt = belasting_tegenbewijs < belasting_forfaitair
    return {
        "berekend": True,
        "geschat": geschat,
        "definitief": params["definitief"],
        "bank": round(bank, 2),
        "overig": round(overig, 2),
        "schulden_boven_drempel": round(schulden, 2),
        "schuldendrempel": schuldendrempel,
        "forfaitair_rendement": round(forfaitair_rendement, 2),
        "grondslag": round(grondslag, 2),
        "heffingsvrij": heffingsvrij,
        "grondslag_na_vrijstelling": round(na_vrijstelling, 2),
        "tarief": params["tarief"],
        "belasting_forfaitair": round(belasting_forfaitair, 2),
        "koersresultaat": round(koersresultaat, 2),
        "kosten": j["kosten"],
        "dividend_bruto": j["dividend_bruto"],
        "spaarrente": ander["spaarrente"],
        "rendement_ander_vermogen": ander["overig"],
        "werkelijk_rendement": round(werkelijk, 2),
        "belasting_tegenbewijs": round(belasting_tegenbewijs, 2),
        "geldt": "tegenbewijs" if tegenbewijs_geldt else "forfaitair",
        "belasting": round(min(belasting_forfaitair, belasting_tegenbewijs), 2),
    }


def _wwr_jaar(resultaat_portfolio, dividend, ander, invoer, verliesvoorraad):
    """Eén jaar volgens het wetsvoorstel; geeft (tussenstappen, nieuwe verliesvoorraad)."""
    heffingsvrij = WWR_HEFFINGSVRIJ_RESULTAAT * (2 if invoer["fiscale_partner"] else 1)
    rendement = resultaat_portfolio + dividend + ander["totaal"]
    verlies_erbij = max(0.0, -rendement - WWR_VERLIESDREMPEL) if rendement < 0 else 0.0
    # Verrekenen met wat na het heffingsvrije resultaat overblijft, anders gaat verlies verloren aan de vrijstelling.
    verrekend = min(verliesvoorraad, max(0.0, rendement - heffingsvrij))
    belastbaar = max(0.0, rendement - verrekend - heffingsvrij)
    nieuwe_voorraad = verliesvoorraad - verrekend + verlies_erbij
    return {
        "rendement": round(rendement, 2),
        "verlies_erbij": round(verlies_erbij, 2),
        "verrekend_verlies": round(verrekend, 2),
        "verliesvoorraad": round(nieuwe_voorraad, 2),
        "heffingsvrij": heffingsvrij,
        "belastbaar": round(belastbaar, 2),
        "belasting": round(belastbaar * WWR_TARIEF, 2),
    }, nieuwe_voorraad


def _b_alles_verkopen(j, latente_winst, dividend, ander, invoer, verliesvoorraad):
    """B in het lopende jaar alsof alles vandaag verkocht wordt: de latente winst telt als gerealiseerd,
    met dezelfde verliesvoorraad als B dat jaar. Geeft {jaar, rendement, belastbaar, heffingsvrij, belasting,
    extra_belasting}; extra = verschil met B zonder verkopen."""
    gewoon, _ = _wwr_jaar(j["gerealiseerd"], dividend, ander, invoer, verliesvoorraad)
    variant, _ = _wwr_jaar(j["gerealiseerd"] + latente_winst, dividend, ander, invoer, verliesvoorraad)
    return {
        "jaar": j["jaar"],
        "latente_winst": round(latente_winst, 2),
        "rendement": variant["rendement"],
        "belastbaar": variant["belastbaar"],
        "heffingsvrij": variant["heffingsvrij"],
        "belasting": variant["belasting"],
        "extra_belasting": round(variant["belasting"] - gewoon["belasting"], 2),
    }


def bereken_box3(basis, invoer):
    """Per jaar de tussenstappen van de drie stelsels (huidig, aanwas, vermogenswinst), plus totalen."""
    spaarrente = invoer["banktegoeden"] * invoer["spaarrente_pct"] / 100
    ander = {
        "spaarrente": round(spaarrente, 2),
        "overig": invoer["rendement_ander_vermogen"],
        "totaal": spaarrente + invoer["rendement_ander_vermogen"],
    }
    jaren = []
    alles_verkopen = None
    voorraad_aanwas = voorraad_winst = 0.0
    for j in basis["jaren"]:
        koersresultaat = j["waarde_eind"] - j["waarde_begin"] - j["netto_inleg"]
        dividend = j["dividend_bruto"] or 0.0
        gemeenschappelijk = {
            "dividend_bruto": j["dividend_bruto"],
            "spaarrente": ander["spaarrente"],
            "rendement_ander_vermogen": ander["overig"],
        }
        aanwas, voorraad_aanwas = _wwr_jaar(koersresultaat, dividend, ander, invoer, voorraad_aanwas)
        if j["lopend"]:
            alles_verkopen = _b_alles_verkopen(j, basis["latente_winst"], dividend, ander, invoer, voorraad_winst)
        winst, voorraad_winst = _wwr_jaar(j["gerealiseerd"], dividend, ander, invoer, voorraad_winst)
        jaren.append({
            "jaar": j["jaar"],
            "lopend": j["lopend"],
            "kosten_onvolledig": j["kosten_onvolledig"],
            "huidig": _huidig_stelsel(j, invoer, koersresultaat, dividend, ander),
            "aanwas": {"koersresultaat": round(koersresultaat, 2), **gemeenschappelijk, **aanwas},
            "vermogenswinst": {"gerealiseerd": j["gerealiseerd"], **gemeenschappelijk, **winst},
        })

    def totaal(stelsel, alleen_lopend=False):
        bedragen = [
            j[stelsel]["belasting"] for j in jaren
            if "belasting" in j[stelsel] and (j["lopend"] or not alleen_lopend)
        ]
        return round(sum(bedragen), 2) if bedragen else None

    stelsels = ("huidig", "aanwas", "vermogenswinst")
    return {
        "parameters_stand": PARAMETERS_STAND,
        "jaren": jaren,
        "totaal": {s: totaal(s) for s in stelsels},
        "lopend_jaar": {s: totaal(s, alleen_lopend=True) for s in stelsels},
        "b_alles_verkopen": alles_verkopen,
        "tarief_wwr": WWR_TARIEF,
        "heffingsvrij_wwr": WWR_HEFFINGSVRIJ_RESULTAAT,
        "verliesdrempel": WWR_VERLIESDREMPEL,
    }
