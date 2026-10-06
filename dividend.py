"""DeGiro-rekeningoverzicht verwerken en de dividend-samenvatting. Zie CLAUDE.md: DeGiro-bestanden."""
import hashlib

import pandas as pd

from db import db_get_dividenden, db_get_isin_ticker_product
from transactie_utils import OngeldigExcelBestand

ORDER_ID_KOLOM_REKENING = "Order Id"
VERWACHTE_KOLOMMEN_REKENING = ["Datum", "Omschrijving", "Mutatie", ORDER_ID_KOLOM_REKENING]
MELDING_GEEN_REKENINGOVERZICHT = ("Dit lijkt geen DeGiro-rekeningoverzicht. Kies bij DeGiro "
                                  "Inbox > Rekeningoverzicht > Excel.")


def _koppel_valutaconversie_paren(df):
    """Paren op exact gelijke (Datum, Tijd), niet op Valutadatum.
    Geeft [{datum, tijd, valuta, vreemd_bedrag, eur_bedrag (met teken), gebruikt}]."""
    fx_rows = df[df["Omschrijving"].isin(["Valuta Debitering", "Valuta Creditering"])]
    paren = []
    for (datum, tijd), groep in fx_rows.groupby(["Datum", "Tijd"]):
        debitering = groep[groep["Omschrijving"] == "Valuta Debitering"]
        creditering = groep[groep["Omschrijving"] == "Valuta Creditering"]
        if debitering.empty or creditering.empty:
            continue
        deb = debitering.iloc[0]
        cred = creditering.iloc[0]
        # Soms wisselt DeGiro EUR -> vreemd; dan is juist de Debitering-rij EUR.
        if deb["valuta_mutatie"] == "EUR":
            eur_rij, vreemd_rij = deb, cred
        else:
            eur_rij, vreemd_rij = cred, deb
        paar = {
            "datum": datum,
            "tijd": tijd,
            "valuta": vreemd_rij["valuta_mutatie"],
            "vreemd_bedrag": abs(float(vreemd_rij["mutatie"])),
            "eur_bedrag": float(eur_rij["mutatie"]),
            "gebruikt": False,
        }
        paren.append(paar)
    return paren


def _match_valutaconversie(paren, valuta, netto_ruw, datum, tolerantie=0.02):
    """Ongebruikt paar met zelfde valuta en bedrag; bij meerdere wint de dichtstbijzijnde datum."""
    kandidaten = [
        p for p in paren
        if not p["gebruikt"] and p["valuta"] == valuta and abs(p["vreemd_bedrag"] - abs(netto_ruw)) <= tolerantie
    ]
    if not kandidaten:
        return None
    kandidaten.sort(key=lambda p: abs((p["datum"] - datum).days))
    gekozen = kandidaten[0]
    gekozen["gebruikt"] = True
    return gekozen


DIVIDEND_POOL_MAX_DAGEN_VERSCHIL = 3
# Ruim genoeg voor DeGiro's afwikkeling (~1 dag), klein genoeg om losse dividenden niet samen te voegen.


def _clusters_binnen_venster(items, max_dagen):
    """Clusters van op datum gesorteerde items met hooguit max_dagen tussen twee opeenvolgende."""
    items_gesorteerd = sorted(items, key=lambda x: x["datum"])
    clusters = []
    huidig = []
    for item in items_gesorteerd:
        if huidig and (item["datum"] - huidig[-1]["datum"]).days > max_dagen:
            clusters.append(huidig)
            huidig = []
        huidig.append(item)
    if huidig:
        clusters.append(huidig)
    return clusters


