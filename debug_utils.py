"""Logging-helpers voor het hele project (dprint, meet_tijd)."""
import re
import time
import traceback
from contextlib import contextmanager

# diagnostiek.py importeert zelf geen projectmodules (alleen Flask), dus
# geen circulaire import.
from diagnostiek import meld_laadtijd

# Zet op True om overal in het project debug-prints aan te zetten.
DEBUG = True


# Postgres noemt de waarden van een sleutel in de foutmelding, dus ook de portfolio-code.
_CODE_IN_DB_FOUT = re.compile(r"(Key \(code(?:, \w+)*\)=\()[A-Z]{3}")


def maskeer_codes(tekst):
    return _CODE_IN_DB_FOUT.sub(r"\1***", str(tekst))


def print_traceback():
    print(maskeer_codes(traceback.format_exc()), end="")


def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


@contextmanager
def meet_tijd(label):
    """Logt de duur van het omsloten codeblok als [timing] en meldt hem aan de Diagnostiek."""
    start = time.time()
    try:
        yield
    finally:
        duur = time.time() - start
        print(f"[timing] {label}: {duur:.2f}s")
        meld_laadtijd(label, duur)
