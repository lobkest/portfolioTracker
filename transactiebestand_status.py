"""Beoordeelt of het transactiebestand (Transactions.xlsx) de hele periode lijkt te beslaan. Alleen aanwijzingen."""
import datetime
import re

import pandas as pd

from diagnostiek_checks import kale_order_id
from portfolio_calc import MIN_AANTAL_TEKORT, stand_per_dag_per_keten
from rekening_regelsoorten import REGELSOORT_KOOP_VERKOOP, regelsoort
from split_correctie import isin_ketens, vind_wisselparen
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl

DIVIDEND_ZOEKVENSTER_DAGEN = 60
MAX_GENOEMD_IN_TEKST = 5
AANTAL_DECIMALEN = 4

REDEN_NEGATIEF_AANTAL = "negatief_aantal"
REDEN_DIVIDEND_ZONDER_POSITIE = "dividend_zonder_positie"
REDEN_REKENING_ZONDER_TRANSACTIE = "rekening_zonder_transactie"

CONTROLE_AANWIJZINGEN = "aanwijzingen"
CONTROLE_NIET_TE_CONTROLEREN = "niet_te_controleren"
CONTROLE_GEEN_AANWIJZINGEN = "geen_aanwijzingen"

_AANTAL_IN_OMSCHRIJVING = re.compile(r"^(Koop|Verkoop) ([\d.,]+)")


def _naam(rij):
    naam = rij.get("echte_naam")
    return naam if pd.notna(naam) else rij.get("product")


def _dag(waarde):
    return pd.Timestamp(waarde).normalize()


def _isin_naam(df):
    """{isin: weergavenaam}, bij voorkeur van een gewone (geen corporate-action) rij."""
    namen = {}
    for rij in df.dropna(subset=["isin"]).to_dict("records"):
        if rij["isin"] not in namen or not _is_corporate_action_row(rij):
            namen[rij["isin"]] = _naam(rij)
    return namen


def _opsomming(items):
    """'X (12-03-2025), Y (…) en N andere' met hooguit MAX_GENOEMD_IN_TEKST namen."""
    genoemd = [f"{i['product'] or i['isin']} ({formatteer_datum_nl(i['datum'])})" for i in items[:MAX_GENOEMD_IN_TEKST]]
    rest = len(items) - MAX_GENOEMD_IN_TEKST
    return ", ".join(genoemd) + (f" en {rest} andere" if rest > 0 else "")


def _controle_negatief_aantal(stand_per_keten, namen):
    items = []
    for eind, stand in stand_per_keten.items():
        tekort = stand[stand < -MIN_AANTAL_TEKORT]
        if not tekort.empty:
            items.append({"isin": eind, "product": namen.get(eind), "datum": tekort.index[0].date().isoformat()})
    items.sort(key=lambda i: i["datum"])
    if not items:
        return None
    if len(items) == 1:
        i = items[0]
        tekst = (f"Er is een verkoop van {i['product'] or i['isin']} op {formatteer_datum_nl(i['datum'])} zonder "
                 f"voldoende eerdere aankopen; eerdere transacties lijken te ontbreken.")
    else:
        tekst = f"Er zijn verkopen zonder voldoende eerdere aankopen: {_opsomming(items)}."
    return {"code": REDEN_NEGATIEF_AANTAL, "tekst": tekst, "items": items}


def _heeft_positie_in_venster(stand, datum):
    if stand is None:
        return False
    begin = datum - pd.Timedelta(days=DIVIDEND_ZOEKVENSTER_DAGEN)
    voor = stand[stand.index <= begin]
    if not voor.empty and voor.iloc[-1] > MIN_AANTAL_TEKORT:
        return True
    return bool((stand[(stand.index > begin) & (stand.index <= datum)] > MIN_AANTAL_TEKORT).any())


def _controle_dividend(transacties_df, stand_per_keten, namen, dividenden):
    if not dividenden:
        return None
    eind_van = isin_ketens(vind_wisselparen(transacties_df)[0])
    per_isin = {}
    for d in dividenden:
        isin, datum = d.get("isin"), d.get("datum")
        if not isin or datum is None or pd.isna(datum):
            continue
        datum = _dag(datum)
        if not _heeft_positie_in_venster(stand_per_keten.get(eind_van.get(isin, isin)), datum):
            eerste = per_isin.setdefault(isin, {"isin": isin, "product": namen.get(isin) or d.get("product"), "datum": datum})
            eerste["datum"] = min(eerste["datum"], datum)
    items = sorted(({**i, "datum": i["datum"].date().isoformat()} for i in per_isin.values()), key=lambda i: i["datum"])
    if not items:
        return None
    if len(items) == 1:
        i = items[0]
        tekst = (f"Er is dividend ontvangen op {i['product'] or i['isin']} ({formatteer_datum_nl(i['datum'])}) zonder "
                 f"bekende positie; eerdere transacties lijken te ontbreken.")
    else:
        tekst = f"Er is dividend ontvangen zonder bekende positie: {_opsomming(items)}."
    return {"code": REDEN_DIVIDEND_ZONDER_POSITIE, "tekst": tekst, "items": items}


