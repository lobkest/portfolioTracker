"""
Statistieken-tabblad: rendement-, XIRR-, TWR- en jaaroverzicht-berekeningen.

De `bereken_*`-functies hier zijn bewust pure functies (getallen/DataFrames
in, getallen uit, geen DB/netwerk-toegang) -- dat maakt ze met de hand na te
rekenen en apart te unittesten (zie tests/test_rendement.py e.a.) zonder een
databaseverbinding of live yfinance-data nodig te hebben.
bereken_statistieken() is de orkestratie die er transacties_df/price_data/
resultaat (al berekend door portfolio_calc-functies) voor voedt.

Losgetrokken uit analysis.py (was daar het laatste blok functies).
"""
import pandas as pd
from pyxirr import xirr

from transactie_utils import _is_corporate_action_row, _sorteer_chronologisch

# Benchmarks voor de rendement-vergelijking (zie bereken_benchmark_
# vergelijking hieronder) -- allemaal accumulerende (Acc.) UCITS-ETF's in
# EUR, zodat get_prices() ze zonder extra dividend-boekhouding kan gebruiken.
# S&P 500/Nasdaq 100 waren al bekend uit ETF_HOLDINGS_BRON; AEX is apart
# opgezocht en getest (yf.Ticker("IAEA.AS").info -> "iShares AEX UCITS ETF
# EUR (Acc)", koersdata vanaf 2020-07-29) -- er bestaat geen accumulerende
# AEX-ETF met een langere koershistorie op Yahoo.
BENCHMARK_TICKERS = {
    "S&P 500": "VUSA.AS",
    "Nasdaq 100": "CNDX.AS",
    "AEX": "IAEA.AS",
}


# ---------------------------------------------------------------------------
# Statistieken-tabblad
#
# De onderstaande "bereken_*"-functies zijn bewust pure functies (getallen in,
# getallen uit, geen DataFrame/DB-toegang) — dat maakt ze met de hand na te
# rekenen en apart te unittesten (zie tests/test_rendement.py) zonder een
# databaseverbinding of live yfinance-data nodig te hebben. bereken_statistieken()
# hieronder is de orkestratie die er transacties_df/price_data/resultaat
# (al berekend in analyze_transacties) voor voedt.
# ---------------------------------------------------------------------------

def bereken_positie_rendement(gak, aantal, huidige_koers):
    """Rendement van 1 positie op basis van GAK (gemiddelde aankoopkoers).
    geinvesteerd = kostenbasis van de nu aangehouden stukken (GAK x aantal),
    NIET het historische netto-ingelegde bedrag (dat kan door eerdere
    verkopen anders zijn) — voor 'wat heb ik betaald voor wat ik nu heb' is
    de kostenbasis van de huidige positie de juiste noemer."""
    geinvesteerd = gak * aantal
    waarde = aantal * huidige_koers
    rendement_pct = ((waarde - geinvesteerd) / geinvesteerd * 100) if geinvesteerd else None
    return {"waarde": waarde, "geinvesteerd": geinvesteerd, "rendement_pct": rendement_pct}


def bereken_totaal_rendement(geinvesteerd, waarde):
    """Rendement% als simpele ratio winst/geïnvesteerd — houdt GEEN rekening
    met WANNEER er is ingelegd (dat is XIRR, zie bereken_xirr)."""
    rendement_eur = waarde - geinvesteerd
    rendement_pct = (rendement_eur / geinvesteerd * 100) if geinvesteerd else None
    return {"rendement_eur": rendement_eur, "rendement_pct": rendement_pct}


def bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde):
    """Winst van 1 kalenderjaar. winst_pct deelt door (startwaarde + ingelegd)
    — het bedrag dat aan het eind van het jaar 'ingezet' is, niet het
    gemiddelde over het hele jaar — zelfde ratio-methode als
    bereken_totaal_rendement, nu toegepast op dit ene jaar i.p.v. de hele
    portefeuille."""
    winst_eur = eindwaarde - startwaarde - ingelegd
    noemer = startwaarde + ingelegd
    winst_pct = (winst_eur / noemer * 100) if noemer else None
    return {"winst_eur": winst_eur, "winst_pct": winst_pct}


def bereken_xirr(cashflows):
    """cashflows: lijst van (datum, bedrag)-tuples vanuit het perspectief van
    de belegger — aankopen negatief, verkopen positief, plus een laatste
    fictieve 'verkoop' van de huidige waarde op vandaag. Geeft de
    geannualiseerde, tijdgewogen rentevoet terug (als fractie, dus 0.10 =
    10%), of None als pyxirr geen oplossing kan vinden (bv. te weinig of
    tegenstrijdige cashflows)."""
    if len(cashflows) < 2:
        return None
    datums = [c[0] for c in cashflows]
    bedragen = [c[1] for c in cashflows]
    try:
        return xirr(datums, bedragen)
    except Exception as e:
        # print(f"[statistieken] XIRR-berekening mislukt: {e}")
        return None


