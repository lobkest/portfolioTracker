"""Kleine, gedeelde diagnostiek-helpers zonder eigen afhankelijkheden op
andere projectmodules — gebruikt door vrijwel elke andere module
(dprint/meet_tijd waren voorheen bovenaan analysis.py gedefinieerd)."""
import time
from contextlib import contextmanager

# Zet op True om overal in het project debug-prints aan te zetten.
DEBUG = True


def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


@contextmanager
def meet_tijd(label):
    """Herbruikbare timing-helper voor de performance-meting van de upload/
    analyse-flow: logt de verstreken tijd van het omsloten codeblok met een
    [timing]-prefix, in lijn met de bestaande [upload]/[koersen]/[split]-
    prefix-conventie. Eén centrale plek i.p.v. losse
    `t0 = time.time(); ...; time.time() - t0`-boilerplate in elke functie
    die een fase wil timen."""
    start = time.time()
    try:
        yield
    finally:
        print(f"[timing] {label}: {time.time() - start:.2f}s")
