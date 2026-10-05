"""Pure diagnostiek-checks: DataFrames/lijsten in, bevindingen uit. Melden doet de aanroeper (meld() is een no-op in threads).

Een bevinding is {"niveau", "tekst", "sleutel"}.
"""
import bisect
import re

import pandas as pd

from diagnostiek import GOED, INFO, LET_OP
from portfolio_calc import holdings_op_datums
from split_correctie import vind_wisselparen
from ticker_matching import _openfigi_root_matches
from ticker_zekerheid import BEURS_OTC_NA_DELISTING, beurs_status
from transactie_utils import _is_corporate_action_row, formatteer_datum_nl

MAX_BEVINDINGEN_PER_CHECK = 5
SYNTHETISCH_ORDER_ID_PREFIX = "SYN-"
# Alleen deze kolommen kunnen in oude rijen NULL zijn doordat er geen backfill is.
KOLOMMEN_ZONDER_BACKFILL = {
    "tijd": "de volgorde van transacties binnen een dag",
    "transactiekosten": "de getoonde kosten",
    "waarde_eur": "de GAK (die valt terug op Totaal EUR, incl. kosten, en wordt iets te hoog)",
}


# Intraday-koers en Yahoo-slot (plus DeGiro- vs. Yahoo-FX) verschillen normaal enkele procenten; een gemiste
# split geeft minstens 50%, een voor latere splits gecorrigeerde koers vaak een veelvoud.
MAX_TRANSACTIE_AFWIJKING_FRACTIE = 0.25
# Een echte 20x binnen één positie is zeldzaam; een verkeerde splitbasis geeft meestal een veel groter veelvoud.
MAX_WAARDE_INLEG_FACTOR = 20
# Een gemiste 2:1-split is -50% of +100% op één dag; zo'n dagbeweging is bij gewone aandelen zeldzaam.
MAX_DAGSPRONG_FRACTIE = 0.4
# Twee beursweken: langer dan een handelsstop of feestdagen (Chinees Nieuwjaar ~1 week); daarna is de ticker
# waarschijnlijk gedelist of levert Yahoo niets meer.
MAX_FORWARD_FILL_DAGEN = 10
# Kleinere bedragen zijn afrondingsresten (bv. na een bijna volledige verkoop): een verhouding zegt dan niets.
MIN_BEDRAG_EUR = 1.0


def _bevinding(niveau, tekst, sleutel):
    return {"niveau": niveau, "tekst": tekst, "sleutel": sleutel}


def _beperk(bevindingen, niveau, sleutel_meer):
    """Bevindingen moeten al van ernstig naar licht gesorteerd zijn."""
    if len(bevindingen) <= MAX_BEVINDINGEN_PER_CHECK:
        return bevindingen
    rest = len(bevindingen) - MAX_BEVINDINGEN_PER_CHECK
    return bevindingen[:MAX_BEVINDINGEN_PER_CHECK] + [_bevinding(niveau, f"... en {rest} meer.", sleutel_meer)]


def _corporate_action_masker(df):
    if df.empty:
        return pd.Series(False, index=df.index)
    return df.apply(_is_corporate_action_row, axis=1).astype(bool)


def _wisselrij_masker(df):
    """Omboekingen bij een ISIN-wissel: geen Order ID en geen kosten, en dat hoort zo."""
    masker = pd.Series(False, index=df.index)
    if "is_wisselrij" in df.columns:
        masker |= df["is_wisselrij"].fillna(False).astype(bool)
    paren, _onduidelijk = vind_wisselparen(df)
    labels = [label for paar in paren for label in paar.oud_rijen + paar.nieuw_rijen]
    masker.loc[labels] = True
    return masker


def _gewone_rijen(df):
    return df[~_corporate_action_masker(df) & ~_wisselrij_masker(df)]


def _weergavenaam(rij):
    naam = rij.get("echte_naam")
    return naam if pd.notna(naam) else rij.get("product")