def bereken_twr(transacties_df, resultaat):
    """Time-Weighted Return (TWR): rendementsmaat die, anders dan XIRR, niet
    vertekend wordt door de TIMING van stortingen/onttrekkingen — elke
    sub-periode (tussen twee opeenvolgende datums in `resultaat`) krijgt een
    eigen rendement op basis van de portfoliowaarde, onafhankelijk van
    hoeveel geld er die dag bij kwam. Poort van compute_twr_from_values() uit
    het oude class_degiro.py.

    cf_lookup: per datum de som van externe cashflows (-totaal_eur, positief
    bij een aankoop/storting, net als delta_cash in bereken_holdings_en_
    gesloten) — DEGIRO's corporate-action-boekingsrijen (zie
    _is_corporate_action_row) tellen niet mee, dat is geen geld dat de
    belegger zelf inlegt/onttrekt.

    Sub-periode-rendement r = waarde_eind / (waarde_start + cf) - 1, waarbij
    cf de cashflow van de EIND-datum van de sub-periode is (cash komt binnen
    vóór de koersbeweging van die dag telt, dus telt mee in de noemer).
    Sub-periodes met een noemer van (ongeveer) 0 — bv. vóór de eerste
    aankoop, of een volledige verkoop die de waarde naar 0 brengt — worden
    overgeslagen, geen zinnig rendement te berekenen. De losse sub-periode-
    rendementen worden samen vermenigvuldigd (linking) en aan het eind -1
    gedaan.

    Geeft TWR als fractie terug (0.10 = 10%), of None als er geen enkele
    geldige sub-periode is (zelfde edge case als bereken_xirr)."""
    if resultaat.empty or len(resultaat) < 2:
        return None

    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cf_lookup = {}
    for _, row in df.iterrows():
        cf = -float(row["totaal_eur"])
        if cf == 0:
            continue
        datum = pd.Timestamp(row["datum"]).normalize()
        cf_lookup[datum] = cf_lookup.get(datum, 0.0) + cf

    product = 1.0
    geldige_periode = False
    for i in range(1, len(resultaat)):
        waarde_start = float(resultaat["waarde"].iloc[i - 1])
        waarde_eind = float(resultaat["waarde"].iloc[i])
        cf = cf_lookup.get(pd.Timestamp(resultaat.index[i]).normalize(), 0.0)
        noemer = waarde_start + cf
        if abs(noemer) < 1e-9:
            continue
        product *= waarde_eind / noemer
        geldige_periode = True

    if not geldige_periode:
        return None
    return product - 1


def bereken_holdings_gak(transacties_df):
    """Per ticker: huidige aantal + GAK (gemiddelde aankoopkoers) via de
    lopende-gemiddelde-kostprijs-methode (zelfde methode als DEGIRO zelf
    hanteert).

    ALLE rijen tellen mee voor het aantal — ook DEGIRO's
    corporate-action-boekingsrijen (zie _is_corporate_action_row): die
    overslaan zou bij een split het aandelenaantal dubbel tellen (oude +
    nieuwe stukken blijven dan allebei meetellen). Of een negatieve rij de
    kostenbasis evenredig verlaagt, hangt af van of er een ECHTE cashflow
    bij zit (totaal_eur != 0): bij een verkoop realiseer je een deel van de
    kostenbasis (dat deel gaat eraf), maar bij een split-boekingsrij (aantal
    negatief, totaal_eur=0, geen geld dat van eigenaar wisselt) blijft de
    kostenbasis intact — alleen het aantal daalt tijdelijk, om vervolgens via
    de bijbehorende conversie-rij weer (met meer stukken) aangevuld te
    worden. Zo verdunt een split de GAK per aandeel vanzelf correct, zonder
    de kostenbasis aan te tasten. Posities die volledig verkocht zijn
    (aantal <= 0) komen niet in het resultaat terecht.

    Geeft {ticker: {"aantal": float, "gak": float}} terug. Dunne wrapper om
    bereken_holdings_en_gesloten() — behouden voor bestaande aanroepers/tests
    die alleen de open posities nodig hebben."""
    open_posities, _ = bereken_holdings_en_gesloten(transacties_df)
    return open_posities


