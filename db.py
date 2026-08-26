import os
import psycopg2
from psycopg2.extras import execute_values
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
            totaal_eur NUMERIC NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prijzen (
            ticker TEXT NOT NULL,
            datum DATE NOT NULL,
            koers_eur NUMERIC NOT NULL,
            PRIMARY KEY (ticker, datum)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


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