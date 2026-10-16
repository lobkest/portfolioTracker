"""Voortgang van een upload (de laadbalk), als events in een queue; importeert geen projectmodules.

Buiten een actieve voortgang is meld_stap() een stille no-op, zodat functies er niets van merken.
"""
import contextvars
from contextlib import contextmanager
import queue
import time

# (stap_id, tekst, gewicht): het gewicht is het geschatte aandeel van de totale duur, zie bereken_procent().
STAPPEN_UPLOAD_OPSLAAN = (
    ("bestand_inlezen", "Bestand inlezen", 5),
    ("portfolio_zoeken", "Controleren of deze portfolio al bestaat", 5),
    ("tickers_zoeken", "Tickers zoeken", 30),
    ("transacties_opslaan", "Transacties opslaan", 5),
    ("rekeningoverzicht", "Rekeningoverzicht verwerken", 5),
    ("koersen_analyseren", "Koersen ophalen en analyseren", 45),
    ("klaar_maken", "Klaar maken", 5),
)

STAPPEN_UPLOAD_NIET_OPSLAAN = (
    ("bestand_inlezen", "Bestand inlezen", 5),
    ("tickers_zoeken", "Tickers zoeken", 35),
    ("koersen_analyseren", "Koersen ophalen en analyseren", 55),
    ("klaar_maken", "Klaar maken", 5),
)


STAPPEN_BIJWERKEN = (
    ("bestand_inlezen", "Bestand(en) inlezen", 5),
    ("bestanden_controleren", "Controleren of de bestanden bij deze portfolio horen", 5),
    ("tickers_zoeken", "Tickers zoeken", 30),
    ("transacties_opslaan", "Opslaan", 10),
    ("koersen_analyseren", "Koersen ophalen en analyseren", 45),
    ("klaar_maken", "Klaar maken", 5),
)

STAPPEN_OPHALEN = (
    ("tickers_herbepalen", "Tickers opnieuw bepalen", 50),
    ("koersen_analyseren", "Koersen ophalen en analyseren", 45),
    ("klaar_maken", "Klaar maken", 5),
)

# Zonder "opnieuw bepalen" begint de balk bij de koersen.
STAPPEN_OPHALEN_ZONDER_HERBEPALEN = STAPPEN_OPHALEN[1:]


def bereken_procent(stappen, stap_id, gedaan=0, totaal=0):
    """Percentage in de stap: begin van de stap (de gewichten ervoor, geschaald naar 0-100), met gedaan/totaal lineair
    richting het begin van de volgende stap."""
    som = sum(gewicht for _, _, gewicht in stappen)
    ervoor = 0
    for id_, _, gewicht in stappen:
        if id_ == stap_id:
            begin = ervoor * 100 / som
            if totaal <= 0:
                return round(begin)
            eind = (ervoor + gewicht) * 100 / som
            aandeel = min(max(gedaan, 0), totaal) / totaal
            return round(begin + (eind - begin) * aandeel)
        ervoor += gewicht
    raise KeyError(stap_id)


def stand_verschil(begin, eind):
    """Verschil per teller tussen twee standen; niet negatief (een reset door een andere request geeft anders min)."""
    return tuple(max(0, nu - toen) for nu, toen in zip(eind, begin))


def stap_logregel(stap_id, seconden, begin, eind):
    """Standen zijn (db-verbindingen, seconden verbinden, yahoo-calls)."""
    db_aantal, db_seconden, yahoo_calls = stand_verschil(begin, eind)
    return (f"[voortgang] stap {stap_id}: {seconden:.2f}s, {db_aantal} DB-verbinding(en) ({db_seconden:.2f}s), "
            f"{yahoo_calls} Yahoo-call(s)")


class Voortgang:
    def __init__(self, stappen, stand=None, flow=""):
        """`stand()` geeft (db-verbindingen, seconden verbinden, yahoo-calls); zonder stand wordt er niets gelogd."""
        self.stappen = stappen
        self.events = queue.Queue()
        self._teksten = {id_: tekst for id_, tekst, _ in stappen}
        self._stap = None
        self._procent = 0
        self._stand = stand
        self._flow = flow
        self._stap_begin = None
        self._flow_begin = None

    def _sluit_stap_af(self):
        if self._stand is None or self._stap is None:
            return
        nu, stand = time.time(), self._stand()
        print(stap_logregel(self._stap, nu - self._stap_begin[0], self._stap_begin[1], stand))
        self._stap_begin = None

    def start_stap(self, stap_id):
        if stap_id not in self._teksten:
            return
        if self._stand is not None:
            if self._stap_begin is not None:
                self._sluit_stap_af()
            if self._flow_begin is None:
                self._flow_begin = (time.time(), self._stand())
            self._stap_begin = (time.time(), self._stand())
        self._stap = stap_id
        self._procent = max(self._procent, bereken_procent(self.stappen, stap_id))
        self.events.put({
            "type": "voortgang", "stap": stap_id, "tekst": self._teksten[stap_id], "procent": self._procent,
        })

    def teller(self, gedaan, totaal):
        """Alleen aanroepen vanuit de thread die de voortgang heeft. Hooguit één event per procentpunt, plus het laatste."""
        if self._stap is None or totaal <= 0:
            return
        procent = max(self._procent, bereken_procent(self.stappen, self._stap, gedaan, totaal))
        if procent == self._procent and gedaan < totaal:
            return
        self._procent = procent
        self.events.put({
            "type": "voortgang", "stap": self._stap, "tekst": self._teksten[self._stap], "procent": procent,
            "gedaan": gedaan, "totaal": totaal,
        })

    def klaar(self, status, data):
        if self._stand is not None and self._flow_begin is not None:
            if self._stap_begin is not None:
                self._sluit_stap_af()
            print(stap_logregel(f"totaal ({self._flow})", time.time() - self._flow_begin[0],
                                self._flow_begin[1], self._stand()))
        self.events.put({"type": "klaar", "status": status, "data": data})


_huidige = contextvars.ContextVar("huidige_voortgang", default=None)
_tellers_uit = contextvars.ContextVar("voortgang_tellers_uit", default=False)


def huidige_voortgang():
    return _huidige.get()


def zet_voortgang(voortgang):
    return _huidige.set(voortgang)


def reset_voortgang(token):
    _huidige.reset(token)


def meld_stap(stap_id):
    voortgang = _huidige.get()
    if voortgang is not None:
        voortgang.start_stap(stap_id)


@contextmanager
def zonder_tellers():
    """Voor een geneste telling (bv. de FX-koers midden in een koersdownload): die mag de teller van de buitenste niet overschrijven."""
    token = _tellers_uit.set(True)
    try:
        yield
    finally:
        _tellers_uit.reset(token)


def meld_teller(gedaan, totaal):
    voortgang = _huidige.get()
    if voortgang is not None and not _tellers_uit.get():
        voortgang.teller(gedaan, totaal)