def bereken_holdings_en_gesloten(transacties_df):
    """Zelfde lopende-gemiddelde-kostprijs-methode als bereken_holdings_gak()
    hierboven, maar houdt in dezelfde doorloop ook posities bij die
    volledig verkocht zijn (één pas door de data, zodat de boekhoud-logica
    — split-correctie, GAK-methode — niet op twee plekken hoeft te kloppen).

    Geeft (open_posities, gesloten_posities) terug:
    - open_posities: {ticker: {"aantal", "gak"}} — ongewijzigd t.o.v.
      bereken_holdings_gak().
    - gesloten_posities: {ticker: {"aantal", "gemiddelde_aankoopkoers",
      "gemiddelde_verkoopkoers", "gerealiseerd_eur"}} voor tickers die ooit
      een positie hadden (totaal_gekocht_aantal > 0) en nu op (ongeveer)
      nul staan.

    Net als bij de kostenbasis geldt: alleen rijen met een ECHTE cashflow
    (totaal_eur != 0) tellen mee voor de gemiddelde aankoop-/verkoopkoers —
    DEGIRO's split-conversierijen (aantal negatief/positief, totaal_eur=0)
    zijn geen echte koop/verkoop en zouden de gemiddelde prijs vertekenen."""
    open_posities = {}
    gesloten_posities = {}
    df = transacties_df.dropna(subset=["ticker"])
    for ticker, groep in df.groupby("ticker"):
        # Chronologisch (datum+tijd), niet alleen datum: bij een koop en
        # verkoop op dezelfde dag (bv. een beurswissel) kon de verkoop vóór
        # de koop verwerkt worden — de aantal_lopend > 0-check hieronder
        # faalt dan en de verkoopopbrengst wordt stilzwijgend niet meegeteld
        # (zichtbaar als een 'onbekende' verkoopkoers op Statistieken). Zie
        # _sorteer_chronologisch().
        groep = _sorteer_chronologisch(groep)
        aantal_lopend = 0.0
        kostprijs_lopend = 0.0
        totaal_gekocht_aantal = 0.0
        totaal_gekocht_bedrag = 0.0
        totaal_verkocht_aantal = 0.0
        totaal_verkocht_bedrag = 0.0
        totaal_verkochte_kostenbasis = 0.0

        for _, row in groep.iterrows():
            delta_aantal = float(row["aantal"])
            # totaal_eur incl. AutoFX/transactiekosten; alleen gebruikt voor
            # de cashflow-check (corporate-action-rijen hebben totaal_eur=0)
            # en de verkoopkant, die bewust ongewijzigd blijft — zie
            # CLAUDE.md, "GAK gebruikt verkeerde kolom".
            delta_cash = -float(row["totaal_eur"])  # positief = geld uitgegeven (aankoop)
            if delta_aantal > 0:
                # Kostenbasis o.b.v. de kale Waarde EUR, niet totaal_eur —
                # zie compute_per_ticker() hierboven voor dezelfde fix/reden.
                waarde_bron = row["waarde_eur"] if pd.notna(row.get("waarde_eur")) else row["totaal_eur"]
                delta_cash_aankoop = -float(waarde_bron)
                aantal_lopend += delta_aantal
                kostprijs_lopend += delta_cash_aankoop
                if delta_cash != 0:
                    totaal_gekocht_aantal += delta_aantal
                    totaal_gekocht_bedrag += delta_cash_aankoop
            elif delta_aantal < 0:
                if delta_cash != 0 and aantal_lopend > 0:
                    gak_op_dat_moment = kostprijs_lopend / aantal_lopend
                    verkocht_nu = min(-delta_aantal, aantal_lopend)
                    kostenbasis_verkocht_nu = gak_op_dat_moment * verkocht_nu
                    kostprijs_lopend -= kostenbasis_verkocht_nu
                    totaal_verkocht_aantal += verkocht_nu
                    totaal_verkocht_bedrag += -delta_cash  # delta_cash negatief bij verkoop
                    totaal_verkochte_kostenbasis += kostenbasis_verkocht_nu
                aantal_lopend += delta_aantal

        if aantal_lopend > 1e-9:
            positie = {"aantal": aantal_lopend, "gak": kostprijs_lopend / aantal_lopend}
            if totaal_verkocht_aantal > 1e-9:
                # Positie staat nog (deels) open, maar er is onderweg wél
                # verkocht — die gerealiseerde winst/verlies mag niet
                # verloren gaan (zie instructiedocument "gedeeltelijke
                # verkopen"). Zelfde velden als een volledig gesloten
                # positie hieronder, zodat bereken_statistieken() ze
                # uniform kan verwerken.
                positie["deels_verkocht"] = {
                    "aantal": totaal_verkocht_aantal,
                    "gemiddelde_aankoopkoers": (
                        totaal_verkochte_kostenbasis / totaal_verkocht_aantal
                        if totaal_verkocht_aantal > 1e-9 else None
                    ),
                    "gemiddelde_verkoopkoers": totaal_verkocht_bedrag / totaal_verkocht_aantal,
                    "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_verkochte_kostenbasis,
                }
            open_posities[ticker] = positie
        elif totaal_gekocht_aantal > 1e-9:
            gesloten_posities[ticker] = {
                "aantal": totaal_gekocht_aantal,
                "gemiddelde_aankoopkoers": totaal_gekocht_bedrag / totaal_gekocht_aantal,
                "gemiddelde_verkoopkoers": (
                    totaal_verkocht_bedrag / totaal_verkocht_aantal
                    if totaal_verkocht_aantal > 1e-9 else None
                ),
                "gerealiseerd_eur": totaal_verkocht_bedrag - totaal_gekocht_bedrag,
            }

    return open_posities, gesloten_posities


