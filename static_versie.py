import hashlib
import os

VERSIE_LENGTE = 10


def bestand_hash(pad):
    with open(pad, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:VERSIE_LENGTE]


class StaticVersies:
    """Inhoudshash per static-bestand, één keer berekend en daarna uit het geheugen."""

    def __init__(self, static_map):
        self.static_map = static_map
        self._cache = {}

    def versie(self, filename):
        if filename not in self._cache:
            pad = os.path.join(self.static_map, *filename.split("/"))
            self._cache[filename] = bestand_hash(pad) if os.path.isfile(pad) else None
        return self._cache[filename]


def registreer_static_versies(app):
    versies = StaticVersies(app.static_folder)

    @app.url_defaults
    def _voeg_static_versie_toe(endpoint, values):
        if endpoint != "static" or "v" in values or "filename" not in values:
            return
        versie = versies.versie(values["filename"])
        if versie:
            values["v"] = versie

    return versies
