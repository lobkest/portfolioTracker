import os
import threading
import time

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv
from transactie_utils import formatteer_transacties_overzicht

load_dotenv()


# Globaal per proces, net als de Yahoo-tellers: gelijktijdige requests tellen bij elkaar op.
_verbinding_lock = threading.Lock()
_verbinding_teller = {"aantal": 0, "seconden": 0.0}


def db_reset_verbinding_teller():
    with _verbinding_lock:
        _verbinding_teller["aantal"] = 0
        _verbinding_teller["seconden"] = 0.0


def db_log_verbinding_samenvatting():
    with _verbinding_lock:
        aantal, seconden = _verbinding_teller["aantal"], _verbinding_teller["seconden"]
    print(f"[timing] DB-verbindingen sinds laatste reset: {aantal}, samen {seconden:.2f}s verbinden")


def db_connect():
    start = time.time()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with _verbinding_lock:
        _verbinding_teller["aantal"] += 1
        _verbinding_teller["seconden"] += time.time() - start
    return conn


def db_init():
    conn = db_connect()
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
            wisselkoers NUMERIC,
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
        CREATE TABLE IF NOT EXISTS koersen (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            -- ruwe koers in EUR: de koers zoals hij die dag noteerde, nooit achteraf voor splits gecorrigeerd
            koers_eur NUMERIC NOT NULL,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, datum)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS koers_splits (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            ratio NUMERIC NOT NULL,
            PRIMARY KEY (ticker, datum)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prijscheck_koersen (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            -- ruwe slotkoers en dagrange in eigen valuta (zoals koersen.koers_eur, maar zonder FX)
            slotkoers NUMERIC,
            valuta TEXT,
            high NUMERIC,
            low NUMERIC,
            opgehaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            long_name TEXT
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ishares_fondsen (
            portfolio_id TEXT PRIMARY KEY,
            isin TEXT NOT NULL,
            naam TEXT,
            product_url TEXT NOT NULL,
            asset_class TEXT,
            regio TEXT,
            markt_type TEXT,
            sub_asset_class TEXT,
            strategie_codes JSONB,
            fondsgrootte NUMERIC,
            opgehaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS etf_proxy (
            bron_isin TEXT PRIMARY KEY,
            -- NULL = gezocht maar geen proxy gevonden (ook gecachet, anders bij elke load opnieuw zoeken)
            proxy_isin TEXT,
            proxy_naam TEXT,
            max_afwijking_pp NUMERIC,
            vergelijking JSONB,
            bepaald_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            -- {land: fractie 0-1} uit de holdings van de proxy
            proxy_land JSONB
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def db_get_cached_classifications(tickers):
    if not tickers:
        return {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT ticker, is_etf FROM ticker_info WHERE ticker = ANY(%s)", (tickers,))
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


def db_save_classification(ticker, is_etf, details=None):
    """details (optioneel): {"land", "sector", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category",
    "long_name"}. Een ontbrekende long_name overschrijft een bekende niet."""
    details = details or {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_info (ticker, is_etf, land, sector, quote_type, valuta, yahoo_beurs, fund_family, category, "
        "long_name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET is_etf = EXCLUDED.is_etf, land = EXCLUDED.land, "
        "sector = EXCLUDED.sector, quote_type = EXCLUDED.quote_type, valuta = EXCLUDED.valuta, "
        "yahoo_beurs = EXCLUDED.yahoo_beurs, fund_family = EXCLUDED.fund_family, "
        "category = EXCLUDED.category, long_name = COALESCE(EXCLUDED.long_name, ticker_info.long_name), "
        "bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, is_etf, details.get("land"), details.get("sector"), details.get("quote_type"),
         details.get("valuta"), details.get("yahoo_beurs"), details.get("fund_family"), details.get("category"),
         details.get("long_name")),
    )
    conn.commit()
    cur.close()
    conn.close()


def db_get_ticker_details(tickers):
    if not tickers:
        return {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, land, sector, quote_type, valuta, yahoo_beurs, fund_family, category, long_name "
        "FROM ticker_info WHERE ticker = ANY(%s)",
        (tickers,),
    )
    kolommen = ["land", "sector", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category", "long_name"]
    result = {row[0]: dict(zip(kolommen, row[1:])) for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


# Geldt niet voor ticker_info, ticker_prijscheck en openfigi_cache: die verlopen nooit.
CACHE_GELDIGHEID = "30 days"
ISHARES_FONDSEN_GELDIGHEID = "7 days"


def db_get_cached_land_sector(tickers):
    if not tickers:
        return {}
    conn = db_connect()
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


def db_save_land_sector(ticker, land, sector):
    conn = db_connect()
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


def db_get_cached_etf_sector_verdeling(etf_ticker):
    conn = db_connect()
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


def db_save_etf_sector_verdeling(etf_ticker, sector_dict):
    """Delete + bulk insert, zodat alle rijen dezelfde bijgewerkt_op krijgen. Niet aanroepen met een lege dict."""
    conn = db_connect()
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


def db_get_cached_etf_holdings(etf_ticker):
    conn = db_connect()
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


def db_save_etf_holdings(etf_ticker, holdings_lijst):
    """Delete + bulk insert. Niet aanroepen met een lege lijst."""
    conn = db_connect()
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


def db_get_cached_prijscheck(ticker, datum):
    """(yahoo_slotkoers, valuta, high, low), of None als nooit geprobeerd; None ín de tuple = geprobeerd, mislukt."""
    conn = db_connect()
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


def db_save_prijscheck(ticker, datum, koers, valuta, high=None, low=None):
    """Permanent, ook bij koers None. Upsert, zodat later gevonden high/low nog wordt bijgeschreven."""
    conn = db_connect()
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


def db_get_cached_splits(ticker):
    """{iso_datum: ratio} of None. Verloopt wel: een ticker kan later nog splitsen."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        f"SELECT splits FROM ticker_splits WHERE ticker = %s AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (ticker,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def db_save_splits(ticker, splits):
    """Ook een leeg dict cachen: dat betekent 'geen splits'."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_splits (ticker, splits) VALUES (%s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET splits = EXCLUDED.splits, bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, Json(splits)),
    )
    conn.commit()
    cur.close()
    conn.close()


def db_get_cached_openfigi(isin):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT resultaten FROM openfigi_cache WHERE isin = %s", (isin,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def db_get_cached_openfigi_voor_isins(isins):
    """{isin: resultaten}; een ISIN die nog nooit is opgevraagd ontbreekt."""
    if not isins:
        return {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT isin, resultaten FROM openfigi_cache WHERE isin = ANY(%s)", (list(isins),))
    result = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return result


def db_save_long_names(long_names):
    """Alleen bestaande ticker_info-rijen; None slaat een ticker over."""
    paren = [(naam, ticker) for ticker, naam in long_names.items() if naam]
    if not paren:
        return
    conn = db_connect()
    cur = conn.cursor()
    cur.executemany("UPDATE ticker_info SET long_name = %s WHERE ticker = %s", paren)
    conn.commit()
    cur.close()
    conn.close()


def db_save_openfigi(isin, resultaten):
    """Alleen na een geslaagde aanroep; een lege lijst ('geen match') mag wel."""
    conn = db_connect()
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


def db_delete_portfolio(code):
    """De caches blijven staan: dat is anonieme marktdata."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM transacties WHERE code = %s", (code,))
    cur.execute("DELETE FROM dividenden WHERE code = %s", (code,))
    cur.execute("DELETE FROM portfolios WHERE code = %s", (code,))
    conn.commit()
    cur.close()
    conn.close()


def db_wijzig_portfolio_code(oude_code, nieuwe_code):
    """FK zonder ON UPDATE CASCADE: eerst nieuwe rij, dan verhuizen, dan oude weg. Geeft (gelukt, foutmelding)."""
    conn = db_connect()
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


def db_save_dividenden(code, records):
    """Bewust een upsert, zie CLAUDE.md: DeGiro-bestanden."""
    if not records:
        return
    conn = db_connect()
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


def db_get_dividenden(code):
    conn = db_connect()
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


def db_get_transacties_overzicht(code):
    """tijd en transactiekosten kunnen None zijn; nooit een verzonnen waarde invullen."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT datum, tijd, COALESCE(echte_naam, product), aantal, koers, totaal_eur, transactiekosten, isin, beurs, wisselkoers "
        "FROM transacties WHERE code = %s ORDER BY datum DESC, tijd DESC",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return formatteer_transacties_overzicht(rows)


def db_save_prices(rows):
    """rows: (ticker, datum, koers_eur)-tuples. DO NOTHING: historische koersen veranderen niet."""
    if not rows:
        return
    conn = db_connect()
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


def db_upsert_prices(rows):
    """Alleen voor de verse koers van vandaag (kan tussentijds zijn); historie via db_save_prices()."""
    if not rows:
        return
    conn = db_connect()
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


def db_get_laatste_prijs_update(tickers):
    """(laatste_koersdatum, laatst_opgehaald_op in UTC), of (None, None)."""
    if not tickers:
        return None, None
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(datum), MAX(bijgewerkt_op) FROM prijzen WHERE ticker = ANY(%s)",
        (tickers,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def db_get_gecachte_prijzen(tickers, vandaag, start_datum):
    """Geeft ({ticker: (eerste_datum, laatste_datum)}, {ticker: bijgewerkt_op van vandaag}, [(ticker, datum, koers_eur)])."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM prijzen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums = {ticker: (eerste, laatste) for ticker, eerste, laatste in cur.fetchall()}

    cur.execute(
        "SELECT ticker, bijgewerkt_op FROM prijzen WHERE ticker = ANY(%s) AND datum = %s",
        (tickers, vandaag),
    )
    bijgewerkt_vandaag = {ticker: bijgewerkt_op for ticker, bijgewerkt_op in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM prijzen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_datum),
    )
    koersen = cur.fetchall()
    cur.close()
    conn.close()
    return datums, bijgewerkt_vandaag, koersen


def db_save_koersen(rijen, splits_per_ticker):
    """rijen: (ticker, datum, koers_eur) met RUWE koersen; splits_per_ticker: {ticker: {iso_datum: ratio}} uit
    dezelfde Yahoo-response (ook leeg). Eén transactie: koersen zonder bijbehorende splits mogen niet bestaan."""
    if not rijen and not splits_per_ticker:
        return
    conn = db_connect()
    cur = conn.cursor()
    try:
        if rijen:
            execute_values(
                cur,
                "INSERT INTO koersen (ticker, datum, koers_eur, bijgewerkt_op) VALUES %s "
                "ON CONFLICT (ticker, datum) DO UPDATE SET "
                "koers_eur = EXCLUDED.koers_eur, bijgewerkt_op = EXCLUDED.bijgewerkt_op",
                rijen,
                template="(%s, %s, %s, NOW())",
            )
        split_rijen = [
            (ticker, datum, ratio)
            for ticker, splits in splits_per_ticker.items() for datum, ratio in splits.items()
        ]
        if split_rijen:
            execute_values(
                cur,
                "INSERT INTO koers_splits (ticker, datum, ratio) VALUES %s "
                "ON CONFLICT (ticker, datum) DO UPDATE SET ratio = EXCLUDED.ratio",
                split_rijen,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def db_get_gecachte_koersen(tickers, vandaag, start_datum):
    """Geeft ({ticker: (eerste_datum, laatste_datum)}, {ticker: bijgewerkt_op van vandaag}, [(ticker, datum, koers_eur)])."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM koersen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums = {ticker: (eerste, laatste) for ticker, eerste, laatste in cur.fetchall()}

    cur.execute(
        "SELECT ticker, bijgewerkt_op FROM koersen WHERE ticker = ANY(%s) AND datum = %s",
        (tickers, vandaag),
    )
    bijgewerkt_vandaag = {ticker: bijgewerkt_op for ticker, bijgewerkt_op in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM koersen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_datum),
    )
    koersen = cur.fetchall()
    cur.close()
    conn.close()
    return datums, bijgewerkt_vandaag, koersen


def db_get_koers_splits(tickers):
    """{ticker: {iso_datum: ratio}} voor elke ticker die koersen heeft (leeg dict = geen splits sinds de eerste koers).
    Tickers zonder koersen ontbreken: daar is de splitlijst onbekend."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT k.ticker, s.datum, s.ratio "
        "FROM (SELECT DISTINCT ticker FROM koersen WHERE ticker = ANY(%s)) k "
        "LEFT JOIN koers_splits s ON s.ticker = k.ticker",
        (tickers,),
    )
    resultaat = {}
    for ticker, datum, ratio in cur.fetchall():
        splits = resultaat.setdefault(ticker, {})
        if datum is not None:
            splits[datum.isoformat()] = float(ratio)
    cur.close()
    conn.close()
    return resultaat


def db_get_laatste_koers_update(tickers):
    """(laatste_koersdatum, laatst_opgehaald_op in UTC), of (None, None)."""
    if not tickers:
        return None, None
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT MAX(datum), MAX(bijgewerkt_op) FROM koersen WHERE ticker = ANY(%s)",
        (tickers,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def db_get_cached_prijscheck_koers(ticker, datum):
    """(slotkoers, valuta, high, low), of None als nooit geprobeerd; None ín de tuple = geprobeerd, mislukt."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT slotkoers, valuta, high, low FROM prijscheck_koersen WHERE ticker = %s AND datum = %s",
        (ticker, datum),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    slotkoers, valuta, high, low = row
    return (
        float(slotkoers) if slotkoers is not None else None,
        valuta,
        float(high) if high is not None else None,
        float(low) if low is not None else None,
    )


def db_save_prijscheck_koers(ticker, datum, slotkoers, valuta, high=None, low=None):
    """Permanent, ook bij slotkoers None. Alleen RUWE waarden opslaan."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO prijscheck_koersen (ticker, datum, slotkoers, valuta, high, low) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ticker, datum) DO UPDATE SET slotkoers = EXCLUDED.slotkoers, "
        "valuta = EXCLUDED.valuta, high = EXCLUDED.high, low = EXCLUDED.low, "
        "opgehaald_op = CURRENT_TIMESTAMP",
        (ticker, datum, slotkoers, valuta, high, low),
    )
    conn.commit()
    cur.close()
    conn.close()


TRANSACTIE_KOLOMMEN = [
    "datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur",
    "echte_naam", "transactiekosten", "waarde_eur", "tijd", "wisselkoers",
]


def db_get_portfolio_naam_en_transacties(code):
    """(naam, rijen in volgorde van TRANSACTIE_KOLOMMEN), of (None, None) als de code niet bestaat.
    Een portfolio zonder naam heeft NULL in de DB en geeft hier "" terug."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT naam FROM portfolios WHERE code = %s", (code,))
    result = cur.fetchone()
    if result is None:
        cur.close()
        conn.close()
        return None, None
    cur.execute(f"SELECT {', '.join(TRANSACTIE_KOLOMMEN)} FROM transacties WHERE code = %s", (code,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return result[0] or "", rows


ORDER_ID_KOLOMMEN = ["order_id", "datum", "tijd", "product", "isin", "beurs", "aantal", "koers", "transactiekosten"]


def db_get_order_id_rijen(code):
    """Rijen in de volgorde van ORDER_ID_KOLOMMEN, zodat wissel- en corporate-action-rijen herkenbaar zijn."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(ORDER_ID_KOLOMMEN)} FROM transacties WHERE code = %s AND order_id IS NOT NULL", (code,))
    rijen = cur.fetchall()
    cur.close()
    conn.close()
    return rijen


def db_portfolio_bestaat_met_cursor(cur, code):
    cur.execute("SELECT 1 FROM portfolios WHERE code = %s", (code,))
    return cur.fetchone() is not None


def db_portfolio_bestaat(code):
    conn = db_connect()
    cur = conn.cursor()
    bestaat = db_portfolio_bestaat_met_cursor(cur, code)
    cur.close()
    conn.close()
    return bestaat


def db_maak_portfolio(cur, code, naam):
    cur.execute("INSERT INTO portfolios (code, naam) VALUES (%s, %s)", (code, naam))


def db_zet_portfolio_naam(cur, code, naam):
    cur.execute("UPDATE portfolios SET naam = %s WHERE code = %s", (naam, code))


def db_get_order_id_sets(cur):
    cur.execute("SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL")
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def db_get_bekende_tickers(cur, code):
    """{(isin, beurs): ticker} van de transacties van deze portfolio die al een ticker hebben."""
    cur.execute(
        "SELECT isin, beurs, ticker FROM transacties WHERE code = %s AND ticker IS NOT NULL",
        (code,),
    )
    return {(isin, beurs): ticker for isin, beurs, ticker in cur.fetchall()}


def db_get_product_per_ticker(cur, code):
    """{ticker: product} van de laatst toegevoegde rij per ticker."""
    cur.execute(
        "SELECT DISTINCT ON (ticker) ticker, product FROM transacties "
        "WHERE code = %s AND ticker IS NOT NULL ORDER BY ticker, id DESC",
        (code,),
    )
    return {ticker: product for ticker, product in cur.fetchall()}


def db_laad_product_per_ticker(code):
    conn = db_connect()
    cur = conn.cursor()
    try:
        return db_get_product_per_ticker(cur, code)
    finally:
        cur.close()
        conn.close()


def db_insert_transactie(cur, code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur,
                      order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers):
    """Geeft False als de rij al bestond (ON CONFLICT DO NOTHING)."""
    cur.execute(
        """INSERT INTO transacties
                   (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, order_id) DO NOTHING""",
        (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur,
         order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers),
    )
    return cur.rowcount != 0


def db_get_transacties_voor_tickercheck(cur, code):
    cur.execute(
        "SELECT isin, beurs, ticker, product, echte_naam, datum, koers FROM transacties WHERE code = %s",
        (code,),
    )
    return cur.fetchall()


def db_wijzig_ticker(cur, code, isin, beurs, ticker):
    cur.execute(
        "UPDATE transacties SET ticker = %s WHERE code = %s AND isin = %s AND beurs = %s",
        (ticker, code, isin, beurs),
    )


def db_get_isin_ticker_product(code):
    """(isin, ticker, product)-rijen van de transacties met een ticker."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT isin, ticker, product FROM transacties WHERE code = %s AND ticker IS NOT NULL",
        (code,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def db_wijzig_bijnaam(code, ticker, bijnaam):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = %s WHERE code = %s AND ticker = %s",
        (bijnaam, code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()


def db_wijzig_bijnamen(code, bijnaam_per_ticker):
    """Alle bijnamen in één transactie; alle rijen van een ticker krijgen dezelfde naam."""
    conn = db_connect()
    cur = conn.cursor()
    try:
        for ticker, bijnaam in bijnaam_per_ticker.items():
            cur.execute(
                "UPDATE transacties SET product = %s WHERE code = %s AND ticker = %s",
                (bijnaam, code, ticker),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def db_herstel_echte_naam(code, ticker):
    """Alle rijen van de ticker krijgen de echte_naam van de nieuwste rij (één product per ticker)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE transacties SET product = COALESCE("
        "(SELECT echte_naam FROM transacties WHERE code = %s AND ticker = %s ORDER BY id DESC LIMIT 1), product) "
        "WHERE code = %s AND ticker = %s",
        (code, ticker, code, ticker),
    )
    conn.commit()
    cur.close()
    conn.close()

_ISHARES_FONDS_KOLOMMEN = ["portfolio_id", "isin", "naam", "product_url", "asset_class", "regio", "markt_type",
                           "sub_asset_class", "strategie_codes", "fondsgrootte"]


def db_get_ishares_fondsen():
    """None als de lijst leeg of ouder dan ISHARES_FONDSEN_GELDIGHEID is (dan in z'n geheel opnieuw ophalen)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(_ISHARES_FONDS_KOLOMMEN)} FROM ishares_fondsen "
        f"WHERE opgehaald_op > NOW() - INTERVAL '{ISHARES_FONDSEN_GELDIGHEID}'"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        return None
    fondsen = [dict(zip(_ISHARES_FONDS_KOLOMMEN, row)) for row in rows]
    for f in fondsen:
        f["fondsgrootte"] = float(f["fondsgrootte"]) if f["fondsgrootte"] is not None else None
        f["strategie_codes"] = f["strategie_codes"] or []
    return fondsen


def db_save_ishares_fondsen(fondsen):
    """Delete + bulk insert: de hele lijst krijgt dezelfde opgehaald_op. Niet aanroepen met een lege lijst."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM ishares_fondsen")
    execute_values(
        cur,
        f"INSERT INTO ishares_fondsen ({', '.join(_ISHARES_FONDS_KOLOMMEN)}) VALUES %s "
        "ON CONFLICT (portfolio_id) DO NOTHING",
        [tuple(Json(f[k]) if k == "strategie_codes" else f.get(k) for k in _ISHARES_FONDS_KOLOMMEN)
         for f in fondsen],
    )
    conn.commit()
    cur.close()
    conn.close()


def db_get_etf_proxies(isins):
    """{bron_isin: {proxy_isin, proxy_naam, max_afwijking_pp, vergelijking, proxy_land}}; ontbreekt = nog nooit gezocht."""
    if not isins:
        return {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT bron_isin, proxy_isin, proxy_naam, max_afwijking_pp, vergelijking, proxy_land "
        "FROM etf_proxy WHERE bron_isin = ANY(%s)",
        (list(isins),),
    )
    result = {
        row[0]: {
            "proxy_isin": row[1], "proxy_naam": row[2],
            "max_afwijking_pp": float(row[3]) if row[3] is not None else None,
            "vergelijking": row[4], "proxy_land": row[5],
        }
        for row in cur.fetchall()
    }
    cur.close()
    conn.close()
    return result


def db_save_etf_proxy(bron_isin, proxy):
    """Upsert; proxy_isin None legt vast dat er geen proxy is gevonden."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO etf_proxy (bron_isin, proxy_isin, proxy_naam, max_afwijking_pp, vergelijking, proxy_land) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (bron_isin) DO UPDATE SET proxy_isin = EXCLUDED.proxy_isin, proxy_naam = EXCLUDED.proxy_naam, "
        "max_afwijking_pp = EXCLUDED.max_afwijking_pp, vergelijking = EXCLUDED.vergelijking, "
        "proxy_land = EXCLUDED.proxy_land, bepaald_op = CURRENT_TIMESTAMP",
        (bron_isin, proxy.get("proxy_isin"), proxy.get("proxy_naam"), proxy.get("max_afwijking_pp"),
         Json(proxy.get("vergelijking")), Json(proxy.get("proxy_land")) if proxy.get("proxy_land") else None),
    )
    conn.commit()
    cur.close()
    conn.close()


def db_wis_etf_proxies_voor_portfolio(code):
    """Bij 'Ticker-informatie opnieuw bepalen': de volgende verrijking zoekt de proxy dan opnieuw."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM etf_proxy WHERE bron_isin IN (SELECT DISTINCT isin FROM transacties WHERE code = %s)",
        (code,),
    )
    conn.commit()
    cur.close()
    conn.close()