def bereken_jaren_overzicht(resultaat, eerste_datum=None):
    """resultaat: DataFrame zoals compute_value_over_time() teruggeeft
    (index=datum, kolommen 'waarde'/'geinvesteerd'). Geeft per kalenderjaar
    waarin belegd is een overzicht terug (zie bereken_jaar_rendement).

    eerste_datum: de echte eerste transactiedatum (kan iets vóór
    resultaat.index.min() liggen door weekend/feestdag-afronding in
    price_data) — begrenst het eerste jaar zodat dagen_verstreken/
    pct_van_jaar niet ten onrechte een vol jaar tonen. Valt terug op
    resultaat.index.min() als niet meegegeven."""
    if resultaat.empty:
        return []

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    laatste_datum = resultaat.index.max()
    eerste_datum = pd.Timestamp(eerste_datum) if eerste_datum is not None else resultaat.index.min()
    eerste_jaar = eerste_datum.year
    laatste_jaar = laatste_datum.year

    jaren = []
    for jaar in range(eerste_jaar, laatste_jaar + 1):
        jaar_start = pd.Timestamp(year=jaar, month=1, day=1)
        jaar_eind = pd.Timestamp(year=jaar, month=12, day=31)
        dagen_in_jaar = 366 if pd.Timestamp(year=jaar, month=12, day=31).is_leap_year else 365

        periode_start = max(jaar_start, eerste_datum)
        periode_eind = min(jaar_eind, laatste_datum)
        dagen_verstreken = (periode_eind - periode_start).days + 1
        pct_van_jaar = dagen_verstreken / dagen_in_jaar * 100

        startwaarde = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "waarde")
        geinvesteerd_voor = waarde_op_of_voor(jaar_start - pd.Timedelta(days=1), "geinvesteerd")
        geinvesteerd_na = waarde_op_of_voor(periode_eind, "geinvesteerd")
        ingelegd = geinvesteerd_na - geinvesteerd_voor
        eindwaarde = waarde_op_of_voor(periode_eind, "waarde")

        rendement = bereken_jaar_rendement(startwaarde, ingelegd, eindwaarde)
        jaren.append({
            "jaar": jaar,
            "dagen_verstreken": dagen_verstreken,
            "pct_van_jaar": round(pct_van_jaar, 1),
            "startwaarde": round(startwaarde, 2),
            "ingelegd": round(ingelegd, 2),
            "eindwaarde": round(eindwaarde, 2),
            "winst_eur": round(rendement["winst_eur"], 2),
            "winst_pct": round(rendement["winst_pct"], 2) if rendement["winst_pct"] is not None else None,
        })
    return jaren


def _bouw_xirr_cashflows(transacties_df, resultaat):
    """Bouwt de cashflow-lijst voor bereken_xirr(): elke echte transactie
    (geen corporate-action-boekingsrij, geen €0-splitconversie) plus een
    laatste fictieve cashflow op de laatste bekende datum ter grootte van de
    huidige portfoliowaarde (alsof alles vandaag verkocht wordt — nodig om
    XIRR een eindpunt te geven).

    Nagelopen tegen dezelfde same-day-sorteerbug als
    bereken_holdings_en_gesloten() (zie _sorteer_chronologisch): hier is
    geen fix nodig. XIRR is een NPV-berekening puur op basis van (datum,
    bedrag)-paren — de volgorde van de cashflows-lijst zelf beïnvloedt de
    uitkomst niet (in tegenstelling tot de GAK-boekhouding hierboven, die
    per rij een lopend saldo bijhoudt en dus wél afhankelijk is van de
    verwerkingsvolgorde). Alleen de einddatum sortering (hieronder) is voor
    de leesbaarheid, niet voor de correctheid."""
    if resultaat.empty:
        return []
    df = transacties_df.dropna(subset=["ticker"])
    df = df[~df.apply(_is_corporate_action_row, axis=1)]
    cashflows = [
        (pd.Timestamp(row["datum"]).date(), float(row["totaal_eur"]))
        for _, row in df.iterrows() if float(row["totaal_eur"]) != 0
    ]
    if not cashflows:
        return []
    laatste_datum = resultaat.index.max()
    laatste_waarde = float(resultaat["waarde"].iloc[-1])
    cashflows.append((pd.Timestamp(laatste_datum).date(), laatste_waarde))
    cashflows.sort(key=lambda c: c[0])
    return cashflows