def verwerk_rekeningoverzicht_df(df):
    """Rekenwerk op een al ingelezen, hernoemde DataFrame (los van Excel, zodat het testbaar is).
    Per uitkering (Datum + ISIN) netten, dan 1-op-1 koppelen aan een conversie, daarna gepoold."""
    conversie_paren = _koppel_valutaconversie_paren(df)

    dividend_rows = df[df["Omschrijving"].isin(
        ["Dividend", "Dividend Herinvestering", "Dividendbelasting"]
    )]

    herinvesteerd_keys = {
        (datum, isin) for datum, isin in
        df.loc[df["Omschrijving"] == "Dividend Herinvestering", ["Datum", "ISIN"]]
        .itertuples(index=False, name=None)
    }

    tussenresultaten = []
    for (datum, isin), groep in dividend_rows.groupby(["Datum", "ISIN"]):
        bruto_rijen = groep[groep["Omschrijving"].isin(["Dividend", "Dividend Herinvestering"])]
        belasting_rijen = groep[groep["Omschrijving"] == "Dividendbelasting"]

        product = groep["Product"].iloc[0]
        valuta = groep["valuta_mutatie"].dropna().iloc[0] if groep["valuta_mutatie"].notna().any() else "EUR"

        bruto_ruw = float(bruto_rijen["mutatie"].sum()) if not bruto_rijen.empty else 0.0
        belasting_ruw = float(belasting_rijen["mutatie"].sum()) if not belasting_rijen.empty else 0.0
        netto_ruw = bruto_ruw + belasting_ruw
        herinvesteerd = (datum, isin) in herinvesteerd_keys


        if valuta == "EUR":
            bruto_eur, belasting_eur, netto_eur = bruto_ruw, belasting_ruw, netto_ruw
        else:
            match = _match_valutaconversie(conversie_paren, valuta, netto_ruw, datum)
            if match is None:
                bruto_eur = belasting_eur = netto_eur = None
            else:
                netto_eur = match["eur_bedrag"]
                if netto_ruw != 0:
                    bruto_eur = netto_eur * (bruto_ruw / netto_ruw)
                    belasting_eur = netto_eur * (belasting_ruw / netto_ruw)
                else:
                    bruto_eur = belasting_eur = 0.0

        # Ruwe bedragen: de id blijft gelijk als de EUR-koppeling verandert.
        dividend_id = "DIV-" + hashlib.md5(
            f"{datum.date()}|{isin}|{bruto_ruw:.6f}|{belasting_ruw:.6f}".encode()
        ).hexdigest()[:16]

        tussenresultaten.append({
            "datum": datum,
            "product": product,
            "isin": isin,
            "valuta": valuta,
            "bruto_ruw": bruto_ruw,
            "belasting_ruw": belasting_ruw,
            "netto_ruw": netto_ruw,
            "bruto_eur": bruto_eur,
            "belasting_eur": belasting_eur,
            "netto_eur": netto_eur,
            "dividend_id": dividend_id,
            "herinvesteerd": herinvesteerd,
        })

    # Tweede ronde: DeGiro poolt soms meerdere dividenden in één conversie.
    onopgelost_per_valuta = {}
    for r in tussenresultaten:
        if r["valuta"] != "EUR" and r["netto_eur"] is None:
            onopgelost_per_valuta.setdefault(r["valuta"], []).append(r)

    for valuta, items in onopgelost_per_valuta.items():
        for cluster in _clusters_binnen_venster(items, DIVIDEND_POOL_MAX_DAGEN_VERSCHIL):
            if len(cluster) < 2:
                continue  # zelfde poging als de al mislukte 1-op-1 match
            som_netto_ruw = sum(item["netto_ruw"] for item in cluster)
            referentiedatum = max(item["datum"] for item in cluster)
            match = _match_valutaconversie(conversie_paren, valuta, som_netto_ruw, referentiedatum)
            if match is None:
                continue
            for item in cluster:
                aandeel = (item["netto_ruw"] / som_netto_ruw) if som_netto_ruw != 0 else 0.0
                item["netto_eur"] = match["eur_bedrag"] * aandeel
                if item["netto_ruw"] != 0:
                    item["bruto_eur"] = item["netto_eur"] * (item["bruto_ruw"] / item["netto_ruw"])
                    item["belasting_eur"] = item["netto_eur"] * (item["belasting_ruw"] / item["netto_ruw"])
                else:
                    item["bruto_eur"] = item["belasting_eur"] = 0.0

    records = [
        {
            "datum": r["datum"].date(),
            "product": r["product"],
            "isin": r["isin"],
            "valuta": r["valuta"],
            "bruto_eur": r["bruto_eur"],
            "belasting_eur": r["belasting_eur"],
            "netto_eur": r["netto_eur"],
            "dividend_id": r["dividend_id"],
            "herinvesteerd": r["herinvesteerd"],
        }
        for r in tussenresultaten
    ]
    return records


