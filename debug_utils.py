"""Logging-helpers voor het hele project (dprint, meet_tijd)."""
import time
from contextlib import contextmanager

# diagnostiek.py importeert zelf geen projectmodules (alleen Flask), dus
# geen circulaire import.
from diagnostiek import meld_laadtijd

# Zet op True om overal in het project debug-prints aan te zetten.
DEBUG = True


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
