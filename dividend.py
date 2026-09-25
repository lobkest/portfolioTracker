"""
Verwerking van het DeGiro-rekeningoverzicht (dividend-tabblad):
valutaconversie-koppeling, netto-per-uitkering-berekening en de
opgeslagen-dividenden-samenvatting voor de UI.

Losgetrokken uit analysis.py (was daar een grotendeels zelfstandig blok).
"""
import hashlib

import pandas as pd

from db import get_db_connection, get_dividenden


def _koppel_valutaconversie_paren(df):
    """
    Bouwt de lijst van valutaconversie-'paren' uit een rekeningoverzicht:
    een 'Valuta Debitering'-rij en een 'Valuta Creditering'-rij die bij
    elkaar horen.

    De koppeling gaat via een EXACT gelijke (Datum, Tijd) — DEGIRO boekt
    zo'n conversie altijd als twee rijen met identiek tijdstip. Dit is
    bewust NIET gekoppeld via Valutadatum: de 'Dividend'-rij die tot deze
    conversie leidde heeft vaak een Valutadatum van 1 (bank-)dag eerder dan
    de conversie zelf (de conversie wordt pas de volgende werkdag
    afgewikkeld) — matchen op Valutadatum was precies de eerdere bug (zie
    verwerk_rekeningoverzicht).

    Normaliter is de Debitering-rij de vreemde valuta (negatief) en de
    Creditering-rij EUR (positief) — de normale dividend-conversie (vreemd
    -> EUR). Maar soms wisselt DEGIRO de andere kant op: EUR -> vreemd, bv.
    om een buitenlandse dividendbelasting te dekken die niet uit een eerder
    ontvangen vreemde-valuta-dividend betaald kon worden. Dan is juist de
    Debitering-rij EUR. Dit paar bepaalt daarom zelf, per rij, welke van de
    twee EUR is (ongeacht Debitering/Creditering) i.p.v. dat aan te nemen.

    Geeft een lijst van dicts terug: {datum, tijd, valuta (de vreemde
    valuta), vreemd_bedrag (positief), eur_bedrag (het bedrag van de
    EUR-rij MET teken: positief als EUR is bijgeschreven — vreemd->EUR —
    negatief als EUR is afgeschreven — EUR->vreemd), gebruikt (bool, wordt
    True gezet zodra een dividendgroep hem claimt)}.
    """
    fx_rows = df[df["Omschrijving"].isin(["Valuta Debitering", "Valuta Creditering"])]
    paren = []
    for (datum, tijd), groep in fx_rows.groupby(["Datum", "Tijd"]):
        debitering = groep[groep["Omschrijving"] == "Valuta Debitering"]
        creditering = groep[groep["Omschrijving"] == "Valuta Creditering"]
        if debitering.empty or creditering.empty:
            continue
        deb = debitering.iloc[0]
        cred = creditering.iloc[0]
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
    """Zoekt in 'paren' (zie _koppel_valutaconversie_paren) het nog niet
    gebruikte paar met dezelfde valuta en (bijna) hetzelfde bedrag als
    'netto_ruw' — dat is het paar dat DEZE dividenduitkering heeft
    omgewisseld naar EUR. Bij meerdere kandidaten (zelfde valuta+bedrag,
    bv. twee identieke dividendbedragen in dezelfde periode) wint de
    kandidaat die qua datum het dichtst bij de dividenddatum ligt. Geeft
    None terug als er geen match binnen tolerantie is."""
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
# Hoeveel dagen twee opeenvolgende (op datum gesorteerde) ongematchte
# dividendgroepen uit elkaar mogen liggen om nog in dezelfde STAP-A-pool
# te vallen (zie verwerk_rekeningoverzicht_df). Bewust ruim genoeg voor
# DeGiro's afwikkeltiming (dividend -> conversie is meestal 1 dag), maar
# begrensd zodat losstaande dividenden van weken uit elkaar niet per
# ongeluk samengevoegd worden.


