import os
import threading
import time
from contextlib import contextmanager

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extensions import TRANSACTION_STATUS_IDLE
from psycopg2.extras import execute_values, Json
from dotenv import load_dotenv
from flask import g, has_app_context
from transactie_utils import formatteer_transacties_overzicht

load_dotenv()


# Globaal per proces, net als de Yahoo-tellers: gelijktijdige requests tellen bij elkaar op.
_verbinding_lock = threading.Lock()
_verbinding_teller = {"aantal": 0, "seconden": 0.0}


def db_reset_verbinding_teller():
    with _verbinding_lock:
        _verbinding_teller["aantal"] = 0
        _verbinding_teller["seconden"] = 0.0


def db_verbinding_teller_stand():
    """(aantal, seconden verbinden) sinds de laatste reset."""
    with _verbinding_lock:
        return _verbinding_teller["aantal"], _verbinding_teller["seconden"]


def db_log_verbinding_samenvatting():
    with _verbinding_lock:
        aantal, seconden = _verbinding_teller["aantal"], _verbinding_teller["seconden"]
    print(f"[timing] DB-verbindingen sinds laatste reset: {aantal}, samen {seconden:.2f}s verbinden")


def _open_verbinding():
    start = time.time()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with _verbinding_lock:
        _verbinding_teller["aantal"] += 1
        _verbinding_teller["seconden"] += time.time() - start
    return conn


_G_ATTR_DEEL_VERBINDING = "_db_deel_verbinding"
_G_ATTR_GEDEELDE_VERBINDING = "_db_gedeelde_verbinding"


class _GedeeldeVerbinding:
    """close() doet alleen een rollback: de verbinding gaat door voor de volgende db_-functie van dezelfde request."""

    def __init__(self, verbinding):
        self.verbinding = verbinding

    def close(self):
        self.verbinding.rollback()

    def __getattr__(self, naam):
        return getattr(self.verbinding, naam)


@contextmanager
def db_deel_verbinding():
    """Binnen het blok gebruiken de db_-functies van deze request één verbinding (scheelt ~65 ms per functie).
    Alleen voor leespaden: de rollback in close() zou een open schrijftransactie van de aanroeper terugdraaien.
    Worker-threads hebben geen request-context en houden hun eigen verbindingen."""
    if not has_app_context():
        yield
        return
    setattr(g, _G_ATTR_DEEL_VERBINDING, True)
    try:
        yield
    finally:
        setattr(g, _G_ATTR_DEEL_VERBINDING, False)
        gedeeld = g.pop(_G_ATTR_GEDEELDE_VERBINDING, None)
        if gedeeld is not None:
            gedeeld.verbinding.close()


def db_connect():
    if not (has_app_context() and getattr(g, _G_ATTR_DEEL_VERBINDING, False)):
        return _open_verbinding()
    gedeeld = getattr(g, _G_ATTR_GEDEELDE_VERBINDING, None)
    if gedeeld is None or gedeeld.verbinding.closed:
        gedeeld = _GedeeldeVerbinding(_open_verbinding())
        setattr(g, _G_ATTR_GEDEELDE_VERBINDING, gedeeld)
    # Een eerdere functie die op een fout stopte, liet de transactie open (of afgebroken) staan.
    if gedeeld.verbinding.info.transaction_status != TRANSACTION_STATUS_IDLE:
        gedeeld.verbinding.rollback()
    return gedeeld