def bereken_benchmark_vergelijking(transacties_df, resultaat, benchmark_koersen):
    """Simuleert wat de portfolio waard zou zijn geweest als exact dezelfde
    cashflows (zelfde bedrag, zelfde datum) in een benchmark waren gestoken
    i.p.v. in de echte posities — voor de "Vergelijk met..."-optie op het
    Rendement-tabblad (zie BENCHMARK_TICKERS hierboven).

    `benchmark_koersen`: pd.Series, datum-index -> koers in EUR (al
    opgehaald door de aanroeper via de bestaande get_prices()-cache, zie
    app.py) — deze functie blijft bewust DB/netwerk-vrij, zelfde patroon als
    compute_value_over_time()/compute_per_ticker() die ook al opgehaalde
    price_data als parameter krijgen i.p.v. zelf te fetchen.

    Hergebruikt _bouw_xirr_cashflows() (zelfde cashflows als XIRR): bedrag is
    daar negatief bij een investering (geld uit) en positief bij een
    onttrekking (geld terug) — dus een benchmark-"aankoop" ter grootte van
    het bedrag is `aantal += -bedrag / koers` (bij een onttrekking is bedrag
    positief en -bedrag/koers dus negatief, wat de hypothetische positie
    evenredig verkleint). De laatste entry in die lijst is een FICTIEVE
    'verkoop vandaag'-cashflow (nodig voor XIRR, geen echte transactie) en
    wordt hier expliciet weggelaten.

    Als de benchmark pas later koersdata heeft dan de eerste cashflow (bv.
    AEX/IAEA.AS, pas vanaf 2020-07-29), begint de teruggegeven reeks pas
    vanaf de eerst beschikbare koersdatum -- "onvolledige_dekking"
    signaleert dat aan de aanroeper i.p.v. stilzwijgend een te lage
    hypothetische waarde te tonen (cashflows van vóór die datum tellen dan
    simpelweg niet mee in de simulatie).

    Geeft {"labels", "waarde", "rendement", "vanaf_datum",
    "onvolledige_dekking"} terug ("rendement" = "waarde" - het bijbehorende
    "geinvesteerd" uit `resultaat`, zelfde definitie als de bestaande
    Rendement-lijn), of None als er geen bruikbare cashflows of koersdata
    zijn."""
    if resultaat.empty:
        return None
    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    if len(cashflows) < 2:
        return None
    cashflows = cashflows[:-1]  # laatste = fictieve 'verkoop vandaag', geen echte transactie
    if not cashflows:
        return None

    koersen = benchmark_koersen.dropna()
    if koersen.empty:
        return None

    eerst_beschikbaar = koersen.index.min()
    eerste_cashflow_datum = pd.Timestamp(min(d for d, _ in cashflows))
    labels = [d for d in resultaat.index if d >= eerst_beschikbaar]
    if not labels:
        return None

    cashflows_ts = sorted((pd.Timestamp(d), bedrag) for d, bedrag in cashflows)

    aantal = 0.0
    waarde_per_dag = []
    cf_i = 0
    for datum in labels:
        while cf_i < len(cashflows_ts) and cashflows_ts[cf_i][0] <= datum:
            cf_datum, bedrag = cashflows_ts[cf_i]
            koers_op_cf_datum = koersen.reindex([cf_datum], method="ffill").iloc[0]
            if pd.notna(koers_op_cf_datum) and koers_op_cf_datum > 0:
                aantal += -bedrag / koers_op_cf_datum
            cf_i += 1
        koers_vandaag = koersen.reindex([datum], method="ffill").iloc[0]
        waarde_per_dag.append(aantal * koers_vandaag if pd.notna(koers_vandaag) else None)

    geinvesteerd_per_label = resultaat.loc[labels, "geinvesteerd"]
    rendement_per_dag = [
        (round(w - g, 2) if w is not None else None)
        for w, g in zip(waarde_per_dag, geinvesteerd_per_label)
    ]

    return {
        "labels": [d.strftime("%Y-%m-%d") for d in labels],
        "waarde": [round(w, 2) if w is not None else None for w in waarde_per_dag],
        "rendement": rendement_per_dag,
        "vanaf_datum": labels[0].strftime("%Y-%m-%d"),
        "onvolledige_dekking": eerste_cashflow_datum < eerst_beschikbaar,
    }