def check_ontbrekende_kolommen(df):
    """Corporate-action- en wisselrijen tellen niet mee: die hebben van nature geen kosten of waarde."""
    if df.empty:
        return []
    echte = _gewone_rijen(df)
    bevindingen = []
    for kolom, geraakt in KOLOMMEN_ZONDER_BACKFILL.items():
        rijen = echte[echte[kolom].isna()]
        if kolom == "waarde_eur":
            rijen = rijen[rijen["aantal"].astype(float) > 0]
        if rijen.empty:
            continue
        bevindingen.append(_bevinding(
            LET_OP,
            f"{len(rijen)} transacties hebben geen '{kolom}' (oudste: {formatteer_datum_nl(rijen['datum'].min())}). "
            f"Dit raakt {geraakt}. Er is geen backfill voor bestaande rijen; de oplossing is het portfolio "
            f"te verwijderen en opnieuw te uploaden.",
            f"data:ontbrekend:{kolom}",
        ))
    return bevindingen


def check_posities_zonder_ticker(df):
    if df.empty:
        return []
    kandidaten = df[~_corporate_action_masker(df) & df["ticker"].isna()]
    per_isin = []
    for isin, groep in kandidaten.groupby("isin", dropna=False):
        per_isin.append((len(groep), isin, groep))
    per_isin.sort(key=lambda x: -x[0])
    bevindingen = [
        _bevinding(
            LET_OP,
            f"{_weergavenaam(groep.iloc[0])} ({isin}) heeft {aantal} transacties zonder ticker "
            f"(eerste op {formatteer_datum_nl(groep['datum'].min())}): geen koersen, de positie telt niet mee.",
            f"data:geen_ticker:{isin}",
        )
        for aantal, isin, groep in per_isin
    ]
    return _beperk(bevindingen, LET_OP, "data:geen_ticker:meer")


def check_synthetische_order_ids(df):
    """df met 'order_id' plus de kolommen om wissel- en corporate-action-rijen te herkennen (ORDER_ID_KOLOMMEN):
    die krijgen van DeGiro nooit een Order ID, hun SYN-ID is deterministisch."""
    if df.empty:
        return []
    gewone = _gewone_rijen(df)
    aantal = int(gewone["order_id"].astype(str).str.startswith(SYNTHETISCH_ORDER_ID_PREFIX).sum())
    if not aantal:
        return []
    return [_bevinding(
        INFO,
        f"{aantal} van {len(gewone)} gewone transacties hebben een synthetische Order ID ({SYNTHETISCH_ORDER_ID_PREFIX}...): "
        f"bij een volgende upload kan het portfolio daardoor minder betrouwbaar herkend worden.",
        "data:synthetisch",
    )]


def check_corporate_action_rijen(df):
    if df.empty:
        return []
    ca = df[_corporate_action_masker(df)]
    per_isin = sorted(ca.groupby("isin", dropna=False), key=lambda x: -len(x[1]))
    bevindingen = [
        _bevinding(
            INFO,
            f"{_weergavenaam(groep.iloc[0])} ({isin}): {len(groep)} corporate-action-rijen "
            f"({formatteer_datum_nl(groep['datum'].min())} t/m {formatteer_datum_nl(groep['datum'].max())}); "
            f"hier is een split of wissel geboekt.",
            f"data:corporate_action:{isin}",
        )
        for isin, groep in per_isin
    ]
    return _beperk(bevindingen, INFO, "data:corporate_action:meer")