@contextmanager
def db_transactie():
    """Cursor voor één transactie: commit na het blok, rollback bij een exception; sluit altijd."""
    conn = db_connect()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


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
            uitvoeringsplaats TEXT,
            koers_lokaal NUMERIC,
            koers_valuta TEXT,
            lokale_waarde NUMERIC,
            lokale_waarde_valuta TEXT,
            autofx_kosten NUMERIC,
            UNIQUE (code, order_id)
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
        CREATE TABLE IF NOT EXISTS koers_begin (
            ticker TEXT PRIMARY KEY,
            -- Een volledige download vanaf deze datum begon later: Yahoo heeft niets eerder.
            gevraagd_vanaf DATE NOT NULL,
            bijgewerkt_op TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticker_info (
            ticker TEXT PRIMARY KEY,
            is_etf BOOLEAN NOT NULL,
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
        CREATE TABLE IF NOT EXISTS ticker_dividenden (
            ticker TEXT PRIMARY KEY,
            -- {iso_ex_datum: bedrag per aandeel, in Yahoo-valuta}
            dividenden JSONB NOT NULL,
            dividend_rate NUMERIC,
            trailing_rate NUMERIC,
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
        CREATE TABLE IF NOT EXISTS rekening_regels (
            id SERIAL PRIMARY KEY,
            code TEXT NOT NULL,
            regel_id TEXT NOT NULL,
            datum DATE,
            tijd TIME,
            valutadatum DATE,
            product TEXT,
            isin TEXT,
            omschrijving TEXT,
            fx NUMERIC,
            mutatie_valuta TEXT,
            mutatie NUMERIC,
            saldo_valuta TEXT,
            saldo NUMERIC,
            order_id TEXT,
            UNIQUE (code, regel_id)
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kassaldo (
            code TEXT PRIMARY KEY,
            saldo_eur NUMERIC NOT NULL,
            netto_gestort_eur NUMERIC NOT NULL,
            eerste_datum DATE NOT NULL,
            per_datum DATE NOT NULL,
            vanaf_opening BOOLEAN NOT NULL
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


def db_save_classification(ticker, is_etf, details=None):
    """details (optioneel): {"quote_type", "valuta", "yahoo_beurs", "fund_family", "category", "long_name"}.
    Een ontbrekende long_name overschrijft een bekende niet."""
    details = details or {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_info (ticker, is_etf, quote_type, valuta, yahoo_beurs, fund_family, category, long_name) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET is_etf = EXCLUDED.is_etf, "
        "quote_type = EXCLUDED.quote_type, valuta = EXCLUDED.valuta, "
        "yahoo_beurs = EXCLUDED.yahoo_beurs, fund_family = EXCLUDED.fund_family, "
        "category = EXCLUDED.category, long_name = COALESCE(EXCLUDED.long_name, ticker_info.long_name), "
        "bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, is_etf, details.get("quote_type"), details.get("valuta"), details.get("yahoo_beurs"),
         details.get("fund_family"), details.get("category"), details.get("long_name")),
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
        "SELECT ticker, is_etf, quote_type, valuta, yahoo_beurs, fund_family, category, long_name "
        "FROM ticker_info WHERE ticker = ANY(%s)",
        (tickers,),
    )
    kolommen = ["is_etf", "quote_type", "valuta", "yahoo_beurs", "fund_family", "category", "long_name"]
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


def db_get_cached_prijschecks(ticker, datums):
    """{datum: (yahoo_slotkoers, valuta, high, low)} voor de datums die al in de cache staan."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT datum, yahoo_slotkoers, valuta, high, low FROM ticker_prijscheck WHERE ticker = %s AND datum = ANY(%s)",
        (ticker, list(datums)),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {
        datum: (
            float(koers) if koers is not None else None,
            valuta,
            float(high) if high is not None else None,
            float(low) if low is not None else None,
        )
        for datum, koers, valuta, high, low in rows
    }


def db_save_prijschecks(ticker, rijen):
    """rijen: [(datum, koers, valuta, high, low)]; zelfde upsert als db_save_prijscheck(), in één statement."""
    if not rijen:
        return
    conn = db_connect()
    cur = conn.cursor()
    execute_values(
        cur,
        "INSERT INTO ticker_prijscheck (ticker, datum, yahoo_slotkoers, valuta, high, low) VALUES %s "
        "ON CONFLICT (ticker, datum) DO UPDATE SET yahoo_slotkoers = EXCLUDED.yahoo_slotkoers, "
        "valuta = EXCLUDED.valuta, high = EXCLUDED.high, low = EXCLUDED.low, "
        "opgehaald_op = CURRENT_TIMESTAMP",
        [(ticker, datum, koers, valuta, high, low) for datum, koers, valuta, high, low in rijen],
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


def db_get_cached_ticker_dividenden(ticker):
    """{dividenden, dividend_rate, trailing_rate} of None (niet gecachet of verlopen)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT dividenden, dividend_rate, trailing_rate FROM ticker_dividenden "
        f"WHERE ticker = %s AND bijgewerkt_op > NOW() - INTERVAL '{CACHE_GELDIGHEID}'",
        (ticker,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    dividenden, dividend_rate, trailing_rate = row
    return {
        "dividenden": dividenden,
        "dividend_rate": float(dividend_rate) if dividend_rate is not None else None,
        "trailing_rate": float(trailing_rate) if trailing_rate is not None else None,
    }


def db_save_ticker_dividenden(ticker, dividenden, dividend_rate, trailing_rate):
    """Ook een leeg dict cachen: dat betekent 'keert niet uit'."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ticker_dividenden (ticker, dividenden, dividend_rate, trailing_rate) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (ticker) DO UPDATE SET dividenden = EXCLUDED.dividenden, "
        "dividend_rate = EXCLUDED.dividend_rate, trailing_rate = EXCLUDED.trailing_rate, "
        "bijgewerkt_op = CURRENT_TIMESTAMP",
        (ticker, Json(dividenden), dividend_rate, trailing_rate),
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
    cur.execute("DELETE FROM kassaldo WHERE code = %s", (code,))
    cur.execute("DELETE FROM rekening_regels WHERE code = %s", (code,))
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
        cur.execute("UPDATE kassaldo SET code = %s WHERE code = %s", (nieuwe_code, oude_code))
        cur.execute("UPDATE rekening_regels SET code = %s WHERE code = %s", (nieuwe_code, oude_code))
        cur.execute("DELETE FROM portfolios WHERE code = %s", (oude_code,))
        conn.commit()
        return True, None
    finally:
        cur.close()
        conn.close()


def db_save_dividenden(cur, code, records):
    """Bewust een upsert, zie CLAUDE.md: DeGiro-bestanden."""
    if not records:
        return
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


def db_save_kassaldo(cur, code, kassaldo):
    cur.execute(
        "INSERT INTO kassaldo (code, saldo_eur, netto_gestort_eur, eerste_datum, per_datum, vanaf_opening) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET "
        "saldo_eur = EXCLUDED.saldo_eur, netto_gestort_eur = EXCLUDED.netto_gestort_eur, "
        "eerste_datum = EXCLUDED.eerste_datum, per_datum = EXCLUDED.per_datum, "
        "vanaf_opening = EXCLUDED.vanaf_opening",
        (code, kassaldo["saldo_eur"], kassaldo["netto_gestort_eur"], kassaldo["eerste_datum"],
         kassaldo["per_datum"], kassaldo["vanaf_opening"]),
    )


def db_get_kassaldo(code):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT saldo_eur, netto_gestort_eur, eerste_datum, per_datum, vanaf_opening FROM kassaldo WHERE code = %s",
        (code,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        return None
    saldo, netto_gestort, eerste_datum, per_datum, vanaf_opening = row
    return {
        "saldo_eur": float(saldo), "netto_gestort_eur": float(netto_gestort),
        "eerste_datum": eerste_datum, "per_datum": per_datum, "vanaf_opening": bool(vanaf_opening),
    }


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


def db_get_gecachte_koersen(tickers, start_datum):
    """Geeft ({ticker: (eerste_datum, laatste_datum)}, {ticker: laatste bijgewerkt_op}, [(ticker, datum, koers_eur)])."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT ticker, MIN(datum), MAX(datum) FROM koersen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    datums = {ticker: (eerste, laatste) for ticker, eerste, laatste in cur.fetchall()}

    # Niet op de rij van vandaag: die bestaat buiten beurstijd (en in het weekend) niet, en dan werkte de 2-minutendrempel nooit.
    cur.execute(
        "SELECT ticker, MAX(bijgewerkt_op) FROM koersen WHERE ticker = ANY(%s) GROUP BY ticker",
        (tickers,),
    )
    laatst_bijgewerkt = {ticker: bijgewerkt_op for ticker, bijgewerkt_op in cur.fetchall()}

    cur.execute(
        "SELECT ticker, datum, koers_eur FROM koersen WHERE ticker = ANY(%s) AND datum >= %s",
        (tickers, start_datum),
    )
    koersen = cur.fetchall()
    cur.close()
    conn.close()
    return datums, laatst_bijgewerkt, koersen


def db_get_koers_begin(tickers):
    """{ticker: (gevraagd_vanaf, bijgewerkt_op)}; verlopen beoordeelt de aanroeper."""
    if not tickers:
        return {}
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT ticker, gevraagd_vanaf, bijgewerkt_op FROM koers_begin WHERE ticker = ANY(%s)", (list(tickers),))
    rijen = cur.fetchall()
    cur.close()
    conn.close()
    return {ticker: (gevraagd_vanaf, bijgewerkt_op) for ticker, gevraagd_vanaf, bijgewerkt_op in rijen}


def db_save_koers_begin(gevraagd_vanaf_per_ticker):
    """DO UPDATE: een latere download vanaf een eerdere datum vervangt de oude rij."""
    if not gevraagd_vanaf_per_ticker:
        return
    conn = db_connect()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO koers_begin (ticker, gevraagd_vanaf, bijgewerkt_op) VALUES (%s, %s, NOW()) "
        "ON CONFLICT (ticker) DO UPDATE SET gevraagd_vanaf = EXCLUDED.gevraagd_vanaf, "
        "bijgewerkt_op = EXCLUDED.bijgewerkt_op",
        list(gevraagd_vanaf_per_ticker.items()),
    )
    conn.commit()
    cur.close()
    conn.close()


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


TRANSACTIE_KOLOMMEN = [
    "datum", "product", "isin", "beurs", "ticker", "aantal", "koers", "totaal_eur",
    "echte_naam", "transactiekosten", "waarde_eur", "tijd", "wisselkoers", "autofx_kosten",
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


def db_get_order_id_periodes(code):
    """[(bron 'transacties'/'rekening', order_id, eerste datum, laatste datum, product)] per Order ID, in één query;
    order_id NULL = de rijen zonder Order ID (alleen voor de periode van het bestand)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT 'transacties', order_id, MIN(datum), MAX(datum), MIN(product) FROM transacties "
        "WHERE code = %s GROUP BY order_id "
        "UNION ALL "
        "SELECT 'rekening', order_id, MIN(datum), MAX(datum), MIN(product) FROM rekening_regels "
        "WHERE code = %s GROUP BY order_id",
        (code, code),
    )
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


def db_get_order_id_sets_met_overlap(cur, order_ids):
    """{code: alle Order ID's} van alleen de portfolio's die minstens één van `order_ids` bevatten."""
    if not order_ids:
        return {}
    cur.execute(
        "SELECT code, order_id FROM transacties WHERE order_id IS NOT NULL AND code IN "
        "(SELECT code FROM transacties WHERE order_id = ANY(%s))",
        (list(order_ids),),
    )
    sets = {}
    for code, order_id in cur.fetchall():
        sets.setdefault(code, set()).add(order_id)
    return sets


def db_get_order_ids(cur, code):
    cur.execute("SELECT order_id FROM transacties WHERE code = %s AND order_id IS NOT NULL", (code,))
    return {order_id for (order_id,) in cur.fetchall()}


def db_get_order_ids_bij_andere_portfolios(cur, code, order_ids):
    """De Order ID's uit `order_ids` die al bij een andere portfolio dan `code` staan."""
    if not order_ids:
        return set()
    cur.execute(
        "SELECT DISTINCT order_id FROM transacties WHERE code <> %s AND order_id = ANY(%s)",
        (code, list(order_ids)),
    )
    return {order_id for (order_id,) in cur.fetchall()}


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


BRONKOLOMMEN = (
    ("uitvoeringsplaats", "text"), ("koers_lokaal", "numeric"), ("koers_valuta", "text"),
    ("lokale_waarde", "numeric"), ("lokale_waarde_valuta", "text"), ("autofx_kosten", "numeric"),
)


def db_insert_transactie(cur, code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur,
                      order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers,
                      uitvoeringsplaats=None, koers_lokaal=None, koers_valuta=None,
                      lokale_waarde=None, lokale_waarde_valuta=None, autofx_kosten=None):
    """Geeft False als de rij al bestond (ON CONFLICT DO NOTHING)."""
    cur.execute(
        """INSERT INTO transacties
                   (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur, order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers,
                    uitvoeringsplaats, koers_lokaal, koers_valuta, lokale_waarde, lokale_waarde_valuta, autofx_kosten)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, order_id) DO NOTHING""",
        (code, datum, product, isin, beurs, ticker, aantal, koers, totaal_eur,
         order_id, echte_naam, transactiekosten, waarde_eur, tijd, wisselkoers,
         uitvoeringsplaats, koers_lokaal, koers_valuta, lokale_waarde, lokale_waarde_valuta, autofx_kosten),
    )
    return cur.rowcount != 0


def db_vul_bronkolommen_aan(cur, code, rijen):
    """rijen: (order_id, *waarden in volgorde van BRONKOLOMMEN). Overschrijft nooit een bestaande waarde; geeft het aantal bijgewerkte rijen."""
    if not rijen:
        return 0
    kolommen = [kolom for kolom, _ in BRONKOLOMMEN]
    zet = ", ".join(f"{k} = COALESCE(t.{k}, v.{k})" for k in kolommen)
    # Alleen rijen waar echt iets wordt aangevuld, anders telt een lege Excel-cel elke herupload mee.
    ergens_aan_te_vullen = " OR ".join(f"(t.{k} IS NULL AND v.{k} IS NOT NULL)" for k in kolommen)
    # Casts nodig: een VALUES-kolom met alleen NULLs wordt anders text en botst met numeric.
    template = "(%s::text, %s::text, " + ", ".join(f"%s::{t}" for _, t in BRONKOLOMMEN) + ")"
    # Eén statement (page_size), anders telt rowcount alleen de laatste pagina.
    execute_values(
        cur,
        f"""UPDATE transacties AS t SET {zet}
            FROM (VALUES %s) AS v (code, order_id, {", ".join(kolommen)})
            WHERE t.code = v.code AND t.order_id = v.order_id AND ({ergens_aan_te_vullen})""",
        [(code, *rij) for rij in rijen],
        template=template,
        page_size=len(rijen),
    )
    return cur.rowcount


REKENING_REGEL_KOLOMMEN = (
    "regel_id", "datum", "tijd", "valutadatum", "product", "isin", "omschrijving", "fx",
    "mutatie_valuta", "mutatie", "saldo_valuta", "saldo", "order_id",
)


def db_save_rekening_regels(cur, code, regels):
    """Geeft het aantal nieuw ingevoegde regels."""
    if not regels:
        return 0
    # DO NOTHING is hier juist: regel_id is een hash van de hele rij, een gewijzigde rij krijgt een andere id.
    execute_values(
        cur,
        f"INSERT INTO rekening_regels (code, {', '.join(REKENING_REGEL_KOLOMMEN)}) VALUES %s "
        "ON CONFLICT (code, regel_id) DO NOTHING",
        [(code, *(regel[k] for k in REKENING_REGEL_KOLOMMEN)) for regel in regels],
        page_size=len(regels),
    )
    return cur.rowcount


def db_wijzig_ticker_voor_isins(cur, code, isins, beurs, ticker):
    cur.execute(
        "UPDATE transacties SET ticker = %s WHERE code = %s AND isin = ANY(%s) AND beurs = %s",
        (ticker, code, list(isins), beurs),
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
