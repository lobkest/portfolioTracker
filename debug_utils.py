"""Logging-helpers voor het hele project (dprint, meet_tijd)."""
import os
import re
import time
import traceback
from contextlib import contextmanager

# diagnostiek.py importeert zelf geen projectmodules (alleen Flask), dus
# geen circulaire import.
from diagnostiek import meld_laadtijd

# Zet op True om overal in het project debug-prints aan te zetten.
DEBUG = True

# Per-stap [timing]-regels (30-40 per request) staan standaard uit; lokaal aan met DEBUG_TIMING_DETAIL=1 in .env.
TIMING_DETAIL_VARIABELE = "DEBUG_TIMING_DETAIL"
TIMING_TOTAAL_LABELS = frozenset({
    "ophalen_totaal", "verrijking_totaal", "upload_totaal", "bijwerken_totaal",
    "dividend_verwachting", "historisch_rendement",
})


def timing_detail_aan():
    return os.environ.get(TIMING_DETAIL_VARIABELE) == "1"


# Postgres noemt de waarden van een sleutel in de foutmelding, dus ook de portfolio-code.
_CODE_IN_DB_FOUT = re.compile(r"(Key \(code(?:, \w+)*\)=\()[A-Z]{3}")
# De code staat in het pad: /p/<code> en /api/portfolio/<code>/...
_CODE_IN_PAD = re.compile(r"^(/p/|/api/portfolio/)[^/?#]+")


def maskeer_codes(tekst):
    return _CODE_IN_DB_FOUT.sub(r"\1***", str(tekst))


def maskeer_pad(pad):
    return _CODE_IN_PAD.sub(r"\1***", str(pad))


def print_traceback():
    print(maskeer_codes(traceback.format_exc()), end="")


def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


def tprint(*args, **kwargs):
    """Detail-timing: alleen als timing_detail_aan()."""
    if DEBUG and timing_detail_aan():
        print(*args, **kwargs)


@contextmanager
def meet_tijd(label):
    """Logt de duur van het omsloten codeblok als [timing] (zonder detail-timing alleen de totalen) en meldt hem aan de Diagnostiek."""
    start = time.time()
    try:
        yield
    finally:
        duur = time.time() - start
        if str(label).split(" (")[0] in TIMING_TOTAAL_LABELS:
            print(f"[timing] {label}: {duur:.2f}s")
        else:
            tprint(f"[timing] {label}: {duur:.2f}s")
        meld_laadtijd(label, duur)