def bereken_rendement_over_tijd(transacties_df, resultaat, stap="maand"):
    """Bouwt de drie lijnen voor het "XIRR & rendement"-tabblad: gewoon
    rendement% (bereken_totaal_rendement), XIRR% (bereken_xirr) en TWR%
    (bereken_twr), allemaal op meerdere momenten in de tijd i.p.v. alleen
    het eindcijfer zoals op Statistieken.

    Stapgrootte via `stap`:
      - "maand" (standaard): laatste dag van elke kalendermaand, van de
        eerste tot de laatste datum in `resultaat` — resultaat.index.max()
        is in de praktijk "vandaag" (de laatste beschikbare koersdatum),
        gebruikt i.p.v. een aparte pd.Timestamp.now()-aanroep zodat deze
        functie puur/deterministisch blijft. De laatste (huidige) datum
        wordt altijd als extra stap toegevoegd, ook als die zelf geen
        maand-einde is, zodat de lijn nooit een stuk van de recentste
        periode mist. Licht genoeg om steeds automatisch te herberekenen.
      - "dag": elke datum in `resultaat.index` — preciezer maar
        herberekent bereken_xirr() per dag i.p.v. per maand, wat bij een
        lange historie merkbaar traag kan zijn. Daarom alleen op expliciet
        verzoek van de gebruiker (knop in de UI), niet als standaard.

    Per stapdatum d:
      - waarde/geinvesteerd = de bekende stand op of vóór d (zelfde
        waarde_op_of_voor-patroon als bereken_jaren_overzicht hierboven).
      - rendement_pct via bereken_totaal_rendement — None bij geinvesteerd=0
        (bv. vóór de eerste aankoop), geen verzonnen 0%.
      - xirr_pct: cashflows uit _bouw_xirr_cashflows, MINUS de fictieve
        eind-cashflow van díe functie (die hoort bij de laatste datum in
        resultaat, niet bij d), afgekapt tot en met d, plus een eigen
        fictieve eind-cashflow (waarde op d). bereken_xirr geeft zelf al
        None terug bij te weinig/tegenstrijdige cashflows (bv. de eerste
        maand) — wordt hier gewoon doorgegeven, geen aparte afhandeling
        nodig.
      - twr_pct: bereken_twr() op `resultaat` afgekapt t/m d (resultaat.loc[
        :d]) — TWR is per definitie een gelinkte reeks van sub-periodes
        vanaf het begin, dus herberekent bij elke stap opnieuw vanaf de
        eerste datum (zelfde performance-kanttekening als xirr_pct
        hierboven: prima bij stap="maand", kan bij stap="dag" over een
        lange historie merkbaar trager worden, nog niet geoptimaliseerd).
        Reageert NIET extreem vlak na een storting zoals xirr_pct dat wel
        doet — dat is de reden om 'm ernaast te tonen, geen bug als de
        lijnen dus duidelijk verschillend lopen vlak na een storting.

    Geeft {"labels": [...als YYYY-MM-DD...], "rendement_pct": [...],
    "xirr_pct": [...], "twr_pct": [...]} terug (xirr_pct/twr_pct als
    percentage, dus 10.0 = 10%, niet de fractie 0.10 die bereken_xirr/
    bereken_twr zelf teruggeven). Lege lijsten bij een leeg resultaat."""
    if resultaat.empty:
        return {"labels": [], "rendement_pct": [], "xirr_pct": [], "twr_pct": []}

    def waarde_op_of_voor(datum, kolom):
        subset = resultaat.loc[:datum, kolom]
        return float(subset.iloc[-1]) if len(subset) else 0.0

    if stap == "dag":
        stap_datums = list(resultaat.index)
    else:
        eerste_datum = resultaat.index.min()
        laatste_datum = resultaat.index.max()
        stap_datums = list(pd.date_range(eerste_datum, laatste_datum, freq="ME"))
        if not stap_datums or stap_datums[-1] < laatste_datum:
            stap_datums.append(laatste_datum)

    alle_cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    # laatste entry is de fictieve 'verkoop op laatste_datum' uit
    # _bouw_xirr_cashflows -- die hoort niet bij tussentijdse stappen, elke
    # stap krijgt hieronder zijn EIGEN fictieve eind-cashflow (op d, niet op
    # laatste_datum).
    echte_cashflows = alle_cashflows[:-1] if alle_cashflows else []

    labels, rendement_pct_lijst, xirr_pct_lijst, twr_pct_lijst = [], [], [], []
    for d in stap_datums:
        waarde = waarde_op_of_voor(d, "waarde")
        geinvesteerd = waarde_op_of_voor(d, "geinvesteerd")

        rendement = bereken_totaal_rendement(geinvesteerd, waarde)

        cashflows_tot_d = [(dat, bedrag) for dat, bedrag in echte_cashflows if pd.Timestamp(dat) <= d]
        xirr = None
        if cashflows_tot_d:
            xirr = bereken_xirr(cashflows_tot_d + [(d.date(), waarde)])

        twr = bereken_twr(transacties_df, resultaat.loc[:d])

        labels.append(d.strftime("%Y-%m-%d"))
        rendement_pct_lijst.append(
            round(rendement["rendement_pct"], 2) if rendement["rendement_pct"] is not None else None
        )
        xirr_pct_lijst.append(round(xirr * 100, 2) if xirr is not None else None)
        twr_pct_lijst.append(round(twr * 100, 2) if twr is not None else None)

    return {
        "labels": labels,
        "rendement_pct": rendement_pct_lijst,
        "xirr_pct": xirr_pct_lijst,
        "twr_pct": twr_pct_lijst,
    }