def check_isin_wissels(df):
    """Alleen meerdere ISIN's zónder wisselpatroon: een herkende wissel meldt meld_split_koppeling() met Yahoo's ratio."""
    if df.empty:
        return []
    paren, _onduidelijk = vind_wisselparen(df)
    gewisseld = {isin for paar in paren for isin in (paar.oud_isin, paar.nieuw_isin)}
    bevindingen = []
    for ticker, groep in df.dropna(subset=["ticker"]).groupby("ticker"):
        isins = sorted(groep["isin"].dropna().unique())
        if len(isins) < 2 or set(isins) <= gewisseld:
            continue
        namen = ", ".join(f"{_weergavenaam(groep[groep['isin'] == i].iloc[0])} ({i})" for i in isins)
        bevindingen.append(_bevinding(
            INFO, f"Ticker '{ticker}' heeft meerdere ISIN's: {namen}. Geen wisselpatroon herkend.",
            f"isin_wissel:{ticker}"))
    return _beperk(bevindingen, INFO, "isin_wissel:meer")


def _pct(fractie):
    return f"{fractie * 100:.1f}%".replace(".", ",")


def _eur(bedrag):
    return "EUR " + f"{bedrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _naam_per_ticker(df):
    if df.empty:
        return {}
    met_ticker = df.dropna(subset=["ticker"])
    return {t: _weergavenaam(groep.iloc[0]) for t, groep in met_ticker.groupby("ticker")}


def _marktransacties(df):
    """Zonder corporate-action-, wissel- en conversierijen (koers 0): die hebben geen echte marktkoers."""
    if df.empty:
        return df
    df = df.dropna(subset=["ticker"])
    masker = ~_corporate_action_masker(df) & (df["koers"].astype(float) > 0)
    if "is_wisselrij" in df.columns:
        masker &= ~df["is_wisselrij"].astype(bool)
    return df[masker]


def _mediaan(afwijkingen):
    return float(pd.Series([a[0] for a in afwijkingen]).median())


def _koersafwijkingen(transacties_df, price_data):
    """[(ticker, [(fractie, datum, koers, rekenkoers)])] voor posities met minstens één vergelijkbare transactie."""
    if transacties_df.empty or price_data is None or price_data.empty:
        return []
    per_positie = []
    for ticker, groep in _marktransacties(transacties_df).groupby("ticker"):
        if ticker not in price_data.columns:
            continue
        reeks = price_data[ticker]
        afwijkingen = []
        for _, rij in groep.iterrows():
            rekenkoers = reeks.asof(pd.Timestamp(rij["datum"]))
            if pd.isna(rekenkoers) or rekenkoers <= 0:
                continue
            koers = float(rij["koers"])
            afwijkingen.append((abs(koers / float(rekenkoers) - 1), rij["datum"], koers, float(rekenkoers)))
        if afwijkingen:
            per_positie.append((ticker, afwijkingen))
    return per_positie


def tickers_met_koersafwijking(transacties_df, price_data):
    return {ticker for ticker, afwijkingen in _koersafwijkingen(transacties_df, price_data)
            if max(a[0] for a in afwijkingen) > MAX_TRANSACTIE_AFWIJKING_FRACTIE}


def check_transactiekoers_vs_rekenkoers(transacties_df, price_data):
    """DeGiro-koers (EUR) per transactie tegen de koers waarmee het dashboard op die dag rekent."""
    per_positie = _koersafwijkingen(transacties_df, price_data)
    namen = _naam_per_ticker(transacties_df)
    bevindingen = []
    for ticker, afwijkingen in per_positie:
        grootste, datum, koers, rekenkoers = max(afwijkingen, key=lambda a: a[0])
        if grootste <= MAX_TRANSACTIE_AFWIJKING_FRACTIE:
            continue
        boven = sum(1 for a in afwijkingen if a[0] > MAX_TRANSACTIE_AFWIJKING_FRACTIE)
        bevindingen.append((grootste, _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): {boven} van {len(afwijkingen)} transactiekoersen wijken meer dan "
            f"{_pct(MAX_TRANSACTIE_AFWIJKING_FRACTIE)} af van de koers waarmee het dashboard rekent (mediaan "
            f"{_pct(_mediaan(afwijkingen))}, max. {_pct(grootste)} op {formatteer_datum_nl(datum)}: DeGiro "
            f"{_eur(koers)}, dashboard {_eur(rekenkoers)}). Mogelijk een verkeerde ticker of split.",
            f"plausibel:koers:{ticker}",
        )))
    if bevindingen:
        bevindingen.sort(key=lambda b: -b[0])
        return _beperk([b for _, b in bevindingen], LET_OP, "plausibel:koers:meer")
    if not per_positie:
        return []
    aantal = sum(len(a) for _, a in per_positie)
    return [_bevinding(
        GOED,
        f"Transactiekoersen van {len(per_positie)} posities ({aantal} transacties) liggen binnen "
        f"{_pct(MAX_TRANSACTIE_AFWIJKING_FRACTIE)} van de rekenkoers (hoogste mediane afwijking per positie: "
        f"{_pct(max(_mediaan(a) for _, a in per_positie))}).",
        "plausibel:koers",
    )]


