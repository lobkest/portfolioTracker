"""Gedeelde skip-decorator voor tests die een echte, lokale database nodig hebben."""
import os
import unittest
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv()

TOEGESTANE_HOSTS = {"localhost", "127.0.0.1"}

SKIP_REDEN = (
    "Database niet beschikbaar of geen lokale testdatabase (alleen localhost/127.0.0.1 is toegestaan) "
    "-- draait in de CI-job met Postgres of lokaal met Docker"
)


def database_beschikbaar(url=None):
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        return False
    # Vóór de verbindingspoging: tests mogen nooit in een externe (Neon-productie)database schrijven.
    if urlparse(url).hostname not in TOEGESTANE_HOSTS:
        return False
    try:
        # 10 s: een koude database start soms traag op; korter skipt een echte database ten onrechte.
        psycopg2.connect(url, connect_timeout=10).close()
        return True
    except Exception:
        return False


vereist_database = unittest.skipUnless(database_beschikbaar(), SKIP_REDEN)
