"""Portfolio's zonder gebruik van meer dan BEWAARTERMIJN_MAANDEN worden automatisch verwijderd."""
import threading
import time
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

from db import db_get_verlopen_codes, db_delete_portfolio

BEWAARTERMIJN_MAANDEN = 6
OPRUIM_MIN_INTERVAL_SECONDEN = 3600

_lock = threading.Lock()
_laatste_opruiming = {"tijdstip": None}


def verloopgrens(nu):
    """Wie vóór dit moment (UTC, naive) voor het laatst is gebruikt, is verlopen; precies op de grens nog niet."""
    return nu - relativedelta(months=BEWAARTERMIJN_MAANDEN)


def _nu_utc():
    # Neon draait in UTC (zie CLAUDE.md: Data en rekenen), dus ook laatst_gebruikt.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ruim_verlopen_portfolios_op(nu=None, klok=time.monotonic):
    """Geeft het aantal verwijderde portfolio's, of None als er het afgelopen uur al is opgeruimd of het mislukte.
    Logt nooit codes, alleen het aantal."""
    with _lock:
        tijdstip = klok()
        eerder = _laatste_opruiming["tijdstip"]
        if eerder is not None and tijdstip - eerder < OPRUIM_MIN_INTERVAL_SECONDEN:
            return None
        _laatste_opruiming["tijdstip"] = tijdstip
    try:
        codes = db_get_verlopen_codes(verloopgrens(nu or _nu_utc()))
        for code in codes:
            db_delete_portfolio(code)
    except Exception as e:
        print(f"[opruimen] WARN opruimen mislukt ({type(e).__name__})")
        return None
    print(f"[opruimen] {len(codes)} verlopen portfolio('s) verwijderd")
    return len(codes)