def check_waarde_vs_inleg(per_ticker, namen):
    """per_ticker zoals compute_per_ticker(); 'geinvesteerd' is daar de GAK-kostenbasis."""
    bevindingen = []
    for ticker, reeks in per_ticker.items():
        slechtste = None
        for label, waarde, inleg in zip(reeks["labels"], reeks["waarde"], reeks["geinvesteerd"]):
            if inleg < MIN_BEDRAG_EUR or waarde <= MAX_WAARDE_INLEG_FACTOR * inleg:
                continue
            if slechtste is None or waarde / inleg > slechtste[0]:
                slechtste = (waarde / inleg, label, waarde, inleg)
        if slechtste:
            factor, label, waarde, inleg = slechtste
            bevindingen.append((factor, _bevinding(
                LET_OP,
                f"{namen.get(ticker, ticker)} ({ticker}): de waarde ({_eur(waarde)}) is op {formatteer_datum_nl(label)} "
                f"{factor:.0f}x de inleg ({_eur(inleg)}); de grens is {MAX_WAARDE_INLEG_FACTOR}x. Mogelijk een "
                f"verkeerde ticker of split.",
                f"plausibel:waarde_inleg:{ticker}",
            )))
    bevindingen.sort(key=lambda b: -b[0])
    return _beperk([b for _, b in bevindingen], LET_OP, "plausibel:waarde_inleg:meer")


def _transactiedagen(groep, labels):
    """Eerste koersdag op of na elke boek- en effectieve datum (een weekendboeking telt op maandag)."""
    datums = list(groep["datum"])
    if "effectieve_datum" in groep.columns:
        datums += list(groep["effectieve_datum"])
    dagen = set()
    for datum in datums:
        i = bisect.bisect_left(labels, pd.Timestamp(datum).strftime("%Y-%m-%d"))
        if i < len(labels):
            dagen.add(labels[i])
    return dagen


