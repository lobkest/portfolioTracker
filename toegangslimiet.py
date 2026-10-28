"""Limieten per IP en globaal, in het geheugen. Pure logica met een injecteerbare klok; Flask-koppeling staat in app.py."""
import math
import threading
import time
from collections import deque
from typing import NamedTuple


class Regel(NamedTuple):
    maximum: int
    venster_seconden: int


SECONDEN_PER_MINUUT = 60
SECONDEN_PER_UUR = 3600
SECONDEN_PER_DAG = 86400

CATEGORIE_FOUTE_CODE = "foute_code"
CATEGORIE_FOUTE_CODE_GLOBAAL = "foute_code_globaal"
CATEGORIE_GEVOELIGE_ACTIE = "gevoelige_actie"
CATEGORIE_UPLOAD = "upload"
CATEGORIE_EXTERNE_BRON = "externe_bron"
CATEGORIE_BEREKENING = "berekening"
CATEGORIE_ZOEKEN = "zoeken"

REGELS_FOUTE_CODE = (Regel(10, 10 * SECONDEN_PER_MINUUT), Regel(30, SECONDEN_PER_DAG))
REGELS_FOUTE_CODE_GLOBAAL = (Regel(200, SECONDEN_PER_UUR),)
REGELS_GEVOELIGE_ACTIE = (Regel(5, SECONDEN_PER_UUR),)
REGELS_UPLOAD = (Regel(10, SECONDEN_PER_UUR),)
REGELS_EXTERNE_BRON = (Regel(60, SECONDEN_PER_UUR),)
REGELS_BEREKENING = (Regel(600, SECONDEN_PER_UUR),)
REGELS_ZOEKEN = (Regel(200, SECONDEN_PER_UUR),)

REGELS_PER_CATEGORIE = {
    CATEGORIE_FOUTE_CODE: REGELS_FOUTE_CODE,
    CATEGORIE_FOUTE_CODE_GLOBAAL: REGELS_FOUTE_CODE_GLOBAAL,
    CATEGORIE_GEVOELIGE_ACTIE: REGELS_GEVOELIGE_ACTIE,
    CATEGORIE_UPLOAD: REGELS_UPLOAD,
    CATEGORIE_EXTERNE_BRON: REGELS_EXTERNE_BRON,
    CATEGORIE_BEREKENING: REGELS_BEREKENING,
    CATEGORIE_ZOEKEN: REGELS_ZOEKEN,
}

SLEUTEL_GLOBAAL = "*"

# Een aanvaller met veel IP's mag het geheugen niet vullen.
MAX_BIJGEHOUDEN_SLEUTELS = 50_000


class Toegangslimiet:
    def __init__(self, klok=time.monotonic, regels_per_categorie=None):
        self._klok = klok
        self._regels = regels_per_categorie or REGELS_PER_CATEGORIE
        self._lock = threading.Lock()
        self._gebeurtenissen = {}

    def wachttijd(self, categorie, sleutel):
        """Seconden tot de limiet weer ruimte geeft (0 = vrij). Registreert niets."""
        with self._lock:
            return self._wachttijd(categorie, sleutel, self._klok())

    def registreer(self, categorie, sleutel):
        with self._lock:
            self._registreer(categorie, sleutel, self._klok())

    def verbruik(self, categorie, sleutel):
        """Check en registratie in één stap: 0 = toegestaan en geteld, anders de wachttijd (niet geteld)."""
        with self._lock:
            nu = self._klok()
            wacht = self._wachttijd(categorie, sleutel, nu)
            if wacht == 0:
                self._registreer(categorie, sleutel, nu)
            return wacht

    def wis(self):
        with self._lock:
            self._gebeurtenissen.clear()

    def _wachttijd(self, categorie, sleutel, nu):
        tijden = self._gebeurtenissen.get((categorie, sleutel))
        if not tijden:
            return 0
        wacht = 0.0
        for regel in self._regels[categorie]:
            in_venster = [t for t in tijden if nu - t < regel.venster_seconden]
            if len(in_venster) >= regel.maximum:
                # De poging die het maximum vulde, valt als eerste buiten het venster.
                vrij_op = in_venster[-regel.maximum] + regel.venster_seconden
                wacht = max(wacht, vrij_op - nu)
        return math.ceil(wacht) if wacht > 0 else 0

    def _registreer(self, categorie, sleutel, nu):
        if len(self._gebeurtenissen) >= MAX_BIJGEHOUDEN_SLEUTELS:
            self._ruim_op(nu)
        tijden = self._gebeurtenissen.setdefault((categorie, sleutel), deque())
        tijden.append(nu)
        langste = max(r.venster_seconden for r in self._regels[categorie])
        while tijden and nu - tijden[0] >= langste:
            tijden.popleft()

    def _ruim_op(self, nu):
        for (categorie, sleutel), tijden in list(self._gebeurtenissen.items()):
            langste = max(r.venster_seconden for r in self._regels[categorie])
            while tijden and nu - tijden[0] >= langste:
                tijden.popleft()
            if not tijden:
                del self._gebeurtenissen[(categorie, sleutel)]


def wachttijd_tekst(seconden):
    """Wachttijd als 'over N minuten' (naar boven afgerond, minstens 1)."""
    minuten = max(1, math.ceil(seconden / SECONDEN_PER_MINUUT))
    if minuten >= 120:
        uren = math.ceil(minuten / 60)
        return f"over {uren} uur"
    return f"over {minuten} minuut" if minuten == 1 else f"over {minuten} minuten"
