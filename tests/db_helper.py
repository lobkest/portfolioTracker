"""Gedeelde skip-decorator voor tests die een echte, bereikbare database nodig hebben."""
import os
import unittest

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SKIP_REDEN = (
    "Geen bereikbare database (DATABASE_URL ontbreekt of verbinding mislukt) -- "
    "draait wel in de CI-job met Postgres of lokaal met .env"
)


def database_beschikbaar(url=None):
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        # 10 s: Neon start na inactiviteit traag op; korter skipt een echte database ten onrechte.
        psycopg2.connect(url, connect_timeout=10).close()
        return True
    except Exception:
        return False


vereist_database = unittest.skipUnless(database_beschikbaar(), SKIP_REDEN)