def check_dagsprong(transacties_df, per_ticker, tickers_met_koersafwijking=()):
    """Meme-aandelen bewegen echt meer dan de drempel op een dag: alleen LET_OP als de transactiekoersen ook afwijken.
    Een gekoppelde split zet de effectieve datum op Yahoo's splitdag: die dag telt dus als transactiedag."""
    if transacties_df.empty:
        return []
    namen = _naam_per_ticker(transacties_df)
    rijen_per_ticker = dict(tuple(transacties_df.dropna(subset=["ticker"]).groupby("ticker")))
    bevindingen = []
    for ticker, reeks in per_ticker.items():
        labels, waarden = reeks["labels"], reeks["waarde"]
        transactiedagen = _transactiedagen(rijen_per_ticker.get(ticker, pd.DataFrame(columns=["datum"])), labels)
        sprongen = []
        for i in range(1, len(labels)):
            vorige, huidige = waarden[i - 1], waarden[i]
            if vorige < MIN_BEDRAG_EUR or labels[i] in transactiedagen:
                continue
            fractie = abs(huidige / vorige - 1)
            if fractie > MAX_DAGSPRONG_FRACTIE:
                sprongen.append((fractie, labels[i], vorige, huidige))
        if not sprongen:
            continue
        fractie, label, vorige, huidige = max(sprongen)
        verdacht = ticker in tickers_met_koersafwijking
        uitleg = ("De transactiekoersen wijken ook af: mogelijk een verkeerde ticker of split." if verdacht
                  else "De transactiekoersen kloppen, dus waarschijnlijk een echte koersbeweging.")
        bevindingen.append((not verdacht, -fractie, _bevinding(
            LET_OP if verdacht else INFO,
            f"{namen.get(ticker, ticker)} ({ticker}): de waarde springt op {formatteer_datum_nl(label)} van "
            f"{_eur(vorige)} naar {_eur(huidige)} ({'+' if huidige > vorige else '-'}{_pct(fractie)}) zonder "
            f"transactie of gekoppelde split ({len(sprongen)} dag(en) boven {_pct(MAX_DAGSPRONG_FRACTIE)}). {uitleg}",
            f"plausibel:dagsprong:{ticker}",
        )))
    bevindingen.sort(key=lambda b: b[:2])
    rest_verdacht = any(not b[0] for b in bevindingen[MAX_BEVINDINGEN_PER_CHECK:])
    return _beperk([b[2] for b in bevindingen], LET_OP if rest_verdacht else INFO, "plausibel:dagsprong:meer")


def _langste_stilstand(koersen, holdings):
    """(dagen, begin, eind) van de langste reeks gelijke koersen op rij terwijl er stukken gehouden worden."""
    langste, begin_huidig = (0, None, None), None
    for i in range(1, len(koersen)):
        gelijk = holdings[i] > 1e-6 and pd.notna(koersen.iloc[i]) and koersen.iloc[i] == koersen.iloc[i - 1]
        if not gelijk:
            begin_huidig = None
            continue
        begin_huidig = begin_huidig if begin_huidig is not None else i - 1
        dagen = i - begin_huidig
        if dagen > langste[0]:
            langste = (dagen, koersen.index[begin_huidig], koersen.index[i])
    return langste


def check_koers_stilstand(transacties_df, price_data):
    """get_prices() forward-fillt: een gelijke koers dag na dag is daar het enige spoor van ontbrekende koersen."""
    if transacties_df.empty or price_data is None or price_data.empty:
        return []
    namen = _naam_per_ticker(transacties_df)
    bevindingen = []
    for ticker, groep in transacties_df.dropna(subset=["ticker"]).groupby("ticker"):
        if ticker not in price_data.columns:
            continue
        koersen = price_data[ticker]
        dagen, begin, eind = _langste_stilstand(koersen, holdings_op_datums(groep, koersen.index))
        if dagen <= MAX_FORWARD_FILL_DAGEN:
            continue
        tot_einde = eind == koersen.index[-1]
        bevindingen.append((dagen, _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): de koers staat {dagen} handelsdagen stil op {_eur(koersen[eind])} "
            f"({formatteer_datum_nl(begin)} t/m {formatteer_datum_nl(eind)}) terwijl je stukken hield. "
            + ("Waarschijnlijk levert Yahoo geen koersen meer (gedelist?); de waarde rekent met de laatste koers."
               if tot_einde else "Waarschijnlijk ontbreken daar koersen; de waarde rekent met de laatst bekende koers."),
            f"koers_stilstand:{ticker}",
        )))
    bevindingen.sort(key=lambda b: -b[0])
    return _beperk([b for _, b in bevindingen], LET_OP, "koers_stilstand:meer")


# Woordgrens: "DIS" mag niet matchen in "DISCOVERY", "ACC" niet in "ACCESS".
DIS_KENMERKEN = ("DIS", "DIST", "DISTRIBUTING", "DISTRIBUTION")
ACC_KENMERKEN = ("ACC", "ACCUMULATING", "ACCUMULATION")


