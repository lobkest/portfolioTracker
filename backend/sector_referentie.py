"""Sector per bedrijf opzoeken in een breed iShares-fonds, voor fondsen waarvan het holdingsbestand geen sectorkolom heeft."""
import re

from backend.etf_holdings_provider import (
    SECTOR_MIN_HERKEND_PCT, SECTOR_ONBEKEND, app_sector as vertaal_sector, normaliseer_land,
)

# iShares Core MSCI World: ±1300 bedrijven met sector, genoeg voor dividend- en wereldfondsen (TDIV: ±92 % gevonden).
# Een tweede fonds (MSCI EM IMI) voegt voor TDIV niets toe: de ontbrekende namen zijn ontwikkelde markten.
SECTOR_REFERENTIE_ISINS = ("IE00B4L5Y983",)
SECTOR_REFERENTIE_NAAM = "iShares MSCI World"

# Bloomberg-achtervoegsel ("PFE US") of beurs-achtervoegsel ("ASML.AS"); een enkele letter ("BRK.B") is een aandelenklasse.
_TICKER_ACHTERVOEGSEL = re.compile(r"(\s+[A-Z]{2}|\.[A-Z]{2,3})$")


def normaliseer_ticker(ticker):
    schoon = _TICKER_ACHTERVOEGSEL.sub("", str(ticker or "").strip().upper())
    return schoon if re.search(r"[A-Z0-9]", schoon) else ""


def _naam_sleutel(naam):
    # Lokaal: portfolio_verdeling importeert ticker_classificatie, dat deze module importeert.
    from backend.portfolio_verdeling import _normaliseer_bedrijfsnaam
    return _normaliseer_bedrijfsnaam(naam)


def _sleutels(holding):
    return (("isin", str(holding.get("isin") or "").strip().upper()),
            ("ticker", normaliseer_ticker(holding.get("ticker"))),
            ("naam", _naam_sleutel(holding.get("naam"))))


def _maak_opzoektabel(referentie_holdings, waarde_van):
    kandidaten = {"isin": {}, "ticker": {}, "naam": {}}
    for h in referentie_holdings:
        waarde = waarde_van(h)
        if waarde == SECTOR_ONBEKEND:
            continue
        for soort, sleutel in _sleutels(h):
            if sleutel:
                kandidaten[soort].setdefault(sleutel, set()).add(waarde)
    return {soort: {sleutel: next(iter(waarden)) for sleutel, waarden in per_sleutel.items() if len(waarden) == 1}
            for soort, per_sleutel in kandidaten.items()}


def maak_sector_opzoektabel(referentie_holdings):
    """{"isin": {...}, "ticker": {...}, "naam": {...}}: sleutel -> app-sector. Een sleutel met verschillende sectoren
    (dubbelzinnig) valt weg, net als rijen zonder herkende sector (cash)."""
    return _maak_opzoektabel(referentie_holdings, lambda h: vertaal_sector(h.get("sector")))


def maak_land_opzoektabel(referentie_holdings):
    """Zelfde vorm als maak_sector_opzoektabel, met het land uit de Location-kolom; "Unknown" valt weg."""
    return _maak_opzoektabel(referentie_holdings, lambda h: normaliseer_land(h.get("land")))


def zoek_land_sector(opzoektabel, ticker, naam, isin):
    """(land of None, sector of None): per eigenschap eerst op ISIN, dan ticker, dan naam. Het landdeel zit onder
    opzoektabel["land"] (zie maak_land_opzoektabel)."""
    sleutels = _sleutels({"isin": isin, "ticker": ticker, "naam": naam})
    land_tabel = opzoektabel.get("land") or {}

    def _zoek(tabel):
        return next((tabel[soort][sleutel] for soort, sleutel in sleutels if sleutel and sleutel in tabel.get(soort, {})), None)

    return _zoek(land_tabel), _zoek(opzoektabel)


def sector_verdeling_via_referentie(holdings, opzoektabel):
    """(verdeling {app_sector: fractie 0-1}, dekking in %): per holding eerst op ISIN, dan ticker, dan naam. Onder
    SECTOR_MIN_HERKEND_PCT gevonden gewicht is de verdeling {} (de dekking blijft wel gemeld)."""
    per_sector = {}
    for h in holdings:
        for soort, sleutel in _sleutels(h):
            app_sector = opzoektabel[soort].get(sleutel) if sleutel else None
            if app_sector:
                per_sector[app_sector] = per_sector.get(app_sector, 0.0) + h["gewicht"]
                break
    dekking = sum(per_sector.values())
    if dekking < SECTOR_MIN_HERKEND_PCT:
        return {}, dekking
    return {sector: gewicht / 100.0 for sector, gewicht in per_sector.items()}, dekking
