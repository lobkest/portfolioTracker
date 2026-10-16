"""Voortgang van een upload (de laadbalk), als events in een queue; importeert geen projectmodules.

Buiten een actieve voortgang is meld_stap() een stille no-op, zodat functies er niets van merken.
"""
import contextvars
from contextlib import contextmanager
import queue

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


class Voortgang:
    def __init__(self, stappen):
        self.stappen = stappen
        self.events = queue.Queue()
        self._teksten = {id_: tekst for id_, tekst, _ in stappen}
        self._stap = None
        self._procent = 0

    def start_stap(self, stap_id):
        if stap_id not in self._teksten:
            return
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