def _clusters_binnen_venster(items, max_dagen):
    """Groepeert 'items' (dicts met een 'datum'-sleutel) in clusters van
    opeenvolgende (op datum gesorteerde) items, waarbij het verschil
    tussen twee opeenvolgende datums binnen een cluster niet groter is dan
    'max_dagen'. Gebruikt door STAP A hieronder om per valuta alleen
    dividendgroepen te poolen die qua datum dicht genoeg bij elkaar
    liggen."""
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
    """
    Doet het eigenlijke werk van verwerk_rekeningoverzicht() op een AL
    ingelezen en hernoemde DataFrame (kolommen: Datum, Tijd, Valutadatum,
    Product, ISIN, Omschrijving, FX, valuta_mutatie, mutatie, valuta_saldo,
    saldo, Order Id — Datum/Valutadatum als datetime, mutatie als float).
    Losgetrokken van het Excel-inlezen zodat dit met een handgemaakte
    DataFrame te unittesten is (zie tests/test_dividend.py), zonder een
    echt .xlsx-bestand te hoeven bouwen.

    Per dividenduitkering (gegroepeerd op Datum+ISIN, want correcties/
    meerdere boekingen voor dezelfde uitkering delen dezelfde Datum):
    - alle 'Dividend'-, 'Dividend Herinvestering'- en 'Dividendbelasting'-
      rijen worden genet (inclusief eventuele negatieve correctierijen) tot
      één bruto- en één belastingbedrag in de eigen valuta. Een 'Dividend
      Herinvestering'-rij heft het bijbehorende 'Dividend'-bedrag geheel of
      gedeeltelijk op (automatisch herbelegd i.p.v. uitgekeerd) — het
      record krijgt een 'herinvesteerd'-vlag zodat de frontend kan tonen
      *waarom* een bedrag klein/nul/negatief is.
    - is die valuta EUR, dan is dat meteen het EUR-bedrag
    - is die valuta NIET EUR, dan wordt EERST geprobeerd deze ÉÉN groep
      1-op-1 te koppelen aan een 'Valuta Debitering'/'Valuta Creditering'-
      paar (via _match_valutaconversie) — NIET een eigen FX-herberekening.
      Lukt dat niet, dan is er een TWEEDE ronde (STAP A hieronder): DeGiro
      boekt soms meerdere dividenden van dezelfde dag/valuta samen in ÉÉN
      conversie (bv. twee ETF-uitkeringen dezelfde dag) — dan wordt zo'n
      conversie nooit door de 1-op-1 match gevonden. Alle nog ongematchte
      groepen per valuta worden daarom geclusterd (zie
      _clusters_binnen_venster, max DIVIDEND_POOL_MAX_DAGEN_VERSCHIL dagen
      uit elkaar) en als cluster (som van hun netto ruwe bedragen) alsnog
      tegen een ongebruikt conversiepaar geprobeerd. Bij een match wordt
      het EUR-bedrag van het paar proportioneel verdeeld over de
      deelnemende groepen naar rato van hun eigen aandeel in de pool-som.
      In beide rondes worden bruto/belasting naar rato van hun eigen aandeel
      in het (groeps- of pool-)netto ruwe bedrag verdeeld over het
      gevonden EUR-bedrag, zodat bruto_eur + belasting_eur altijd optelt
      tot netto_eur.
    - is er ook na STAP A geen conversie gevonden, dan blijven bruto_eur/
      belasting_eur/netto_eur expliciet None ('onbekend') — nooit een gok.
    """
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

        # Ruwe (niet-EUR-geconverteerde) bedragen in de dividend_id, zodat die
        # stabiel blijft ongeacht welk valutaconversie-paar er (opnieuw)
        # aan gekoppeld wordt bij een herhaalde upload.
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

    # STAP A — gepoolde valutaconversie (zie docstring hierboven). Alleen
    # groepen die na de 1-op-1 ronde nog geen netto_eur hebben, gegroepeerd
    # per valuta en geclusterd op datumnabijheid.
    onopgelost_per_valuta = {}
    for r in tussenresultaten:
        if r["valuta"] != "EUR" and r["netto_eur"] is None:
            onopgelost_per_valuta.setdefault(r["valuta"], []).append(r)

    for valuta, items in onopgelost_per_valuta.items():
        for cluster in _clusters_binnen_venster(items, DIVIDEND_POOL_MAX_DAGEN_VERSCHIL):
            if len(cluster) < 2:
                # Een cluster van 1 is exact dezelfde poging als de al
                # mislukte 1-op-1 match hierboven — niets te winnen.
                continue
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


