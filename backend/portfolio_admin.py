"""Portfolio-codes genereren/valideren en een upload koppelen aan een bestaande portfolio."""
import os
import re
import secrets
import string

from backend.transactie_utils import SYNTHETISCH_ORDER_ID_PREFIX
from backend.db import db_get_order_id_sets_met_overlap, db_portfolio_bestaat_met_cursor, db_probeer_portfolio_te_maken

CODE_LENGTH = 3


def is_geldige_code(code):
    return bool(re.fullmatch(rf"[A-Z]{{{CODE_LENGTH}}}", code or ""))


ONTWIKKEL_CODES_VARIABELE = "ONTWIKKEL_CODES"
ONTWIKKEL_CODES_SCHEIDER = ","

MAX_CODE_POGINGEN = 100
MAX_BIJNAAM_LENGTE = 100

MELDING_GEEN_VRIJE_CODE = "Er is nu geen vrije code beschikbaar. Probeer het later opnieuw."
MELDING_UPLOAD_HOORT_BIJ_BESTAANDE = ("Dit bestand hoort bij een bestaande portfolio. Haal die op met je code, "
                                      "of upload een volledige export.")
MELDING_BIJNAAM_TE_LANG = f"Een bijnaam mag maximaal {MAX_BIJNAAM_LENGTE} tekens bevatten."


class GeenVrijeCode(Exception):
    pass


class UploadHoortBijBestaandePortfolio(Exception):
    """De upload is een deelverzameling van een bestaande portfolio; de code mag niet worden prijsgegeven."""


def valideer_bijnaam(bijnaam):
    """Foutmelding of None."""
    return MELDING_BIJNAAM_TE_LANG if len(bijnaam) > MAX_BIJNAAM_LENGTE else None


def ontwikkel_codes():
    """Codes uit de omgevingsvariabele; ontbreekt die, dan is geen enkele portfolio een ontwikkel-portfolio."""
    ruw = os.environ.get(ONTWIKKEL_CODES_VARIABELE, "")
    return {c.strip().upper() for c in ruw.split(ONTWIKKEL_CODES_SCHEIDER) if c.strip()}


def is_ontwikkel_code(code):
    return bool(code) and code.upper() in ontwikkel_codes()


def generate_code(cur, length=CODE_LENGTH, uitgesloten=frozenset()):
    for _ in range(MAX_CODE_POGINGEN):
        code = "".join(secrets.choice(string.ascii_uppercase) for _ in range(length))
        if code not in uitgesloten and not db_portfolio_bestaat_met_cursor(cur, code):
            return code
    raise GeenVrijeCode(MELDING_GEEN_VRIJE_CODE)


def maak_portfolio_met_nieuwe_code(cur, naam):
    """Twee uploads tegelijk kunnen dezelfde vrije code kiezen: de verliezer probeert een andere."""
    geprobeerd = set()
    for _ in range(MAX_CODE_POGINGEN):
        code = generate_code(cur, uitgesloten=geprobeerd)
        if db_probeer_portfolio_te_maken(cur, code, naam):
            return code
        geprobeerd.add(code)
    raise GeenVrijeCode(MELDING_GEEN_VRIJE_CODE)


def echte_order_ids(ids):
    return {i for i in ids if not str(i).startswith(SYNTHETISCH_ORDER_ID_PREFIX)}


def ids_om_te_vergelijken(opgeslagen, ids):
    """Alleen de echte Order ID's: een SYN-ID hangt aan datum, tijd, aantal en bedrag en kan in een nieuwere export
    veranderen. Een portfolio met alleen SYN-ID's heeft niets anders om op te herkennen."""
    return echte_order_ids(ids) if echte_order_ids(opgeslagen) else set(ids)


def find_matching_code(cur, new_order_ids):
    """Geeft (code, ontbrekende_order_ids) als een bestaande set ⊆ upload (nieuwere export), anders (None, None).
    Is de upload een deelverzameling van een bestaande portfolio, dan UploadHoortBijBestaandePortfolio."""
    existing = db_get_order_id_sets_met_overlap(cur, new_order_ids)
    for code, ids in existing.items():
        if ids_om_te_vergelijken(ids, ids) <= ids_om_te_vergelijken(ids, new_order_ids):
            return code, new_order_ids - ids
    for ids in existing.values():
        nieuw = ids_om_te_vergelijken(ids, new_order_ids)
        if nieuw and nieuw <= ids:
            raise UploadHoortBijBestaandePortfolio(MELDING_UPLOAD_HOORT_BIJ_BESTAANDE)
    return None, None


FOUT_ANDERE_PORTFOLIO = "andere_portfolio"
FOUT_TRANSACTIES_ONTBREKEN = "transacties_ontbreken"
FOUT_GEEN_ORDER_IDS = "geen_order_ids"
FOUT_ONBEKENDE_TRANSACTIES = "onbekende_transacties"


def controleer_eigen_transactiebestand(opgeslagen, nieuw, ids_andere_portfolios):
    """Strenger dan find_matching_code(): het bestand moet alle opgeslagen Order ID's bevatten.
    Geeft (foutcode, None) of (None, toe_te_voegen_order_ids)."""
    vergelijk_nieuw = ids_om_te_vergelijken(opgeslagen, nieuw)
    if echte_order_ids(nieuw) & ids_andere_portfolios or (opgeslagen and not vergelijk_nieuw & opgeslagen):
        return FOUT_ANDERE_PORTFOLIO, None
    if not ids_om_te_vergelijken(opgeslagen, opgeslagen) <= vergelijk_nieuw:
        return FOUT_TRANSACTIES_ONTBREKEN, None
    return None, nieuw - opgeslagen


def controleer_eigen_rekeningoverzicht(rekening_ids, bekende_ids):
    """Foutcode, of None als elke Order ID uit het rekeningoverzicht bij deze portfolio hoort."""
    if not rekening_ids:
        return FOUT_GEEN_ORDER_IDS
    if not rekening_ids <= bekende_ids:
        return FOUT_ONBEKENDE_TRANSACTIES
    return None