def bereken_totale_transactiekosten(transacties_df):
    """
    Somt de 'transactiekosten'-kolom op (negatieve waarden in de brondata,
    zie KOSTEN_KOLOM in app.py) tot een positief totaalbedrag. Geeft
    beschikbaar=False terug als de kolom ontbreekt of enkel NaN bevat — bv.
    een ouder DeGiro-exportformaat zonder aparte kostenkolom — zodat de UI
    dan een eerlijke 'data ontbreekt'-melding kan tonen i.p.v. een verzonnen
    €0,00."""
    if "transactiekosten" not in transacties_df.columns:
        return {"totaal": None, "beschikbaar": False}
    kosten = pd.to_numeric(transacties_df["transactiekosten"], errors="coerce").dropna()
    if kosten.empty:
        return {"totaal": None, "beschikbaar": False}
    return {"totaal": round(abs(float(kosten.sum())), 2), "beschikbaar": True}


def bereken_statistieken(transacties_df, price_data, resultaat, dividend_per_ticker=None, ticker_namen=None):
    """
    Bouwt alle data voor het Statistieken-tabblad. Gebruikt uitsluitend data
    die analyze_transacties() (app.py) al berekend heeft (transacties_df ná
    compute_split_adjusted_shares, price_data van get_prices(), resultaat van
    compute_value_over_time()) — geen extra yfinance-calls, dus dit hoeft
    (anders dan Ticker-zekerheid) niet lui/lazy geladen te worden.

    dividend_per_ticker (optioneel): {ticker: totaal_netto} uit
    bereken_dividend_samenvatting() (app.py haalt dit apart op, want dat
    raakt de database aan — deze functie blijft bewust DB-vrij). Leeg/None
    bij een 'niet opslaan'-analyse (geen dividendhistorie mogelijk zonder
    opgeslagen code) — dan krijgt elke positie gewoon 0.0.

    ticker_namen (optioneel): {ticker: naam} voor de "naam"-kolom bij
    gesloten posities (zelfde bron als de rest van analyze_transacties).

    Let op: voor 'huidig aantal per positie' wordt (net als bij de
    Verdeling-taart, zie compute_land_sector_verdeling) de ruwe 'aantal'-
    kolom gebruikt, niet 'adj_aantal' — DEGIRO's splitconversierijen zijn
    al ECHTE transactierijen die het aandelenaantal optellen, adj_aantal is
    alleen nodig om HISTORISCHE (vóór-split) waardepunten te corrigeren.
    """
    dividend_per_ticker = dividend_per_ticker or {}
    ticker_namen = ticker_namen or {}
    laatste_prijzen = price_data.iloc[-1] if not price_data.empty else pd.Series(dtype=float)
    holdings, gesloten_posities = bereken_holdings_en_gesloten(transacties_df)

    posities = []
    for ticker, info in holdings.items():
        if ticker not in price_data.columns or pd.isna(laatste_prijzen.get(ticker)):
            continue
        huidige_koers = float(laatste_prijzen[ticker])
        r = bereken_positie_rendement(info["gak"], info["aantal"], huidige_koers)
        posities.append({
            "ticker": ticker,
            "aantal": round(info["aantal"], 4),
            "gak": round(info["gak"], 4),
            "huidige_koers": round(huidige_koers, 4),
            "huidige_waarde": round(r["waarde"], 2),
            "geinvesteerd": round(r["geinvesteerd"], 2),
            "rendement_eur": round(r["waarde"] - r["geinvesteerd"], 2),
            "rendement_pct": round(r["rendement_pct"], 2) if r["rendement_pct"] is not None else None,
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    posities.sort(key=lambda p: p["huidige_waarde"], reverse=True)

    gesloten_posities_output = []
    for ticker, info in gesloten_posities.items():
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(info["aantal"], 4),
            "resterend_aantal": 0.0,
            "nog_in_bezit": False,
            "gemiddelde_aankoopkoers": round(info["gemiddelde_aankoopkoers"], 4),
            "gemiddelde_verkoopkoers": (
                round(info["gemiddelde_verkoopkoers"], 4)
                if info["gemiddelde_verkoopkoers"] is not None else None
            ),
            # Puur koersrendement, exclusief dividend — dividend staat als
            # apart veld ernaast, bewust niet samengevoegd tot één percentage.
            "rendement_eur": round(info["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(info["gerealiseerd_eur"] / (info["gemiddelde_aankoopkoers"] * info["aantal"]) * 100, 2)
                if info["gemiddelde_aankoopkoers"] else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })

    for ticker, info in holdings.items():
        deels = info.get("deels_verkocht")
        if not deels:
            continue
        kostenbasis_verkocht = (deels["gemiddelde_aankoopkoers"] or 0) * deels["aantal"]
        gesloten_posities_output.append({
            "ticker": ticker,
            "naam": ticker_namen.get(ticker, ticker),
            "aantal": round(deels["aantal"], 4),
            "resterend_aantal": round(info["aantal"], 4),
            "nog_in_bezit": True,
            "gemiddelde_aankoopkoers": (
                round(deels["gemiddelde_aankoopkoers"], 4)
                if deels["gemiddelde_aankoopkoers"] is not None else None
            ),
            "gemiddelde_verkoopkoers": round(deels["gemiddelde_verkoopkoers"], 4),
            "rendement_eur": round(deels["gerealiseerd_eur"], 2),
            "rendement_pct": (
                round(deels["gerealiseerd_eur"] / kostenbasis_verkocht * 100, 2)
                if kostenbasis_verkocht else None
            ),
            "dividend_ontvangen": round(dividend_per_ticker.get(ticker, 0.0), 2),
        })
    gesloten_posities_output.sort(key=lambda p: p["rendement_pct"] or 0, reverse=True)

    totaal_geinvesteerd = float(resultaat["geinvesteerd"].iloc[-1]) if not resultaat.empty else 0.0
    totaal_waarde = float(resultaat["waarde"].iloc[-1]) if not resultaat.empty else 0.0
    totaal = bereken_totaal_rendement(totaal_geinvesteerd, totaal_waarde)

    # All-time high is de hoogste behaalde rendement (waarde - geinvesteerd),
    # niet de hoogste portefeuillewaarde — een hoge waarde vlak na een grote
    # storting hoeft geen hoog rendement te zijn.
    all_time_high = {"waarde": None, "datum": None}
    if not resultaat.empty:
        ath_idx = resultaat["rendement"].idxmax()
        all_time_high = {
            "waarde": round(float(resultaat["rendement"].max()), 2),
            "datum": ath_idx.strftime("%Y-%m-%d"),
        }

    eerste_datum = None
    if not resultaat.empty:
        eerste_datum = transacties_df.dropna(subset=["ticker"])["datum"].min()

    jaren = bereken_jaren_overzicht(resultaat, eerste_datum=eerste_datum)
    geldige_pcts = [j["winst_pct"] for j in jaren if j["winst_pct"] is not None]
    gemiddeld_jaarrendement = round(sum(geldige_pcts) / len(geldige_pcts), 2) if geldige_pcts else None

    cashflows = _bouw_xirr_cashflows(transacties_df, resultaat)
    xirr_fractie = bereken_xirr(cashflows) if cashflows else None
    twr_fractie = bereken_twr(transacties_df, resultaat)

    aantal_jaren = None
    if not resultaat.empty:
        aantal_jaren = round((resultaat.index.max() - pd.Timestamp(eerste_datum)).days / 365.25, 2)

    kosten_info = bereken_totale_transactiekosten(transacties_df)

    return {
        "posities": posities,
        "gesloten_posities": gesloten_posities_output,
        "totalen": {
            "geinvesteerd": round(totaal_geinvesteerd, 2),
            "waarde": round(totaal_waarde, 2),
            "rendement_eur": round(totaal["rendement_eur"], 2),
            "rendement_pct": round(totaal["rendement_pct"], 2) if totaal["rendement_pct"] is not None else None,
            "all_time_high": all_time_high,
            "totale_transactiekosten": kosten_info["totaal"],
            "transactiekosten_beschikbaar": kosten_info["beschikbaar"],
        },
        "jaren": jaren,
        "geavanceerd": {
            "gemiddeld_jaarrendement_pct": gemiddeld_jaarrendement,
            "xirr_pct": round(xirr_fractie * 100, 2) if xirr_fractie is not None else None,
            "twr_pct": round(twr_fractie * 100, 2) if twr_fractie is not None else None,
            "aantal_jaren": aantal_jaren,
        },
    }