def _heeft_kenmerk(naam, kenmerken):
    return any(re.search(rf"\b{k}\b", str(naam or ""), re.IGNORECASE) for k in kenmerken)


def _uitkeringsvorm(naam):
    """'DIS', 'ACC' of None (geen of beide kenmerken)."""
    dis, acc = _heeft_kenmerk(naam, DIS_KENMERKEN), _heeft_kenmerk(naam, ACC_KENMERKEN)
    return "DIS" if dis and not acc else "ACC" if acc and not dis else None


def dis_acc_strijdigheden(echte_namen, yahoo_namen):
    """{ticker: (echte_naam, yahoo_naam)} waar de DeGiro-naam en Yahoo's longName een andere uitkeringsvorm noemen."""
    strijdig = {}
    for ticker, namen in echte_namen.items():
        yahoo = _uitkeringsvorm(yahoo_namen.get(ticker))
        for naam in namen:
            degiro = _uitkeringsvorm(naam)
            if yahoo and degiro and yahoo != degiro:
                strijdig[ticker] = (naam, yahoo_namen[ticker])
                break
    return strijdig


def _openfigi_roots(resultaten):
    return sorted({r["ticker"].upper() for r in resultaten if r.get("ticker")})


def openfigi_root_mismatches(isins_per_ticker, openfigi_cache):
    """{ticker: (isin, roots)} waar OpenFIGI resultaten heeft maar de ticker-root er niet tussen staat."""
    mismatch = {}
    for ticker, isins in isins_per_ticker.items():
        for isin in isins:
            resultaten = openfigi_cache.get(isin) or []
            if _openfigi_root_matches(ticker, resultaten) == 0:
                mismatch[ticker] = (isin, _openfigi_roots(resultaten))
                break
    return mismatch


def beurs_oordeel(degiro_beurs, yahoo_beurs, prijs_checks=()):
    """'zeker' (Yahoo-beurs past bij de DeGiro-beurs), 'beurs' (past niet), 'onzeker' (niet te beoordelen) of
    BEURS_OTC_NA_DELISTING; via beurs_status(), dus hetzelfde oordeel als de Ticker-zekerheid-kaart."""
    status = beurs_status(degiro_beurs, yahoo_beurs, list(prijs_checks))
    if status == BEURS_OTC_NA_DELISTING:
        return status
    return {True: "zeker", False: "beurs", None: "onzeker"}[status]


def check_otc_na_delisting(beurzen_per_ticker, oordeel_per_ticker, namen):
    """beurzen_per_ticker: {ticker: (DeGiro-beurs, Yahoo-beurs)}."""
    return [
        _bevinding(
            INFO,
            f"{namen.get(ticker, ticker)} ({ticker}): Excel-beurs {beurzen_per_ticker[ticker][0]}, Yahoo "
            f"{beurzen_per_ticker[ticker][1]}: nu OTC, waarschijnlijk na delisting (koers klopt).",
            f"tickers:otc:{ticker}",
        )
        for ticker, oordeel in sorted(oordeel_per_ticker.items()) if oordeel == BEURS_OTC_NA_DELISTING
    ]


def check_dis_acc(strijdig, namen):
    bevindingen = [
        _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): de DeGiro-naam '{echte}' en de Yahoo-naam '{yahoo}' noemen een "
            f"andere uitkeringsvorm (distribuerend/accumulerend). Waarschijnlijk is de verkeerde share class gekoppeld.",
            f"tickers:dis_acc:{ticker}",
        )
        for ticker, (echte, yahoo) in sorted(strijdig.items())
    ]
    return _beperk(bevindingen, LET_OP, "tickers:dis_acc:meer")