def verwerk_rekeningoverzicht(file_object):
    """
    Leest een DeGiro-rekeningoverzicht in en geeft een lijst van
    dividendrecords terug: {datum, product, isin, valuta, bruto_eur,
    belasting_eur, netto_eur, dividend_id, herinvesteerd}. Het eigenlijke rekenwerk zit in
    verwerk_rekeningoverzicht_df() hierboven; deze functie doet alleen het
    Excel-inlezen en de kolom-normalisatie.

    Kolom-quirk (anders dan bij het transactiebestand): "Mutatie" en
    "Saldo" zijn elk samengevoegde headers over twee kolommen (valutacode +
    bedrag) — pandas geeft de tweede kolom van elk paar de naam
    "Unnamed: 8" / "Unnamed: 10" i.p.v. verkeerd uitgelijnd te zijn, dus die
    hernoemen we hier expliciet naar leesbare namen.
    """
    file_object.seek(0)
    df = pd.read_excel(file_object)
    df.columns = df.columns.str.strip()
    df = df.rename(columns={
        "Mutatie": "valuta_mutatie",
        "Unnamed: 8": "mutatie",
        "Saldo": "valuta_saldo",
        "Unnamed: 10": "saldo",
    })
    df["Datum"] = pd.to_datetime(df["Datum"], dayfirst=True)
    df["Valutadatum"] = pd.to_datetime(df["Valutadatum"], dayfirst=True)
    df["mutatie"] = pd.to_numeric(df["mutatie"], errors="coerce")

    return verwerk_rekeningoverzicht_df(df)


def bereken_dividend_samenvatting(code):
    """
    Samenvatting van alle opgeslagen dividenden voor deze code: totaal
    netto-ontvangen, per ticker/bijnaam, en een gezamenlijke cumulatieve
    tijdreeks per ticker voor de gestapelde grafiek.

    Geeft None terug als er geen dividenden zijn opgeslagen — de aanroeper
    (app.py) weet dan dat er nooit een rekeningoverzicht is geüpload voor
    deze code, i.p.v. dat te verwarren met "wel geüpload, maar toevallig
    geen dividend ontvangen".
    """
    dividenden = get_dividenden(code)
    if not dividenden:
        return None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT isin, ticker, product FROM transacties WHERE code = %s AND ticker IS NOT NULL",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # ISIN is de sleutel voor de koppeling, niet de naam — zelfde aanpak als
    # elders in dit project (zie find_ticker_detailed/verifieer_ticker_met_prijs).
    # Bij meerdere transactierijen voor dezelfde ISIN wint de eerste
    # (willekeurige volgorde uit de query) — voor dividend-koppeling is dat
    # voldoende precisie, in tegenstelling tot de rendementsberekening is er
    # hier geen aparte behandeling per beursnotering nodig.
    isin_naar_ticker = {}
    isin_naar_bijnaam = {}
    for isin, ticker, product in rows:
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

    # Losse uitkeringen, ongeaggregeerd, voor de lijst onderaan het
    # Dividend-tabblad — nieuwste eerst. Rijen met netto_eur=None (onbekende
    # valutaconversie) blijven staan i.p.v. weggefilterd te worden, zelfde
    # bewuste "nooit een gok"-gedrag als de rest van deze functie.
    lijst = sorted(
        (
            {
                "datum": d["datum"].strftime("%Y-%m-%d"),
                "ticker": isin_naar_ticker.get(d["isin"]) or d["isin"],
                "bijnaam": isin_naar_bijnaam.get(d["isin"]) or d["product"] or (isin_naar_ticker.get(d["isin"]) or d["isin"]),
                "valuta": d["valuta"],
                "bruto_eur": round(d["bruto_eur"], 2) if d["bruto_eur"] is not None else None,
                "belasting_eur": round(d["belasting_eur"], 2) if d["belasting_eur"] is not None else None,
                "netto_eur": round(d["netto_eur"], 2) if d["netto_eur"] is not None else None,
                "herinvesteerd": d.get("herinvesteerd", False),
            }
            for d in dividenden
        ),
        key=lambda x: x["datum"], reverse=True,
    )

    # Alle tickers uitlijnen op dezelfde datumas (unie van alle dividend-
    # datums) en forward-fillen, zodat de gestapelde grafiek geen gaten heeft.
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