def _aantal_uit_omschrijving(omschrijving):
    """Koop = +, Verkoop = −; None als er geen aantal in staat."""
    treffer = _AANTAL_IN_OMSCHRIJVING.match(str(omschrijving or "").strip())
    if not treffer:
        return None
    try:
        aantal = float(treffer.group(2).replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return aantal if treffer.group(1) == "Koop" else -aantal


def _sleutel(datum, isin_of_naam, aantal):
    return (_dag(datum), str(isin_of_naam).strip().lower(), round(aantal, AANTAL_DECIMALEN))


def _transactie_index(transacties_df):
    """(kale Order ID's, losse (dag, isin, aantal) en per-dag-sommen) van de gewone transacties."""
    gewone = transacties_df[[not _is_corporate_action_row(r) for r in transacties_df.to_dict("records")]]
    order_ids, rijen, dagsommen = set(), set(), {}
    for r in gewone.to_dict("records"):
        kaal = kale_order_id(r.get("order_id"))
        if kaal:
            order_ids.add(kaal)
        aantal = pd.to_numeric(r.get("aantal"), errors="coerce")
        if pd.isna(aantal) or pd.isna(r.get("datum")):
            continue
        for naam in (r.get("isin"), r.get("product"), r.get("echte_naam")):
            if pd.notna(naam):
                rijen.add(_sleutel(r["datum"], naam, float(aantal)))
                dag_sleutel = _sleutel(r["datum"], naam, 0.0)
                dagsommen[dag_sleutel] = dagsommen.get(dag_sleutel, 0.0) + float(aantal)
    return order_ids, rijen, dagsommen


def _regel_heeft_transactie(regel, index):
    order_ids, rijen, dagsommen = index
    kaal = kale_order_id(regel.get("order_id"))
    if kaal and kaal in order_ids:
        return True
    aantal = _aantal_uit_omschrijving(regel.get("omschrijving"))
    if aantal is None or regel.get("datum") is None:
        # Zonder aantal is er niets te vergelijken: geen aanwijzing.
        return True
    for naam in (regel.get("isin"), regel.get("product")):
        if not naam or pd.isna(naam):
            continue
        if _sleutel(regel["datum"], naam, aantal) in rijen:
            return True
        if abs(dagsommen.get(_sleutel(regel["datum"], naam, 0.0), 0.0) - aantal) <= 10 ** -AANTAL_DECIMALEN:
            return True
    return False


def _controle_rekening(transacties_df, rekening_regels, rekeningoverzicht_status):
    if not rekening_regels or not rekeningoverzicht_status or rekeningoverzicht_status.get("volledig") is not True:
        return None
    index = _transactie_index(transacties_df)
    zonder = [r for r in rekening_regels
              if r.get("datum") is not None and regelsoort(r.get("omschrijving")) == REGELSOORT_KOOP_VERKOOP
              and not _regel_heeft_transactie(r, index)]
    if not zonder:
        return None
    eerste = formatteer_datum_nl(min(r["datum"] for r in zonder))
    items = [{"isin": r.get("isin"), "product": r.get("product"), "datum": pd.Timestamp(r["datum"]).date().isoformat()}
             for r in sorted(zonder, key=lambda r: r["datum"])]
    n = len(zonder)
    tekst = (f"Het rekeningoverzicht bevat {n} {'aan-/verkoop' if n == 1 else 'aan-/verkopen'} die niet in het "
             f"transactiebestand {'staat' if n == 1 else 'staan'} (eerste: {eerste}).")
    return {"code": REDEN_REKENING_ZONDER_TRANSACTIE, "tekst": tekst, "items": items}


def _controle_status(redenen, rekeningoverzicht_status):
    if redenen:
        return CONTROLE_AANWIJZINGEN
    # Controle C draait alleen bij een volledig overzicht; A en B vonden niets, dus er valt niets te zeggen.
    if rekeningoverzicht_status and rekeningoverzicht_status.get("volledig") is False:
        return CONTROLE_NIET_TE_CONTROLEREN
    return CONTROLE_GEEN_AANWIJZINGEN


def beoordeel_transactiebestand(transacties_df, rekening_regels, dividenden, rekeningoverzicht_status):
    """None zonder transacties. Anders {volledig, controle (aanwijzingen / niet_te_controleren / geen_aanwijzingen), redenen [{code, tekst, items [{isin, product, datum}]}],
    eerste_transactie_datum, laatste_transactie_datum}. Controles: A negatief aantal, B dividend zonder positie in de
    60 dagen ervoor, C Koop/Verkoop in een volledig rekeningoverzicht zonder transactie (anders overgeslagen).
    Een bestand dat later begint en daarna alleen aankopen bevat, is zonder rekeningoverzicht niet te herkennen."""
    if transacties_df is None or transacties_df.empty:
        return None
    stand_per_keten = stand_per_dag_per_keten(transacties_df)
    namen = _isin_naam(transacties_df)
    redenen = [r for r in (
        _controle_negatief_aantal(stand_per_keten, namen),
        _controle_dividend(transacties_df, stand_per_keten, namen, dividenden),
        _controle_rekening(transacties_df, rekening_regels, rekeningoverzicht_status),
    ) if r]

    datums = pd.to_datetime(transacties_df["datum"]).dropna()

    def iso(datum):
        return datum.date().isoformat() if isinstance(datum, datetime.date) else None

    return {
        "volledig": not redenen,
        "controle": _controle_status(redenen, rekeningoverzicht_status),
        "redenen": redenen,
        "eerste_transactie_datum": iso(datums.min()) if not datums.empty else None,
        "laatste_transactie_datum": iso(datums.max()) if not datums.empty else None,
    }