def check_openfigi_root(mismatches, namen):
    bevindingen = [
        _bevinding(
            LET_OP,
            f"{namen.get(ticker, ticker)} ({ticker}): de ticker-root '{ticker.split('.')[0].upper()}' staat niet bij "
            f"OpenFIGI voor {isin}; OpenFIGI kent: {', '.join(roots)}.",
            f"tickers:openfigi_root:{ticker}",
        )
        for ticker, (isin, roots) in sorted(mismatches.items())
    ]
    return _beperk(bevindingen, LET_OP, "tickers:openfigi_root:meer")


def check_openfigi_leeg(posities_per_isin, openfigi_cache):
    """posities_per_isin: {isin: (naam, ticker)}. De cache is permanent, dus ook een tijdelijke 'geen match' blijft staan."""
    bevindingen = [
        _bevinding(
            INFO,
            f"{naam} ({ticker}, {isin}): OpenFIGI kent deze ISIN niet; geen extra controle mogelijk.",
            f"tickers:openfigi_leeg:{isin}",
        )
        for isin, (naam, ticker) in sorted(posities_per_isin.items())
        if openfigi_cache.get(isin) == []
    ]
    return _beperk(bevindingen, INFO, "tickers:openfigi_leeg:meer")


def check_ticker_info_onvolledig(details, namen):
    """Alleen tickers met een ticker_info-rij; zonder rij is er nog niets opgehaald."""
    bevindingen = []
    for ticker, info in sorted(details.items()):
        ontbreekt = [veld for veld in ("valuta", "quote_type") if not info.get(veld)]
        if ontbreekt:
            bevindingen.append(_bevinding(
                INFO,
                f"{namen.get(ticker, ticker)} ({ticker}): in de ticker-cache ontbreekt {' en '.join(ontbreekt)}; "
                f"bij de volgende classificatie wordt dit opnieuw opgehaald.",
                f"tickers:ticker_info:{ticker}",
            ))
    return _beperk(bevindingen, INFO, "tickers:ticker_info:meer")


REDEN_LABELS = {"beurs": "beurs", "koers": "prijs", "openfigi": "OpenFIGI", "dis_acc": "DIS/ACC"}


def check_ticker_samenvatting(oordeel_per_ticker, redenen_per_ticker):
    """oordeel_per_ticker: {ticker: beurs_oordeel()}; redenen_per_ticker: {ticker: {"koers", "openfigi", "dis_acc"}}."""
    if not oordeel_per_ticker:
        return []
    redenen = {t: set(redenen_per_ticker.get(t, ())) | ({"beurs"} if o == "beurs" else set())
               for t, o in oordeel_per_ticker.items()}
    met_waarschuwing = {t: r for t, r in redenen.items() if r}
    zeker = sum(1 for t, o in oordeel_per_ticker.items()
                if o in ("zeker", BEURS_OTC_NA_DELISTING) and t not in met_waarschuwing)
    onzeker = len(oordeel_per_ticker) - zeker - len(met_waarschuwing)
    tekst = (f"{len(oordeel_per_ticker)} posities: {zeker} zeker, {onzeker} onzeker (beurs niet te controleren), "
             f"{len(met_waarschuwing)} met waarschuwing")
    if met_waarschuwing:
        tekst += " (" + "; ".join(
            f"{t}: {', '.join(REDEN_LABELS[r] for r in REDEN_LABELS if r in rs)}"
            for t, rs in sorted(met_waarschuwing.items())) + ")"
    return [_bevinding(INFO if met_waarschuwing else GOED, tekst + ".", "tickers:samenvatting")]


