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
            -- DEGIRO's kale Waarde EUR (aantal x koers, zonder kosten); basis voor de GAK, want totaal_eur telt AutoFX/kosten mee
            waarde_eur NUMERIC,
            -- nodig om transacties op dezelfde dag chronologisch te sorteren (koop vóór verkoop)
            tijd TIME,
            UNIQUE (code, order_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prijzen (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            koers_eur NUMERIC NOT NULL,
            -- moment van ophalen; bepaalt of de koers van vandaag ververst moet worden (prijzen.py)
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            -- 'provider_csv' (volledige lijst via fondsprovider) of 'yfinance_top10' (fallback)
            bron TEXT DEFAULT 'yfinance_top10',
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (etf_ticker, holding_naam)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_prijscheck (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            yahoo_slotkoers NUMERIC,
            valuta TEXT,
            opgehaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- intraday-dagrange voor de prijsvergelijking op de Ticker-zekerheid-pagina
            high NUMERIC,
            low NUMERIC,
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
        CREATE TABLE IF NOT EXISTS openfigi_cache (
            isin TEXT PRIMARY KEY,
            resultaten JSONB NOT NULL,
            opgehaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            -- True als er voor deze datum/ISIN ook een "Dividend Herinvestering"-rij was
            herinvesteerd BOOLEAN DEFAULT FALSE,
            UNIQUE (code, dividend_id)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_cached_classifications(tickers):
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


# Geldt niet voor ticker_info, ticker_prijscheck en openfigi_cache: die verlopen nooit.
CACHE_GELDIGHEID = "30 days"


def get_cached_land_sector(tickers):
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
    """Delete + bulk insert, zodat alle rijen dezelfde bijgewerkt_op krijgen. Niet aanroepen met een lege dict."""
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
    """Delete + bulk insert. Niet aanroepen met een lege lijst."""
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
    """(yahoo_slotkoers, valuta, high, low), of None als nooit geprobeerd; None ín de tuple = geprobeerd, mislukt."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT yahoo_slotkoers, valuta, high, low FROM ticker_prijscheck WHERE ticker = %s AND datum = %s",
        (ticker, datum),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    yahoo_slotkoers, valuta, high, low = row
    return (
        float(yahoo_slotkoers) if yahoo_slotkoers is not None else None,
        valuta,
        float(high) if high is not None else None,
        float(low) if low is not None else None,
    )


def save_prijscheck(ticker, datum, koers, valuta, high=None, low=None):
    """Permanent, ook bij koers None. Upsert, zodat later gevonden high/low nog wordt bijgeschreven."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_prijscheck (ticker, datum, yahoo_slotkoers, valuta, high, low) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ticker, datum) DO UPDATE SET yahoo_slotkoers = EXCLUDED.yahoo_slotkoers, "
        "valuta = EXCLUDED.valuta, high = EXCLUDED.high, low = EXCLUDED.low, "
        "opgehaald_op = CURRENT_TIMESTAMP",
        (ticker, datum, koers, valuta, high, low),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_cached_splits(ticker):
    """{iso_datum: ratio} of None. Verloopt wel: een ticker kan later nog splitsen."""
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
    """Ook een leeg dict cachen: dat betekent 'geen splits'."""
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


def get_cached_openfigi(isin):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT resultaten FROM openfigi_cache WHERE isin = %s", (isin,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def save_openfigi(isin, resultaten):
    """Alleen na een geslaagde aanroep; een lege lijst ('geen match') mag wel."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO openfigi_cache (isin, resultaten) VALUES (%s, %s) "
        "ON CONFLICT (isin) DO UPDATE SET resultaten = EXCLUDED.resultaten, "
        "opgehaald_op = CURRENT_TIMESTAMP",
        (isin, Json(resultaten)),
    )
    conn.commit()
    cur.close()
    conn.close()


def delete_portfolio(code):
    """De caches blijven staan: dat is anonieme marktdata."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
    cur.execute("DELETE FROM dividenden WHERE code = %s", (code,))
    cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))
    conn.commit()
    cur.close()
    conn.close()


def wijzig_portfolio_code(oude_code, nieuwe_code):
    """FK zonder ON UPDATE CASCADE: eerst nieuwe rij, dan verhuizen, dan oude weg. Geeft (gelukt, foutmelding)."""
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
    """Bewust een upsert, zie CLAUDE.md: DeGiro-bestanden."""
    if not records:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    execute_values(
        cur,
        "INSERT INTO dividenden (code, dividend_id, datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur, herinvesteerd) "
        "VALUES %s ON CONFLICT (code, dividend_id) DO UPDATE SET "
        "datum = EXCLUDED.datum, product = EXCLUDED.product, isin = EXCLUDED.isin, "
        "valuta = EXCLUDED.valuta, bruto_eur = EXCLUDED.bruto_eur, "
        "belasting_eur = EXCLUDED.belasting_eur, netto_eur = EXCLUDED.netto_eur, "
        "herinvesteerd = EXCLUDED.herinvesteerd",
        [
            (code, r["dividend_id"], r["datum"], r["product"], r["isin"], r["valuta"],
             r["bruto_eur"], r["belasting_eur"], r["netto_eur"], r.get("herinvesteerd", False))
            for r in records
        ],
    )
    conn.commit()
    cur.close()
    conn.close()


def get_dividenden(code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur, herinvesteerd "
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
            "herinvesteerd": bool(herinvesteerd),
        }
        for datum, product, isin, valuta, bruto_eur, belasting_eur, netto_eur, herinvesteerd in rows
    ]
    return resultaat


def get_transacties_overzicht(code):
    """tijd en transactiekosten kunnen None zijn; nooit een verzonnen waarde invullen."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT datum, tijd, product, aantal, koers, totaal_eur, transactiekosten FROM transacties "
        "WHERE code = %s ORDER BY datum DESC, tijd DESC",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "datum": datum.strftime("%Y-%m-%d"),
            "tijd": tijd.strftime("%H:%M") if tijd is not None else None,
            "product": product,
            "aantal": float(aantal),
            "koers": float(koers) if koers is not None else None,
            "totaal_eur": float(totaal_eur),
            "transactiekosten": float(transactiekosten) if transactiekosten is not None else None,
        }
        for datum, tijd, product, aantal, koers, totaal_eur, transactiekosten in rows
    ]


def save_prices(rows):
    """rows: (ticker, datum, koers_eur)-tuples. DO NOTHING: historische koersen veranderen niet."""
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


def upsert_prices(rows):
    """Alleen voor de verse koers van vandaag (kan tussentijds zijn); historie via save_prices()."""
    if not rows:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    execute_values(
        cur,
        "INSERT INTO prijzen (ticker, datum, koers_eur, bijgewerkt_op) VALUES %s "
        "ON CONFLICT (ticker, datum) DO UPDATE SET "
        "koers_eur = EXCLUDED.koers_eur, bijgewerkt_op = EXCLUDED.bijgewerkt_op",
        rows,
        template="(%s, %s, %s, NOW())",
    )
    conn.commit()
    cur.close()
    conn.close()


def get_laatste_prijs_update(tickers):
    """(laatste_koersdatum, laatst_opgehaald_op in UTC), of (None, None)."""
    if not tickers:
        return None, None
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(datum), MAX(bijgewerkt_op) FROM prijzen WHERE ticker = ANY(%s)",
        (tickers,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0], row[1]) if row else (None, None)