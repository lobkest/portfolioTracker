import os
import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            code TEXT PRIMARY KEY,
            naam TEXT,
            aangemaakt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transacties (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL REFERENCES portfolios(code),
            datum DATE NOT NULL,
            product TEXT NOT NULL,
            isin TEXT NOT NULL,
            beurs TEXT,
            ticker TEXT,
            aantal NUMERIC NOT NULL,
            koers NUMERIC,
            totaal_eur NUMERIC NOT NULL,
            order_id TEXT,
            echte_naam TEXT,
            transactiekosten NUMERIC,
            UNIQUE (code, order_id)
        );
    """)
    # transacties bestond al vóór transactiekosten erbij kwam — bestaande
    # (Neon-)tabellen missen deze kolom dus nog, CREATE TABLE IF NOT EXISTS
    # raakt een bestaande tabel niet aan.
    cur.execute("ALTER TABLE transacties ADD COLUMN IF NOT EXISTS transactiekosten NUMERIC;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prijzen (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            koers_eur NUMERIC NOT NULL,
            PRIMARY KEY (ticker, datum)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_info (
            ticker TEXT PRIMARY KEY,
            is_etf BOOLEAN NOT NULL,
            land TEXT,
            sector TEXT,
            quote_type TEXT,
            valuta TEXT,
            yahoo_beurs TEXT,
            fund_family TEXT,
            category TEXT,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # ticker_info bestond al vóór land/sector/etc. erbij kwamen — bestaande
    # (Neon-)tabellen missen deze kolommen dus nog, CREATE TABLE IF NOT EXISTS
    # raakt een bestaande tabel niet aan.
    for kolom in ("land", "sector", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category"):
        cur.execute(f"ALTER TABLE ticker_info ADD COLUMN IF NOT EXISTS {kolom} TEXT;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_matches (
            isin TEXT PRIMARY KEY,
            ticker TEXT,
            zekerheid TEXT NOT NULL,
            alternatieven JSONB,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_land_sector (
            ticker TEXT PRIMARY KEY,
            land TEXT,
            sector TEXT,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS etf_sector_verdeling (
            etf_ticker TEXT NOT NULL,
            sector TEXT NOT NULL,
            gewicht NUMERIC NOT NULL,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (etf_ticker, sector)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS etf_holdings (
            etf_ticker TEXT NOT NULL,
            holding_naam TEXT NOT NULL,
            holding_ticker TEXT,
            gewicht NUMERIC NOT NULL,
            land TEXT,
            bron TEXT DEFAULT 'yfinance_top10',
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (etf_ticker, holding_naam)
        );
    """)
    # etf_holdings bestond al vóór 'bron' erbij kwam — bestaande rijen
    # missen deze kolom dus nog, CREATE TABLE IF NOT EXISTS raakt een
    # bestaande tabel niet aan.
    cur.execute("ALTER TABLE etf_holdings ADD COLUMN IF NOT EXISTS bron TEXT DEFAULT 'yfinance_top10';")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_prijscheck (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            yahoo_slotkoers NUMERIC,
            valuta TEXT,
            opgehaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, datum)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_splits (
            ticker TEXT PRIMARY KEY,
            splits JSONB NOT NULL,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dividenden (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL,
            dividend_id TEXT NOT NULL,
            datum DATE NOT NULL,
            product TEXT,
            isin TEXT,
            valuta TEXT,
            bruto_eur NUMERIC,
            belasting_eur NUMERIC,
            netto_eur NUMERIC,
            UNIQUE (code, dividend_id)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_cached_classifications(tickers):
    """Geeft {ticker: is_etf} terug voor tickers die al eens geclassificeerd zijn."""
    if not tickers:
        return {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT ticker, is_etf FROM ticker_info WHERE ticker = ANY(%s)", (tickers,))
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


def save_classification(ticker, is_etf, details=None):
    """details (optioneel): {"land", "sector", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category"}."""
    details = details or {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_info (ticker, is_etf, land, sector, quote_type, valuta, yahoo_beurs, fund_family, category) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET is_etf = EXCLUDED.is_etf, land = EXCLUDED.land, "
        "sector = EXCLUDED.sector, quote_type = EXCLUDED.quote_type, valuta = EXCLUDED.valuta, "
        "yahoo_beurs = EXCLUDED.yahoo_beurs, fund_family = EXCLUDED.fund_family, "
        "category = EXCLUDED.category, bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, is_etf, details.get("land"), details.get("sector"), details.get("quote_type"),
         details.get("valuta"), details.get("yahoo_beurs"), details.get("fund_family"), details.get("category")),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_ticker_details(tickers):
    """Geeft {ticker: {land, sector, quote_type, valuta, yahoo_beurs, fund_family, category}} terug."""
    if not tickers:
        return {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, land, sector, quote_type, valuta, yahoo_beurs, fund_family, category "
        "FROM ticker_info WHERE ticker = ANY(%s)",
        (tickers,),
    )
    kolommen = ["land", "sector", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category"]
    result = {row[0]: dict(zip(kolommen, row[1:])) for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


# Land/sector-lookups en ETF-holdings/sectorverdeling veranderen traag
# (samenstelling wijzigt hooguit maandelijks) — cache 30 dagen om niet bij
# elke upload opnieuw tegen Yahoo te hoeven, net als ticker_info hierboven.
CACHE_GELDIGHEID = "30 days"


def get_cached_land_sector(tickers):
    """Geeft {ticker: (land, sector)} terug voor tickers die de afgelopen 30 dagen al opgezocht zijn."""
    if not tickers:
        return {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"SELECT ticker, land, sector FROM ticker_land_sector "
        f"WHERE ticker = ANY(%s) AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (tickers,),
    )
    result = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


def save_land_sector(ticker, land, sector):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_land_sector (ticker, land, sector) VALUES (%s, %s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET land = EXCLUDED.land, sector = EXCLUDED.sector, "
        "bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, land, sector),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_cached_etf_sector_verdeling(etf_ticker):
    """Geeft {sector: gewicht} terug als er een niet-verlopen (<30 dagen) cache is, anders None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"SELECT sector, gewicht FROM etf_sector_verdeling "
        f"WHERE etf_ticker = %s AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (etf_ticker,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None
    return {row[0]: float(row[1]) for row in rows}


def save_etf_sector_verdeling(etf_ticker, sector_dict):
    """Vervangt de volledige sectorverdeling van deze ETF (delete + bulk insert, zodat alle
    rijen dezelfde bijgewerkt_op krijgen en de cache-leeftijdscheck consistent blijft).
    Roep dit alleen aan met een niet-lege sector_dict — een mislukte/lege ophaal moet NIET
    gecached worden (zie get_etf_sector_verdeling in analysis.py)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM etf_sector_verdeling WHERE etf_ticker = %s", (etf_ticker,))
    if sector_dict:
        execute_values(
            cur,
            "INSERT INTO etf_sector_verdeling (etf_ticker, sector, gewicht) VALUES %s",
            [(etf_ticker, sector, gewicht) for sector, gewicht in sector_dict.items()],
        )
    conn.commit()
    cur.close()
    conn.close()


def get_cached_etf_holdings(etf_ticker):
    """Geeft lijst van {holding_naam, holding_ticker, gewicht, land, bron} terug als er een
    niet-verlopen (<30 dagen) cache is, anders None."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"SELECT holding_naam, holding_ticker, gewicht, land, bron FROM etf_holdings "
        f"WHERE etf_ticker = %s AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (etf_ticker,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None
    return [
        {"holding_naam": row[0], "holding_ticker": row[1], "gewicht": float(row[2]), "land": row[3],
         "bron": row[4] or "yfinance_top10"}
        for row in rows
    ]


def save_etf_holdings(etf_ticker, holdings_lijst):
    """Vervangt de volledige holdings van deze ETF (delete + bulk insert). Roep dit
    alleen aan met een niet-lege holdings_lijst — zelfde reden als save_etf_sector_verdeling."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM etf_holdings WHERE etf_ticker = %s", (etf_ticker,))
    if holdings_lijst:
        execute_values(
            cur,
            "INSERT INTO etf_holdings (etf_ticker, holding_naam, holding_ticker, gewicht, land, bron) VALUES %s",
            [
                (etf_ticker, h["holding_naam"], h.get("holding_ticker"), h["gewicht"], h.get("land"),
                 h.get("bron", "yfinance_top10"))
                for h in holdings_lijst
            ],
        )
    conn.commit()
    cur.close()
    conn.close()


def get_cached_prijscheck(ticker, datum):
    """
    Geeft (yahoo_slotkoers, valuta) terug als deze (ticker, datum)-combinatie
    al eens gecontroleerd is, anders None. yahoo_slotkoers kan zelf None
    zijn (een eerdere mislukte poging die toch gecached is — zie
    save_prijscheck) — het verschil tussen "nog nooit geprobeerd" (deze
    functie geeft None) en "geprobeerd maar mislukt" (tuple met None erin)
    is precies wat de aanroeper nodig heeft om te weten of het zin heeft om
    het opnieuw te proberen.

    Historische slotkoersen veranderen nooit met terugwerkende kracht, dus
    deze cache heeft — anders dan de andere caches in dit bestand — geen
    leeftijdscheck nodig.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT yahoo_slotkoers, valuta FROM ticker_prijscheck WHERE ticker = %s AND datum = %s",
        (ticker, datum),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    yahoo_slotkoers, valuta = row
    return (float(yahoo_slotkoers) if yahoo_slotkoers is not None else None, valuta)


def save_prijscheck(ticker, datum, koers, valuta):
    """
    Cacht een historische-slotkoers-check permanent — bewust ook als koers
    None is (mislukte lookup). Dit wijkt af van de "None niet cachen"-regel
    bij de andere caches in dit bestand: daar kan een mislukte poging de
    volgende keer wél lukken (bv. na een tijdelijke rate limit), maar hier
    verandert de onderliggende historische koers nooit — als Yahoo op dit
    moment geen koers heeft voor deze ticker op deze datum, blijft dat zo.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_prijscheck (ticker, datum, yahoo_slotkoers, valuta) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (ticker, datum) DO UPDATE SET yahoo_slotkoers = EXCLUDED.yahoo_slotkoers, "
        "valuta = EXCLUDED.valuta, opgehaald_op = CURRENT_TIMESTAMP",
        (ticker, datum, koers, valuta),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_cached_splits(ticker):
    """
    Geeft de gecachte split-geschiedenis van 'ticker' terug als {iso_datum:
    ratio}, of None als er geen (niet-verlopen, <30 dagen) cache is. Zelfde
    leeftijdscheck als land/sector hierboven (CACHE_GELDIGHEID) — in
    tegenstelling tot ticker_prijscheck (historische koersen veranderen
    nooit) kan een ticker in de toekomst een NIEUWE split doen, dus deze
    cache mag niet voor altijd blijven staan.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"SELECT splits FROM ticker_splits WHERE ticker = %s AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (ticker,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def save_splits(ticker, splits):
    """splits: {iso_datum: ratio} — ook een leeg dict cachen (bevestigd geen splits), zie get_cached_splits."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_splits (ticker, splits) VALUES (%s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET splits = EXCLUDED.splits, bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, Json(splits)),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_ticker_matches(isins):
    """Geeft {isin: {"ticker", "zekerheid", "alternatieven"}} terug voor eerder opgeslagen ticker-matches."""
    if not isins:
        return {}
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT isin, ticker, zekerheid, alternatieven FROM ticker_matches WHERE isin = ANY(%s)",
        (isins,),
    )
    result = {
        row[0]: {"ticker": row[1], "zekerheid": row[2], "alternatieven": row[3] or []}
        for row in cur.fetchall()
    }
    cur.close()
    conn.close()
    return result


def save_ticker_match(isin, ticker, zekerheid, alternatieven):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_matches (isin, ticker, zekerheid, alternatieven) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (isin) DO UPDATE SET ticker = EXCLUDED.ticker, zekerheid = EXCLUDED.zekerheid, "
        "alternatieven = EXCLUDED.alternatieven, bijgewerkt_op = CURRENT_TIMESTAMP",
        (isin, ticker, zekerheid, Json(alternatieven)),
    )
    conn.commit()
    cur.close()
    conn.close()


def delete_portfolio(code):
    """Verwijdert een portfolio en al zijn transacties/dividenden permanent.
    Laat de gedeelde caches (prijzen, ticker_info, ticker_matches) met rust
    — dat is anonieme marktdata, geen persoonlijke portfoliodata."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
    cur.execute("DELETE FROM dividenden WHERE code = %s", (code,))
    cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))
    conn.commit()
    cur.close()
    conn.close()


def wijzig_portfolio_code(oude_code, nieuwe_code):
    """Hernoemt een portfolio-code en alle gekoppelde tabellen (transacties,
    dividenden). transacties.code heeft een FK naar portfolios(code) zonder
    ON UPDATE CASCADE, en die check gebeurt meteen aan het eind van elke
    UPDATE-statement (niet pas bij commit) — de portfolios-rij kan dus niet
    zomaar hernoemd worden zolang transacties nog naar de oude code wijst.
    Daarom eerst een nieuwe portfolios-rij met de nieuwe code aanmaken, dan
    transacties/dividenden ernaartoe verhuizen, en pas daarna de oude
    portfolios-rij weggooien — allemaal in dezelfde transactie.

    Geeft (True, None) bij succes, (False, foutmelding) als de nieuwe code
    al in gebruik is of de oude code niet bestaat."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT naam, aangemaakt_op FROM portfolios WHERE code = %s", (oude_code,))
        row = cur.fetchone()
        if row is None:
            return False, f"Geen portfolio gevonden met code '{oude_code}'."
        naam, aangemaakt_op = row

        try:
            cur.execute(
                "INSERT INTO portfolios (code, naam, aangemaakt_op) VALUES (%s, %s, %s)",
                (nieuwe_code, naam, aangemaakt_op),
            )
        except pg_errors.UniqueViolation:
            conn.rollback()
            return False, f"Code '{nieuwe_code}' is al in gebruik."

        cur.execute("UPDATE transacties SET code = %s WHERE code = %s", (nieuwe_code, oude_code))
        cur.execute("UPDATE dividenden SET code = %s WHERE code = %s", (nieuwe_code, oude_code))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (oude_code,))
        conn.commit()
        return True, None
    finally:
        cur.close()
        conn.close()


def save_dividenden(code, records):
    """records: lijst van dicts zoals analysis.verwerk_rekeningoverzicht() teruggeeft.

    ON CONFLICT (code, dividend_id) DO UPDATE — bewust een upsert, GEEN DO
    NOTHING. dividend_id is afgeleid van de RUWE (niet-EUR-geconverteerde)
    bedragen (zie verwerk_rekeningoverzicht), die niet veranderen als er
    later iets verbetert aan de EUR-omrekenlogica zelf. Met DO NOTHING zou
    een bugfix in die omrekenlogica dus nooit een al opgeslagen rij
    corrigeren: een eerdere (foutieve, bv. NULL) waarde zou voor altijd
    blijven staan omdat een latere, juiste herberekening exact dezelfde
    dividend_id oplevert en dus als 'duplicaat' genegeerd werd. Dit was
    precies de oorzaak van een bug waarbij de Dividend-pagina €0,00 bleef
    tonen ondanks een gefixte berekening: de upload-log toonde het juiste
    vers-berekende totaal, maar de oude NULL-rijen in de database bleven
    intact staan omdat DO NOTHING de nieuwe (juiste) waarden nooit liet
    doorschrijven."""
    if not records:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    execute_values(
        cur,
        "INSERT INTO dividenden (code, dividend_id, datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur) "
        "VALUES %s ON CONFLICT (code, dividend_id) DO UPDATE SET "
        "datum = EXCLUDED.datum, product = EXCLUDED.product, isin = EXCLUDED.isin, "
        "valuta = EXCLUDED.valuta, bruto_eur = EXCLUDED.bruto_eur, "
        "belasting_eur = EXCLUDED.belasting_eur, netto_eur = EXCLUDED.netto_eur",
        [
            (code, r["dividend_id"], r["datum"], r["product"], r["isin"], r["valuta"],
             r["bruto_eur"], r["belasting_eur"], r["netto_eur"])
            for r in records
        ],
    )
    conn.commit()
    aantal_bekend = sum(1 for r in records if r["netto_eur"] is not None)
    print(f"[dividend-debug] save_dividenden: code='{code}', {len(records)} record(s) ge-upsert "
          f"({aantal_bekend} met een bekende netto_eur, {len(records) - aantal_bekend} met netto_eur=None)")
    cur.close()
    conn.close()


def get_dividenden(code):
    """Geeft alle dividendrijen voor deze code terug, gesorteerd op datum."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur "
        "FROM dividenden WHERE code = %s ORDER BY datum",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    resultaat = [
        {
            "datum": datum,
            "product": product,
            "isin": isin,
            "valuta": valuta,
            "bruto_eur": float(bruto_eur) if bruto_eur is not None else None,
            "belasting_eur": float(belasting_eur) if belasting_eur is not None else None,
            "netto_eur": float(netto_eur) if netto_eur is not None else None,
        }
        for datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur in rows
    ]
    aantal_bekend = sum(1 for r in resultaat if r["netto_eur"] is not None)
    print(f"[dividend-debug] get_dividenden: code='{code}', {len(resultaat)} rij(en) opgehaald "
          f"({aantal_bekend} met een bekende netto_eur, {len(resultaat) - aantal_bekend} met netto_eur=None)")
    return resultaat


def save_prices(rows):
    """rows: lijst van (ticker, datum, koers_eur) tuples."""
    if not rows:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    execute_values(
        cur,
        "INSERT INTO prijzen (ticker, datum, koers_eur) VALUES %s "
        "ON CONFLICT (ticker, datum) DO NOTHING",
        rows,
    )
    conn.commit()
    cur.close()
    conn.close()