def ticker_bevindingen(transacties_df, details, openfigi_cache, waarschuwingen, prijs_checks=None):
    """Alle Tickers-checks. details: db_get_ticker_details(); openfigi_cache: {isin: resultaten};
    waarschuwingen en prijs_checks: ticker_waarschuwingen_voor_transacties() (de lichte check van het laden)."""
    rijen = _gewone_rijen(transacties_df.dropna(subset=["ticker"])) if not transacties_df.empty else transacties_df
    if rijen.empty:
        return []
    namen = _naam_per_ticker(rijen)
    echte_namen = {t: list(dict.fromkeys(g["echte_naam"].dropna())) for t, g in rijen.groupby("ticker")}
    isins_per_ticker = {t: list(dict.fromkeys(g["isin"].dropna())) for t, g in rijen.groupby("ticker")}
    posities_per_isin = {isin: (namen[t], t) for t, isins in isins_per_ticker.items() for isin in isins}
    yahoo_namen = {t: d.get("long_name") for t, d in details.items()}

    strijdig = dis_acc_strijdigheden(echte_namen, yahoo_namen)
    mismatches = openfigi_root_mismatches(isins_per_ticker, openfigi_cache)
    redenen = {t: set() for t in namen}
    for w in waarschuwingen:
        redenen.setdefault(w["ticker"], set()).update(w.get("redenen", ()))
    for t in mismatches:
        redenen[t].add("openfigi")
    for t in strijdig:
        redenen[t].add("dis_acc")
    prijs_checks = prijs_checks or {}
    beurzen = {t: (g["beurs"].iloc[0], (details.get(t) or {}).get("yahoo_beurs")) for t, g in rijen.groupby("ticker")}
    oordeel = {t: beurs_oordeel(degiro, yahoo, prijs_checks.get(t, ())) for t, (degiro, yahoo) in beurzen.items()}

    return (check_dis_acc(strijdig, namen)
            + check_openfigi_root(mismatches, namen)
            + check_openfigi_leeg(posities_per_isin, openfigi_cache)
            + check_ticker_info_onvolledig({t: details[t] for t in namen if t in details}, namen)
            + check_otc_na_delisting(beurzen, oordeel, namen)
            + check_ticker_samenvatting(oordeel, redenen))


def check_valuta_consistentie(excel_df, ticker_per_isin_beurs, valuta_per_ticker):
    """Alleen direct na een upload. excel_df met ISIN, Beurs, Product en Wisselkoers zoals DeGiro hem levert."""
    if excel_df is None or excel_df.empty:
        return []
    excel = excel_df[~excel_df.apply(
        lambda r: _is_corporate_action_row({"beurs": r["Beurs"], "product": r["Product"]}), axis=1)]
    wisselkoers = pd.to_numeric(excel["Wisselkoers"], errors="coerce")
    heeft_wisselkoers = (wisselkoers.notna() & (wisselkoers != 0)).groupby([excel["ISIN"], excel["Beurs"]]).any()
    bevindingen = []
    for (isin, beurs), met_wisselkoers in heeft_wisselkoers.items():
        ticker = ticker_per_isin_beurs.get((isin, beurs))
        valuta = valuta_per_ticker.get(ticker)
        if not ticker or not valuta:
            continue
        product = excel.loc[(excel["ISIN"] == isin) & (excel["Beurs"] == beurs), "Product"].iloc[0]
        if valuta != "EUR" and not met_wisselkoers:
            tekst = (f"Yahoo noteert {ticker} in {valuta}, maar de Excel heeft voor {product} ({isin}, {beurs}) geen "
                     f"Wisselkoers: DeGiro rekende in EUR. Controleer of ticker en beurs bij elkaar passen.")
        elif valuta == "EUR" and met_wisselkoers:
            tekst = (f"Yahoo noteert {ticker} in EUR, maar de Excel heeft voor {product} ({isin}, {beurs}) een "
                     f"Wisselkoers: DeGiro rekende in een andere valuta. Controleer of ticker en beurs bij elkaar passen.")
        else:
            continue
        bevindingen.append(_bevinding(LET_OP, tekst, f"tickers:valuta:{ticker}"))
    return _beperk(bevindingen, LET_OP, "tickers:valuta:meer")