def lees_rekeningoverzicht(file_object):
    file_object.seek(0)
    df = pd.read_excel(file_object)
    df.columns = df.columns.str.strip()
    if any(kolom not in df.columns for kolom in VERWACHTE_KOLOMMEN_REKENING):
        raise OngeldigExcelBestand(MELDING_GEEN_REKENINGOVERZICHT)
    # Samengevoegde koppen, zie CLAUDE.md: DeGiro-bestanden.
    df = df.rename(columns={
        "Mutatie": "valuta_mutatie",
        "Unnamed: 8": "mutatie",
        "Saldo": "valuta_saldo",
        "Unnamed: 10": "saldo",
    })
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["Valutadatum"] = pd.to_datetime(df["Valutadatum"], dayfirst=True)
    df["mutatie"] = pd.to_numeric(df["mutatie"], errors="coerce")
    return df


def order_ids_uit_rekeningoverzicht_df(df):
    if ORDER_ID_KOLOM_REKENING not in df.columns:
        return set()
    return {str(o).strip() for o in df[ORDER_ID_KOLOM_REKENING].dropna() if str(o).strip()}


def bereken_dividend_samenvatting(code):
    """None = nooit een rekeningoverzicht geüpload (niet hetzelfde als 'geen dividend')."""
    dividenden = db_get_dividenden(code)
    if not dividenden:
        return None

    return bouw_dividend_samenvatting(dividenden, db_get_isin_ticker_product(code))


def _afronden_of_none(bedrag):
    return round(float(bedrag), 2) if bedrag is not None else None


def bouw_dividend_samenvatting(dividenden, transactie_rows):
    """None bij een lege lijst. Bedragen mogen float, Decimal of None zijn; transactie_rows: (isin, ticker, product)."""
    if not dividenden:
        return None

    # Op ISIN alleen (eerste rij wint): per beurs splitsen is hier niet nodig.
    isin_naar_ticker = {}
    isin_naar_bijnaam = {}
    for isin, ticker, product in transactie_rows:
        isin_naar_ticker.setdefault(isin, ticker)
        isin_naar_bijnaam.setdefault(isin, product)

    per_ticker_info = {}
    per_ticker_punten = {}
    totaal_netto = 0.0

    for d in dividenden:
        if d["netto_eur"] is None:
            continue
        netto = float(d["netto_eur"])
        totaal_netto += netto

        ticker = isin_naar_ticker.get(d["isin"]) or d["isin"]
        bijnaam = isin_naar_bijnaam.get(d["isin"]) or d["product"] or ticker

        info = per_ticker_info.setdefault(ticker, {"bijnaam": bijnaam, "totaal": 0.0})
        info["totaal"] += netto
        per_ticker_punten.setdefault(ticker, []).append((d["datum"], netto))

    per_ticker = sorted(
        (
            {"ticker": t, "bijnaam": v["bijnaam"], "totaal_netto": round(v["totaal"], 2)}
            for t, v in per_ticker_info.items()
        ),
        key=lambda x: x["totaal_netto"], reverse=True,
    )

    # Rijen met netto_eur None blijven bewust in de lijst staan.
    lijst = sorted(
        (
            {
                "datum": d["datum"].strftime("%Y-%m-%d"),
                "ticker": isin_naar_ticker.get(d["isin"]) or d["isin"],
                "bijnaam": isin_naar_bijnaam.get(d["isin"]) or d["product"] or (isin_naar_ticker.get(d["isin"]) or d["isin"]),
                "valuta": d["valuta"],
                "bruto_eur": _afronden_of_none(d["bruto_eur"]),
                "belasting_eur": _afronden_of_none(d["belasting_eur"]),
                "netto_eur": _afronden_of_none(d["netto_eur"]),
                "herinvesteerd": d.get("herinvesteerd", False),
            }
            for d in dividenden
        ),
        key=lambda x: x["datum"], reverse=True,
    )

    # Eén gedeelde datumas, zodat de gestapelde grafiek geen gaten heeft.
    alle_datums = sorted({datum for punten in per_ticker_punten.values() for datum, _ in punten})
    cumulatief_per_ticker = {}
    for ticker, punten in per_ticker_punten.items():
        per_datum = {}
        for datum, netto in punten:
            per_datum[datum] = per_datum.get(datum, 0.0) + netto
        cum = 0.0
        reeks = []
        for datum in alle_datums:
            cum += per_datum.get(datum, 0.0)
            reeks.append(round(cum, 2))
        cumulatief_per_ticker[ticker] = reeks

    return {
        "totaal_netto": round(totaal_netto, 2),
        "per_ticker": per_ticker,
        "cumulatief": {
            "datums": [d.strftime("%Y-%m-%d") for d in alle_datums],
            "per_ticker": cumulatief_per_ticker,
        },
        "lijst": lijst,
    }


